package recorderminecraft

import (
	"bytes"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRootHelpUsesCommandGroups(t *testing.T) {
	cmd, err := New()
	require.NoError(t, err)
	var output bytes.Buffer
	cmd.SetOut(&output)
	cmd.SetArgs([]string{"--help"})
	require.NoError(t, cmd.Execute())
	for _, expected := range []string{"Workspace Commands:", "Processing Commands:", "Other Commands:", "actions", "scene"} {
		assert.Contains(t, output.String(), expected, "root help command group")
	}
}

func TestActionsHelpDocumentsExplicitInputs(t *testing.T) {
	cmd, err := New()
	require.NoError(t, err)
	var output bytes.Buffer
	cmd.SetOut(&output)
	cmd.SetArgs([]string{"actions", "extract", "--help"})
	require.NoError(t, cmd.Execute())
	for _, expected := range []string{"--metadata", "--events", "--output", "Examples:"} {
		assert.Contains(t, output.String(), expected, "actions help explicit input")
	}
}

func TestCommandTreeContainsNestedCommands(t *testing.T) {
	cmd, err := New()
	require.NoError(t, err)
	for _, path := range [][]string{{"config", "get"}, {"config", "view"}, {"actions", "extract"}, {"scene", "describe"}, {"scene", "extract"}, {"scene", "prepare"}} {
		found, _, err := cmd.Find(path)
		require.NoError(t, err, "find command path %v", path)
		assert.Equal(t, path[len(path)-1], found.Name(), "find command path %v", path)
	}
}
