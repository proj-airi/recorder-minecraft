package grpcservices

import (
	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
	artifactsv1 "github.com/proj-airi/recorder-minecraft/internal/grpc/services/artifactsv1"
	"github.com/proj-airi/recorder-minecraft/internal/models/catalog"
	"github.com/proj-airi/recorder-minecraft/pkg/grpcpkg"
	"go.uber.org/fx"
	"google.golang.org/grpc/reflection"
)

func Modules() fx.Option {
	// NOTICE: The Fx aggregator and injected register callback follow
	// `https://github.com/proj-airi/kbv-extractor-memes/blob/73329d340faf18834990b64b2d17d222e43bb8ea/internal/grpc/services/services.go#L13-L42`.
	// Register remains available below for the existing samber/do construction path.
	return fx.Options(
		fx.Provide(NewRegisterFx()),
		fx.Options(artifactsv1.Modules()),
	)
}

type NewRegisterParams struct {
	fx.In

	ArtifactsV1 *artifactsv1.Service
}

func NewRegisterFx() func(params NewRegisterParams) *grpcpkg.Register {
	return func(params NewRegisterParams) *grpcpkg.Register {
		return register(params.ArtifactsV1)
	}
}

func Register(catalogService *catalog.Service) *grpcpkg.Register {
	return register(artifactsv1.New(catalogService))
}

func register(service *artifactsv1.Service) *grpcpkg.Register {
	register := grpcpkg.NewRegister()
	register.RegisterGRPCService(func(server reflection.GRPCServer) {
		apiv1.RegisterArtifactCatalogServiceServer(server, service)
	})
	register.RegisterHTTPHandler(apiv1.RegisterArtifactCatalogServiceHandler)
	register.RegisterReflection()
	return register
}
