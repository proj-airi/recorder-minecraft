package configs

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestDefaultTextUsesPortableRuntimePath(t *testing.T) {
	text := DefaultText(false, "00000000-0000-0000-0000-000000000000")

	assert.Contains(t, text, `runtime = ".recorder/minecraft/runtime"`)
	assert.Contains(t, text, `scene_extractor_executable = "processors/scene-extractor/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor"`)
	assert.NotContains(t, text, "scene_extractor_project")
	assert.NotContains(t, text, `runtime = ".recorder\\minecraft\\runtime"`)
}

func TestLoadAcceptsLegacySceneExtractorConfig(t *testing.T) {
	for _, legacyExecutable := range []string{legacyDefaultSceneExtractor, legacyGeneratedSceneExtractor} {
		t.Run(filepath.Base(legacyExecutable), func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "recorder.toml")
			text := strings.ReplaceAll(
				DefaultText(false, "00000000-0000-0000-0000-000000000000"),
				"\n[processors]\nscene_extractor_executable = \"processors/scene-extractor/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor\"\n",
				"",
			)
			text = strings.Replace(
				text,
				"[mods]\n",
				"[mods]\nscene_extractor_project = \"mods/scene-extractor-mod\"\nscene_extractor_executable = \""+legacyExecutable+"\"\n",
				1,
			)
			require.NoError(t, os.WriteFile(path, []byte(text), 0o600))

			config, err := Load(path)
			require.NoError(t, err)
			assert.Equal(
				t,
				filepath.Join(filepath.Dir(path), "processors", "scene-extractor", "build", "install", "mc-recorder-scene-extractor", "bin", "mc-recorder-scene-extractor"),
				config.Processors.SceneExtractorExecutable,
			)
		})
	}
}

func TestLoadReportsUnknownConfigurationFields(t *testing.T) {
	path := filepath.Join(t.TempDir(), "recorder.toml")
	text := strings.Replace(
		DefaultText(false, "00000000-0000-0000-0000-000000000000"),
		"eula = false\n",
		"eula = false\nimage = \"example/minecraft\"\n",
		1,
	) + `
[capture]
epoch_ticks = 6000

[storage]
quota_gib = 10.0
`
	require.NoError(t, os.WriteFile(path, []byte(text), 0o600))

	_, err := Load(path)
	require.ErrorContains(t, err, "unsupported configuration fields: server.image (line ")
	assert.ErrorContains(t, err, "capture (line ")
	assert.ErrorContains(t, err, "storage (line ")
}

func TestResolveConvertsPortablePathSeparators(t *testing.T) {
	base := t.TempDir()

	resolved, err := resolve(base, strings.Join([]string{".recorder", "minecraft", "runtime"}, "/"))
	require.NoError(t, err)
	assert.Equal(t, filepath.Join(base, ".recorder", "minecraft", "runtime"), resolved)
}
