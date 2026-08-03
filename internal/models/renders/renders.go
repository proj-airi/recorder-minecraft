package renders

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/proj-airi/recorder-minecraft/internal/models/replays"
	"github.com/samber/do/v2"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const (
	jobType = "recorder-minecraft-first-person-render-v1"
	owner   = "recorder-minecraft"
)

var frameName = regexp.MustCompile(`^frame_[0-9]+\.png$`)

type Options struct {
	Metadata    string
	Events      string
	Replay      string
	Output      string
	Width       int
	Height      int
	FPS         int
	FromTick    *int64
	ToTick      *int64
	NoGUI       bool
	Overwrite   bool
	PrepareOnly bool
}

type Job struct {
	Directory string
	Manifest  string
	Spec      *artifactsv1.RenderJob
}

type Service struct {
	config   *configs.Config
	captures *captures.Service
	replays  *replays.Service
}

func NewService(injector do.Injector) (*Service, error) {
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
	return &Service{config: config, captures: captureService, replays: replayService}, nil
}

func (service *Service) Prepare(options Options) (Job, error) {
	if options.Width < 64 || options.Width > 16384 || options.Height < 64 || options.Height > 16384 {
		return Job{}, errors.New("render dimensions must be between 64 and 16384 pixels")
	}
	if options.FPS != 20 {
		return Job{}, errors.New("renderer v1 supports exactly 20 FPS")
	}
	metadata, err := service.captures.LoadMetadata(options.Metadata)
	if err != nil {
		return Job{}, err
	}
	var ticks []int64
	events, err := service.captures.ScanEvents(options.Events, metadata, func(event captures.Event) error {
		if event.RecordType == "player_state" {
			ticks = append(ticks, event.ServerTick)
		}
		return nil
	})
	if err != nil {
		return Job{}, err
	}
	if len(ticks) == 0 {
		return Job{}, errors.New("capture has no player_state records")
	}
	for index, tick := range ticks {
		if tick != ticks[0]+int64(index) {
			return Job{}, errors.New("capture player_state timeline is duplicated, unordered, or incomplete")
		}
	}
	first, last := ticks[0], ticks[len(ticks)-1]
	if options.FromTick != nil {
		first = *options.FromTick
	}
	if options.ToTick != nil {
		last = *options.ToTick
	}
	if first < ticks[0] || last > ticks[len(ticks)-1] || first > last {
		return Job{}, fmt.Errorf("render tick range must be within connection range %d..%d", ticks[0], ticks[len(ticks)-1])
	}
	replay, err := service.replays.Verify(options.Replay, metadata.PlayerUUID, metadata.ConnectionID)
	if err != nil {
		return Job{}, err
	}
	output, err := prepareDirectory(options.Output, options.Overwrite)
	if err != nil {
		return Job{}, err
	}
	staging, err := os.MkdirTemp(filepath.Dir(output), "."+filepath.Base(output)+".tmp-*")
	if err != nil {
		return Job{}, fmt.Errorf("create render staging directory: %w", err)
	}
	defer func() { _ = os.RemoveAll(staging) }()
	if err := os.Mkdir(filepath.Join(staging, "fpv_frames"), 0o750); err != nil {
		return Job{}, err
	}
	frames := filepath.Join(output, "fpv_frames")
	resultPath := filepath.Join(output, "result.json")
	spec := &artifactsv1.RenderJob{
		SchemaVersion: 1, Owner: owner, JobType: jobType, CreatedAt: timestamppb.Now(), Status: artifactsv1.RenderJobStatus_RENDER_JOB_STATUS_PREPARED,
		SessionId: metadata.SessionID, ConnectionId: metadata.ConnectionID, PlayerUuid: metadata.PlayerUUID,
		Replay:      &artifactsv1.RenderReplaySource{ReplayId: replay.ReplayID, Path: replay.Path, Format: replay.Format, Sha256: replay.SHA256, SizeBytes: uint64(replay.SizeBytes)},
		GlobalTicks: &artifactsv1.TickRange{FirstTick: first, LastTick: last}, RangePolicy: artifactsv1.RenderRangePolicy_RENDER_RANGE_POLICY_INTERSECTION,
		Width: uint32(options.Width), Height: uint32(options.Height), FramesPerSecond: float64(options.FPS), NoGui: options.NoGUI, StopWhenDone: true,
		OutputPath: frames, ResultPath: resultPath, ProgressPath: filepath.Join(output, "progress.json"), SourceMetadataPath: metadata.Path,
		SourceEvents:     &artifactsv1.RenderArtifact{Path: events.Path, Sha256: events.SHA256, SizeBytes: uint64(events.SizeBytes), MediaType: "application/jsonl"},
		SourceEventCount: events.RecordCount,
	}
	manifest := filepath.Join(staging, "render-job.json")
	if err := writeProtoJSON(manifest, spec); err != nil {
		return Job{}, err
	}
	if err := os.Rename(staging, output); err != nil {
		return Job{}, fmt.Errorf("publish render job: %w", err)
	}
	return Job{Directory: output, Manifest: filepath.Join(output, "render-job.json"), Spec: spec}, nil
}

func (service *Service) Launch(ctx context.Context, job Job, offline bool) (*artifactsv1.RenderResult, error) {
	if info, err := os.Stat(service.config.Mods.RendererProject); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("renderer mod project not found: %s", service.config.Mods.RendererProject)
	}
	gradle := os.Getenv("MC_RECORDER_GRADLE")
	if gradle == "" {
		gradle = "gradle"
	}
	arguments := []string{"--project-dir", service.config.Mods.RendererProject, "runClient", "--no-daemon", "--console=plain"}
	if offline {
		arguments = append(arguments, "--offline")
	}
	// The override selects an executable directly; exec.CommandContext never
	// passes it through a shell, and every argument is constructed above.
	command := exec.CommandContext(ctx, gradle, arguments...) // #nosec G702
	command.Dir = filepath.Dir(service.config.Source)
	command.Env = append(os.Environ(), "MC_RECORDER_RENDER_JOB="+job.Manifest)
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	runErr := command.Run()
	result := &artifactsv1.RenderResult{}
	readErr := readProtoJSON(job.Spec.GetResultPath(), result)
	if runErr != nil {
		return nil, fmt.Errorf("renderer client failed: %w", runErr)
	}
	if readErr != nil {
		return nil, readErr
	}
	if err := service.validateResult(job, result); err != nil {
		return nil, err
	}
	return result, nil
}

// ComposeVideo converts the verified image sequence to a browser-seekable MP4. The temporary file
// stays beside the final output so the rename is atomic on the artifact filesystem.
func (service *Service) ComposeVideo(ctx context.Context, job Job, result *artifactsv1.RenderResult, executable string) (string, error) {
	if result.GetStatus() != artifactsv1.RenderResultStatus_RENDER_RESULT_STATUS_COMPLETE || result.GetFrameCount() == 0 {
		return "", errors.New("cannot compose video without a completed non-empty render")
	}
	if result.GetWidth()%2 != 0 || result.GetHeight()%2 != 0 {
		return "", errors.New("MP4 composition requires even render dimensions")
	}
	if executable == "" {
		executable = "ffmpeg"
	}
	output := filepath.Join(job.Directory, "fpv.mp4")
	staging := output + ".inprogress"
	if err := os.Remove(staging); err != nil && !errors.Is(err, os.ErrNotExist) {
		return "", fmt.Errorf("remove stale video staging file: %w", err)
	}
	defer func() { _ = os.Remove(staging) }()
	arguments := []string{
		"-hide_banner", "-loglevel", "error", "-y",
		"-framerate", fmt.Sprintf("%g", result.GetFramesPerSecond()),
		"-start_number", "1",
		"-i", filepath.Join(job.Spec.GetOutputPath(), "frame_%06d.png"),
		"-frames:v", fmt.Sprintf("%d", result.GetFrameCount()),
		"-c:v", "libx264", "-crf", "18", "-preset", "medium",
		"-pix_fmt", "yuv420p", "-movflags", "+faststart",
		"-f", "mp4",
		staging,
	}
	// The executable is passed directly to exec without a shell. Every media argument is derived
	// from the already verified RenderResult and the renderer-owned output directory.
	command := exec.CommandContext(ctx, executable, arguments...) // #nosec G702
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if err := command.Run(); err != nil {
		return "", fmt.Errorf("compose MP4 with ffmpeg: %w", err)
	}
	file, err := os.OpenFile(staging, os.O_RDONLY, 0)
	if err != nil {
		return "", fmt.Errorf("open composed MP4: %w", err)
	}
	info, statErr := file.Stat()
	syncErr := file.Sync()
	closeErr := file.Close()
	if statErr != nil || syncErr != nil || closeErr != nil || info.Size() == 0 {
		return "", errors.Join(errors.New("ffmpeg did not produce a durable non-empty MP4"), statErr, syncErr, closeErr)
	}
	if err := os.Rename(staging, output); err != nil {
		return "", fmt.Errorf("publish composed MP4: %w", err)
	}
	return output, nil
}

func (service *Service) validateResult(job Job, result *artifactsv1.RenderResult) error {
	spec := job.Spec
	if spec == nil || spec.GetReplay() == nil || spec.GetGlobalTicks() == nil {
		return errors.New("render job is incomplete")
	}
	if result.GetStatus() != artifactsv1.RenderResultStatus_RENDER_RESULT_STATUS_COMPLETE && result.GetStatus() != artifactsv1.RenderResultStatus_RENDER_RESULT_STATUS_NO_COVERAGE {
		return fmt.Errorf("renderer exited without an atomic terminal result at %s", spec.GetResultPath())
	}
	replay := result.GetReplay()
	if result.GetSchemaVersion() != 1 || result.GetSessionId() != spec.GetSessionId() || result.GetConnectionId() != spec.GetConnectionId() || result.GetPlayerUuid() != spec.GetPlayerUuid() ||
		replay.GetReplayId() != spec.GetReplay().GetReplayId() || replay.GetPath() != spec.GetReplay().GetPath() || replay.GetFormat() != spec.GetReplay().GetFormat() ||
		replay.GetSha256() != spec.GetReplay().GetSha256() || replay.GetSizeBytes() != spec.GetReplay().GetSizeBytes() || result.GetOutputPath() != spec.GetOutputPath() ||
		!sameTicks(result.GetRequestedGlobalTicks(), spec.GetGlobalTicks()) || result.GetWidth() != spec.GetWidth() || result.GetHeight() != spec.GetHeight() ||
		result.GetFramesPerSecond() != spec.GetFramesPerSecond() || result.GetNoGui() != spec.GetNoGui() {
		return errors.New("renderer result does not match its prepared job")
	}
	digest, size, err := service.replays.Digest(spec.GetReplay().GetPath())
	if err != nil {
		return err
	}
	if digest != spec.GetReplay().GetSha256() || uint64(size) != spec.GetReplay().GetSizeBytes() {
		return errors.New("replay integrity does not match the completed renderer result")
	}
	if result.GetStatus() == artifactsv1.RenderResultStatus_RENDER_RESULT_STATUS_NO_COVERAGE {
		if result.GetFrameCount() != 0 || result.GetGlobalTicks() != nil || result.GetReplayTicks() != nil || result.GetFrameIndex() != nil {
			return errors.New("no-coverage renderer result contains frame output")
		}
		return nil
	}
	if result.GetGlobalTicks() == nil || result.GetReplayTicks() == nil || result.GetFrameIndex() == nil {
		return errors.New("completed renderer result is missing its tick or frame-index envelope")
	}
	expectedFrames := result.GetGlobalTicks().GetLastTick() - result.GetGlobalTicks().GetFirstTick() + 1
	if expectedFrames <= 0 || result.GetFrameCount() != uint64(expectedFrames) ||
		result.GetGlobalTicks().GetFirstTick() < spec.GetGlobalTicks().GetFirstTick() || result.GetGlobalTicks().GetLastTick() > spec.GetGlobalTicks().GetLastTick() ||
		result.GetReplayTicks().GetLastTick()-result.GetReplayTicks().GetFirstTick()+1 != expectedFrames {
		return errors.New("completed renderer result has inconsistent tick coverage or frame count")
	}
	index := result.GetFrameIndex()
	expectedIndex := filepath.Join(spec.GetOutputPath(), "frames.jsonl")
	if index.GetPath() != expectedIndex || index.GetMediaType() != "application/jsonl" {
		return errors.New("renderer frame index is outside the prepared output")
	}
	indexDigest, indexSize, err := service.replays.Digest(expectedIndex)
	if err != nil {
		return err
	}
	if indexDigest != index.GetSha256() || uint64(indexSize) != index.GetSizeBytes() {
		return errors.New("renderer frame index integrity does not match its result")
	}
	return validateFrameIndex(spec, result)
}

func sameTicks(left, right *artifactsv1.TickRange) bool {
	return left != nil && right != nil && left.GetFirstTick() == right.GetFirstTick() && left.GetLastTick() == right.GetLastTick()
}

func prepareDirectory(path string, overwrite bool) (string, error) {
	output, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	if info, err := os.Lstat(output); err == nil {
		if !overwrite {
			return "", fmt.Errorf("render job output exists: %s; pass --overwrite to replace it", output)
		}
		if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() || !ownedDirectory(output) {
			return "", fmt.Errorf("refusing to replace non-owned render directory: %s; choose an empty output path", output)
		}
		if err := os.RemoveAll(output); err != nil {
			return "", fmt.Errorf("remove prior render output: %w", err)
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(output), 0o750); err != nil {
		return "", err
	}
	return output, nil
}

func ownedDirectory(path string) bool {
	manifest := &artifactsv1.RenderJob{}
	if readProtoJSON(filepath.Join(path, "render-job.json"), manifest) != nil || manifest.GetOwner() != owner || manifest.GetJobType() != jobType {
		return false
	}
	entries, err := os.ReadDir(path)
	if err != nil {
		return false
	}
	for _, entry := range entries {
		switch entry.Name() {
		case "render-job.json", "result.json", "result.json.inprogress", "progress.json", "progress.json.inprogress", "fpv.mp4", "fpv.mp4.inprogress":
			if entry.Type()&os.ModeSymlink != 0 || entry.IsDir() {
				return false
			}
		case "fpv_frames":
			if entry.Type()&os.ModeSymlink != 0 || !entry.IsDir() || !ownedFrames(filepath.Join(path, entry.Name())) {
				return false
			}
		default:
			return false
		}
	}
	return manifest.GetOutputPath() == filepath.Join(path, "fpv_frames") && manifest.GetResultPath() == filepath.Join(path, "result.json") &&
		manifest.GetProgressPath() == filepath.Join(path, "progress.json")
}

func ownedFrames(path string) bool {
	entries, err := os.ReadDir(path)
	if err != nil {
		return false
	}
	for _, entry := range entries {
		if entry.Type()&os.ModeSymlink != 0 || entry.IsDir() {
			return false
		}
		if entry.Name() != "frames.jsonl" && entry.Name() != "frames.jsonl.inprogress" && !frameName.MatchString(entry.Name()) {
			return false
		}
	}
	return true
}

func writeProtoJSON(path string, value proto.Message) error {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return fmt.Errorf("create JSON file: %w", err)
	}
	encoded, err := (protojson.MarshalOptions{Indent: "  "}).Marshal(value)
	if err != nil {
		_ = file.Close()
		return err
	}
	if _, err := file.Write(append(encoded, '\n')); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return err
	}
	return file.Close()
}

func readProtoJSON(path string, value proto.Message) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read ProtoJSON %s: %w", path, err)
	}
	if err := protojson.Unmarshal(raw, value); err != nil {
		return fmt.Errorf("decode ProtoJSON %s: %w", path, err)
	}
	return nil
}

func validateFrameIndex(spec *artifactsv1.RenderJob, result *artifactsv1.RenderResult) error {
	file, err := os.Open(result.GetFrameIndex().GetPath())
	if err != nil {
		return err
	}
	defer func() { _ = file.Close() }()
	reader := bufio.NewReaderSize(file, 128*1024)
	for ordinal := uint64(1); ordinal <= result.GetFrameCount(); ordinal++ {
		line, readErr := reader.ReadBytes('\n')
		if len(line) == 0 || line[len(line)-1] != '\n' {
			return fmt.Errorf("render frame index record %d is not LF-terminated", ordinal)
		}
		entry := &artifactsv1.RenderFrameIndex{}
		if err := protojson.Unmarshal(line[:len(line)-1], entry); err != nil {
			return fmt.Errorf("decode render frame index record %d: %w", ordinal, err)
		}
		expectedGlobalTick := result.GetGlobalTicks().GetFirstTick() + int64(ordinal-1)
		expectedReplayTick := result.GetReplayTicks().GetFirstTick() + int64(ordinal-1)
		expectedName := fmt.Sprintf("frame_%06d.png", ordinal)
		if entry.GetOrdinal() != ordinal || entry.GetServerTick() != expectedGlobalTick || entry.GetReplayTick() != expectedReplayTick || entry.GetPartialTick() != 0 ||
			entry.GetSessionId() != spec.GetSessionId() || entry.GetConnectionId() != spec.GetConnectionId() || entry.GetPlayerUuid() != spec.GetPlayerUuid() ||
			entry.GetReplayId() != spec.GetReplay().GetReplayId() || entry.GetPath() != expectedName {
			return fmt.Errorf("render frame index record %d does not match its result", ordinal)
		}
		image := filepath.Join(spec.GetOutputPath(), expectedName)
		info, err := os.Lstat(image)
		if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("render frame %d is missing or unsafe", ordinal)
		}
		if readErr != nil && !errors.Is(readErr, io.EOF) {
			return readErr
		}
	}
	extra, err := reader.ReadByte()
	if err == nil || !errors.Is(err, io.EOF) {
		return fmt.Errorf("render frame index contains data after frame %d: %q", result.GetFrameCount(), extra)
	}
	return nil
}
