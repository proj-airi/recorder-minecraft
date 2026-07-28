package configs

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestDefaultTextUsesPortableRuntimePath(t *testing.T) {
	text := DefaultText(false, "00000000-0000-0000-0000-000000000000")

	assert.Contains(t, text, `runtime = ".recorder/minecraft/runtime"`)
	assert.NotContains(t, text, `runtime = ".recorder\\minecraft\\runtime"`)
}

func TestResolveConvertsPortablePathSeparators(t *testing.T) {
	base := t.TempDir()

	resolved, err := resolve(base, strings.Join([]string{".recorder", "minecraft", "runtime"}, "/"))
	require.NoError(t, err)
	assert.Equal(t, filepath.Join(base, ".recorder", "minecraft", "runtime"), resolved)
}
