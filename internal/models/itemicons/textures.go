package itemicons

import (
	"bytes"
	"image/png"
	"regexp"
	"strings"
)

// texturePath accepts lower-case slash-separated texture paths without traversal.
var texturePath = regexp.MustCompile(`^[a-z0-9_.-]+(/[a-z0-9_.-]+)*$`)

// servedTextureRoots limits raw texture access to the HUD/slot sprites and status effect icons the
// dashboard draws. Other textures stay unreachable so the endpoint cannot be used to dump the jar.
var servedTextureRoots = []string{"gui/sprites/hud/", "gui/sprites/container/slot/", "mob_effect/"}

// Texture returns one allowed texture as PNG, cropped to its first frame when animated.
func (source *Source) Texture(namespace, path string) ([]byte, error) {
	if !idPart.MatchString(namespace) || !texturePath.MatchString(path) || strings.Contains(path, "..") || !servedTexture(path) {
		return nil, ErrNotFound
	}
	key := "texture:" + namespace + ":" + path
	source.mutex.Lock()
	defer source.mutex.Unlock()
	if cached, ok := source.cache[key]; ok {
		return cached.png, cached.err
	}
	texture, err := source.texture(namespace + ":" + path)
	if err == nil {
		var buffer bytes.Buffer
		if err = png.Encode(&buffer, texture); err == nil {
			source.cache[key] = cachedIcon{png: buffer.Bytes()}
			return buffer.Bytes(), nil
		}
	}
	source.cache[key] = cachedIcon{err: err}
	return nil, err
}

func servedTexture(path string) bool {
	for _, root := range servedTextureRoots {
		if strings.HasPrefix(path, root) {
			return true
		}
	}
	return false
}
