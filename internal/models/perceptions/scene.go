package perceptions

import (
	"bytes"
	"compress/zlib"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	sceneent "github.com/proj-airi/recorder-minecraft/databases/scene/ent"
	"github.com/proj-airi/recorder-minecraft/databases/scene/ent/blockentityversion"
	"github.com/proj-airi/recorder-minecraft/databases/scene/ent/entityversion"
	"github.com/proj-airi/recorder-minecraft/databases/scene/ent/frame"
	"github.com/proj-airi/recorder-minecraft/databases/scene/ent/playerstate"
	"github.com/proj-airi/recorder-minecraft/databases/scene/ent/sectionversion"
	"google.golang.org/protobuf/encoding/protojson"
)

const (
	sceneSchemaName    = "recorder-minecraft-scene-store-v2"
	sceneSchemaVersion = 2
	// Section blobs hold 4096 little-endian uint16 palette indices plus a
	// small palette; anything far larger is not a section.
	maxSceneBlobBytes = 16 * 1024 * 1024
)

// sceneView is the part of one Scene Store V2 the visibility pass reads.
type sceneView struct {
	client     *sceneent.Client
	ctx        context.Context
	dimensions map[string]int32
	states     map[int64]*sceneent.PlayerState
	coverage   map[int64]bool
	entities   []*entityTarget
	blocks     []blockEntityTarget
	world      *voxelWorld
	uuids      map[string]*string
}

// loadScene reads the observer states, frames, and every version that
// overlaps first..last. Section payloads stay in SQLite until a ray needs
// them.
func loadScene(ctx context.Context, client *sceneent.Client, first, last int64) (*sceneView, error) {
	view := &sceneView{client: client, ctx: ctx, dimensions: map[string]int32{}, states: map[int64]*sceneent.PlayerState{},
		coverage: map[int64]bool{}, uuids: map[string]*string{}}
	view.world = newVoxelWorld(view.decodeSection)

	states, err := client.PlayerState.Query().Where(playerstate.IDGTE(first), playerstate.IDLTE(last)).All(ctx)
	if err != nil {
		return nil, fmt.Errorf("read scene player states: %w", err)
	}
	for _, state := range states {
		view.states[state.ID] = state
		view.dimension(state.Dimension)
	}
	frames, err := client.Frame.Query().Where(frame.IDGTE(first), frame.IDLTE(last)).All(ctx)
	if err != nil {
		return nil, fmt.Errorf("read scene frames: %w", err)
	}
	for _, value := range frames {
		view.coverage[value.ID] = value.CoverageComplete
	}

	sections, err := client.SectionVersion.Query().Where(sectionversion.StartTickLTE(last), sectionversion.EndTickGT(first)).All(ctx)
	if err != nil {
		return nil, fmt.Errorf("read scene section versions: %w", err)
	}
	for _, section := range sections {
		coord := sectionCoord{view.dimension(section.Dimension), int32(section.SectionX), int32(section.SectionY), int32(section.SectionZ)}
		view.world.addSpan(coord, sectionSpan{start: section.StartTick, end: section.EndTick, blob: section.BlobSha256})
	}
	view.world.seal()

	entities, err := client.EntityVersion.Query().Where(entityversion.StartTickLTE(last), entityversion.EndTickGT(first)).All(ctx)
	if err != nil {
		return nil, fmt.Errorf("read scene entity versions: %w", err)
	}
	for _, entity := range entities {
		view.entities = append(view.entities, &entityTarget{
			instanceID: entity.InstanceID, networkID: entity.NetworkID, dimension: entity.Dimension, typeID: entity.TypeID,
			start: entity.StartTick, end: entity.EndTick, blobSHA256: entity.BlobSha256,
			box: aabb{min: vec{entity.MinX, entity.MinY, entity.MinZ}, max: vec{entity.MaxX, entity.MaxY, entity.MaxZ}},
		})
	}

	blocks, err := client.BlockEntityVersion.Query().Where(blockentityversion.StartTickLTE(last), blockentityversion.EndTickGT(first)).All(ctx)
	if err != nil {
		return nil, fmt.Errorf("read scene block entity versions: %w", err)
	}
	for _, block := range blocks {
		view.blocks = append(view.blocks, blockEntityTarget{dimension: block.Dimension, x: block.BlockX, y: block.BlockY, z: block.BlockZ,
			typeID: block.TypeID, start: block.StartTick, end: block.EndTick})
	}
	return view, nil
}

// dimension interns a dimension name so section keys stay small.
func (view *sceneView) dimension(name string) int32 {
	if id, ok := view.dimensions[name]; ok {
		return id
	}
	id := int32(len(view.dimensions))
	view.dimensions[name] = id
	return id
}

// blobPayload inflates one content-addressed scene blob and checks that its
// bytes still hash to the digest that names it.
func (view *sceneView) blobPayload(digest, kind string) (*artifactsv1.SceneBlobPayload, error) {
	row, err := view.client.Blob.Get(view.ctx, digest)
	if err != nil {
		return nil, fmt.Errorf("read %s blob %s: %w", kind, digest, err)
	}
	if row.Kind != kind || row.Encoding != "zlib" || row.UncompressedSize > maxSceneBlobBytes {
		return nil, fmt.Errorf("scene blob %s is not a bounded zlib %s blob", digest, kind)
	}
	reader, err := zlib.NewReader(bytes.NewReader(row.Data))
	if err != nil {
		return nil, fmt.Errorf("inflate %s blob %s: %w", kind, digest, err)
	}
	raw, err := io.ReadAll(io.LimitReader(reader, maxSceneBlobBytes+1))
	closeErr := reader.Close()
	if err != nil {
		return nil, fmt.Errorf("inflate %s blob %s: %w", kind, digest, err)
	}
	if closeErr != nil {
		return nil, fmt.Errorf("inflate %s blob %s: %w", kind, digest, closeErr)
	}
	sum := sha256.Sum256(raw)
	if int64(len(raw)) != row.UncompressedSize || hex.EncodeToString(sum[:]) != digest {
		return nil, fmt.Errorf("scene blob %s fails its content digest", digest)
	}
	payload := &artifactsv1.SceneBlobPayload{}
	if err := protojson.Unmarshal(raw, payload); err != nil {
		return nil, fmt.Errorf("decode %s blob %s: %w", kind, digest, err)
	}
	return payload, nil
}

// decodeSection turns a section blob into its occluder bitset. The occluder
// rule runs once per palette entry, not once per cell.
func (view *sceneView) decodeSection(digest string) (*occupancy, error) {
	payload, err := view.blobPayload(digest, "section")
	if err != nil {
		return nil, err
	}
	section := payload.GetSection()
	if section == nil {
		return nil, fmt.Errorf("scene blob %s is not a section", digest)
	}
	indices := section.GetIndicesLeU16()
	if len(indices) != 4096*2 || len(section.GetPalette()) == 0 {
		return nil, fmt.Errorf("section blob %s does not hold 4096 palette indices", digest)
	}
	paletteOccludes := make([]bool, len(section.GetPalette()))
	for index, state := range section.GetPalette() {
		paletteOccludes[index] = occludes(state.GetName(), state.GetProperties())
	}
	result := &occupancy{}
	for cell := range 4096 {
		paletteIndex := int(binary.LittleEndian.Uint16(indices[cell*2:]))
		if paletteIndex >= len(paletteOccludes) {
			return nil, fmt.Errorf("section blob %s references palette entry %d of %d", digest, paletteIndex, len(paletteOccludes))
		}
		if paletteOccludes[paletteIndex] {
			result.set(cell)
		}
	}
	return result, nil
}

// entityUUID reads the UUID from an entity blob once per instance. Only seen
// or undetermined entities pay this cost.
func (view *sceneView) entityUUID(target *entityTarget) (*string, error) {
	if value, ok := view.uuids[target.instanceID]; ok {
		return value, nil
	}
	payload, err := view.blobPayload(target.blobSHA256, "entity")
	if err != nil {
		return nil, err
	}
	entity := payload.GetEntity()
	if entity == nil {
		return nil, errors.New("scene entity blob has no entity payload")
	}
	view.uuids[target.instanceID] = entity.Uuid
	return entity.Uuid, nil
}
