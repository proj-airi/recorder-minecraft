package artifactsv1

import (
	"context"
	"errors"

	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/catalog"
	"github.com/samber/do/v2"
	"go.uber.org/fx"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type Service struct {
	apiv1.UnimplementedArtifactCatalogServiceServer
	catalog *catalog.Service
}

type NewServiceParams struct {
	fx.In

	Catalog *catalog.Service
}

func New(catalog *catalog.Service) *Service {
	return &Service{catalog: catalog}
}

func NewService(injector do.Injector) (*Service, error) {
	catalogService, err := do.Invoke[*catalog.Service](injector)
	if err != nil {
		return nil, err
	}
	return New(catalogService), nil
}

func NewServiceFx() func(params NewServiceParams) *Service {
	return func(params NewServiceParams) *Service {
		return &Service{catalog: params.Catalog}
	}
}

func Modules() fx.Option {
	return fx.Options(fx.Provide(NewServiceFx()))
}

func (service *Service) ListArtifacts(ctx context.Context, request *apiv1.ListArtifactsRequest) (*apiv1.ListArtifactsResponse, error) {
	servers, err := service.catalog.Snapshot(ctx, filter(request.GetServerInstanceId(), request.GetPlayerUuid(), request.GetStartedAtOrAfter(), request.GetStartedBefore()))
	if err != nil {
		return nil, status.Errorf(codes.Internal, "read artifact catalog: %v", err)
	}
	return &apiv1.ListArtifactsResponse{ServerInstances: servers}, nil
}

func (service *Service) ListServerInstances(ctx context.Context, _ *apiv1.ListServerInstancesRequest) (*apiv1.ListServerInstancesResponse, error) {
	servers, err := service.catalog.Snapshot(ctx, catalog.Filter{})
	if err != nil {
		return nil, status.Errorf(codes.Internal, "read artifact catalog: %v", err)
	}
	response := &apiv1.ListServerInstancesResponse{ServerInstances: make([]*apiv1.ServerInstanceSummary, 0, len(servers))}
	for _, server := range servers {
		var replayCount uint64
		for _, player := range server.GetPlayers() {
			replayCount += uint64(len(player.GetReplays()))
		}
		response.ServerInstances = append(response.ServerInstances, &apiv1.ServerInstanceSummary{
			Name: server.GetName(), InstanceId: server.GetInstanceId(), PlayerCount: uint64(len(server.GetPlayers())), ReplayCount: replayCount,
		})
	}
	return response, nil
}

func (service *Service) ListPlayers(ctx context.Context, request *apiv1.ListPlayersRequest) (*apiv1.ListPlayersResponse, error) {
	servers, err := service.catalog.Snapshot(ctx, catalog.Filter{ServerInstanceID: request.GetServerInstanceId()})
	if err != nil {
		return nil, status.Errorf(codes.Internal, "read artifact catalog: %v", err)
	}
	response := &apiv1.ListPlayersResponse{}
	for _, server := range servers {
		for _, player := range server.GetPlayers() {
			response.Players = append(response.Players, &apiv1.PlayerSummary{
				Name: player.GetName(), Uuid: player.GetUuid(), ServerName: server.GetName(), ServerInstanceId: server.GetInstanceId(), ReplayCount: uint64(len(player.GetReplays())),
			})
		}
	}
	return response, nil
}

func (service *Service) ListReplays(ctx context.Context, request *apiv1.ListReplaysRequest) (*apiv1.ListReplaysResponse, error) {
	catalogFilter := filter(request.GetServerInstanceId(), request.GetPlayerUuid(), request.GetStartedAtOrAfter(), request.GetStartedBefore())
	var servers []*apiv1.ServerInstance
	var err error
	if request.GetIncludeSummary() {
		servers, err = service.catalog.SnapshotWithSummaries(ctx, catalogFilter)
	} else {
		servers, err = service.catalog.Snapshot(ctx, catalogFilter)
	}
	if err != nil {
		var summaryError *catalog.SummaryError
		if errors.As(err, &summaryError) {
			return nil, status.Error(codes.DataLoss, summaryError.PublicMessage())
		}
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return nil, status.FromContextError(err).Err()
		}
		return nil, status.Errorf(codes.Internal, "read artifact catalog: %v", err)
	}
	response := &apiv1.ListReplaysResponse{}
	for _, server := range servers {
		for _, player := range server.GetPlayers() {
			response.Replays = append(response.Replays, player.GetReplays()...)
		}
	}
	return response, nil
}

func (service *Service) GetReplay(ctx context.Context, request *apiv1.GetReplayRequest) (*apiv1.GetReplayResponse, error) {
	servers, err := service.catalog.Snapshot(ctx, catalog.Filter{ServerInstanceID: request.GetServerInstanceId(), PlayerUUID: request.GetPlayerUuid()})
	if err != nil {
		return nil, status.Errorf(codes.Internal, "read artifact catalog: %v", err)
	}
	for _, server := range servers {
		for _, player := range server.GetPlayers() {
			for _, replay := range player.GetReplays() {
				if replay.GetConnectionId() == request.GetConnectionId() {
					return &apiv1.GetReplayResponse{Replay: replay}, nil
				}
			}
		}
	}
	return nil, status.Error(codes.NotFound, "replay not found")
}

func filter(serverID, playerID string, after, before *timestamppb.Timestamp) catalog.Filter {
	result := catalog.Filter{ServerInstanceID: serverID, PlayerUUID: playerID}
	if after != nil {
		result.StartedAtOrAfter = after.AsTime()
	}
	if before != nil {
		result.StartedBefore = before.AsTime()
	}
	return result
}
