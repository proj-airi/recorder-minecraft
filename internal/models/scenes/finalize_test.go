package scenes

import (
	"bytes"
	"compress/zlib"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"

	artifactsv1 "github.com/proj-airi/mc-play-recorder/apis/sdk/go/mc-play-recorder/artifacts/v1"
	"github.com/proj-airi/mc-play-recorder/internal/datastore"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

func TestFinalizeBuildsOneTickStore(t *testing.T) {
	root := t.TempDir()
	stream := filepath.Join(root, "stream")
	require.NoError(t, os.MkdirAll(filepath.Join(stream, "blobs"), 0o700))
	const session = "session"
	player := "00000000-0000-4000-8000-000000000001"
	const connection = "00000000-0000-4000-8000-000000000002"
	sectionDigest, sectionBytes := writeTestBlob(t, stream, &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_Section{Section: &artifactsv1.SectionBlob{}}})
	entityDigest, entityBytes := writeTestBlob(t, stream, &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_Entity{Entity: &artifactsv1.EntityBlob{
		Bounds: &artifactsv1.Bounds{MinX: 0, MinY: 0, MinZ: 0, MaxX: 1, MaxY: 2, MaxZ: 1}, Dimension: "minecraft:overworld",
		NetworkId: 7, Uuid: &player, TypeId: "minecraft:player",
	}}})
	blockDigest, blockBytes := writeTestBlob(t, stream, &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_BlockEntity{BlockEntity: &artifactsv1.BlockEntityBlob{TypeId: "minecraft:chest"}}})
	frame := &artifactsv1.SceneFrameRecord{ServerTick: 10, FrameId: "frame", ReplayTick: 10, Dimension: "minecraft:overworld", SubjectPosition: &artifactsv1.Vector3{}}
	frames := delimited(t, frame)
	changes := delimited(t,
		&artifactsv1.SceneChangeRecord{Sequence: 0, ServerTick: 10, Change: &artifactsv1.SceneChangeRecord_SegmentBegin{SegmentBegin: &artifactsv1.SegmentBegin{}}},
		&artifactsv1.SceneChangeRecord{Sequence: 1, ServerTick: 10, Change: &artifactsv1.SceneChangeRecord_SectionSet{SectionSet: &artifactsv1.SectionChange{Dimension: "minecraft:overworld", BlobSha256: sectionDigest}}},
		&artifactsv1.SceneChangeRecord{Sequence: 2, ServerTick: 10, Change: &artifactsv1.SceneChangeRecord_EntitySet{EntitySet: &artifactsv1.EntityChange{InstanceId: "replay:7:0", BlobSha256: entityDigest}}},
		&artifactsv1.SceneChangeRecord{Sequence: 3, ServerTick: 10, Change: &artifactsv1.SceneChangeRecord_BlockEntitySet{BlockEntitySet: &artifactsv1.BlockEntityChange{Dimension: "minecraft:overworld", BlobSha256: blockDigest}}},
	)
	framesPath, changesPath := filepath.Join(stream, "frames.jsonl"), filepath.Join(stream, "changes.jsonl")
	writeTestFile(t, framesPath, frames)
	writeTestFile(t, changesPath, changes)
	state := &artifactsv1.CaptureEvent{Identity: &artifactsv1.EventIdentity{SchemaVersion: 1, SessionId: session, PlayerUuid: player, ConnectionId: connection, ServerTick: 10, EntityId: 7},
		Record: &artifactsv1.CaptureEvent_PlayerState{PlayerState: &artifactsv1.PlayerStateEvent{Dimension: "minecraft:overworld", Position: &artifactsv1.Vector3{}, Velocity: &artifactsv1.Vector3{}, Rotation: &artifactsv1.Rotation{}, Pose: "standing", GameMode: "survival"}}}
	statePath := filepath.Join(root, "player-states.jsonl")
	writeTestFile(t, statePath, delimited(t, state))
	frameSum, changeSum := sha256.Sum256(frames), sha256.Sum256(changes)
	result := &artifactsv1.SceneExtractionResult{Status: artifactsv1.SceneExtractionResult_STATUS_COMPLETE, SessionId: session, PlayerUuid: player, ConnectionId: connection,
		Ticks: &artifactsv1.TickRange{FirstTick: 10, LastTick: 10}, Stream: &artifactsv1.SceneStreamResult{Path: stream, Frames: "frames.jsonl", Changes: "changes.jsonl", BlobsDirectory: "blobs",
			FrameCount: 1, ChangeCount: 4, BlobCount: 3, BlobBytes: uint64(sectionBytes + entityBytes + blockBytes), FramesSha256: hex.EncodeToString(frameSum[:]), FramesSizeBytes: uint64(len(frames)), ChangesSha256: hex.EncodeToString(changeSum[:]), ChangesSizeBytes: uint64(len(changes))}}
	resultPath := filepath.Join(root, "result.json")
	resultBytes, err := protojson.Marshal(result)
	require.NoError(t, err)
	writeTestFile(t, resultPath, resultBytes)
	scene := datastore.NewTestScene(t, filepath.Join(root, "scene.sqlite3"))
	finalizer := &Finalizer{scene: scene}
	info, err := finalizer.Finalize(context.Background(), Input{Result: resultPath, Stream: stream, PlayerStates: statePath})
	require.NoError(t, err)
	assert.Equal(t, 1, info.FrameCount)
	assert.Equal(t, 1, info.SectionVersions)
	assert.Equal(t, 1, info.EntityVersions)
	assert.Equal(t, 1, info.BlockEntityCount)
	assert.Equal(t, 1, info.PlayerStates)
}

func TestStreamBlobRejectsNonCanonicalDigest(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	writer := &storeWriter{stream: root}
	for name, digest := range map[string]string{
		"Traversal": "../" + strings.Repeat("a", 61),
		"Uppercase": strings.Repeat("A", 64),
		"NonHex":    strings.Repeat("g", 64),
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()

			_, err := writer.streamBlob(digest, "section")
			require.Error(t, err)
			assert.EqualError(t, err, "invalid section blob digest")
		})
	}
}

func writeTestBlob(t *testing.T, stream string, value proto.Message) (string, int64) {
	t.Helper()
	raw, err := protojson.Marshal(value)
	require.NoError(t, err)
	sum := sha256.Sum256(raw)
	digest := hex.EncodeToString(sum[:])
	var compressed bytes.Buffer
	writer, err := zlib.NewWriterLevel(&compressed, 6)
	require.NoError(t, err)
	_, err = writer.Write(raw)
	require.NoError(t, err)
	require.NoError(t, writer.Close())
	writeTestFile(t, filepath.Join(stream, "blobs", digest+".zlib"), compressed.Bytes())
	return digest, int64(compressed.Len())
}

func delimited(t *testing.T, values ...proto.Message) []byte {
	t.Helper()
	var result bytes.Buffer
	for _, value := range values {
		encoded, err := protojson.Marshal(value)
		require.NoError(t, err)
		result.Write(encoded)
		result.WriteByte('\n')
	}
	return result.Bytes()
}

func writeTestFile(t *testing.T, path string, value []byte) {
	t.Helper()
	require.NoError(t, os.WriteFile(path, value, 0o600))
}
