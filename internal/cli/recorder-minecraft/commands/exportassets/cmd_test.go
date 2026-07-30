package exportassets

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/proj-airi/recorder-minecraft/internal/bundled"
	"github.com/spf13/cobra"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestExportWritesArtifactsAndRefusesUnexpectedReplacement(t *testing.T) {
	directory := t.TempDir()
	items := []bundled.Artifact{{Name: "renderer.jar", Data: []byte("renderer")}, {Name: "extractor.zip", Data: []byte("extractor")}}
	cmd := &cobra.Command{}
	var output bytes.Buffer
	cmd.SetOut(&output)

	require.NoError(t, export(cmd, exportOptions{Output: directory}, items))
	assert.Contains(t, output.String(), filepath.Join(directory, "renderer.jar"))
	data, err := os.ReadFile(filepath.Join(directory, "extractor.zip"))
	require.NoError(t, err)
	assert.Equal(t, []byte("extractor"), data)

	err = export(cmd, exportOptions{Output: directory}, items)
	assert.ErrorContains(t, err, "pass --overwrite")

	items[0].Data = []byte("new renderer")
	require.NoError(t, export(cmd, exportOptions{Output: directory, Overwrite: true}, items))
	data, err = os.ReadFile(filepath.Join(directory, "renderer.jar"))
	require.NoError(t, err)
	assert.Equal(t, []byte("new renderer"), data)
}
