package captures

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/google/uuid"
	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/samber/do/v2"
	"google.golang.org/protobuf/encoding/protojson"
)

const (
	maxMetadataBytes  = 4 * 1024 * 1024
	maxEventLineBytes = 64 * 1024 * 1024
)

type Metadata struct {
	Path         string
	SessionID    string
	PlayerUUID   string
	ConnectionID string
	StartTick    int64
	EndTick      int64
}

type Event struct {
	Message      *artifactsv1.CaptureEvent
	RecordType   string
	ServerTick   int64
	Sequence     uint64
	PlayerUUID   string
	ConnectionID string
}

type EventsSource struct {
	Path        string
	SHA256      string
	SizeBytes   int64
	RecordCount uint64
}

type Service struct{}

func NewService(do.Injector) (*Service, error) {
	return &Service{}, nil
}

func (*Service) LoadMetadata(path string) (Metadata, error) {
	resolved, info, err := regularFile(path, "capture metadata")
	if err != nil {
		return Metadata{}, err
	}
	if info.Size() > maxMetadataBytes {
		return Metadata{}, fmt.Errorf("capture metadata exceeds %d bytes", maxMetadataBytes)
	}
	raw, err := os.ReadFile(resolved)
	if err != nil {
		return Metadata{}, fmt.Errorf("read capture metadata: %w", err)
	}
	after, err := os.Stat(resolved)
	if err != nil || !os.SameFile(info, after) || info.Size() != after.Size() || !info.ModTime().Equal(after.ModTime()) {
		return Metadata{}, errors.New("capture metadata changed while it was being read")
	}
	value := &artifactsv1.ServerMetadata{}
	if err := protojson.Unmarshal(raw, value); err != nil {
		return Metadata{}, fmt.Errorf("capture metadata is not valid ProtoJSON: %w", err)
	}
	if value.GetSchemaVersion() != 1 || value.GetLayoutVersion() != "v1" {
		return Metadata{}, errors.New("capture metadata has an unsupported schema or layout version")
	}
	if value.GetCapture().GetEvents() != "capture/events.jsonl" || value.GetCapture().GetReplay() != "capture/replay.zip" || value.GetCapture().GetReplayFormat() != "flashback" {
		return Metadata{}, errors.New("capture metadata does not declare the canonical primitive inputs")
	}
	if value.GetConnection().EndServerTick == nil {
		return Metadata{}, errors.New("capture is incomplete; metadata end tick must be written before processing")
	}
	if value.GetSessionId() == "" || len(value.GetSessionId()) > 160 {
		return Metadata{}, errors.New("capture session_id is invalid")
	}
	player, err := canonicalUUID(value.GetPlayer().GetUuid(), "capture player UUID")
	if err != nil {
		return Metadata{}, err
	}
	connection, err := canonicalUUID(value.GetConnection().GetId(), "capture connection ID")
	if err != nil {
		return Metadata{}, err
	}
	if value.GetConnection().GetEndServerTick() < value.GetConnection().GetStartServerTick() {
		return Metadata{}, errors.New("capture end tick precedes its start tick")
	}
	return Metadata{resolved, value.GetSessionId(), player, connection, value.GetConnection().GetStartServerTick(), value.GetConnection().GetEndServerTick()}, nil
}

func (*Service) ScanEvents(path string, metadata Metadata, visit func(Event) error) (EventsSource, error) {
	resolved, before, err := regularFile(path, "capture events")
	if err != nil {
		return EventsSource{}, err
	}
	file, err := os.Open(resolved)
	if err != nil {
		return EventsSource{}, fmt.Errorf("read capture events: %w", err)
	}
	defer func() { _ = file.Close() }()
	hash := sha256.New()
	reader := bufio.NewReaderSize(io.TeeReader(file, hash), 128*1024)
	var count uint64
	var size int64
	var previous uint64
	for recordNumber := 1; ; recordNumber++ {
		line, readErr := reader.ReadBytes('\n')
		if len(line) > maxEventLineBytes {
			return EventsSource{}, fmt.Errorf("%s record %d exceeds the size limit", resolved, recordNumber)
		}
		if len(line) == 0 && errors.Is(readErr, io.EOF) {
			break
		}
		if len(line) == 0 || line[len(line)-1] != '\n' || (len(line) > 1 && line[len(line)-2] == '\r') {
			return EventsSource{}, fmt.Errorf("%s record %d must be LF-terminated ProtoJSON", resolved, recordNumber)
		}
		message := &artifactsv1.CaptureEvent{}
		if err := protojson.Unmarshal(line[:len(line)-1], message); err != nil {
			return EventsSource{}, fmt.Errorf("%s record %d: invalid CaptureEvent ProtoJSON: %w", resolved, recordNumber, err)
		}
		event := eventView(message)
		if err := validateEvent(resolved, recordNumber, metadata, event, previous, count > 0); err != nil {
			return EventsSource{}, err
		}
		previous = event.Sequence
		count++
		if err := visit(event); err != nil {
			return EventsSource{}, err
		}
		if readErr != nil && !errors.Is(readErr, io.EOF) {
			return EventsSource{}, fmt.Errorf("read capture events: %w", readErr)
		}
	}
	if count == 0 {
		return EventsSource{}, errors.New("capture events file is empty")
	}
	after, err := os.Stat(resolved)
	size = before.Size()
	if err != nil || !os.SameFile(before, after) || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return EventsSource{}, errors.New("capture events changed while they were being read")
	}
	return EventsSource{resolved, hex.EncodeToString(hash.Sum(nil)), size, count}, nil
}

func eventView(message *artifactsv1.CaptureEvent) Event {
	recordType := ""
	switch message.GetRecord().(type) {
	case *artifactsv1.CaptureEvent_ControlState:
		recordType = "control_state"
	case *artifactsv1.CaptureEvent_PacketArrival:
		recordType = "packet_arrival"
	case *artifactsv1.CaptureEvent_PacketApply:
		recordType = "packet_apply"
	case *artifactsv1.CaptureEvent_PlayerState:
		recordType = "player_state"
	case *artifactsv1.CaptureEvent_ReplayTimeline:
		recordType = "replay_timeline"
	}
	identity := message.GetIdentity()
	return Event{message, recordType, identity.GetServerTick(), identity.GetSequence(), identity.GetPlayerUuid(), identity.GetConnectionId()}
}

func validateEvent(path string, line int, metadata Metadata, event Event, previous uint64, hasPrevious bool) error {
	prefix := fmt.Sprintf("%s:%d", path, line)
	identity := event.Message.GetIdentity()
	if identity.GetSchemaVersion() != 1 || event.RecordType == "" {
		return fmt.Errorf("%s: unsupported event schema", prefix)
	}
	if identity.GetSessionId() != metadata.SessionID {
		return fmt.Errorf("%s: event session does not match metadata", prefix)
	}
	if event.PlayerUUID != metadata.PlayerUUID || event.ConnectionID != metadata.ConnectionID {
		return fmt.Errorf("%s: event subject does not match metadata", prefix)
	}
	if event.ServerTick < metadata.StartTick || event.ServerTick > metadata.EndTick {
		return fmt.Errorf("%s: event tick is outside capture bounds", prefix)
	}
	if hasPrevious && event.Sequence <= previous {
		return fmt.Errorf("%s: event sequence is not strictly increasing", prefix)
	}
	return nil
}

func regularFile(path, label string) (string, os.FileInfo, error) {
	resolved, err := filepath.Abs(path)
	if err != nil {
		return "", nil, fmt.Errorf("resolve %s: %w", label, err)
	}
	info, err := os.Lstat(resolved)
	if err != nil {
		return "", nil, fmt.Errorf("inspect %s: %w", label, err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return "", nil, fmt.Errorf("%s must be a non-symlinked regular file: %s", label, resolved)
	}
	return resolved, info, nil
}

func canonicalUUID(value, label string) (string, error) {
	parsed, err := uuid.Parse(value)
	if err != nil || parsed.String() != value {
		return "", fmt.Errorf("%s must be a canonical UUID", label)
	}
	return value, nil
}
