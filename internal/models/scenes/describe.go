package scenes

import (
	"context"
	"fmt"

	"github.com/proj-airi/recorder-minecraft/internal/datastore"
	"github.com/samber/do/v2"
)

type Description struct {
	SchemaName       string `json:"schema_name"`
	SchemaVersion    int    `json:"schema_version"`
	SessionID        string `json:"session_id"`
	PlayerUUID       string `json:"player_uuid"`
	ConnectionID     string `json:"connection_id"`
	StartTick        int64  `json:"start_tick"`
	EndTick          int64  `json:"end_tick"`
	FrameCount       int    `json:"frame_count"`
	SectionCount     int    `json:"section_version_count"`
	EntityCount      int    `json:"entity_version_count"`
	BlockEntityCount int    `json:"block_entity_version_count"`
	PlayerStateCount int    `json:"player_state_count"`
	BlobCount        int    `json:"blob_count"`
}

type Reader struct{}

func NewReader(do.Injector) (*Reader, error) {
	return &Reader{}, nil
}

func (*Reader) Describe(ctx context.Context, path string) (Description, error) {
	scene, err := datastore.OpenScene(path)
	if err != nil {
		return Description{}, err
	}
	defer func() { _ = scene.Close() }()
	schemaInfo, err := scene.SchemaInfo.Query().Only(ctx)
	if err != nil {
		return Description{}, fmt.Errorf("read scene schema info: %w", err)
	}
	metadata, err := scene.SceneMeta.Query().Only(ctx)
	if err != nil {
		return Description{}, fmt.Errorf("read scene metadata: %w", err)
	}
	frames, err := scene.Frame.Query().Count(ctx)
	if err != nil {
		return Description{}, fmt.Errorf("count scene frames: %w", err)
	}
	sections, err := scene.SectionVersion.Query().Count(ctx)
	if err != nil {
		return Description{}, fmt.Errorf("count scene sections: %w", err)
	}
	entities, err := scene.EntityVersion.Query().Count(ctx)
	if err != nil {
		return Description{}, fmt.Errorf("count scene entities: %w", err)
	}
	blockEntities, err := scene.BlockEntityVersion.Query().Count(ctx)
	if err != nil {
		return Description{}, fmt.Errorf("count scene block entities: %w", err)
	}
	playerStates, err := scene.PlayerState.Query().Count(ctx)
	if err != nil {
		return Description{}, fmt.Errorf("count scene player states: %w", err)
	}
	blobs, err := scene.Blob.Query().Count(ctx)
	if err != nil {
		return Description{}, fmt.Errorf("count scene blobs: %w", err)
	}
	return Description{
		SchemaName: schemaInfo.SchemaName, SchemaVersion: schemaInfo.SchemaVersion,
		SessionID: metadata.SessionID, PlayerUUID: metadata.PlayerUUID,
		ConnectionID: metadata.ConnectionID, StartTick: metadata.StartTick, EndTick: metadata.EndTick,
		FrameCount: frames, SectionCount: sections, EntityCount: entities,
		BlockEntityCount: blockEntities, PlayerStateCount: playerStates, BlobCount: blobs,
	}, nil
}
