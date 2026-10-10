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

func TestAssetsServeArtifactMediaTypesWithRanges(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	files := map[string]string{
		"world/sessions/s/alignments/session-alignment.jsonl": "application/x-ndjson",
		"plays/p/perception.jsonl":                            "application/x-ndjson",
		"plays/p/renders/fpv_frames/frames.jsonl":             "application/x-ndjson",
		"plays/p/scene.sqlite3":                               "application/vnd.sqlite3",
		"plays/p/renders/fpv.mp4":                             "video/mp4",
		"plays/p/metadata.json":                               "application/json",
	}
	for name := range files {
		path := filepath.Join(root, filepath.FromSlash(name))
		require.NoError(t, os.MkdirAll(filepath.Dir(path), 0o700))
		require.NoError(t, os.WriteFile(path, []byte("0123456789"), 0o600))
	}
	server := assetsServer(t, root)
	for name, mediaType := range files {
		t.Run(name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, "/assets/"+name, nil)
			request.Header.Set("Range", "bytes=0-3")
			response := httptest.NewRecorder()

			server.ServeHTTP(response, request)

			assert.Equal(t, http.StatusPartialContent, response.Code)
			assert.Equal(t, mediaType, response.Header().Get("Content-Type"))
			assert.Equal(t, "bytes 0-3/10", response.Header().Get("Content-Range"))
			assert.Equal(t, "0123", response.Body.String())
		})
	}
	t.Run("missing file", func(t *testing.T) {
		response := httptest.NewRecorder()

		server.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/assets/plays/p/actions.jsonl", nil))

		assert.Equal(t, http.StatusNotFound, response.Code)
		assert.NotEqual(t, "application/x-ndjson", response.Header().Get("Content-Type"))
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
