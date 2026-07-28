package replays

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/google/uuid"
	"github.com/samber/do/v2"
)

const (
	CaptureContract  = "client_visible_scene_v1"
	maxMetadataBytes = 4 * 1024 * 1024
)

type Source struct {
	ReplayID                 string
	PlayerUUID               string
	ConnectionID             string
	Path                     string
	Format                   string
	SHA256                   string
	SizeBytes                int64
	FlashbackCaptureContract string
}

type recorderMetadata struct {
	Recorder struct {
		ReplayID                 string `json:"replay_id"`
		PlayerUUID               string `json:"player_uuid"`
		ConnectionID             string `json:"connection_id"`
		FlashbackCaptureContract string `json:"flashback_capture_contract"`
	} `json:"recorder-minecraft"`
}

type Service struct{}

func NewService(do.Injector) (*Service, error) {
	return &Service{}, nil
}

func (service *Service) Verify(path, playerUUID, connectionID string) (Source, error) {
	resolved, err := filepath.Abs(path)
	if err != nil {
		return Source{}, fmt.Errorf("resolve replay: %w", err)
	}
	before, err := os.Lstat(resolved)
	if err != nil {
		return Source{}, fmt.Errorf("inspect replay: %w", err)
	}
	if before.Mode()&os.ModeSymlink != 0 || !before.Mode().IsRegular() {
		return Source{}, fmt.Errorf("replay must be a non-symlinked regular file: %s", resolved)
	}
	archive, err := zip.OpenReader(resolved)
	if err != nil {
		return Source{}, fmt.Errorf("replay is not a readable Flashback ZIP: %s", resolved)
	}
	defer func() { _ = archive.Close() }()
	flashback, err := readEntry(archive.File, "metadata.json")
	if err != nil {
		return Source{}, err
	}
	var flashbackValue struct {
		Chunks map[string]json.RawMessage `json:"chunks"`
	}
	if err := json.Unmarshal(flashback, &flashbackValue); err != nil || flashbackValue.Chunks == nil {
		return Source{}, errors.New("replay does not contain Flashback metadata")
	}
	arcade, err := readEntry(archive.File, "arcade_replay_meta.json")
	if err != nil {
		return Source{}, err
	}
	var metadata recorderMetadata
	if err := json.Unmarshal(arcade, &metadata); err != nil {
		return Source{}, errors.New("replay does not contain ServerReplay metadata")
	}
	identity := metadata.Recorder
	for label, value := range map[string]string{
		"replay ID": identity.ReplayID, "replay player UUID": identity.PlayerUUID,
		"replay connection ID": identity.ConnectionID,
	} {
		parsed, parseErr := uuid.Parse(value)
		if parseErr != nil || parsed.String() != value {
			return Source{}, fmt.Errorf("%s must use canonical UUID spelling", label)
		}
	}
	if identity.FlashbackCaptureContract != CaptureContract {
		return Source{}, fmt.Errorf("replay requires %s", CaptureContract)
	}
	if identity.PlayerUUID != playerUUID || identity.ConnectionID != connectionID {
		return Source{}, errors.New("replay input identity does not match the requested connection")
	}
	digest, size, err := service.Digest(resolved)
	if err != nil {
		return Source{}, err
	}
	after, err := os.Stat(resolved)
	if err != nil || !os.SameFile(before, after) || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return Source{}, errors.New("replay changed while it was being read")
	}
	return Source{
		ReplayID: identity.ReplayID, PlayerUUID: identity.PlayerUUID,
		ConnectionID: identity.ConnectionID, Path: resolved, Format: "flashback",
		SHA256: digest, SizeBytes: size, FlashbackCaptureContract: identity.FlashbackCaptureContract,
	}, nil
}

func (*Service) Digest(path string) (string, int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", 0, fmt.Errorf("read replay: %w", err)
	}
	defer func() { _ = file.Close() }()
	hash := sha256.New()
	size, err := io.Copy(hash, file)
	if err != nil {
		return "", 0, fmt.Errorf("hash replay: %w", err)
	}
	return hex.EncodeToString(hash.Sum(nil)), size, nil
}

func readEntry(files []*zip.File, name string) ([]byte, error) {
	for _, file := range files {
		if file.Name != name {
			continue
		}
		if file.UncompressedSize64 > maxMetadataBytes {
			return nil, errors.New("flashback metadata exceeds the byte limit")
		}
		reader, err := file.Open()
		if err != nil {
			return nil, fmt.Errorf("read replay metadata %s: %w", name, err)
		}
		defer func() { _ = reader.Close() }()
		return io.ReadAll(io.LimitReader(reader, maxMetadataBytes+1))
	}
	return nil, fmt.Errorf("replay is missing %s", name)
}
