package middlewares

import (
	"errors"
	"net/http"
	"strings"

	"github.com/labstack/echo/v5"
	"github.com/proj-airi/recorder-minecraft/internal/models/itemicons"
)

// ItemIcons serves `/item-icons/:namespace/:file` where file is `<item path>.png`. Without a
// configured client jar every icon is 404, and the dashboard falls back to the item name.
func ItemIcons(source *itemicons.Source) echo.HandlerFunc {
	return func(context *echo.Context) error {
		path, ok := strings.CutSuffix(context.Param("file"), ".png")
		if source == nil || !ok {
			return echo.ErrNotFound
		}
		icon, err := source.Icon(context.Param("namespace"), path)
		if errors.Is(err, itemicons.ErrNotFound) {
			return echo.ErrNotFound
		}
		if err != nil {
			return err
		}
		// Icons only change when the operator swaps the jar, which needs a server restart.
		context.Response().Header().Set(echo.HeaderCacheControl, "public, max-age=86400")
		return context.Blob(http.StatusOK, "image/png", icon)
	}
}

// GameTextures serves `/game-textures/:namespace/*` (a `.png` texture path such as
// `gui/sprites/hud/heart/full.png`) for the allowed HUD, slot, and status effect sprites.
func GameTextures(source *itemicons.Source) echo.HandlerFunc {
	return func(context *echo.Context) error {
		path, ok := strings.CutSuffix(context.Param("*"), ".png")
		if source == nil || !ok {
			return echo.ErrNotFound
		}
		texture, err := source.Texture(context.Param("namespace"), path)
		if errors.Is(err, itemicons.ErrNotFound) {
			return echo.ErrNotFound
		}
		if err != nil {
			return err
		}
		context.Response().Header().Set(echo.HeaderCacheControl, "public, max-age=86400")
		return context.Blob(http.StatusOK, "image/png", texture)
	}
}
