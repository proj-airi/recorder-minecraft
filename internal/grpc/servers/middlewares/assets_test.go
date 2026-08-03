package middlewares

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/labstack/echo/v5"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestAssetsSupportsByteRanges(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(root, "video.mp4"), []byte("0123456789"), 0o600))
	request := httptest.NewRequest(http.MethodGet, "/assets/video.mp4", nil)
	request.Header.Set("Range", "bytes=2-5")
	response := httptest.NewRecorder()

	assetsServer(t, root).ServeHTTP(response, request)

	assert.Equal(t, http.StatusPartialContent, response.Code)
	assert.Equal(t, "bytes 2-5/10", response.Header().Get("Content-Range"))
	assert.Equal(t, "2345", response.Body.String())
}

func TestAssetsDoesNotServeExternalSymlinksOrDirectories(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "outside.mp4")
	require.NoError(t, os.WriteFile(outside, []byte("private"), 0o600))
	require.NoError(t, os.Symlink(outside, filepath.Join(root, "linked.mp4")))
	require.NoError(t, os.Mkdir(filepath.Join(root, "capture"), 0o700))

	server := assetsServer(t, root)
	t.Run("external symlink", func(t *testing.T) {
		request := httptest.NewRequest(http.MethodGet, "/assets/linked.mp4", nil)
		response := httptest.NewRecorder()

		server.ServeHTTP(response, request)

		assert.NotEqual(t, http.StatusOK, response.Code)
		assert.NotContains(t, response.Body.String(), "private")
	})
	t.Run("directory", func(t *testing.T) {
		path := "/assets/capture"
		request := httptest.NewRequest(http.MethodGet, path, nil)
		response := httptest.NewRecorder()

		server.ServeHTTP(response, request)

		assert.Equal(t, http.StatusNotFound, response.Code)
	})
}

func assetsServer(t *testing.T, root string) *echo.Echo {
	t.Helper()
	filesystem, err := os.OpenRoot(root)
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, filesystem.Close()) })
	server := echo.New()
	server.GET("/assets/*", func(*echo.Context) error { return echo.ErrNotFound }, Assets(filesystem.FS()))
	return server
}
