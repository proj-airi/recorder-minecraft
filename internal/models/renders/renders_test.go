package renders

import (
	"os"
	"path/filepath"
	"testing"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/replays"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

func TestValidateResultBindsCompletedRenderToJob(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	frames := filepath.Join(root, "fpv_frames")
	require.NoError(t, os.Mkdir(frames, 0o700))
	replayPath := filepath.Join(root, "replay.zip")
	require.NoError(t, os.WriteFile(replayPath, []byte("replay"), 0o600))
	service := &Service{replays: &replays.Service{}}
	replayDigest, replaySize, err := service.replays.Digest(replayPath)
	require.NoError(t, err)
	spec := &artifactsv1.RenderJob{
		SchemaVersion: 1, SessionId: "session", ConnectionId: "connection", PlayerUuid: "player",
		Replay:      &artifactsv1.RenderReplaySource{ReplayId: "replay-id", Path: replayPath, Format: "flashback", Sha256: replayDigest, SizeBytes: uint64(replaySize)},
		GlobalTicks: &artifactsv1.TickRange{FirstTick: 100, LastTick: 100}, Width: 640, Height: 360, FramesPerSecond: 20,
		OutputPath: frames, ResultPath: filepath.Join(root, "result.json"),
	}
	entry := &artifactsv1.RenderFrameIndex{Ordinal: 1, ServerTick: 100, ReplayTick: 7, SessionId: "session", ConnectionId: "connection", PlayerUuid: "player", ReplayId: "replay-id", Path: "frame_000001.png"}
	encoded, err := protojson.Marshal(entry)
	require.NoError(t, err)
	indexPath := filepath.Join(frames, "frames.jsonl")
	require.NoError(t, os.WriteFile(indexPath, append(encoded, '\n'), 0o600))
	require.NoError(t, os.WriteFile(filepath.Join(frames, "frame_000001.png"), []byte("png"), 0o600))
	indexDigest, indexSize, err := service.replays.Digest(indexPath)
	require.NoError(t, err)
	result := &artifactsv1.RenderResult{
		SchemaVersion: 1, Status: artifactsv1.RenderResultStatus_RENDER_RESULT_STATUS_COMPLETE,
		SessionId: "session", ConnectionId: "connection", PlayerUuid: "player", Replay: proto.CloneOf(spec.Replay),
		OutputPath: frames, RequestedGlobalTicks: proto.CloneOf(spec.GlobalTicks),
		GlobalTicks: &artifactsv1.TickRange{FirstTick: 100, LastTick: 100}, ReplayTicks: &artifactsv1.TickRange{FirstTick: 7, LastTick: 7},
		FramesPerSecond: 20, Width: 640, Height: 360, FrameCount: 1,
		FrameIndex: &artifactsv1.RenderArtifact{Path: indexPath, Sha256: indexDigest, SizeBytes: uint64(indexSize), MediaType: "application/jsonl"},
	}
	job := Job{Directory: root, Spec: spec}
	require.NoError(t, service.validateResult(job, result))

	t.Run("Identity", func(t *testing.T) {
		mismatch := proto.CloneOf(result)
		mismatch.ConnectionId = "another"
		err := service.validateResult(job, mismatch)
		require.Error(t, err)
		assert.EqualError(t, err, "renderer result does not match its prepared job")
	})

	t.Run("RequestedTicks", func(t *testing.T) {
		mismatch := proto.CloneOf(result)
		mismatch.RequestedGlobalTicks.FirstTick++
		err := service.validateResult(job, mismatch)
		require.Error(t, err)
		assert.EqualError(t, err, "renderer result does not match its prepared job")
	})

	t.Run("FrameCount", func(t *testing.T) {
		mismatch := proto.CloneOf(result)
		mismatch.FrameCount = 2
		err := service.validateResult(job, mismatch)
		require.Error(t, err)
		assert.EqualError(t, err, "completed renderer result has inconsistent tick coverage or frame count")
	})

	t.Run("FrameIndexIntegrity", func(t *testing.T) {
		mismatch := proto.CloneOf(result)
		mismatch.FrameIndex.Sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
		err := service.validateResult(job, mismatch)
		require.Error(t, err)
		assert.EqualError(t, err, "renderer frame index integrity does not match its result")
	})
}

func TestPrepareDirectoryOverwritesOwnedCompletedRender(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	output := filepath.Join(root, "renders")
	frames := filepath.Join(output, "fpv_frames")
	require.NoError(t, os.MkdirAll(frames, 0o700))
	spec := &artifactsv1.RenderJob{
		SchemaVersion: 1, Owner: owner, JobType: jobType,
		OutputPath: frames, ResultPath: filepath.Join(output, "result.json"), ProgressPath: filepath.Join(output, "progress.json"),
	}
	require.NoError(t, writeProtoJSON(filepath.Join(output, "render-job.json"), spec))
	require.NoError(t, os.WriteFile(filepath.Join(output, "result.json"), []byte("result\n"), 0o600))
	require.NoError(t, os.WriteFile(filepath.Join(output, "progress.json"), []byte("progress\n"), 0o600))
	require.NoError(t, os.WriteFile(filepath.Join(frames, "frames.jsonl"), []byte("index\n"), 0o600))
	require.NoError(t, os.WriteFile(filepath.Join(frames, "frame_000001.png"), []byte("png"), 0o600))

	resolved, err := prepareDirectory(output, true)
	require.NoError(t, err)
	assert.Equal(t, output, resolved)
	_, err = os.Stat(output)
	require.Error(t, err)
	assert.ErrorIs(t, err, os.ErrNotExist)
}
