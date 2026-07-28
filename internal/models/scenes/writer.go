package scenes

import (
	"bufio"
	"bytes"
	"compress/zlib"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	sceneent "github.com/proj-airi/recorder-minecraft/databases/scene/ent"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

const maxBlobBytes = 64 * 1024 * 1024

type storeWriter struct {
	ctx             context.Context
	tx              *sceneent.Tx
	stream          string
	blobKinds       map[string]string
	sourceBlobs     map[string]bool
	sourceBlobBytes int64
}

func (w *storeWriter) writeFrames(path string, result *artifactsv1.SceneExtractionResult) (map[int64]frameValue, error) {
	frames := map[int64]frameValue{}
	err := readDelimited(path, func() *artifactsv1.SceneFrameRecord { return &artifactsv1.SceneFrameRecord{} }, func(record int, value *artifactsv1.SceneFrameRecord) error {
		ticks := result.GetTicks()
		if value.GetServerTick() < ticks.GetFirstTick() || value.GetServerTick() > ticks.GetLastTick() || value.SubjectPosition == nil || value.GetFrameId() == "" || value.GetDimension() == "" {
			return fmt.Errorf("frame record %d violates the scene contract", record)
		}
		if _, exists := frames[value.GetServerTick()]; exists {
			return fmt.Errorf("duplicate scene frame at tick %d", value.GetServerTick())
		}
		payload := &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_Frame{Frame: value}}
		digest, err := w.putBlob("frame", payload)
		if err != nil {
			return err
		}
		replayTick := value.GetReplayTick()
		err = w.tx.Frame.Create().SetID(value.GetServerTick()).SetFrameID(value.GetFrameId()).SetNillableReplayTick(&replayTick).
			SetDimension(value.GetDimension()).SetSubjectX(value.GetSubjectPosition().GetX()).SetSubjectY(value.GetSubjectPosition().GetY()).
			SetSubjectZ(value.GetSubjectPosition().GetZ()).SetCoverageComplete(value.GetCoverageComplete()).SetPayloadSha256(digest).Exec(w.ctx)
		if err != nil {
			return fmt.Errorf("insert frame at tick %d: %w", value.GetServerTick(), err)
		}
		frames[value.GetServerTick()] = frameValue{frame: value}
		return nil
	})
	if err != nil {
		return nil, err
	}
	for tick := result.GetTicks().GetFirstTick(); tick <= result.GetTicks().GetLastTick(); tick++ {
		if _, ok := frames[tick]; !ok {
			return nil, fmt.Errorf("scene frames omit tick %d", tick)
		}
	}
	return frames, nil
}

func (w *storeWriter) writeChanges(path string, result *artifactsv1.SceneExtractionResult) ([]entityVersion, versionCounts, error) {
	activeSections := map[sectionKey]sectionActive{}
	activeBlocks := map[blockKey]blockActive{}
	activeEntities := map[string]entityActive{}
	closedEntities := map[string]bool{}
	aliases := map[string]string{}
	aliasIdentity := map[string]string{}
	activeIdentity := map[string]string{}
	canonicalAliases := map[string]map[string]bool{}
	var versions []entityVersion
	counts := versionCounts{}
	var previousSequence int64 = -1
	closeSection := func(key sectionKey, end int64) error {
		value := activeSections[key]
		delete(activeSections, key)
		if value.start == end {
			return nil
		}
		counts.sections++
		return w.tx.SectionVersion.Create().SetID(int64(counts.sections)).SetDimension(key.dimension).SetSectionX(key.x).SetSectionY(key.y).
			SetSectionZ(key.z).SetStartTick(value.start).SetEndTick(end).SetBlobSha256(value.blob).Exec(w.ctx)
	}
	closeBlock := func(key blockKey, end int64) error {
		value := activeBlocks[key]
		delete(activeBlocks, key)
		if value.start == end {
			return nil
		}
		counts.blocks++
		return w.tx.BlockEntityVersion.Create().SetID(int64(counts.blocks)).SetDimension(key.dimension).SetBlockX(key.x).SetBlockY(key.y).
			SetBlockZ(key.z).SetTypeID(value.typeID).SetStartTick(value.start).SetEndTick(end).SetBlobSha256(value.blob).Exec(w.ctx)
	}
	closeEntity := func(instance string, end int64) error {
		value := activeEntities[instance]
		delete(activeEntities, instance)
		closedEntities[instance] = true
		if value.start == end {
			return nil
		}
		counts.entities++
		version := entityVersion{id: int64(counts.entities), instance: instance, entityActive: value, end: end}
		versions = append(versions, version)
		var network *int64
		if value.hasNetwork {
			network = &value.networkID
		}
		return w.tx.EntityVersion.Create().SetID(version.id).SetInstanceID(instance).SetNillableNetworkID(network).SetDimension(value.dimension).
			SetTypeID(value.typeID).SetStartTick(value.start).SetEndTick(end).SetMinX(value.aabb[0]).SetMinY(value.aabb[1]).SetMinZ(value.aabb[2]).
			SetMaxX(value.aabb[3]).SetMaxY(value.aabb[4]).SetMaxZ(value.aabb[5]).SetBlobSha256(value.blob).Exec(w.ctx)
	}
	err := readDelimited(path, func() *artifactsv1.SceneChangeRecord { return &artifactsv1.SceneChangeRecord{} }, func(record int, event *artifactsv1.SceneChangeRecord) error {
		counts.changes++
		if int64(event.GetSequence()) != previousSequence+1 {
			return fmt.Errorf("change sequence is not contiguous at record %d", record)
		}
		previousSequence = int64(event.GetSequence())
		if event.GetSegmentBegin() != nil {
			return nil
		}
		if event.GetServerTick() < result.GetTicks().GetFirstTick() || event.GetServerTick() > result.GetTicks().GetLastTick() {
			return fmt.Errorf("change record %d has an unexpected tick", record)
		}
		switch change := event.GetChange().(type) {
		case *artifactsv1.SceneChangeRecord_SectionSet:
			blob, rawBlob, err := w.sceneBlob(change.SectionSet.GetBlobSha256(), "section")
			if err != nil {
				return err
			}
			if blob.GetSection() == nil {
				return errors.New("section blob has the wrong protobuf payload")
			}
			key := sectionKey{change.SectionSet.GetDimension(), int64(change.SectionSet.GetX()), int64(change.SectionSet.GetY()), int64(change.SectionSet.GetZ())}
			current, ok := activeSections[key]
			if ok && current.blob == change.SectionSet.GetBlobSha256() {
				return nil
			}
			if ok {
				if err := closeSection(key, event.GetServerTick()); err != nil {
					return err
				}
			}
			if _, err := w.putBlobBytes("section", rawBlob); err != nil {
				return err
			}
			activeSections[key] = sectionActive{event.GetServerTick(), change.SectionSet.GetBlobSha256()}
		case *artifactsv1.SceneChangeRecord_SectionUnload:
			key := sectionKey{change.SectionUnload.GetDimension(), int64(change.SectionUnload.GetX()), int64(change.SectionUnload.GetY()), int64(change.SectionUnload.GetZ())}
			if _, ok := activeSections[key]; ok {
				return closeSection(key, event.GetServerTick())
			}
		case *artifactsv1.SceneChangeRecord_EntitySet:
			blob, rawBlob, err := w.sceneBlob(change.EntitySet.GetBlobSha256(), "entity")
			if err != nil {
				return err
			}
			entity := blob.GetEntity()
			if entity == nil || entity.GetDimension() == "" || entity.GetTypeId() == "" || entity.Bounds == nil {
				return errors.New("entity blob is incomplete")
			}
			incoming := change.EntitySet.GetInstanceId()
			fields := entityFields(entity)
			identity := entityIdentity(incoming, fields)
			canonical, ok := aliases[incoming]
			if !ok {
				canonical = activeIdentity[identity]
				if canonical == "" {
					canonical = incoming
					activeIdentity[identity] = canonical
				}
				aliases[incoming], aliasIdentity[incoming] = canonical, identity
				if canonicalAliases[canonical] == nil {
					canonicalAliases[canonical] = map[string]bool{}
				}
				canonicalAliases[canonical][incoming] = true
			} else if aliasIdentity[incoming] != identity {
				return fmt.Errorf("entity alias changes identity: %s", incoming)
			}
			current, active := activeEntities[canonical]
			if active && (current.hasNetwork != fields.hasNetwork || current.networkID != fields.networkID) {
				return fmt.Errorf("entity %s changes network id", canonical)
			}
			if active && current.blob == change.EntitySet.GetBlobSha256() {
				return nil
			}
			if active {
				if err := closeEntity(canonical, event.GetServerTick()); err != nil {
					return err
				}
				delete(closedEntities, canonical)
			}
			if closedEntities[canonical] {
				return fmt.Errorf("entity instance cannot be reused: %s", canonical)
			}
			if _, err = w.putBlobBytes("entity", rawBlob); err != nil {
				return err
			}
			fields.start, fields.blob = event.GetServerTick(), change.EntitySet.GetBlobSha256()
			activeEntities[canonical] = fields
		case *artifactsv1.SceneChangeRecord_EntityRemove:
			incoming := change.EntityRemove.GetInstanceId()
			canonical := aliases[incoming]
			if canonical == "" {
				return fmt.Errorf("scene removes unknown entity alias %s", incoming)
			}
			delete(canonicalAliases[canonical], incoming)
			if len(canonicalAliases[canonical]) == 0 {
				if _, ok := activeEntities[canonical]; ok {
					if err := closeEntity(canonical, event.GetServerTick()); err != nil {
						return err
					}
				}
				if activeIdentity[aliasIdentity[incoming]] == canonical {
					delete(activeIdentity, aliasIdentity[incoming])
				}
			}
		case *artifactsv1.SceneChangeRecord_BlockEntitySet:
			blob, rawBlob, err := w.sceneBlob(change.BlockEntitySet.GetBlobSha256(), "block_entity")
			if err != nil {
				return err
			}
			block := blob.GetBlockEntity()
			if block == nil || block.GetTypeId() == "" {
				return errors.New("block entity has no type_id")
			}
			key := blockKey{change.BlockEntitySet.GetDimension(), int64(change.BlockEntitySet.GetX()), int64(change.BlockEntitySet.GetY()), int64(change.BlockEntitySet.GetZ())}
			current, ok := activeBlocks[key]
			if ok && current.blob == change.BlockEntitySet.GetBlobSha256() {
				return nil
			}
			if ok {
				if err := closeBlock(key, event.GetServerTick()); err != nil {
					return err
				}
			}
			if _, err = w.putBlobBytes("block_entity", rawBlob); err != nil {
				return err
			}
			activeBlocks[key] = blockActive{event.GetServerTick(), block.GetTypeId(), change.BlockEntitySet.GetBlobSha256()}
		case *artifactsv1.SceneChangeRecord_BlockEntityRemove:
			key := blockKey{change.BlockEntityRemove.GetDimension(), int64(change.BlockEntityRemove.GetX()), int64(change.BlockEntityRemove.GetY()), int64(change.BlockEntityRemove.GetZ())}
			if _, ok := activeBlocks[key]; ok {
				return closeBlock(key, event.GetServerTick())
			}
		default:
			return errors.New("scene change has no recognized payload")
		}
		return nil
	})
	if err != nil {
		return nil, counts, err
	}
	finalTick := result.GetTicks().GetLastTick() + 1
	sectionKeys := sortedKeys(activeSections)
	for _, key := range sectionKeys {
		if err := closeSection(key, finalTick); err != nil {
			return nil, counts, err
		}
	}
	entityKeys := make([]string, 0, len(activeEntities))
	for key := range activeEntities {
		entityKeys = append(entityKeys, key)
	}
	sort.Strings(entityKeys)
	for _, key := range entityKeys {
		if err := closeEntity(key, finalTick); err != nil {
			return nil, counts, err
		}
	}
	blockKeys := sortedKeys(activeBlocks)
	for _, key := range blockKeys {
		if err := closeBlock(key, finalTick); err != nil {
			return nil, counts, err
		}
	}
	return versions, counts, nil
}

func (w *storeWriter) writeStates(path string, result *artifactsv1.SceneExtractionResult, frames map[int64]frameValue, versions []entityVersion) (int, error) {
	count := 0
	seen := map[int64]bool{}
	err := readDelimited(path, func() *artifactsv1.CaptureEvent { return &artifactsv1.CaptureEvent{} }, func(record int, event *artifactsv1.CaptureEvent) error {
		state := event.GetPlayerState()
		if state == nil {
			return nil
		}
		identity := event.GetIdentity()
		if identity.GetSessionId() != result.GetSessionId() || identity.GetPlayerUuid() != result.GetPlayerUuid() || identity.GetConnectionId() != result.GetConnectionId() {
			return fmt.Errorf("player state record %d identity does not match", record)
		}
		tick := identity.GetServerTick()
		if seen[tick] {
			return fmt.Errorf("duplicate player state at tick %d", tick)
		}
		seen[tick] = true
		frame, ok := frames[tick]
		if !ok || state.GetDimension() != frame.frame.GetDimension() || !sameVector(state.GetPosition(), frame.frame.GetSubjectPosition()) {
			return fmt.Errorf("player state at tick %d does not match its frame", tick)
		}
		var linked *entityVersion
		for index := range versions {
			version := &versions[index]
			if version.hasNetwork && version.networkID == identity.GetEntityId() && version.dimension == state.GetDimension() && version.start <= tick && tick < version.end {
				if linked != nil {
					return fmt.Errorf("player state at tick %d links multiple entities", tick)
				}
				linked = version
			}
		}
		if linked == nil || linked.typeID != "minecraft:player" || linked.uuid != result.GetPlayerUuid() {
			return fmt.Errorf("player state at tick %d does not link the subject player", tick)
		}
		payload := &artifactsv1.SceneBlobPayload{Value: &artifactsv1.SceneBlobPayload_PlayerState{PlayerState: event}}
		digest, err := w.putBlob("player_state", payload)
		if err != nil {
			return err
		}
		rotation := state.GetRotation()
		position, velocity := state.GetPosition(), state.GetVelocity()
		err = w.tx.PlayerState.Create().SetID(tick).SetEntityVersionID(linked.id).SetEntityInstanceID(linked.instance).SetEntityID(identity.GetEntityId()).
			SetDimension(state.GetDimension()).SetPositionX(position.GetX()).SetPositionY(position.GetY()).SetPositionZ(position.GetZ()).
			SetVelocityX(velocity.GetX()).SetVelocityY(velocity.GetY()).SetVelocityZ(velocity.GetZ()).SetYaw(rotation.GetYaw()).SetPitch(rotation.GetPitch()).
			SetHeadYaw(rotation.GetHeadYaw()).SetAlive(state.GetAlive()).SetOnGround(state.GetOnGround()).SetPose(state.GetPose()).SetSprinting(state.GetSprinting()).
			SetSneaking(state.GetSneaking()).SetSwimming(state.GetSwimming()).SetFallFlying(state.GetFallFlying()).SetUsingItem(state.GetUsingItem()).
			SetUseItemRemainingTicks(int(state.GetUseItemRemainingTicks())).SetGameMode(state.GetGameMode()).SetHealth(state.GetHealth()).SetMaxHealth(state.GetMaxHealth()).
			SetAbsorption(state.GetAbsorption()).SetArmor(int(state.GetArmor())).SetAir(int(state.GetAir())).SetMaxAir(int(state.GetMaxAir())).SetFoodLevel(int(state.GetFoodLevel())).
			SetSaturation(state.GetSaturation()).SetExperienceLevel(int(state.GetExperienceLevel())).SetExperienceProgress(state.GetExperienceProgress()).
			SetTotalExperience(int(state.GetTotalExperience())).SetSelectedSlot(int(state.GetSelectedSlot())).SetStateBarrierApplySequence(int64(state.GetStateBarrierApplySequence())).
			SetPayloadSha256(digest).Exec(w.ctx)
		if err != nil {
			return fmt.Errorf("insert player state at tick %d: %w", tick, err)
		}
		count++
		return nil
	})
	if err != nil {
		return 0, err
	}
	if count != len(frames) {
		return 0, errors.New("player-state ticks do not exactly match scene frames")
	}
	return count, nil
}

func entityFields(value *artifactsv1.EntityBlob) entityActive {
	bounds := value.GetBounds()
	return entityActive{networkID: int64(value.GetNetworkId()), hasNetwork: true, dimension: value.GetDimension(), typeID: value.GetTypeId(), uuid: value.GetUuid(),
		aabb: [6]float64{bounds.GetMinX(), bounds.GetMinY(), bounds.GetMinZ(), bounds.GetMaxX(), bounds.GetMaxY(), bounds.GetMaxZ()}}
}

func entityIdentity(instance string, fields entityActive) string {
	parts := strings.Split(instance, ":")
	generation := ""
	if len(parts) >= 3 {
		generation = parts[len(parts)-1]
	}
	if fields.uuid != "" {
		return "uuid:" + fields.uuid + ":" + fields.typeID + ":" + generation
	}
	if fields.hasNetwork {
		return fmt.Sprintf("network:%d:%s:%s", fields.networkID, fields.typeID, generation)
	}
	return "instance:" + instance
}

func sameVector(left, right *artifactsv1.Vector3) bool {
	return left != nil && right != nil && left.GetX() == right.GetX() && left.GetY() == right.GetY() && left.GetZ() == right.GetZ()
}

func (w *storeWriter) sceneBlob(digest, kind string) (*artifactsv1.SceneBlobPayload, []byte, error) {
	raw, err := w.streamBlob(digest, kind)
	if err != nil {
		return nil, nil, err
	}
	value := &artifactsv1.SceneBlobPayload{}
	if err := protojson.Unmarshal(raw, value); err != nil {
		return nil, nil, fmt.Errorf("decode %s blob: %w", kind, err)
	}
	return value, raw, nil
}

func (w *storeWriter) streamBlob(digest, kind string) ([]byte, error) {
	decoded, err := hex.DecodeString(digest)
	if err != nil || len(decoded) != sha256.Size || hex.EncodeToString(decoded) != digest {
		return nil, fmt.Errorf("invalid %s blob digest", kind)
	}
	blobs := filepath.Join(w.stream, "blobs")
	path := filepath.Join(blobs, digest+".zlib")
	relative, err := filepath.Rel(blobs, path)
	if err != nil || !filepath.IsLocal(relative) {
		return nil, fmt.Errorf("invalid %s blob path", kind)
	}
	compressed, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	reader, err := zlib.NewReader(bytes.NewReader(compressed))
	if err != nil {
		return nil, err
	}
	value, err := io.ReadAll(io.LimitReader(reader, maxBlobBytes+1))
	closeErr := reader.Close()
	if err != nil {
		return nil, err
	}
	if closeErr != nil {
		return nil, closeErr
	}
	if len(value) > maxBlobBytes {
		return nil, fmt.Errorf("%s blob exceeds safety limit", kind)
	}
	sum := sha256.Sum256(value)
	if hex.EncodeToString(sum[:]) != digest {
		return nil, fmt.Errorf("%s blob %s has invalid content hash", kind, digest)
	}
	if !w.sourceBlobs[digest] {
		w.sourceBlobs[digest] = true
		w.sourceBlobBytes += int64(len(compressed))
	}
	return value, nil
}

func (w *storeWriter) putBlob(kind string, value proto.Message) (string, error) {
	raw, err := protojson.Marshal(value)
	if err != nil {
		return "", err
	}
	return w.putBlobBytes(kind, raw)
}

func (w *storeWriter) putBlobBytes(kind string, raw []byte) (string, error) {
	sum := sha256.Sum256(raw)
	digest := hex.EncodeToString(sum[:])
	if existing, ok := w.blobKinds[digest]; ok {
		if existing != kind {
			return "", fmt.Errorf("blob %s appears with two kinds", digest)
		}
		return digest, nil
	}
	var buffer bytes.Buffer
	writer, _ := zlib.NewWriterLevel(&buffer, 9)
	_, _ = writer.Write(raw)
	_ = writer.Close()
	compressed := buffer.Bytes()
	err := w.tx.Blob.Create().SetID(digest).SetKind(kind).SetEncoding("zlib").SetUncompressedSize(int64(len(raw))).
		SetCompressedSize(int64(len(compressed))).SetData(compressed).Exec(w.ctx)
	if err == nil {
		w.blobKinds[digest] = kind
	}
	return digest, err
}

func (w *storeWriter) writeMetadata(result *artifactsv1.SceneExtractionResult) error {
	sources := &artifactsv1.SceneExtractionResult{SourceReplays: result.GetSourceReplays()}
	sourceJSON, err := protojson.Marshal(sources)
	if err != nil {
		return fmt.Errorf("encode scene sources: %w", err)
	}
	provenanceJSON, err := protojson.Marshal(result)
	if err != nil {
		return fmt.Errorf("encode scene provenance: %w", err)
	}
	if err := w.tx.SchemaInfo.Create().SetID(1).SetSchemaName("recorder-minecraft-scene-store-v2").SetSchemaVersion(2).Exec(w.ctx); err != nil {
		return err
	}
	return w.tx.SceneMeta.Create().SetID(1).SetSessionID(result.GetSessionId()).SetPlayerUUID(result.GetPlayerUuid()).SetConnectionID(result.GetConnectionId()).
		SetStartTick(result.GetTicks().GetFirstTick()).SetEndTick(result.GetTicks().GetLastTick()).SetSourceReplaysJSON(sourceJSON).SetSensitive(true).
		SetProvenanceJSON(provenanceJSON).Exec(w.ctx)
}

func readDelimited[T proto.Message](path string, newValue func() T, visit func(int, T) error) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer func() { _ = file.Close() }()
	reader := bufio.NewReaderSize(file, 128*1024)
	for record := 1; ; record++ {
		line, readErr := reader.ReadBytes('\n')
		if len(line) == 0 && errors.Is(readErr, io.EOF) {
			return nil
		}
		if len(line) == 0 || len(line) > maxBlobBytes || line[len(line)-1] != '\n' {
			return fmt.Errorf("%s record %d must be bounded LF-terminated ProtoJSON", path, record)
		}
		value := newValue()
		err := protojson.Unmarshal(line[:len(line)-1], value)
		if err != nil {
			return fmt.Errorf("%s record %d: %w", path, record, err)
		}
		if err := visit(record, value); err != nil {
			return fmt.Errorf("%s record %d: %w", path, record, err)
		}
		if readErr != nil && !errors.Is(readErr, io.EOF) {
			return readErr
		}
	}
}

func sortedKeys[K comparable, V any](values map[K]V) []K {
	keys := make([]K, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool { return fmt.Sprint(keys[i]) < fmt.Sprint(keys[j]) })
	return keys
}
