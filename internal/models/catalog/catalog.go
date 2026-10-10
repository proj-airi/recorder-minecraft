package catalog

import (
	"context"
	"errors"
	"fmt"
	"math"
	"net/url"
	"os"
	pathpkg "path"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	catalogv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/catalog/v1"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/proj-airi/recorder-minecraft/internal/models/plays"
	"github.com/proj-airi/recorder-minecraft/internal/models/replays"
	"github.com/proj-airi/recorder-minecraft/internal/models/worldcaptures"
	"github.com/samber/do/v2"
	"go.uber.org/fx"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const maxMetadataBytes = 4 * 1024 * 1024

var extensionTypePattern = regexp.MustCompile(`^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$`)

type Filter struct {
	ServerInstanceID string
	PlayerUUID       string
	StartedAtOrAfter time.Time
	StartedBefore    time.Time
}

// DefaultCacheTTL bounds how long a catalog snapshot is reused. Every read in that window, such as
// the burst of list requests a dashboard sends on load, shares one walk of the artifacts root.
const DefaultCacheTTL = 2 * time.Second

type Service struct {
	root      string
	inspector *replays.Service
	summaries *plays.Service
	worlds    *worldcaptures.Service
	cacheTTL  time.Duration
	now       func() time.Time

	mutex  sync.Mutex
	cached *snapshotState
}

// snapshotState is one unfiltered walk of the artifacts root. Its messages are shared and must be
// cloned before they leave the package.
type snapshotState struct {
	builtAt time.Time
	servers []*apiv1.ServerInstance
	plays   map[*apiv1.Replay]playSource
}

type playSource struct {
	path string
	// startedAt is the directory start time, which time filters compare.
	startedAt time.Time
	// readErr is the metadata failure behind an invalid Replay resource.
	readErr error
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
	return &Service{
		root: resolved, inspector: &replays.Service{}, summaries: plays.New(&captures.Service{}), worlds: &worldcaptures.Service{},
		cacheTTL: DefaultCacheTTL, now: time.Now,
	}, nil
}

func (service *Service) Root() string {
	return service.root
}

// Snapshot derives a catalog from the canonical V1 hierarchy. Directory names locate candidates,
// while metadata.json remains authoritative for identity and time values.
func (service *Service) Snapshot(ctx context.Context, filter Filter) ([]*apiv1.ServerInstance, error) {
	state, err := service.state(ctx, false)
	if err != nil {
		return nil, err
	}
	servers, _ := filterServers(state, filter)
	return servers, nil
}

// SnapshotWithSummaries calculates summaries for selected completed Plays. Unlike Snapshot,
// this operation fails atomically when a completed Play violates capture invariants.
func (service *Service) SnapshotWithSummaries(ctx context.Context, filter Filter) ([]*apiv1.ServerInstance, error) {
	state, err := service.state(ctx, false)
	if err != nil {
		return nil, err
	}
	servers, sources := filterServers(state, filter)
	for _, server := range servers {
		for _, player := range server.GetPlayers() {
			for _, replay := range player.GetReplays() {
				if err := ctx.Err(); err != nil {
					return nil, err
				}
				source := sources[replay]
				if source.readErr != nil {
					return nil, summaryError(source.path, server.GetInstanceId(), player.GetUuid(), replay.GetConnectionId(), source.readErr)
				}
				if replay.EndServerTick == nil {
					continue
				}
				summary, err := service.summaries.Summarize(ctx, source.path)
				if err != nil {
					if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
						return nil, err
					}
					return nil, summaryError(source.path, server.GetInstanceId(), player.GetUuid(), replay.GetConnectionId(), err)
				}
				replay.Summary = toPlaySummary(summary)
			}
		}
	}
	return servers, nil
}

// WorldSessions lists world sessions, optionally restricted to one server instance or session_id.
func (service *Service) WorldSessions(ctx context.Context, serverID, sessionID string) ([]*apiv1.WorldSession, error) {
	state, err := service.state(ctx, false)
	if err != nil {
		return nil, err
	}
	sessions := []*apiv1.WorldSession{}
	for _, server := range state.servers {
		if serverID != "" && server.GetInstanceId() != serverID {
			continue
		}
		for _, session := range server.GetWorldSessions() {
			if sessionID == "" || session.GetSessionId() == sessionID {
				sessions = append(sessions, proto.CloneOf(session))
			}
		}
	}
	return sessions, nil
}

// WorldSession returns one world session by server instance and directory name.
func (service *Service) WorldSession(ctx context.Context, serverID, worldSessionID string) (*apiv1.WorldSession, bool, error) {
	sessions, err := service.WorldSessions(ctx, serverID, "")
	if err != nil {
		return nil, false, err
	}
	for _, session := range sessions {
		if session.GetId() == worldSessionID {
			return session, true, nil
		}
	}
	return nil, false, nil
}

// RefreshResult describes the snapshot a Refresh produced.
type RefreshResult struct {
	RefreshedAt       time.Time
	ServerInstances   int
	Replays           int
	WorldSessionCount int
}

// Refresh discards the cached snapshot and walks the artifacts root again.
func (service *Service) Refresh(ctx context.Context) (RefreshResult, error) {
	state, err := service.state(ctx, true)
	if err != nil {
		return RefreshResult{}, err
	}
	result := RefreshResult{RefreshedAt: state.builtAt, ServerInstances: len(state.servers), Replays: len(state.plays)}
	for _, server := range state.servers {
		result.WorldSessionCount += len(server.GetWorldSessions())
	}
	return result, nil
}

// state returns a snapshot no older than the cache TTL. Concurrent callers wait for one walk.
func (service *Service) state(ctx context.Context, force bool) (*snapshotState, error) {
	service.mutex.Lock()
	defer service.mutex.Unlock()
	now := service.now()
	if !force && service.cached != nil && now.Sub(service.cached.builtAt) < service.cacheTTL {
		return service.cached, nil
	}
	state, err := service.build(ctx)
	if err != nil {
		return nil, err
	}
	state.builtAt = now
	service.cached = state
	return state, nil
}

func (service *Service) build(ctx context.Context) (*snapshotState, error) {
	state := &snapshotState{servers: []*apiv1.ServerInstance{}, plays: map[*apiv1.Replay]playSource{}}
	root := filepath.Join(service.root, "v1")
	serverEntries, err := readDirectory(root)
	if errors.Is(err, os.ErrNotExist) {
		return state, nil
	}
	if err != nil {
		return nil, fmt.Errorf("read artifact layout %s: %w", root, err)
	}

	for _, serverEntry := range serverEntries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		serverName, serverID, ok := splitIdentity(serverEntry.Name())
		if !ok {
			continue
		}
		serverPath := filepath.Join(root, serverEntry.Name())
		if !safeDirectory(serverPath, serverEntry) {
			continue
		}
		sessions := service.worldSessions(serverPath, serverName, serverID)
		players, err := service.players(ctx, state, serverPath, serverName, serverID, newSessionIndex(sessions))
		if err != nil {
			return nil, err
		}
		linkPlays(sessions, players)
		state.servers = append(state.servers, &apiv1.ServerInstance{Name: serverName, InstanceId: serverID, Players: players, WorldSessions: sessions})
	}
	return state, nil
}

// filterServers applies a Filter to a shared snapshot. It returns independent copies and the
// source of each copied Replay.
func filterServers(state *snapshotState, filter Filter) ([]*apiv1.ServerInstance, map[*apiv1.Replay]playSource) {
	result := make([]*apiv1.ServerInstance, 0, len(state.servers))
	sources := map[*apiv1.Replay]playSource{}
	timeFiltered := !filter.StartedAtOrAfter.IsZero() || !filter.StartedBefore.IsZero()
	for _, server := range state.servers {
		if filter.ServerInstanceID != "" && server.GetInstanceId() != filter.ServerInstanceID {
			continue
		}
		players := make([]*apiv1.Player, 0, len(server.GetPlayers()))
		for _, player := range server.GetPlayers() {
			if filter.PlayerUUID != "" && player.GetUuid() != filter.PlayerUUID {
				continue
			}
			replays := make([]*apiv1.Replay, 0, len(player.GetReplays()))
			for _, replay := range player.GetReplays() {
				source := state.plays[replay]
				if matchesTime(source.startedAt, filter) {
					clone := proto.CloneOf(replay)
					sources[clone] = source
					replays = append(replays, clone)
				}
			}
			if len(replays) > 0 || !timeFiltered {
				players = append(players, &apiv1.Player{Name: player.GetName(), Uuid: player.GetUuid(), Replays: replays})
			}
		}
		if len(players) > 0 || filter.PlayerUUID == "" {
			sessions := make([]*apiv1.WorldSession, 0, len(server.GetWorldSessions()))
			for _, session := range server.GetWorldSessions() {
				sessions = append(sessions, proto.CloneOf(session))
			}
			result = append(result, &apiv1.ServerInstance{Name: server.GetName(), InstanceId: server.GetInstanceId(), Players: players, WorldSessions: sessions})
		}
	}
	return result, sources
}

func (service *Service) players(ctx context.Context, state *snapshotState, serverPath, serverName, serverID string, sessions sessionIndex) ([]*apiv1.Player, error) {
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
		if !ok {
			continue
		}
		playerPath := filepath.Join(root, entry.Name())
		if !safeDirectory(playerPath, entry) {
			continue
		}
		replays, err := service.replays(ctx, state, playerPath, serverName, serverID, name, id, sessions)
		if err != nil {
			return nil, err
		}
		players = append(players, &apiv1.Player{Name: name, Uuid: id, Replays: replays})
	}
	return players, nil
}

func (service *Service) replays(ctx context.Context, state *snapshotState, playerPath, serverName, serverID, playerName, playerID string, sessions sessionIndex) ([]*apiv1.Replay, error) {
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
		startedAt, err := parseDirectoryTime(startedName)
		if err != nil {
			continue
		}
		playPath := filepath.Join(root, entry.Name())
		if !safeDirectory(playPath, entry) {
			continue
		}
		replay, err := service.readReplay(playPath, serverName, serverID, playerName, playerID, connectionID, startedAt, sessions)
		if err != nil {
			replay = service.invalidReplay(playPath, serverName, serverID, playerName, playerID, connectionID, startedAt, err)
		}
		state.plays[replay] = playSource{path: playPath, startedAt: startedAt, readErr: err}
		replays = append(replays, replay)
	}
	return replays, nil
}

func parseDirectoryTime(value string) (time.Time, error) {
	return time.Parse("20060102T150405.999999999Z", value)
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

func (service *Service) readReplay(playPath, serverName, serverID, playerName, playerID, connectionID string, directoryStartedAt time.Time, sessions sessionIndex) (*apiv1.Replay, error) {
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
	replay.Video = service.video(playPath, serverID, playerID, connectionID)
	replay.Extensions = service.extensions(playPath, serverID, playerID, connectionID)
	replay.PerceptionUrl = service.optionalAssetURL(playPath, "perception.jsonl")
	replay.ActionsUrl = service.optionalAssetURL(playPath, "actions.jsonl")
	replay.SceneUrl = service.optionalAssetURL(playPath, "scene.sqlite3")
	replay.FramesIndexUrl = service.optionalAssetURL(playPath, "renders/fpv_frames/frames.jsonl")
	if id, link := sessions.resolve(metadata); link != apiv1.WorldSessionLink_WORLD_SESSION_LINK_UNSPECIFIED {
		replay.WorldSessionId = &id
		replay.WorldSessionLink = link
	}
	if _, err := service.inspector.Inspect(filepath.Join(playPath, metadata.GetCapture().GetReplay()), playerID, connectionID); err != nil {
		message := publicValidationError(playPath, err)
		replay.ValidationError = &message
		replay.Video = nil
	}
	return replay, nil
}

func (service *Service) extensions(playPath, serverID, playerID, connectionID string) []*apiv1.PlayExtension {
	root := filepath.Join(playPath, "extensions")
	entries, err := readDirectory(root)
	if errors.Is(err, os.ErrNotExist) {
		return []*apiv1.PlayExtension{}
	}
	if err != nil {
		return []*apiv1.PlayExtension{}
	}

	extensions := make([]*apiv1.PlayExtension, 0, len(entries))
	for _, entry := range entries {
		extensionType := entry.Name()
		extensionPath := filepath.Join(root, extensionType)
		if !extensionTypePattern.MatchString(extensionType) || !safeDirectory(extensionPath, entry) {
			continue
		}

		manifest := &artifactsv1.PlayExtensionManifest{}
		if readProtoJSON(filepath.Join(extensionPath, "manifest.json"), manifest) != nil ||
			manifest.GetManifestVersion() != 1 || manifest.GetExtensionType() != extensionType ||
			len(manifest.GetAssets()) == 0 ||
			manifest.GetTimeDomain() != artifactsv1.PlayExtensionTimeDomain_PLAY_EXTENSION_TIME_DOMAIN_SERVER_TICK ||
			manifest.GetPlay().GetServerInstanceId() != serverID ||
			manifest.GetPlay().GetPlayerUuid() != playerID ||
			manifest.GetPlay().GetConnectionId() != connectionID {
			continue
		}

		assets := extensionAssets(service, playPath, extensionPath, extensionType, manifest.GetAssets())
		if assets == nil {
			continue
		}
		extensions = append(extensions, &apiv1.PlayExtension{ExtensionType: extensionType, Assets: assets})
	}
	return extensions
}

func extensionAssets(service *Service, playPath, extensionPath, extensionType string, assets []*artifactsv1.PlayExtensionAsset) []*apiv1.PlayExtensionAsset {
	result := make([]*apiv1.PlayExtensionAsset, 0, len(assets))
	for _, asset := range assets {
		relative := asset.GetPath()
		clean := pathpkg.Clean(relative)
		if asset.GetRole() == "" || asset.GetMediaType() == "" || asset.GetSchema() == "" ||
			relative == "" || clean != relative || pathpkg.IsAbs(clean) || clean == "." || strings.HasPrefix(clean, "../") {
			return nil
		}

		assetPath := filepath.Join(extensionPath, filepath.FromSlash(clean))
		info, err := os.Lstat(assetPath)
		if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
			return nil
		}
		result = append(result, &apiv1.PlayExtensionAsset{
			Role: asset.GetRole(), Url: service.assetURL(playPath, pathpkg.Join("extensions", extensionType, clean)),
			MediaType: asset.GetMediaType(), Schema: asset.GetSchema(),
		})
	}
	return result
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

func (service *Service) video(playPath, serverID, playerID, connectionID string) *apiv1.VideoAsset {
	videoPath := filepath.Join(playPath, "renders", "fpv.mp4")
	info, err := os.Lstat(videoPath)
	if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return nil
	}
	video := &apiv1.VideoAsset{
		Url:       service.assetURL(playPath, filepath.Join("renders", "fpv.mp4")),
		MediaType: "video/mp4",
		SizeBytes: uint64(info.Size()),
	}
	// A producer without a render job (an Airicraft screen capture) describes fpv.mp4 with
	// fpv.json. The manifest records the MP4 size, so a later render that replaced the MP4 makes
	// the manifest stale, and the render result then describes the video instead.
	if manifest := readFpvManifest(playPath, serverID, playerID, connectionID, info.Size()); manifest != nil {
		anchors := manifest.GetFrames()
		video.Width = manifest.GetWidth()
		video.Height = manifest.GetHeight()
		video.FramesPerSecond = manifest.GetFramesPerSecond()
		video.FrameCount = manifest.GetFrameCount()
		video.DurationSeconds = manifest.GetDurationSeconds()
		video.Timing = &apiv1.VideoTiming{
			Format:          apiv1.VideoTiming_FORMAT_FPV_MANIFEST,
			Url:             service.assetURL(playPath, filepath.Join("renders", "fpv.json")),
			FirstServerTick: anchors[0].GetServerTick(),
			LastServerTick:  anchors[len(anchors)-1].GetServerTick(),
			AnchorCount:     uint64(len(anchors)),
			Complete:        manifest.GetComplete(),
		}
		return video
	}
	// NOTICE: Render manifests produced before the current Protobuf contract may not decode strictly.
	// The independently complete MP4 remains usable; a current, complete manifest only enriches the
	// catalog entry with geometry and frame metadata.
	result := &artifactsv1.RenderResult{}
	if readProtoJSON(filepath.Join(playPath, "renders", "result.json"), result) == nil &&
		result.GetStatus() == artifactsv1.RenderResultStatus_RENDER_RESULT_STATUS_COMPLETE {
		video.Width = result.GetWidth()
		video.Height = result.GetHeight()
		video.FramesPerSecond = result.GetFramesPerSecond()
		video.FrameCount = result.GetFrameCount()
		if result.GetFramesPerSecond() > 0 {
			video.DurationSeconds = float64(result.GetFrameCount()) / result.GetFramesPerSecond()
		}
		frameIndex := filepath.Join("renders", "fpv_frames", "frames.jsonl")
		if regularFile(filepath.Join(playPath, frameIndex)) && result.GetGlobalTicks() != nil {
			video.Timing = &apiv1.VideoTiming{
				Format:          apiv1.VideoTiming_FORMAT_RENDER_FRAME_INDEX,
				Url:             service.assetURL(playPath, frameIndex),
				FirstServerTick: result.GetGlobalTicks().GetFirstTick(),
				LastServerTick:  result.GetGlobalTicks().GetLastTick(),
				AnchorCount:     result.GetFrameCount(),
				Complete:        true,
			}
		}
	}
	return video
}

// optionalAssetURL returns the URL of a derived file only when it is a regular, non-symlinked file.
func (service *Service) optionalAssetURL(playPath, relative string) *string {
	info, err := os.Lstat(filepath.Join(playPath, filepath.FromSlash(relative)))
	if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return nil
	}
	value := service.assetURL(playPath, relative)
	if value == "" {
		return nil
	}
	return &value
}

// readFpvManifest returns renders/fpv.json when it describes this Play and an MP4 of videoSize
// bytes, with usable anchors. Any other manifest is ignored rather than reported: the MP4 itself
// stays playable, only without timing.
func readFpvManifest(playPath, serverID, playerID, connectionID string, videoSize int64) *artifactsv1.FpvVideoManifest {
	manifest := &artifactsv1.FpvVideoManifest{}
	if readProtoJSON(filepath.Join(playPath, "renders", "fpv.json"), manifest) != nil ||
		manifest.GetSchemaVersion() != 1 ||
		manifest.GetServerInstanceId() != serverID || manifest.GetPlayerUuid() != playerID ||
		manifest.GetConnectionId() != connectionID ||
		manifest.GetSizeBytes() != uint64(videoSize) || len(manifest.GetFrames()) == 0 {
		return nil
	}
	previous := manifest.GetFrames()[0]
	if !validVideoSeconds(previous.GetVideoSeconds()) {
		return nil
	}
	// Consumers binary-search the anchors by server tick and by video time, so both must be ordered.
	for _, anchor := range manifest.GetFrames()[1:] {
		if !validVideoSeconds(anchor.GetVideoSeconds()) || anchor.GetVideoSeconds() <= previous.GetVideoSeconds() ||
			anchor.GetServerTick() < previous.GetServerTick() {
			return nil
		}
		previous = anchor
	}
	return manifest
}

func validVideoSeconds(value float64) bool {
	return value >= 0 && !math.IsInf(value, 0) && !math.IsNaN(value)
}

func regularFile(path string) bool {
	info, err := os.Lstat(path)
	return err == nil && info.Mode()&os.ModeSymlink == 0 && info.Mode().IsRegular()
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
