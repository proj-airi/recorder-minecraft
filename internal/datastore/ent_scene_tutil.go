package datastore

import (
	"path/filepath"
	"testing"

	"github.com/proj-airi/mc-play-recorder/internal/configs"
	"github.com/samber/do/v2"
	"github.com/stretchr/testify/require"
)

// NOTICE: This test-constructor pattern follows `NewTestEnt` from
// `https://github.com/proj-airi/kbv-extractor-memes/blob/73329d340faf18834990b64b2d17d222e43bb8ea/internal/datastore/ent_tutil.go#L15-L38`.
func NewTestScene(t *testing.T, target string) *Scene {
	t.Helper()

	injector := do.New(func(injector do.Injector) {
		do.ProvideValue(injector, &configs.Config{Paths: configs.Paths{
			Runtime: filepath.Join(t.TempDir(), "runtime"),
		}})
		do.Provide(injector, NewSceneDatabase(target, false))
	})
	scene, err := do.Invoke[*Scene](injector)
	require.NoError(t, err, "create test Scene datastore")
	t.Cleanup(func() {
		report := injector.Shutdown()
		require.True(t, report.Succeed, report.Error())
	})
	return scene
}
