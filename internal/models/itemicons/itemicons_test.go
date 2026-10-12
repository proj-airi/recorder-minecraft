package itemicons

import (
	"archive/zip"
	"bytes"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// writeJar builds a client-jar-shaped zip from name → content; PNG entries come from solid().
func writeJar(t *testing.T, entries map[string][]byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "client.jar")
	file, err := os.Create(path)
	require.NoError(t, err)
	writer := zip.NewWriter(file)
	for name, content := range entries {
		entry, err := writer.Create(name)
		require.NoError(t, err)
		_, err = entry.Write(content)
		require.NoError(t, err)
	}
	require.NoError(t, writer.Close())
	require.NoError(t, file.Close())
	return path
}

func solid(t *testing.T, width, height int, fill color.NRGBA) []byte {
	t.Helper()
	img := image.NewNRGBA(image.Rect(0, 0, width, height))
	for y := range height {
		for x := range width {
			img.SetNRGBA(x, y, fill)
		}
	}
	var buffer bytes.Buffer
	require.NoError(t, png.Encode(&buffer, img))
	return buffer.Bytes()
}

func decode(t *testing.T, data []byte) image.Image {
	t.Helper()
	img, err := png.Decode(bytes.NewReader(data))
	require.NoError(t, err)
	return img
}

func nrgba(img image.Image, x, y int) color.NRGBA {
	return color.NRGBAModel.Convert(img.At(x, y)).(color.NRGBA)
}

func fixtureJar(t *testing.T) string {
	white := color.NRGBA{R: 255, G: 255, B: 255, A: 255}
	return writeJar(t, map[string][]byte{
		"assets/minecraft/items/bread.json":               []byte(`{"model":{"type":"minecraft:model","model":"minecraft:item/bread"}}`),
		"assets/minecraft/models/item/bread.json":         []byte(`{"parent":"minecraft:item/generated","textures":{"layer0":"minecraft:item/bread"}}`),
		"assets/minecraft/textures/item/bread.png":        solid(t, 16, 16, color.NRGBA{R: 200, G: 150, B: 50, A: 255}),
		"assets/minecraft/items/oak_log.json":             []byte(`{"model":{"type":"minecraft:model","model":"minecraft:block/oak_log"}}`),
		"assets/minecraft/models/block/oak_log.json":      []byte(`{"parent":"minecraft:block/cube_column","textures":{"end":"minecraft:block/oak_log_top","side":"minecraft:block/oak_log"}}`),
		"assets/minecraft/models/block/cube_column.json":  []byte(`{"parent":"block/cube","textures":{"up":"#end","south":"#side","east":"#side"}}`),
		"assets/minecraft/models/block/cube.json":         []byte(`{"elements":[{"faces":{"up":{"texture":"#up"},"south":{"texture":"#south"},"east":{"texture":"#east"}}}]}`),
		"assets/minecraft/textures/block/oak_log_top.png": solid(t, 16, 16, white),
		"assets/minecraft/textures/block/oak_log.png":     solid(t, 16, 16, white),
		"assets/minecraft/items/oak_leaves.json":          []byte(`{"model":{"type":"minecraft:model","model":"minecraft:block/oak_leaves","tints":[{"type":"minecraft:constant","value":-12012264}]}}`),
		"assets/minecraft/models/block/oak_leaves.json":   []byte(`{"textures":{"all":"minecraft:block/oak_leaves"},"elements":[{"faces":{"up":{"texture":"#all","tintindex":0},"south":{"texture":"#all","tintindex":0},"east":{"texture":"#all","tintindex":0}}}]}`),
		"assets/minecraft/textures/block/oak_leaves.png":  solid(t, 16, 16, white),
		"assets/minecraft/items/chest.json":               []byte(`{"model":{"type":"minecraft:select","cases":[],"fallback":{"type":"minecraft:special","base":"minecraft:item/chest","model":{"type":"minecraft:chest"}}}}`),
		"assets/minecraft/models/item/chest.json":         []byte(`{"parent":"minecraft:builtin/entity","textures":{"particle":"minecraft:block/oak_planks"}}`),
		"assets/minecraft/textures/block/oak_planks.png":  solid(t, 16, 16, color.NRGBA{R: 160, G: 120, B: 70, A: 255}),
		"assets/minecraft/items/magma_cream.json":         []byte(`{"model":{"type":"minecraft:model","model":"minecraft:item/magma_cream"}}`),
		"assets/minecraft/models/item/magma_cream.json":   []byte(`{"parent":"item/generated","textures":{"layer0":"item/magma_cream"}}`),
		// An animated texture is a vertical strip of square frames.
		"assets/minecraft/textures/item/magma_cream.png": solid(t, 16, 48, color.NRGBA{R: 255, A: 255}),
	})
}

func TestIconRendersFlatItemsFromLayer0(t *testing.T) {
	t.Parallel()
	source, err := Open(fixtureJar(t))
	require.NoError(t, err)
	t.Cleanup(func() { _ = source.Close() })

	icon := decode(t, must(t)(source.Icon("minecraft", "bread")))
	assert.Equal(t, image.Rect(0, 0, 16, 16), icon.Bounds())
	assert.Equal(t, color.NRGBA{R: 200, G: 150, B: 50, A: 255}, nrgba(icon, 8, 8))

	animated := decode(t, must(t)(source.Icon("minecraft", "magma_cream")))
	assert.Equal(t, image.Rect(0, 0, 16, 16), animated.Bounds(), "only the first frame of an animated strip")
}

func TestIconRendersBlocksAsShadedCubes(t *testing.T) {
	t.Parallel()
	source, err := Open(fixtureJar(t))
	require.NoError(t, err)
	t.Cleanup(func() { _ = source.Close() })

	cube := decode(t, must(t)(source.Icon("minecraft", "oak_log")))
	assert.Equal(t, image.Rect(0, 0, cubeSize, cubeSize), cube.Bounds())
	top, left, right := nrgba(cube, 16, 8), nrgba(cube, 6, 20), nrgba(cube, 26, 20)
	assert.Equal(t, uint8(255), top.R, "top face is fully lit")
	assert.Greater(t, left.R, right.R, "south side is brighter than east side")
	assert.Equal(t, uint8(0), nrgba(cube, 0, 0).A, "corners outside the cube stay transparent")

	leaves := decode(t, must(t)(source.Icon("minecraft", "oak_leaves")))
	tinted := nrgba(leaves, 16, 8)
	assert.Equal(t, color.NRGBA{R: 0x48, G: 0xb5, B: 0x18, A: 255}, tinted, "constant tint -12012264 is #48B518")
}

func TestIconFallsBackToParticleForEntityRenderedItems(t *testing.T) {
	t.Parallel()
	source, err := Open(fixtureJar(t))
	require.NoError(t, err)
	t.Cleanup(func() { _ = source.Close() })

	chest := decode(t, must(t)(source.Icon("minecraft", "chest")))
	assert.Equal(t, color.NRGBA{R: 160, G: 120, B: 70, A: 255}, nrgba(chest, 4, 4))
}

func TestIconRejectsUnknownAndUnsafeIDs(t *testing.T) {
	t.Parallel()
	source, err := Open(fixtureJar(t))
	require.NoError(t, err)
	t.Cleanup(func() { _ = source.Close() })

	for _, id := range [][2]string{{"minecraft", "missing"}, {"minecraft", "../items/bread"}, {"Minecraft", "bread"}, {"minecraft", "bread/../bread"}} {
		_, err := source.Icon(id[0], id[1])
		assert.ErrorIs(t, err, ErrNotFound, id)
	}
}

func TestOpenRejectsJarsWithoutItemDefinitions(t *testing.T) {
	t.Parallel()
	_, err := Open(writeJar(t, map[string][]byte{"data/minecraft/recipe/bread.json": []byte(`{}`)}))
	assert.ErrorContains(t, err, "not a server jar")
}

func must(t *testing.T) func([]byte, error) []byte {
	return func(data []byte, err error) []byte {
		t.Helper()
		require.NoError(t, err)
		return data
	}
}

func TestTextureServesOnlyAllowedRoots(t *testing.T) {
	t.Parallel()
	white := color.NRGBA{R: 255, G: 255, B: 255, A: 255}
	source, err := Open(writeJar(t, map[string][]byte{
		"assets/minecraft/items/bread.json":                        []byte(`{}`),
		"assets/minecraft/textures/gui/sprites/hud/heart/full.png": solid(t, 9, 9, white),
		"assets/minecraft/textures/mob_effect/speed.png":           solid(t, 18, 18, white),
		"assets/minecraft/textures/entity/player/wide/steve.png":   solid(t, 64, 64, white),
	}))
	require.NoError(t, err)
	t.Cleanup(func() { _ = source.Close() })

	heart := decode(t, must(t)(source.Texture("minecraft", "gui/sprites/hud/heart/full")))
	assert.Equal(t, image.Rect(0, 0, 9, 9), heart.Bounds())
	_, err = source.Texture("minecraft", "mob_effect/speed")
	assert.NoError(t, err)
	for _, path := range []string{"entity/player/wide/steve", "gui/sprites/hud/../../../entity/player/wide/steve", "gui/sprites/hud//x"} {
		_, err := source.Texture("minecraft", path)
		assert.ErrorIs(t, err, ErrNotFound, path)
	}
}
