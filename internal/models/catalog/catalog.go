package catalog

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	catalogv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/catalog/v1"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/proj-airi/recorder-minecraft/internal/models/plays"
	"github.com/proj-airi/recorder-minecraft/internal/models/replays"
	"github.com/samber/do/v2"
	"go.uber.org/fx"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const maxMetadataBytes = 4 * 1024 * 1024

type Filter struct {
	ServerInstanceID string
	PlayerUUID       string
	StartedAtOrAfter time.Time
	StartedBefore    time.Time
}

type Service struct {
	root      string
	inspector *replays.Service
	summaries *plays.Service
}

type SummaryError struct {
	ServerInstanceID string
	PlayerUUID       string
	ConnectionID     string
	PlayPath         string
	Cause            error
}

func (err *SummaryError) Error() string {
	return fmt.Sprintf("summarize Play %s: %v", err.PlayPath, err.Cause)
}

func (err *SummaryError) Unwrap() error {
	return err.Cause
}

func (err *SummaryError) PublicMessage() string {
	return fmt.Sprintf(
		"summarize Play server_instance_id=%s player_uuid=%s connection_id=%s: %s",
		err.ServerInstanceID, err.PlayerUUID, err.ConnectionID, publicValidationError(err.PlayPath, err.Cause),
	)
}

func NewService(injector do.Injector) (*Service, error) {
	config, err := do.Invoke[*configs.Config](injector)
	if err != nil {
		return nil, err
	}
	return New(config.Paths.Artifacts)
}

type NewServiceParams struct {
	fx.In

	Config *configs.Config
}

func NewServiceFx() func(params NewServiceParams) (*Service, error) {
	return func(params NewServiceParams) (*Service, error) {
		return New(params.Config.Paths.Artifacts)
	}
}

func Modules() fx.Option {
	return fx.Options(fx.Provide(NewServiceFx()))
}

func New(root string) (*Service, error) {
	resolved, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("resolve artifacts root: %w", err)
	}
	return &Service{root: resolved, inspector: &replays.Service{}, summaries: plays.New(&captures.Service{})}, nil
}

func (service *Service) Root() string {
	return service.root
}

// Snapshot derives a catalog from the canonical V1 hierarchy. Directory names locate candidates,
// while metadata.json remains authoritative for identity and time values.
func (service *Service) Snapshot(ctx context.Context, filter Filter) ([]*apiv1.ServerInstance, error) {
	return service.snapshot(ctx, filter, false)
}

// SnapshotWithSummaries calculates summaries for selected completed Plays. Unlike Snapshot,
// this operation fails atomically when a completed Play violates capture invariants.
func (service *Service) SnapshotWithSummaries(ctx context.Context, filter Filter) ([]*apiv1.ServerInstance, error) {
	return service.snapshot(ctx, filter, true)
}

func (service *Service) snapshot(ctx context.Context, filter Filter, includeSummaries bool) ([]*apiv1.ServerInstance, error) {
	root := filepath.Join(service.root, "v1")
	serverEntries, err := readDirectory(root)
	if errors.Is(err, os.ErrNotExist) {
		return []*apiv1.ServerInstance{}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read artifact layout %s: %w", root, err)
	}

	servers := make([]*apiv1.ServerInstance, 0, len(serverEntries))
	for _, serverEntry := range serverEntries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		serverName, serverID, ok := splitIdentity(serverEntry.Name())
		if !ok || (filter.ServerInstanceID != "" && serverID != filter.ServerInstanceID) {
			continue
		}
		serverPath := filepath.Join(root, serverEntry.Name())
		if !safeDirectory(serverPath, serverEntry) {
			continue
		}
		players, err := service.players(ctx, serverPath, serverName, serverID, filter, includeSummaries)
		if err != nil {
			return nil, err
		}
		if len(players) > 0 || filter.PlayerUUID == "" {
			servers = append(servers, &apiv1.ServerInstance{Name: serverName, InstanceId: serverID, Players: players})
		}
	}
	return servers, nil
}

func (service *Service) players(ctx context.Context, serverPath, serverName, serverID string, filter Filter, includeSummaries bool) ([]*apiv1.Player, error) {
	root := filepath.Join(serverPath, "players")
	entries, err := readDirectory(root)
	if errors.Is(err, os.ErrNotExist) {
		return []*apiv1.Player{}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read players in %s: %w", serverPath, err)
	}
	players := make([]*apiv1.Player, 0, len(entries))
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		name, id, ok := splitIdentity(entry.Name())
		if !ok || (filter.PlayerUUID != "" && id != filter.PlayerUUID) {
			continue
		}
		playerPath := filepath.Join(root, entry.Name())
		if !safeDirectory(playerPath, entry) {
			continue
		}
		replays, err := service.replays(ctx, playerPath, serverName, serverID, name, id, filter, includeSummaries)
		if err != nil {
			return nil, err
		}
		if len(replays) > 0 || (filter.StartedAtOrAfter.IsZero() && filter.StartedBefore.IsZero()) {
			players = append(players, &apiv1.Player{Name: name, Uuid: id, Replays: replays})
		}
	}
	return players, nil
}

func (service *Service) replays(ctx context.Context, playerPath, serverName, serverID, playerName, playerID string, filter Filter, includeSummaries bool) ([]*apiv1.Replay, error) {
	root := filepath.Join(playerPath, "plays")
	entries, err := readDirectory(root)
	if errors.Is(err, os.ErrNotExist) {
		return []*apiv1.Replay{}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read plays in %s: %w", playerPath, err)
	}
	replays := make([]*apiv1.Replay, 0, len(entries))
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		startedName, connectionID, ok := splitIdentity(entry.Name())
		if !ok {
			continue
		}
		startedAt, err := time.Parse("20060102T150405.999999999Z", startedName)
		if err != nil || !matchesTime(startedAt, filter) {
			continue
		}
		playPath := filepath.Join(root, entry.Name())
		if !safeDirectory(playPath, entry) {
			continue
		}
		replay, err := service.readReplay(playPath, serverName, serverID, playerName, playerID, connectionID, startedAt)
		if err != nil {
			if includeSummaries {
				return nil, summaryError(playPath, serverID, playerID, connectionID, err)
			}
			replay = service.invalidReplay(playPath, serverName, serverID, playerName, playerID, connectionID, startedAt, err)
		}
		if includeSummaries && replay.EndServerTick != nil {
			summary, err := service.summaries.Summarize(ctx, playPath)
			if err != nil {
				if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
					return nil, err
				}
				return nil, summaryError(playPath, serverID, playerID, connectionID, err)
			}
			replay.Summary = toPlaySummary(summary)
		}
		replays = append(replays, replay)
	}
	return replays, nil
}

func summaryError(playPath, serverID, playerID, connectionID string, cause error) *SummaryError {
	return &SummaryError{
		ServerInstanceID: serverID,
		PlayerUUID:       playerID,
		ConnectionID:     connectionID,
		PlayPath:         playPath,
		Cause:            cause,
	}
}

func toPlaySummary(summary plays.Summary) *catalogv1.PlaySummary {
	result := &catalogv1.PlaySummary{
		DurationTicks:              uint64(summary.DurationTicks),
		ObservedPathDistanceBlocks: summary.ObservedPathDistanceBlocks,
		IdlePercentage:             summary.IdlePercentage,
		PlayerStateCount:           summary.PlayerStateCount,
		FinalInventory:             make([]*catalogv1.FinalInventoryItem, 0, len(summary.FinalInventory)),
	}
	for _, item := range summary.FinalInventory {
		result.FinalInventory = append(result.FinalInventory, &catalogv1.FinalInventoryItem{
			Slot: item.Slot, ItemId: item.ItemID, Count: item.Count, Damage: item.Damage, MaxDamage: item.MaxDamage,
		})
	}
	return result
}

func (service *Service) readReplay(playPath, serverName, serverID, playerName, playerID, connectionID string, directoryStartedAt time.Time) (*apiv1.Replay, error) {
	metadata := &artifactsv1.ServerMetadata{}
	if err := readProtoJSON(filepath.Join(playPath, "metadata.json"), metadata); err != nil {
		return nil, err
	}
	connection := metadata.GetConnection()
	startedAt := connection.GetStartedAt()
	if metadata.GetSchemaVersion() != 1 || metadata.GetLayoutVersion() != "v1" ||
		metadata.GetServer().GetName() != serverName || metadata.GetServer().GetInstanceId() != serverID ||
		metadata.GetPlayer().GetName() != playerName || metadata.GetPlayer().GetUuid() != playerID ||
		connection.GetId() != connectionID || startedAt == nil || !startedAt.AsTime().Truncate(time.Millisecond).Equal(directoryStartedAt) {
		return nil, fmt.Errorf("artifact metadata identity does not match %s", playPath)
	}
	replay := &apiv1.Replay{
		ConnectionId:     connectionID,
		SessionId:        metadata.GetSessionId(),
		ServerName:       serverName,
		ServerInstanceId: serverID,
		PlayerName:       playerName,
		PlayerUuid:       playerID,
		StartedAt:        startedAt,
		EndedAt:          connection.GetEndedAt(),
		StartServerTick:  connection.GetStartServerTick(),
		TerminalReason:   connection.TerminalReason,
		CaptureFailure:   connection.CaptureFailure,
		ReplayFormat:     metadata.GetCapture().GetReplayFormat(),
		ReplayUrl:        service.assetURL(playPath, metadata.GetCapture().GetReplay()),
		EventsUrl:        service.assetURL(playPath, metadata.GetCapture().GetEvents()),
	}
	if connection.EndServerTick != nil {
		value := connection.GetEndServerTick()
		replay.EndServerTick = &value
	}
	replay.Video = service.video(playPath)
	if _, err := service.inspector.Inspect(filepath.Join(playPath, metadata.GetCapture().GetReplay()), playerID, connectionID); err != nil {
		message := publicValidationError(playPath, err)
		replay.ValidationError = &message
		replay.Video = nil
	}
	return replay, nil
}

func (service *Service) invalidReplay(playPath, serverName, serverID, playerName, playerID, connectionID string, startedAt time.Time, cause error) *apiv1.Replay {
	message := publicValidationError(playPath, cause)
	return &apiv1.Replay{
		ConnectionId: connectionID, ServerName: serverName, ServerInstanceId: serverID,
		PlayerName: playerName, PlayerUuid: playerID, StartedAt: timestamppb.New(startedAt),
		ReplayUrl: service.assetURL(playPath, "capture/replay.zip"),
		EventsUrl: service.assetURL(playPath, "capture/events.jsonl"), ValidationError: &message,
	}
}

func publicValidationError(playPath string, err error) string {
	message := strings.ReplaceAll(err.Error(), playPath, "play")
	message = strings.ReplaceAll(message, filepath.Join(playPath, "capture", "replay.zip"), "replay.zip")
	return message
}

func (service *Service) video(playPath string) *apiv1.VideoAsset {
	videoPath := filepath.Join(playPath, "renders", "fpv.mp4")
	info, err := os.Lstat(videoPath)
	if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return nil
	}
	result := &artifactsv1.RenderResult{}
	video := &apiv1.VideoAsset{
		Url:       service.assetURL(playPath, filepath.Join("renders", "fpv.mp4")),
		MediaType: "video/mp4",
		SizeBytes: uint64(info.Size()),
	}
	// NOTICE: Render manifests produced before the current Protobuf contract may not decode strictly.
	// The independently complete MP4 remains usable; a current, complete manifest only enriches the
	// catalog entry with geometry and frame metadata.
	if readProtoJSON(filepath.Join(playPath, "renders", "result.json"), result) == nil &&
		result.GetStatus() == artifactsv1.RenderResultStatus_RENDER_RESULT_STATUS_COMPLETE {
		video.Width = result.GetWidth()
		video.Height = result.GetHeight()
		video.FramesPerSecond = result.GetFramesPerSecond()
		video.FrameCount = result.GetFrameCount()
	}
	return video
}

func (service *Service) assetURL(playPath, relative string) string {
	target := filepath.Join(playPath, filepath.FromSlash(relative))
	path, err := filepath.Rel(service.root, target)
	if err != nil || path == "." || strings.HasPrefix(path, ".."+string(filepath.Separator)) {
		return ""
	}
	parts := strings.Split(filepath.ToSlash(path), "/")
	for index := range parts {
		parts[index] = url.PathEscape(parts[index])
	}
	return "/assets/" + strings.Join(parts, "/")
}

func readDirectory(path string) ([]os.DirEntry, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return nil, fmt.Errorf("artifact directory is unsafe: %s", path)
	}
	return os.ReadDir(path)
}

func safeDirectory(path string, entry os.DirEntry) bool {
	if entry.Type()&os.ModeSymlink != 0 || !entry.IsDir() {
		return false
	}
	info, err := os.Lstat(path)
	return err == nil && info.Mode()&os.ModeSymlink == 0 && info.IsDir()
}

func splitIdentity(name string) (string, string, bool) {
	index := strings.LastIndex(name, "--")
	if index <= 0 || index+2 >= len(name) {
		return "", "", false
	}
	return name[:index], name[index+2:], true
}

func matchesTime(value time.Time, filter Filter) bool {
	return (filter.StartedAtOrAfter.IsZero() || !value.Before(filter.StartedAtOrAfter)) &&
		(filter.StartedBefore.IsZero() || value.Before(filter.StartedBefore))
}

func readProtoJSON(path string, value proto.Message) error {
	info, err := os.Lstat(path)
	if err != nil {
		return fmt.Errorf("inspect artifact metadata %s: %w", path, err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() || info.Size() > maxMetadataBytes {
		return fmt.Errorf("artifact metadata is unsafe: %s", path)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read artifact metadata %s: %w", path, err)
	}
	if err := protojson.Unmarshal(raw, value); err != nil {
		return fmt.Errorf("decode artifact metadata %s: %w", path, err)
	}
	return nil
}
