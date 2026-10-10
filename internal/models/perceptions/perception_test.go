package perceptions

import (
	"bufio"
	"bytes"
	"compress/zlib"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	sceneent "github.com/proj-airi/recorder-minecraft/databases/scene/ent"
	"github.com/proj-airi/recorder-minecraft/internal/datastore"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

const (
	testSession    = "session-a"
	testPlayer     = "00000000-0000-4000-8000-000000000001"
	testConnection = "00000000-0000-4000-8000-000000000002"
	testActor      = "00000000-0000-4000-8000-000000000003"
	overworld      = "minecraft:overworld"
)

// The fixture is a flat world over ticks 10..20. The observer stands at the
// origin; the actor stands at z=8 and a chest sits at (0, 0, 9).
//
//   - ticks 10..14: open field, observer faces south: actor and chest visible.
//     A second chest at z=40 lies in a section the scene never received, so
//     it is undetermined.
//   - ticks 15..17: a stone wall appears at z=6: everything is hidden.
//   - ticks 18..20: the observer turns north and lowers view distance to 2
//     chunks, which caps the distance at 32 blocks.
func TestExtractWritesVisibilitySamples(t *testing.T) {
	play := t.TempDir()
	writeCapture(t, play, testSession)
	writeScene(t, filepath.Join(play, "scene.sqlite3"))
	service := &Service{captures: &captures.Service{}}
	options := testOptions(play)

	result, err := service.Extract(context.Background(), options)
	require.NoError(t, err)
	assert.EqualValues(t, 6, result.SampleCount)
	assert.EqualValues(t, 10, result.FirstTick)
	assert.EqualValues(t, 20, result.LastTick)

	header, samples := readPerception(t, options.Output)
	assert.Equal(t, ProcessorName, header.GetProcessor().GetName())
	assert.Equal(t, "actor perception", header.GetScope())
	assert.Equal(t, "reconstructed", header.GetProvenance())
	assert.False(t, header.GetUsesFutureContext())
	assert.EqualValues(t, 2, header.GetAssumptions().GetSamplingIntervalTicks())
	assert.Equal(t, artifactsv1.UnknownCellPolicy_UNKNOWN_CELL_POLICY_RAY_UNDETERMINED, header.GetAssumptions().GetUnknownCellPolicy())
	require.Len(t, header.GetInputs(), 3)
	assert.Equal(t, "scene.sqlite3", header.GetInputs()[2].GetFile().GetPath())
	assert.Len(t, header.GetInputs()[2].GetFile().GetSha256(), 64)

	require.Len(t, samples, 6)
	for _, sample := range samples[:3] {
		require.Len(t, sample.GetVisibleEntities(), 1, "tick %d", sample.GetServerTick())
		actor := sample.GetVisibleEntities()[0]
		assert.Equal(t, testActor, actor.GetUuid())
		assert.EqualValues(t, 2, actor.GetNetworkId())
		assert.Equal(t, "minecraft:player", actor.GetTypeId())
		require.Len(t, sample.GetVisibleBlockEntities(), 1)
		assert.EqualValues(t, 9, sample.GetVisibleBlockEntities()[0].GetBlockPos().GetZ())
		require.Len(t, sample.GetUndeterminedBlockEntities(), 1)
		assert.EqualValues(t, 40, sample.GetUndeterminedBlockEntities()[0].GetBlockPos().GetZ())
		assert.InDelta(t, 1.62, sample.GetObserver().GetEye().GetY(), epsilon)
		assert.EqualValues(t, 10, sample.GetViewDistanceChunks())
	}
	for _, sample := range samples[3:] {
		assert.Empty(t, sample.GetVisibleEntities(), "tick %d", sample.GetServerTick())
		assert.Empty(t, sample.GetVisibleBlockEntities(), "tick %d", sample.GetServerTick())
		assert.Empty(t, sample.GetUndeterminedBlockEntities(), "tick %d", sample.GetServerTick())
	}
	assert.EqualValues(t, 2, samples[4].GetViewDistanceChunks())
	assert.InDelta(t, 32, samples[4].GetMaxDistanceBlocks(), epsilon)

	// Overwrite replaces only an output this processor wrote.
	_, err = service.Extract(context.Background(), options)
	require.ErrorContains(t, err, "pass --overwrite")
	options.Overwrite = true
	options.FromTick = ptr(int64(12))
	options.ToTick = ptr(int64(13))
	result, err = service.Extract(context.Background(), options)
	require.NoError(t, err)
	assert.EqualValues(t, 1, result.SampleCount)
	require.NoError(t, os.WriteFile(options.Output, []byte("{\"schemaVersion\":1}\n"), 0o600))
	_, err = service.Extract(context.Background(), options)
	require.ErrorContains(t, err, "did not write")
}

func TestExtractRejectsAMismatchedScene(t *testing.T) {
	play := t.TempDir()
	writeCapture(t, play, "session-b")
	writeScene(t, filepath.Join(play, "scene.sqlite3"))
	options := testOptions(play)
	service := &Service{captures: &captures.Service{}}
	_, err := service.Extract(context.Background(), options)
	require.ErrorContains(t, err, "scene identity does not match")
	_, statErr := os.Stat(options.Output)
	assert.ErrorIs(t, statErr, os.ErrNotExist, "no partial output is published")
}

func testOptions(play string) Options {
	return Options{
		Metadata: filepath.Join(play, "metadata.json"), Events: filepath.Join(play, "capture", "events.jsonl"),
		Scene: filepath.Join(play, "scene.sqlite3"), Output: filepath.Join(play, "perception.jsonl"),
		IntervalTicks: 2, VerticalFOVDegrees: DefaultVerticalFOVDegrees, AspectRatio: DefaultAspectRatio, MaxDistanceBlocks: DefaultMaxDistanceBlocks,
	}
}

func ptr[T any](value T) *T { return &value }

func writeCapture(t *testing.T, play, session string) {
	t.Helper()
	end := int64(20)
	writeLines(t, filepath.Join(play, "metadata.json"), &artifactsv1.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: session,
		Player:     &artifactsv1.PlayerIdentity{Uuid: testPlayer},
		Connection: &artifactsv1.Connection{Id: testConnection, StartServerTick: 10, EndServerTick: &end},
		Capture:    &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	})
	require.NoError(t, os.MkdirAll(filepath.Join(play, "capture"), 0o700))
	var records []proto.Message
	sequence := uint64(0)
	identity := func(tick int64) *artifactsv1.EventIdentity {
		sequence++
		return &artifactsv1.EventIdentity{SchemaVersion: 1, SessionId: session, ServerTick: tick, Sequence: sequence, PlayerUuid: testPlayer, ConnectionId: testConnection}
	}
	records = append(records, &artifactsv1.CaptureEvent{Identity: identity(10), Record: &artifactsv1.CaptureEvent_ClientInformation{
		ClientInformation: &artifactsv1.ClientInformationEvent{Source: artifactsv1.ClientInformationSource_CLIENT_INFORMATION_SOURCE_JOIN_SNAPSHOT, ViewDistance: 12}}})
	for tick := int64(10); tick <= 20; tick++ {
		if tick == 18 {
			records = append(records, &artifactsv1.CaptureEvent{Identity: identity(tick), Record: &artifactsv1.CaptureEvent_ClientInformation{
				ClientInformation: &artifactsv1.ClientInformationEvent{Source: artifactsv1.ClientInformationSource_CLIENT_INFORMATION_SOURCE_PACKET, ViewDistance: 2}}})
		}
		records = append(records, &artifactsv1.CaptureEvent{Identity: identity(tick), Record: &artifactsv1.CaptureEvent_PlayerState{PlayerState: &artifactsv1.PlayerStateEvent{
			Dimension: overworld, ReplayCoverage: &artifactsv1.ReplayCoverage{ViewDistanceChunks: 10}}}})
	}
	writeLines(t, filepath.Join(play, "capture", "events.jsonl"), records...)
}

func writeLines(t *testing.T, path string, messages ...proto.Message) {
	t.Helper()
	var buffer bytes.Buffer
	for _, message := range messages {
		encoded, err := protojson.Marshal(message)
		require.NoError(t, err)
		buffer.Write(encoded)
		buffer.WriteByte('\n')
	}
	require.NoError(t, os.WriteFile(path, buffer.Bytes(), 0o600))
}

func writeScene(t *testing.T, path string) {
	t.Helper()
	ctx := context.Background()
	scene := datastore.NewTestScene(t, path)
	err := scene.WithTx(ctx, func(tx *sceneent.Tx) error {
		blobs := map[string]bool{}
		put := func(kind string, payload *artifactsv1.SceneBlobPayload) string {
			raw, err := protojson.Marshal(payload)
			require.NoError(t, err)
			sum := sha256.Sum256(raw)
			digest := hex.EncodeToString(sum[:])
			if blobs[digest] {
				return digest
			}
			blobs[digest] = true
			var compressed bytes.Buffer
			writer := zlib.NewWriter(&compressed)
			_, _ = writer.Write(raw)
			require.NoError(t, writer.Close())
			require.NoError(t, tx.Blob.Create().SetID(digest).SetKind(kind).SetEncoding("zlib").SetUncompressedSize(int64(len(raw))).
				SetCompressedSize(int64(compressed.Len())).SetData(compressed.Bytes()).Exec(ctx))
			return digest
		}

		ground := put("section", sectionPayload(func(int, int, int) bool { return true }))
		open := put("section", sectionPayload(func(int, int, int) bool { return false }))
		walled := put("section", sectionPayload(func(x, y, z int) bool { return z == 6 && y <= 2 }))
		versionID := int64(0)
		addSection := func(x, y, z, start, end int64, blob string) {
			versionID++
			require.NoError(t, tx.SectionVersion.Create().SetID(versionID).SetDimension(overworld).SetSectionX(x).SetSectionY(y).SetSectionZ(z).
				SetStartTick(start).SetEndTick(end).SetBlobSha256(blob).Exec(ctx))
		}
		// Sections x -1..1, y -1..1, z -1..1 are known; z=2 (blocks 32..47)
		// never arrived. Section (0, 0, 0) gains the wall at tick 15.
		for x := int64(-1); x <= 1; x++ {
			for y := int64(-1); y <= 1; y++ {
				for z := int64(-1); z <= 1; z++ {
					blob := open
					if y < 0 {
						blob = ground
					}
					if x == 0 && y == 0 && z == 0 {
						addSection(x, y, z, 10, 15, open)
						addSection(x, y, z, 15, 21, walled)
						continue
					}
					addSection(x, y, z, 10, 21, blob)
				}
			}
		}

		observerUUID, actorUUID := testPlayer, testActor
		observerBlob := put("entity", &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_Entity{Entity: &artifactsv1.EntityBlob{
			Dimension: overworld, NetworkId: 1, Uuid: &observerUUID, TypeId: "minecraft:player"}}})
		actorBlob := put("entity", &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_Entity{Entity: &artifactsv1.EntityBlob{
			Dimension: overworld, NetworkId: 2, Uuid: &actorUUID, TypeId: "minecraft:player"}}})
		require.NoError(t, tx.EntityVersion.Create().SetID(1).SetInstanceID("replay:1:0").SetNetworkID(1).SetDimension(overworld).SetTypeID("minecraft:player").
			SetStartTick(10).SetEndTick(21).SetMinX(0.2).SetMinY(0).SetMinZ(0.2).SetMaxX(0.8).SetMaxY(1.8).SetMaxZ(0.8).SetBlobSha256(observerBlob).Exec(ctx))
		require.NoError(t, tx.EntityVersion.Create().SetID(2).SetInstanceID("replay:2:0").SetNetworkID(2).SetDimension(overworld).SetTypeID("minecraft:player").
			SetStartTick(10).SetEndTick(21).SetMinX(0.2).SetMinY(0).SetMinZ(7.7).SetMaxX(0.8).SetMaxY(1.8).SetMaxZ(8.3).SetBlobSha256(actorBlob).Exec(ctx))
		chestBlob := put("block_entity", &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_BlockEntity{BlockEntity: &artifactsv1.BlockEntityBlob{TypeId: "minecraft:chest"}}})
		for index, z := range []int64{9, 40} {
			require.NoError(t, tx.BlockEntityVersion.Create().SetID(int64(index+1)).SetDimension(overworld).SetBlockX(0).SetBlockY(0).SetBlockZ(z).
				SetTypeID("minecraft:chest").SetStartTick(10).SetEndTick(21).SetBlobSha256(chestBlob).Exec(ctx))
		}

		for tick := int64(10); tick <= 20; tick++ {
			frame := put("frame", &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_Frame{Frame: &artifactsv1.SceneFrameRecord{ServerTick: tick}}})
			require.NoError(t, tx.Frame.Create().SetID(tick).SetFrameID(fmt.Sprint("frame-", tick)).SetDimension(overworld).
				SetSubjectX(0.5).SetSubjectY(0).SetSubjectZ(0.5).SetCoverageComplete(true).SetPayloadSha256(frame).Exec(ctx))
			yaw := 0.0
			if tick >= 18 {
				yaw = 180
			}
			state := put("player_state", &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_PlayerState{PlayerState: &artifactsv1.CaptureEvent{}}})
			require.NoError(t, tx.PlayerState.Create().SetID(tick).SetEntityVersionID(1).SetEntityInstanceID("replay:1:0").SetEntityID(1).
				SetDimension(overworld).SetPositionX(0.5).SetPositionY(0).SetPositionZ(0.5).SetVelocityX(0).SetVelocityY(0).SetVelocityZ(0).
				SetYaw(yaw).SetPitch(0).SetHeadYaw(yaw).SetAlive(true).SetOnGround(true).SetPose("standing").SetSprinting(false).SetSneaking(false).
				SetSwimming(false).SetFallFlying(false).SetUsingItem(false).SetUseItemRemainingTicks(0).SetGameMode("survival").SetHealth(20).
				SetMaxHealth(20).SetAbsorption(0).SetArmor(0).SetAir(300).SetMaxAir(300).SetFoodLevel(20).SetSaturation(5).SetExperienceLevel(0).
				SetExperienceProgress(0).SetTotalExperience(0).SetSelectedSlot(0).SetStateBarrierApplySequence(0).SetPayloadSha256(state).Exec(ctx))
		}
		if err := tx.SchemaInfo.Create().SetID(1).SetSchemaName(sceneSchemaName).SetSchemaVersion(sceneSchemaVersion).Exec(ctx); err != nil {
			return err
		}
		return tx.SceneMeta.Create().SetID(1).SetSessionID(testSession).SetPlayerUUID(testPlayer).SetConnectionID(testConnection).
			SetStartTick(10).SetEndTick(20).SetSourceReplaysJSON([]byte("{}")).SetSensitive(true).SetProvenanceJSON([]byte("{}")).Exec(ctx)
	})
	require.NoError(t, err)
	require.NoError(t, scene.Publish())
}

// sectionPayload builds a stone/air section; solid reports stone at local
// coordinates.
func sectionPayload(solid func(x, y, z int) bool) *artifactsv1.SceneBlobPayload {
	indices := make([]byte, 4096*2)
	for y := range 16 {
		for z := range 16 {
			for x := range 16 {
				if solid(x, y, z) {
					binary.LittleEndian.PutUint16(indices[(y*256+z*16+x)*2:], 1)
				}
			}
		}
	}
	return &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_Section{Section: &artifactsv1.SectionBlob{
		Palette:      []*artifactsv1.BlockState{{Name: "minecraft:air"}, {Name: "minecraft:stone"}},
		IndicesLeU16: indices,
	}}}
}

func readPerception(t *testing.T, path string) (*artifactsv1.PerceptionHeader, []*artifactsv1.PerceptionSample) {
	t.Helper()
	file, err := os.Open(path)
	require.NoError(t, err)
	defer func() { _ = file.Close() }()
	var header *artifactsv1.PerceptionHeader
	var samples []*artifactsv1.PerceptionSample
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 1024*1024), 16*1024*1024)
	for scanner.Scan() {
		record := &artifactsv1.PerceptionRecord{}
		require.NoError(t, protojson.Unmarshal(scanner.Bytes(), record))
		require.EqualValues(t, 1, record.GetSchemaVersion())
		if value := record.GetHeader(); value != nil {
			require.Nil(t, header, "one header")
			require.Empty(t, samples, "header comes first")
			header = value
			continue
		}
		samples = append(samples, record.GetSample())
	}
	require.NoError(t, scanner.Err())
	require.NotNil(t, header)
	return header, samples
}
