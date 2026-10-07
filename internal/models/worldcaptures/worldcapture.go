// Package worldcaptures reads the session-level world stream written by the
// recorder mod under world/sessions/<start>--<session>/.
package worldcaptures

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/samber/do/v2"
	"google.golang.org/protobuf/encoding/protojson"
)

const (
	maxMetadataBytes  = 4 * 1024 * 1024
	maxEventLineBytes = 64 * 1024 * 1024

	// Scope and Provenance are the only values V1 world sessions declare.
	Scope      = "world"
	Provenance = "engine-reported"
	EventsFile = "world-events.jsonl"

	RecordContainerSnapshot = "container_snapshot"
	RecordContainerRemoved  = "container_removed"
)

type Metadata struct {
	Path      string
	SessionID string
	StartTick int64
	// EndTick is the last tick the stream covers. After a contained stream
	// failure it precedes the end of Plays from the same session.
	EndTick        int64
	TerminalReason string
	// Failure is non-empty when the stream stopped early; Plays continued.
	Failure string
}

type Event struct {
	Message    *artifactsv1.WorldEvent
	RecordType string
	ServerTick int64
	Sequence   uint64
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

// LoadMetadata reads a closed world session metadata.json. A missing end tick
// means the stream never closed, and the session is rejected like an
// incomplete Play.
func (*Service) LoadMetadata(path string) (Metadata, error) {
	resolved, info, err := regularFile(path, "world session metadata")
	if err != nil {
		return Metadata{}, err
	}
	if info.Size() > maxMetadataBytes {
		return Metadata{}, fmt.Errorf("world session metadata exceeds %d bytes", maxMetadataBytes)
	}
	raw, err := os.ReadFile(resolved)
	if err != nil {
		return Metadata{}, fmt.Errorf("read world session metadata: %w", err)
	}
	after, err := os.Stat(resolved)
	if err != nil || !os.SameFile(info, after) || info.Size() != after.Size() || !info.ModTime().Equal(after.ModTime()) {
		return Metadata{}, errors.New("world session metadata changed while it was being read")
	}
	value := &artifactsv1.WorldSessionMetadata{}
	if err := protojson.Unmarshal(raw, value); err != nil {
		return Metadata{}, fmt.Errorf("world session metadata is not valid ProtoJSON: %w", err)
	}
	if value.GetSchemaVersion() != 1 || value.GetLayoutVersion() != "v1" {
		return Metadata{}, errors.New("world session metadata has an unsupported schema or layout version")
	}
	if value.GetScope() != Scope || value.GetProvenance() != Provenance || value.GetEvents() != EventsFile {
		return Metadata{}, errors.New("world session metadata does not declare the canonical world stream")
	}
	session := value.GetSessionId()
	if session == "" || len(session) > 160 {
		return Metadata{}, errors.New("world session_id is invalid")
	}
	// The directory is <started-at>--<session-id>; a renamed or copied
	// directory must not silently describe another session.
	if !strings.HasSuffix(filepath.Base(filepath.Dir(resolved)), "--"+session) {
		return Metadata{}, errors.New("world session directory does not match its session_id")
	}
	if value.EndServerTick == nil {
		return Metadata{}, errors.New("world stream is incomplete; metadata end tick must be written before processing")
	}
	if value.GetEndServerTick() < value.GetStartServerTick() {
		return Metadata{}, errors.New("world stream end tick precedes its start tick")
	}
	return Metadata{
		Path:           resolved,
		SessionID:      session,
		StartTick:      value.GetStartServerTick(),
		EndTick:        value.GetEndServerTick(),
		TerminalReason: value.GetTerminalReason(),
		Failure:        value.GetStreamFailure(),
	}, nil
}

// ScanEvents validates and visits each world event in file order.
func (*Service) ScanEvents(path string, metadata Metadata, visit func(Event) error) (EventsSource, error) {
	resolved, before, err := regularFile(path, "world events")
	if err != nil {
		return EventsSource{}, err
	}
	file, err := os.Open(resolved)
	if err != nil {
		return EventsSource{}, fmt.Errorf("read world events: %w", err)
	}
	defer func() { _ = file.Close() }()
	hash := sha256.New()
	reader := bufio.NewReaderSize(io.TeeReader(file, hash), 128*1024)
	var count uint64
	var previous Event
	for recordNumber := 1; ; recordNumber++ {
		line, readErr := reader.ReadBytes('\n')
		if len(line) > maxEventLineBytes {
			return EventsSource{}, fmt.Errorf("%s record %d exceeds the size limit", resolved, recordNumber)
		}
		if len(line) == 0 && errors.Is(readErr, io.EOF) {
			break
		}
		if readErr != nil && !errors.Is(readErr, io.EOF) {
			return EventsSource{}, fmt.Errorf("read world events: %w", readErr)
		}
		if line[len(line)-1] != '\n' || (len(line) > 1 && line[len(line)-2] == '\r') {
			return EventsSource{}, fmt.Errorf("%s record %d must be LF-terminated ProtoJSON", resolved, recordNumber)
		}
		message := &artifactsv1.WorldEvent{}
		if err := protojson.Unmarshal(line[:len(line)-1], message); err != nil {
			return EventsSource{}, fmt.Errorf("%s record %d: invalid WorldEvent ProtoJSON: %w", resolved, recordNumber, err)
		}
		event := eventView(message)
		if err := validateEvent(fmt.Sprintf("%s:%d", resolved, recordNumber), metadata, event, previous, count > 0); err != nil {
			return EventsSource{}, err
		}
		previous = event
		count++
		if err := visit(event); err != nil {
			return EventsSource{}, err
		}
	}
	after, err := os.Stat(resolved)
	if err != nil || !os.SameFile(before, after) || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return EventsSource{}, errors.New("world events changed while they were being read")
	}
	// Unlike Play events, an empty world stream is valid: a session can end
	// before any container is loaded.
	return EventsSource{resolved, hex.EncodeToString(hash.Sum(nil)), before.Size(), count}, nil
}

func eventView(message *artifactsv1.WorldEvent) Event {
	recordType := ""
	switch message.GetRecord().(type) {
	case *artifactsv1.WorldEvent_ContainerSnapshot:
		recordType = RecordContainerSnapshot
	case *artifactsv1.WorldEvent_ContainerRemoved:
		recordType = RecordContainerRemoved
	}
	identity := message.GetIdentity()
	return Event{message, recordType, identity.GetServerTick(), identity.GetSequence()}
}

func validateEvent(prefix string, metadata Metadata, event, previous Event, hasPrevious bool) error {
	identity := event.Message.GetIdentity()
	if identity.GetSchemaVersion() != 1 || event.RecordType == "" {
		return fmt.Errorf("%s: unsupported world event schema", prefix)
	}
	if identity.GetSessionId() != metadata.SessionID {
		return fmt.Errorf("%s: world event session does not match metadata", prefix)
	}
	if event.ServerTick < metadata.StartTick || event.ServerTick > metadata.EndTick {
		return fmt.Errorf("%s: world event tick is outside stream bounds", prefix)
	}
	if hasPrevious && event.Sequence <= previous.Sequence {
		return fmt.Errorf("%s: world event sequence is not strictly increasing", prefix)
	}
	if hasPrevious && event.ServerTick < previous.ServerTick {
		return fmt.Errorf("%s: world event tick decreases", prefix)
	}
	if snapshot := event.Message.GetContainerSnapshot(); snapshot != nil {
		return validateSnapshot(prefix, snapshot)
	}
	removed := event.Message.GetContainerRemoved()
	if err := validateLocation(prefix, removed.GetDimension(), removed.GetBlockPos(), removed.GetBlockEntityType()); err != nil {
		return err
	}
	if removed.GetCause() == artifactsv1.ContainerRemoved_CAUSE_UNSPECIFIED {
		return fmt.Errorf("%s: container removal has no cause", prefix)
	}
	return nil
}

func validateSnapshot(prefix string, snapshot *artifactsv1.ContainerSnapshot) error {
	if err := validateLocation(prefix, snapshot.GetDimension(), snapshot.GetBlockPos(), snapshot.GetBlockEntityType()); err != nil {
		return err
	}
	if snapshot.GetReason() == artifactsv1.ContainerSnapshot_REASON_UNSPECIFIED {
		return fmt.Errorf("%s: container snapshot has no reason", prefix)
	}
	if snapshot.GetContainerSize() < 0 {
		return fmt.Errorf("%s: container size is negative", prefix)
	}
	switch snapshot.GetContentsState() {
	case artifactsv1.ContainerSnapshot_CONTENTS_STATE_KNOWN:
	case artifactsv1.ContainerSnapshot_CONTENTS_STATE_LOOT_UNGENERATED:
		// Undetermined contents must never be readable as an empty inventory.
		if len(snapshot.GetSlots()) != 0 || snapshot.GetLootTable() == "" {
			return fmt.Errorf("%s: ungenerated loot snapshot must name its loot table and list no slots", prefix)
		}
	default:
		return fmt.Errorf("%s: container snapshot has no contents state", prefix)
	}
	previous := int32(-1)
	for _, slot := range snapshot.GetSlots() {
		if slot.GetSlot() <= previous || slot.GetSlot() >= snapshot.GetContainerSize() {
			return fmt.Errorf("%s: container slot %d is out of order or outside the container", prefix, slot.GetSlot())
		}
		previous = slot.GetSlot()
	}
	return nil
}

func validateLocation(prefix, dimension string, position *artifactsv1.BlockPosition, blockEntityType string) error {
	if dimension == "" || position == nil || blockEntityType == "" {
		return fmt.Errorf("%s: world event has no dimension, block position, or block entity type", prefix)
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
