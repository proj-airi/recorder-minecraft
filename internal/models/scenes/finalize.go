package scenes

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	sceneent "github.com/proj-airi/recorder-minecraft/databases/scene/ent"
	"github.com/proj-airi/recorder-minecraft/internal/datastore"
	"github.com/samber/do/v2"
	"google.golang.org/protobuf/encoding/protojson"
)

type Input struct {
	Result       string
	Stream       string
	PlayerStates string
}

type Finalizer struct{ scene *datastore.Scene }

func NewFinalizer(injector do.Injector) (*Finalizer, error) {
	scene, err := do.Invoke[*datastore.Scene](injector)
	if err != nil {
		return nil, err
	}
	return &Finalizer{scene: scene}, nil
}

type Info struct {
	Path             string `json:"path"`
	SessionID        string `json:"session_id"`
	PlayerUUID       string `json:"player_uuid"`
	ConnectionID     string `json:"connection_id"`
	StartTick        int64  `json:"start_tick"`
	EndTick          int64  `json:"end_tick"`
	FrameCount       int    `json:"frame_count"`
	SectionVersions  int    `json:"section_version_count"`
	EntityVersions   int    `json:"entity_version_count"`
	BlockEntityCount int    `json:"block_entity_version_count"`
	PlayerStates     int    `json:"player_state_count"`
}

type frameValue struct{ frame *artifactsv1.SceneFrameRecord }
type versionCounts struct{ changes, sections, entities, blocks int }
type sectionKey struct {
	dimension string
	x, y, z   int64
}
type blockKey struct {
	dimension string
	x, y, z   int64
}
type sectionActive struct {
	start int64
	blob  string
}
type blockActive struct {
	start        int64
	typeID, blob string
}
type entityActive struct {
	start, networkID        int64
	hasNetwork              bool
	dimension, typeID, blob string
	aabb                    [6]float64
	uuid                    string
}
type entityVersion struct {
	entityActive
	id       int64
	instance string
	end      int64
}

func (finalizer *Finalizer) Finalize(ctx context.Context, input Input) (info Info, err error) {
	result, err := loadResult(input.Result)
	if err != nil {
		return info, err
	}
	stream, err := filepath.Abs(input.Stream)
	if err != nil {
		return info, err
	}
	if filepath.Clean(result.GetStream().GetPath()) != stream {
		return info, fmt.Errorf("scene result stream path does not match %s", stream)
	}
	if err := verifyFile(filepath.Join(stream, result.GetStream().GetFrames()), int64(result.GetStream().GetFramesSizeBytes()), result.GetStream().GetFramesSha256()); err != nil {
		return info, err
	}
	if err := verifyFile(filepath.Join(stream, result.GetStream().GetChanges()), int64(result.GetStream().GetChangesSizeBytes()), result.GetStream().GetChangesSha256()); err != nil {
		return info, err
	}
	var frames map[int64]frameValue
	var counts versionCounts
	var stateCount int
	err = finalizer.scene.WithTx(ctx, func(tx *sceneent.Tx) error {
		writer := &storeWriter{ctx: ctx, tx: tx, stream: stream, blobKinds: map[string]string{}, sourceBlobs: map[string]bool{}}
		var versions []entityVersion
		frames, err = writer.writeFrames(filepath.Join(stream, result.GetStream().GetFrames()), result)
		if err != nil {
			return err
		}
		versions, counts, err = writer.writeChanges(filepath.Join(stream, result.GetStream().GetChanges()), result)
		if err != nil {
			return err
		}
		stateCount, err = writer.writeStates(input.PlayerStates, result, frames, versions)
		if err != nil {
			return err
		}
		if len(frames) != int(result.GetStream().GetFrameCount()) || counts.changes != int(result.GetStream().GetChangeCount()) {
			return errors.New("scene stream counts do not match extractor result")
		}
		if len(writer.sourceBlobs) != int(result.GetStream().GetBlobCount()) || writer.sourceBlobBytes != int64(result.GetStream().GetBlobBytes()) {
			return errors.New("scene stream blob integrity does not match extractor result")
		}
		if len(writer.blobKinds) != int(result.GetStream().GetBlobCount())+len(frames)+stateCount {
			return errors.New("scene blob count does not match extractor result")
		}
		return writer.writeMetadata(result)
	})
	if err != nil {
		return info, err
	}
	if err := finalizer.scene.Publish(); err != nil {
		return info, err
	}
	ticks := result.GetTicks()
	return Info{Path: finalizer.scene.Path(), SessionID: result.GetSessionId(), PlayerUUID: result.GetPlayerUuid(), ConnectionID: result.GetConnectionId(),
		StartTick: ticks.GetFirstTick(), EndTick: ticks.GetLastTick(), FrameCount: len(frames), SectionVersions: counts.sections,
		EntityVersions: counts.entities, BlockEntityCount: counts.blocks, PlayerStates: stateCount}, nil
}

func loadResult(path string) (*artifactsv1.SceneExtractionResult, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	result := &artifactsv1.SceneExtractionResult{}
	if err := protojson.Unmarshal(raw, result); err != nil {
		return nil, err
	}
	if result.GetStatus() != artifactsv1.SceneExtractionResult_STATUS_COMPLETE || result.GetStream().GetFrames() != "frames.jsonl" ||
		result.GetStream().GetChanges() != "changes.jsonl" || result.GetStream().GetBlobsDirectory() != "blobs" || result.Ticks == nil {
		return nil, errors.New("scene extractor result is not a completed protobuf stream")
	}
	return result, nil
}

func verifyFile(path string, size int64, digest string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer func() { _ = file.Close() }()
	hash := sha256.New()
	observed, err := io.Copy(hash, file)
	if err != nil {
		return err
	}
	if observed != size || hex.EncodeToString(hash.Sum(nil)) != digest {
		return fmt.Errorf("scene stream integrity check failed for %s", path)
	}
	return nil
}
