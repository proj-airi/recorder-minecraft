package actions

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	artifactsv1 "github.com/proj-airi/mc-play-recorder/apis/sdk/go/mc-play-recorder/artifacts/v1"
	"github.com/proj-airi/mc-play-recorder/internal/models/captures"
	"github.com/samber/do/v2"
	"google.golang.org/protobuf/encoding/protojson"
)

type Options struct {
	Metadata  string
	Events    string
	Output    string
	FromTick  *int64
	ToTick    *int64
	Overwrite bool
}

type Result struct {
	Output      string
	RecordCount uint64
	FirstTick   int64
	LastTick    int64
}

type Service struct {
	captures *captures.Service
}

func NewService(injector do.Injector) (*Service, error) {
	captureService, err := do.Invoke[*captures.Service](injector)
	if err != nil {
		return nil, err
	}
	return &Service{captures: captureService}, nil
}

func (service *Service) Extract(options Options) (Result, error) {
	metadata, err := service.captures.LoadMetadata(options.Metadata)
	if err != nil {
		return Result{}, err
	}
	if options.FromTick != nil && options.ToTick != nil && *options.FromTick > *options.ToTick {
		return Result{}, errors.New("first tick cannot be greater than last tick")
	}
	destination, err := prepareOutput(options.Output, options.Overwrite)
	if err != nil {
		return Result{}, err
	}
	temporary, err := os.CreateTemp(filepath.Dir(destination), "."+filepath.Base(destination)+".*.inprogress")
	if err != nil {
		return Result{}, fmt.Errorf("create action staging file: %w", err)
	}
	temporaryName := temporary.Name()
	defer func() { _ = os.Remove(temporaryName) }()
	if err := temporary.Chmod(0o600); err != nil {
		_ = temporary.Close()
		return Result{}, err
	}
	result := Result{Output: destination}
	_, err = service.captures.ScanEvents(options.Events, metadata, func(event captures.Event) error {
		if event.RecordType != "control_state" && event.RecordType != "packet_apply" {
			return nil
		}
		if options.FromTick != nil && event.ServerTick < *options.FromTick || options.ToTick != nil && event.ServerTick > *options.ToTick {
			return nil
		}
		action := &artifactsv1.PlayerAction{
			SchemaVersion: 1, SourceRecordType: event.RecordType, ConnectionId: event.ConnectionID,
			PlayerUuid: event.PlayerUUID, ServerTick: event.ServerTick, Sequence: event.Sequence,
		}
		if state := event.Message.GetControlState(); state != nil {
			action.Payload = &artifactsv1.PlayerAction_ControlState{ControlState: state.GetState()}
		} else if applied := event.Message.GetPacketApply(); applied != nil {
			applySequence := applied.GetApplySequence()
			action.ApplySequence = &applySequence
			packet := applied.GetPacket()
			action.Payload = &artifactsv1.PlayerAction_Packet{Packet: &artifactsv1.PacketAction{
				ActionKind: packet.GetActionKind(), Packet: packet.GetIdentity(),
			}}
		}
		encoded, err := protojson.Marshal(action)
		if err != nil {
			return fmt.Errorf("encode action ProtoJSON: %w", err)
		}
		if _, err := temporary.Write(append(encoded, '\n')); err != nil {
			return fmt.Errorf("write action JSONL: %w", err)
		}
		if result.RecordCount == 0 {
			result.FirstTick = event.ServerTick
		}
		result.LastTick = event.ServerTick
		result.RecordCount++
		return nil
	})
	if err != nil {
		_ = temporary.Close()
		return Result{}, err
	}
	if result.RecordCount == 0 {
		_ = temporary.Close()
		return Result{}, errors.New("no reconstructed actions matched the requested connection and tick range")
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return Result{}, fmt.Errorf("sync action output: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return Result{}, fmt.Errorf("close action output: %w", err)
	}
	if err := os.Rename(temporaryName, destination); err != nil {
		return Result{}, fmt.Errorf("publish action output: %w", err)
	}
	return result, nil
}

func prepareOutput(path string, overwrite bool) (string, error) {
	destination, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("resolve action output: %w", err)
	}
	if info, err := os.Lstat(destination); err == nil {
		if !overwrite {
			return "", fmt.Errorf("action output exists: %s; pass --overwrite to replace it", destination)
		}
		if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
			return "", fmt.Errorf("action output is not a replaceable regular file: %s", destination)
		}
		if !ownedOutput(destination) {
			return "", fmt.Errorf("refusing to replace non-PlayerAction output: %s", destination)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", fmt.Errorf("inspect action output: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o750); err != nil {
		return "", fmt.Errorf("create action output directory: %w", err)
	}
	return destination, nil
}

func ownedOutput(path string) bool {
	file, err := os.Open(path)
	if err != nil {
		return false
	}
	defer func() { _ = file.Close() }()
	reader := bufio.NewReaderSize(file, 1024*1024)
	count := 0
	for {
		line, readErr := reader.ReadSlice('\n')
		if len(line) == 0 && errors.Is(readErr, io.EOF) {
			return count > 0
		}
		if readErr != nil || len(line) == 0 || line[len(line)-1] != '\n' {
			return false
		}
		action := &artifactsv1.PlayerAction{}
		if protojson.Unmarshal(line[:len(line)-1], action) != nil || action.GetSchemaVersion() != 1 || action.GetConnectionId() == "" ||
			action.GetPlayerUuid() == "" || action.GetSourceRecordType() == "" || action.GetPayload() == nil {
			return false
		}
		count++
	}
}
