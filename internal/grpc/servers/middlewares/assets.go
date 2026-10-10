package middlewares

import (
	"io/fs"
	"path"
	"strings"

	"github.com/labstack/echo/v5"
	"github.com/labstack/echo/v5/middleware"
)

// assetMediaTypes pins the media types of artifact files. Go's mime package reads the host's
// mime.types files, so without this table JSONL, SQLite, and MP4 files would be served with
// different or sniffed types on different machines.
var assetMediaTypes = map[string]string{
	".json":    "application/json",
	".jsonl":   "application/x-ndjson",
	".mp4":     "video/mp4",
	".png":     "image/png",
	".sqlite3": "application/vnd.sqlite3",
	".zip":     "application/zip",
}

// Assets serves artifact files from an already confined filesystem.
func Assets(filesystem fs.FS) echo.MiddlewareFunc {
	// NOTICE: Echo owns URL cleaning, wildcard extraction, range responses, and file serving here;
	// the caller supplies an os.Root-backed fs.FS only to establish the filesystem boundary.
	// See `https://github.com/labstack/echo/blob/ed8bbe4b6cbf519766c99e492b9cc427404b3719/middleware/static.go#L171-L235`.
	static := middleware.StaticWithConfig(middleware.StaticConfig{
		Filesystem: filesystem,
		Root:       ".",
	})
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		serve := static(next)
		return func(context *echo.Context) error {
			// NOTICE: http.ServeContent keeps a Content-Type that is already set and only sniffs
			// when it is absent, so setting it here does not change Range handling.
			mediaType, ok := assetMediaTypes[strings.ToLower(path.Ext(context.Request().URL.Path))]
			if ok {
				context.Response().Header().Set(echo.HeaderContentType, mediaType)
			}
			err := serve(context)
			if err != nil && ok {
				// The error handler writes its own body; do not label it as the missing asset.
				context.Response().Header().Del(echo.HeaderContentType)
			}
			return err
		}
	}
}
