package itemicons

import (
	"image"
	"image/color"
)

type point struct{ x, y float64 }

// cubeFace maps texture coordinates (u, v in [0, 1), v pointing down the texture) onto the icon as
// origin + u*across + v*down.
type cubeFace struct {
	texture image.Image
	origin  point
	across  point
	down    point
	shade   float64
}

// renderCube draws a block item the way the inventory shows it: an isometric cube seen from above
// the south-east corner, with the top lit fully and the two visible sides darker.
//
// Corners of the cubeSize×cubeSize icon (s = cubeSize):
//
//	        T (s/2, 0)
//	L (0, s/4)    R (s, s/4)
//	        C (s/2, s/2)
//	BL (0, 3s/4)  BR (s, 3s/4)
//	        B (s/2, s)
//
// The top face is the rhombus T-R-C-L, the south face L-C-B-BL, and the east face C-R-BR-B.
func renderCube(top, south, east image.Image) image.Image {
	const s = float64(cubeSize)
	t, l, r, c := point{s / 2, 0}, point{0, s / 4}, point{s, s / 4}, point{s / 2, s / 2}
	bl, b := point{0, 3 * s / 4}, point{s / 2, s}
	faces := []cubeFace{
		// Texture top-left sits at the back corner T, so the texture's north edge runs along the back.
		{texture: top, origin: t, across: sub(r, t), down: sub(l, t), shade: 1.0},
		// NOTICE: 0.8 and 0.6 approximate the fixed GUI item lighting, which keeps the left (south)
		// side brighter than the right (east) side. Exact values differ slightly per texture because
		// the game lights normals, not faces; flat shading is close enough to recognize the block.
		{texture: south, origin: l, across: sub(c, l), down: sub(bl, l), shade: 0.8},
		{texture: east, origin: c, across: sub(r, c), down: sub(b, c), shade: 0.6},
	}

	icon := image.NewNRGBA(image.Rect(0, 0, cubeSize, cubeSize))
	for y := range cubeSize {
		for x := range cubeSize {
			// Sample at the pixel centre so shared edges belong to exactly one face.
			p := point{float64(x) + 0.5, float64(y) + 0.5}
			for _, face := range faces {
				u, v, ok := face.inverse(p)
				if !ok {
					continue
				}
				icon.SetNRGBA(x, y, face.sample(u, v))
				break
			}
		}
	}
	return icon
}

// inverse solves p = origin + u*across + v*down for (u, v) with Cramer's rule.
func (face cubeFace) inverse(p point) (float64, float64, bool) {
	d := sub(p, face.origin)
	determinant := face.across.x*face.down.y - face.across.y*face.down.x
	if determinant == 0 {
		return 0, 0, false
	}
	u := (d.x*face.down.y - d.y*face.down.x) / determinant
	v := (face.across.x*d.y - face.across.y*d.x) / determinant
	return u, v, u >= 0 && u < 1 && v >= 0 && v < 1
}

func (face cubeFace) sample(u, v float64) color.NRGBA {
	bounds := face.texture.Bounds()
	x := bounds.Min.X + int(u*float64(bounds.Dx()))
	y := bounds.Min.Y + int(v*float64(bounds.Dy()))
	pixel := color.NRGBAModel.Convert(face.texture.At(x, y)).(color.NRGBA)
	pixel.R = uint8(float64(pixel.R) * face.shade)
	pixel.G = uint8(float64(pixel.G) * face.shade)
	pixel.B = uint8(float64(pixel.B) * face.shade)
	return pixel
}

func sub(a, b point) point {
	return point{a.x - b.x, a.y - b.y}
}
