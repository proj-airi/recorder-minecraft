package middlewares

import (
	"io/fs"

	"github.com/labstack/echo/v5"
	"github.com/labstack/echo/v5/middleware"
)

// Assets serves artifact files from an already confined filesystem.
func Assets(filesystem fs.FS) echo.MiddlewareFunc {
	// NOTICE: Echo owns URL cleaning, wildcard extraction, range responses, and file serving here;
	// the caller supplies an os.Root-backed fs.FS only to establish the filesystem boundary.
	// See `https://github.com/labstack/echo/blob/ed8bbe4b6cbf519766c99e492b9cc427404b3719/middleware/static.go#L171-L235`.
	return middleware.StaticWithConfig(middleware.StaticConfig{
		Filesystem: filesystem,
		Root:       ".",
	})
}
