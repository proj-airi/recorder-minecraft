//go:build !bundled_artifacts

package bundled

import (
	"errors"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestArtifactsReportsUnbundledDevelopmentBuild(t *testing.T) {
	result, err := Artifacts()
	require.Error(t, err)
	assert.True(t, errors.Is(err, ErrUnavailable))
	assert.Nil(t, result)
}
