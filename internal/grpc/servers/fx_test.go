package grpcservers

import (
	"testing"

	grpcservices "github.com/proj-airi/recorder-minecraft/internal/grpc/services"
	"github.com/proj-airi/recorder-minecraft/internal/models/catalog"
	"github.com/stretchr/testify/require"
	"go.uber.org/fx"
)

func TestServeFxGraph(t *testing.T) {
	t.Parallel()

	catalogService, err := catalog.New(t.TempDir())
	require.NoError(t, err)

	err = fx.ValidateApp(
		fx.NopLogger,
		fx.Supply(catalogService, Options{}),
		fx.Options(grpcservices.Modules()),
		fx.Options(Modules()),
		fx.Invoke(RunServer()),
	)
	require.NoError(t, err)
}
