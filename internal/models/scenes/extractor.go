package scenes

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/google/uuid"
	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/proj-airi/recorder-minecraft/internal/models/replays"
	"github.com/samber/do/v2"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

const maxPoses = 256 * 1024 * 1024

type Options struct {
	Metadata  string
	Events    string
	Replay    string
	FromTick  *int64
	ToTick    *int64
	Output    string
	Overwrite bool
}

type Job struct {
	Directory    string
	Manifest     string
	Result       string
	Stream       string
	PlayerStates string
	JobID        string
	SessionID    string
	PlayerUUID   string
	ConnectionID string
	FirstTick    int64
	LastTick     int64
	StateTicks   []int64
	Replay       replays.Source
	Poses        *artifactsv1.ArtifactFile
}

type Extractor struct {
	config   *configs.Config
	captures *captures.Service
	replays  *replays.Service
}

func NewExtractor(injector do.Injector) (*Extractor, error) {
	config, err := do.Invoke[*configs.Config](injector)
	if err != nil {
		return nil, err
	}
	captureService, err := do.Invoke[*captures.Service](injector)
	if err != nil {
		return nil, err
	}
	replayService, err := do.Invoke[*replays.Service](injector)
	if err != nil {
		return nil, err
	}
	return &Extractor{config: config, captures: captureService, replays: replayService}, nil
}

func (extractor *Extractor) Prepare(options Options) (Job, error) {
	metadata, err := extractor.captures.LoadMetadata(options.Metadata)
	if err != nil {
		return Job{}, err
	}
	var poses bytes.Buffer
	var states bytes.Buffer
	var ticks []int64
	events, err := extractor.captures.ScanEvents(options.Events, metadata, func(event captures.Event) error {
		state := event.Message.GetPlayerState()
		if state == nil || options.FromTick != nil && event.ServerTick < *options.FromTick || options.ToTick != nil && event.ServerTick > *options.ToTick {
			return nil
		}
		pose := &artifactsv1.SubjectPose{
			ServerTick: event.ServerTick, SessionId: metadata.SessionID, PlayerUuid: metadata.PlayerUUID,
			ConnectionId: metadata.ConnectionID, EntityId: int32(event.Message.GetIdentity().GetEntityId()),
			Dimension: state.GetDimension(), Position: state.GetPosition(), Velocity: state.GetVelocity(),
			Rotation: state.GetRotation(), OnGround: state.GetOnGround(),
		}
		if err := appendProtoJSON(&poses, pose); err != nil {
			return err
		}
		if err := appendProtoJSON(&states, event.Message); err != nil {
			return err
		}
		if poses.Len() > maxPoses {
			return fmt.Errorf("subject pose stream exceeds the %d-byte safety limit", maxPoses)
		}
		ticks = append(ticks, event.ServerTick)
		return nil
	})
	if err != nil {
		return Job{}, err
	}
	if len(ticks) == 0 {
		return Job{}, errors.New("the completed capture has no player_state ticks in the requested range")
	}
	for index, tick := range ticks {
		if tick != ticks[0]+int64(index) {
			return Job{}, errors.New("the selected subject timeline is duplicated, out of order, or not contiguous")
		}
	}
	replay, err := extractor.replays.Verify(options.Replay, metadata.PlayerUUID, metadata.ConnectionID)
	if err != nil {
		return Job{}, err
	}
	jobID := uuid.NewString()
	directory := options.Output
	if directory == "" {
		directory = filepath.Join(extractor.config.Paths.Runtime, "scene-jobs", jobID)
	}
	directory, err = prepareDirectory(directory, options.Overwrite)
	if err != nil {
		return Job{}, err
	}
	staging, err := os.MkdirTemp(filepath.Dir(directory), "."+filepath.Base(directory)+".tmp-*")
	if err != nil {
		return Job{}, fmt.Errorf("create scene staging directory: %w", err)
	}
	defer func() { _ = os.RemoveAll(staging) }()
	poseBytes := poses.Bytes()
	poseHash := sha256.Sum256(poseBytes)
	posePath := filepath.Join(directory, "subject-poses.jsonl")
	streamPath := filepath.Join(directory, "stream")
	poseFile := &artifactsv1.ArtifactFile{Path: posePath, Sha256: hex.EncodeToString(poseHash[:]), SizeBytes: uint64(len(poseBytes)), MediaType: "application/jsonl"}
	manifest := &artifactsv1.SceneExtractionJob{
		JobId: jobID, SessionId: metadata.SessionID, PlayerUuid: metadata.PlayerUUID, ConnectionId: metadata.ConnectionID,
		Ticks: &artifactsv1.TickRange{FirstTick: ticks[0], LastTick: ticks[len(ticks)-1]}, Scope: "client_visible",
		MetadataPolicy: "full_packet_metadata", FlashbackCaptureContract: replay.FlashbackCaptureContract,
		SourceReplays: []*artifactsv1.SceneSourceReplay{{SegmentId: replay.ReplayID, Path: replay.Path, Sha256: replay.SHA256, SizeBytes: uint64(replay.SizeBytes), Format: replay.Format}},
		SubjectPoses:  poseFile,
		SourceEvents:  &artifactsv1.ArtifactFile{Path: events.Path, Sha256: events.SHA256, SizeBytes: uint64(events.SizeBytes), MediaType: "application/jsonl"},
		OutputPath:    streamPath, StopWhenDone: true,
	}
	if err := writeFile(filepath.Join(staging, "subject-poses.jsonl"), poseBytes); err != nil {
		return Job{}, err
	}
	if err := writeFile(filepath.Join(staging, "player-states.jsonl"), states.Bytes()); err != nil {
		return Job{}, err
	}
	encodedJob, err := (protojson.MarshalOptions{Indent: "  "}).Marshal(manifest)
	if err != nil {
		return Job{}, err
	}
	if err := writeFile(filepath.Join(staging, "scene-job.json"), append(encodedJob, '\n')); err != nil {
		return Job{}, err
	}
	if err := os.Rename(staging, directory); err != nil {
		return Job{}, fmt.Errorf("publish scene job: %w", err)
	}
	return Job{
		Directory: directory, Manifest: filepath.Join(directory, "scene-job.json"), Result: filepath.Join(directory, "result.json"),
		Stream: streamPath, PlayerStates: filepath.Join(directory, "player-states.jsonl"), JobID: jobID,
		SessionID: metadata.SessionID, PlayerUUID: metadata.PlayerUUID, ConnectionID: metadata.ConnectionID,
		FirstTick: ticks[0], LastTick: ticks[len(ticks)-1], StateTicks: ticks, Replay: replay, Poses: poseFile,
	}, nil
}

func (extractor *Extractor) Launch(ctx context.Context, job Job) (*artifactsv1.SceneExtractionResult, error) {
	executable := os.Getenv("MC_RECORDER_SCENE_EXTRACTOR")
	if executable == "" {
		executable = extractor.config.Processors.SceneExtractorExecutable
	}
	if !filepath.IsAbs(executable) {
		return nil, errors.New("MC_RECORDER_SCENE_EXTRACTOR must be an absolute executable path")
	}
	info, err := os.Stat(executable) // #nosec G703
	if err != nil || !info.Mode().IsRegular() || info.Mode()&0o111 == 0 {
		return nil, fmt.Errorf("scene extractor executable not found or not executable: %s; run 'pixi run build-scene-extractor'", executable)
	}
	command := exec.CommandContext(ctx, executable, "--job", job.Manifest) // #nosec G702
	command.Dir = job.Directory
	command.Env = os.Environ()
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	runErr := command.Run()
	result, readErr := readResult(job.Result)
	if runErr != nil {
		return nil, fmt.Errorf("scene extractor failed: %w", runErr)
	}
	if readErr != nil {
		return nil, readErr
	}
	if result.GetStatus() != artifactsv1.SceneExtractionResult_STATUS_COMPLETE {
		return nil, fmt.Errorf("scene extraction failed: %s", result.GetError())
	}
	if result.GetJobId() != job.JobID || result.GetSessionId() != job.SessionID || result.GetPlayerUuid() != job.PlayerUUID ||
		result.GetConnectionId() != job.ConnectionID || result.GetTicks().GetFirstTick() != job.FirstTick || result.GetTicks().GetLastTick() != job.LastTick {
		return nil, errors.New("scene extractor result identity or tick range does not match its job")
	}
	return result, nil
}

func readResult(path string) (*artifactsv1.SceneExtractionResult, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read scene result %s: %w", path, err)
	}
	result := &artifactsv1.SceneExtractionResult{}
	if err := protojson.Unmarshal(raw, result); err != nil {
		return nil, fmt.Errorf("decode scene result %s: %w", path, err)
	}
	return result, nil
}

func prepareDirectory(path string, overwrite bool) (string, error) {
	directory, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	if info, err := os.Lstat(directory); err == nil {
		if !overwrite {
			return "", fmt.Errorf("scene job output exists: %s; pass --overwrite to replace it", directory)
		}
		if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() || !owned(directory) {
			return "", fmt.Errorf("refusing to replace non-owned scene job directory: %s", directory)
		}
		if err := os.RemoveAll(directory); err != nil {
			return "", err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(directory), 0o750); err != nil {
		return "", err
	}
	return directory, nil
}

func owned(directory string) bool {
	raw, err := os.ReadFile(filepath.Join(directory, "scene-job.json"))
	if err != nil {
		return false
	}
	job := &artifactsv1.SceneExtractionJob{}
	return protojson.Unmarshal(raw, job) == nil && job.GetJobId() != "" && filepath.Clean(job.GetOutputPath()) == filepath.Join(directory, "stream")
}

func appendProtoJSON(destination *bytes.Buffer, value proto.Message) error {
	encoded, err := protojson.Marshal(value)
	if err != nil {
		return err
	}
	destination.Write(encoded)
	destination.WriteByte('\n')
	return nil
}

func (*Extractor) Cleanup(job Job) error {
	if !owned(job.Directory) {
		return fmt.Errorf("refusing to remove non-owned scene job directory: %s", job.Directory)
	}
	return os.RemoveAll(job.Directory)
}

func writeFile(path string, data []byte) error {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	if _, err := file.Write(data); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return err
	}
	return file.Close()
}
