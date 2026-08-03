package grpcservers

import (
	"context"
	"errors"

	"github.com/proj-airi/recorder-minecraft/internal/models/catalog"
	"github.com/proj-airi/recorder-minecraft/pkg/grpcpkg"
	"go.uber.org/fx"
)

func Modules() fx.Option {
	return fx.Options(fx.Provide(NewServerFx()))
}

type NewServerParams struct {
	fx.In

	Catalog  *catalog.Service
	Options  Options
	Register *grpcpkg.Register
}

func NewServerFx() func(params NewServerParams) *Server {
	return func(params NewServerParams) *Server {
		return newServer(params.Catalog.Root(), params.Options, params.Register)
	}
}

type RunServerParams struct {
	fx.In

	Lifecycle fx.Lifecycle
	Server    *Server
	Shutdown  fx.Shutdowner
}

func RunServer() func(params RunServerParams) {
	return func(params RunServerParams) {
		// NOTICE: Construction and runtime startup are separated following
		// `https://github.com/proj-airi/kbv-extractor-memes/blob/73329d340faf18834990b64b2d17d222e43bb8ea/internal/grpc/servers/api-server/grpc_server.go#L21-L73`.
		// This adapter owns cancellation while Server.Serve retains the existing combined runtime.
		serveContext, cancelServe := context.WithCancel(context.Background())
		serveResult := make(chan error, 1)

		params.Lifecycle.Append(fx.Hook{
			OnStart: func(context.Context) error {
				go func() {
					err := params.Server.Serve(serveContext)
					serveResult <- err
					if err != nil {
						_ = params.Shutdown.Shutdown()
					}
				}()
				return nil
			},
			OnStop: func(ctx context.Context) error {
				cancelServe()
				select {
				case err := <-serveResult:
					if errors.Is(err, context.Canceled) {
						return nil
					}
					return err
				case <-ctx.Done():
					return ctx.Err()
				}
			},
		})
	}
}
