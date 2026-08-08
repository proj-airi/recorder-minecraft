package serve

import (
	"context"
	"fmt"
	"time"

	"github.com/proj-airi/recorder-minecraft/internal/cli/recorder-minecraft/command"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	grpcservers "github.com/proj-airi/recorder-minecraft/internal/grpc/servers"
	grpcservices "github.com/proj-airi/recorder-minecraft/internal/grpc/services"
	"github.com/proj-airi/recorder-minecraft/internal/models/catalog"
	"github.com/spf13/cobra"
	"go.uber.org/fx"
)

func NewCommand() *cobra.Command {
	options := grpcservers.Options{GRPCAddress: "127.0.0.1:9090", HTTPAddress: "127.0.0.1:8080"}
	cmd := &cobra.Command{
		Use:     "serve",
		Short:   "Serve the artifact catalog over gRPC and HTTP",
		GroupID: command.WorkspaceGroup,
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			configPath, err := command.ConfigPath(cmd)
			if err != nil {
				return err
			}
			config, err := configs.Load(configPath)
			if err != nil {
				return err
			}

			// NOTICE: The serve command selects broad Fx modules and runtime invokes following
			// `https://github.com/proj-airi/kbv-extractor-memes/blob/73329d340faf18834990b64b2d17d222e43bb8ea/cmd/api-server/main.go#L32-L45`.
			// Other recorder commands retain their samber/do graph; only the long-running API process
			// needs Fx lifecycle and shutdown ordering.
			app := fx.New(
				fx.NopLogger,
				fx.Supply(options),
				fx.Supply(config),
				fx.Options(catalog.Modules()),
				fx.Options(grpcservices.Modules()),
				fx.Options(grpcservers.Modules()),
				fx.Invoke(grpcservers.RunServer()),
			)
			if err := app.Err(); err != nil {
				return err
			}

			startContext, cancelStart := context.WithTimeout(cmd.Context(), 15*time.Second)
			defer cancelStart()
			if err := app.Start(startContext); err != nil {
				return err
			}
			if _, err := fmt.Fprintf(cmd.OutOrStdout(), "Serving artifact API at http://%s\n", options.HTTPAddress); err != nil {
				return err
			}

			select {
			case <-cmd.Context().Done():
			case <-app.Done():
			}

			stopContext, cancelStop := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancelStop()
			return app.Stop(stopContext)
		},
	}
	cmd.Flags().StringVar(&options.GRPCAddress, "grpc-address", options.GRPCAddress, "gRPC listen address")
	cmd.Flags().StringVar(&options.HTTPAddress, "http-address", options.HTTPAddress, "HTTP gateway listen address")
	return cmd
}
