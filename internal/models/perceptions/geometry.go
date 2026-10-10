package perceptions

import "math"

// vec is a world-space vector in blocks. Minecraft axes: +X east, +Y up,
// +Z south.
type vec struct{ x, y, z float64 }

func (v vec) add(o vec) vec          { return vec{v.x + o.x, v.y + o.y, v.z + o.z} }
func (v vec) sub(o vec) vec          { return vec{v.x - o.x, v.y - o.y, v.z - o.z} }
func (v vec) scale(f float64) vec    { return vec{v.x * f, v.y * f, v.z * f} }
func (v vec) dot(o vec) float64      { return v.x*o.x + v.y*o.y + v.z*o.z }
func (v vec) lengthSquared() float64 { return v.dot(v) }
func (v vec) length() float64        { return math.Sqrt(v.lengthSquared()) }
func (v vec) cross(o vec) vec        { return vec{v.y*o.z - v.z*o.y, v.z*o.x - v.x*o.z, v.x*o.y - v.y*o.x} }

func (v vec) normalize() vec {
	length := v.length()
	if length < 1e-12 {
		return vec{}
	}
	return v.scale(1 / length)
}

// basis is a first-person camera frame. Screen +X is right, screen +Y is up,
// and the camera looks along forward (OpenGL camera-space -Z).
type basis struct{ right, up, forward vec }

// NOTICE: forward, cameraBasis, perspective, and projectNDC are a Go port of
// Airicraft's dataset camera so both projects place targets on screen the same
// way:
// `https://github.com/shinohara-rin/airicraft/blob/cfc7e0265d166260c779d6ee5e0c83cb5540cd04/src/main/java/ai/moeru/airicraft/dataset/ViewGeometry.java#L16-L40`
// and
// `https://github.com/shinohara-rin/airicraft/blob/cfc7e0265d166260c779d6ee5e0c83cb5540cd04/src/main/java/ai/moeru/airicraft/dataset/ViewGeometry.java#L70-L90`
// and
// `https://github.com/shinohara-rin/airicraft/blob/cfc7e0265d166260c779d6ee5e0c83cb5540cd04/src/main/java/ai/moeru/airicraft/dataset/ViewGeometry.java#L168-L173`.
// The source branch is devin/1790449171-vision-dataset. The pixel and
// letterbox stages are dropped: frustum membership only needs normalized
// device coordinates.

// forward converts Minecraft yaw/pitch degrees to a unit look vector. Yaw 0
// faces south (+Z) and grows clockwise seen from above (90 faces west, -X).
// Pitch -90 faces straight up, so the Y component is -sin(pitch).
func forward(yawDegrees, pitchDegrees float64) vec {
	yaw := yawDegrees * math.Pi / 180
	pitch := pitchDegrees * math.Pi / 180
	cosPitch := math.Cos(pitch)
	return vec{-math.Sin(yaw) * cosPitch, -math.Sin(pitch), math.Cos(yaw) * cosPitch}
}

func cameraBasis(yawDegrees, pitchDegrees float64) basis {
	look := forward(yawDegrees, pitchDegrees)
	right := look.cross(vec{0, 1, 0})
	if right.lengthSquared() < 1e-10 {
		// Looking straight up or down leaves forward parallel to world up, so
		// the cross product vanishes. Keep the horizontal axis implied by yaw.
		yaw := yawDegrees * math.Pi / 180
		right = vec{-math.Cos(yaw), 0, -math.Sin(yaw)}
	}
	right = right.normalize()
	up := right.cross(look).normalize()
	return basis{right: right, up: up, forward: look}
}

// projection holds the perspective matrix terms that scale camera-space x
// and y. Off-center terms are always zero for a first-person camera.
type projection struct{ m00, m11 float64 }

// perspective mirrors JOML Matrix4f.perspective, which vanilla
// GameRenderer.getProjectionMatrix calls with the FOV option as the vertical
// angle and the window width/height as aspect.
func perspective(verticalFOVDegrees, aspect float64) projection {
	focal := 1 / math.Tan(verticalFOVDegrees*math.Pi/180/2)
	return projection{m00: focal / aspect, m11: focal}
}

// horizontalFOV derives the horizontal angle that a vertical FOV covers at an
// aspect ratio: tan(h/2) = aspect * tan(v/2).
func horizontalFOV(verticalFOVDegrees, aspect float64) float64 {
	half := math.Atan(aspect * math.Tan(verticalFOVDegrees*math.Pi/180/2))
	return 2 * half * 180 / math.Pi
}

// camera is one observer view: eye position, frame, and projection.
type camera struct {
	eye        vec
	frame      basis
	projection projection
	near       float64
}

// projectNDC maps a world point to normalized device coordinates. ok is false
// when the point is at or behind the near plane, where the perspective divide
// is meaningless.
func (c camera) projectNDC(point vec) (x, y, depth float64, ok bool) {
	relative := point.sub(c.eye)
	depth = relative.dot(c.frame.forward)
	if depth <= c.near {
		return 0, 0, depth, false
	}
	// At view depth d the frustum half-width is d/m00 and half-height d/m11,
	// so NDC is the camera-space offset divided by that half-extent.
	x = c.projection.m00 * relative.dot(c.frame.right) / depth
	y = c.projection.m11 * relative.dot(c.frame.up) / depth
	return x, y, depth, true
}

// inView reports whether a world point lies inside the view frustum. There is
// no far plane: distance is limited separately.
func (c camera) inView(point vec) bool {
	x, y, _, ok := c.projectNDC(point)
	return ok && math.Abs(x) <= 1 && math.Abs(y) <= 1
}

// aabb is an axis-aligned box in world coordinates.
type aabb struct{ min, max vec }

func (box aabb) center() vec { return box.min.add(box.max).scale(0.5) }

// distanceTo is the Euclidean distance from point to the nearest point of the
// box; zero when the point is inside.
func (box aabb) distanceTo(point vec) float64 {
	dx := math.Max(math.Max(box.min.x-point.x, 0), point.x-box.max.x)
	dy := math.Max(math.Max(box.min.y-point.y, 0), point.y-box.max.y)
	dz := math.Max(math.Max(box.min.z-point.z, 0), point.z-box.max.z)
	return math.Sqrt(dx*dx + dy*dy + dz*dz)
}

// samplePoints returns the box center followed by its eight corners pulled
// toward the center by factor (1 keeps the corners, 0 collapses them). Inset
// corners keep rays from grazing a neighbouring cell exactly on a face.
func (box aabb) samplePoints(factor float64) []vec {
	center := box.center()
	half := box.max.sub(box.min).scale(0.5 * factor)
	points := make([]vec, 0, 9)
	points = append(points, center)
	for _, sx := range []float64{-1, 1} {
		for _, sy := range []float64{-1, 1} {
			for _, sz := range []float64{-1, 1} {
				points = append(points, center.add(vec{sx * half.x, sy * half.y, sz * half.z}))
			}
		}
	}
	return points
}

// entryDistance returns the ray parameter where a ray from origin along the
// unit direction first enters the box (slab method). It returns 0 when the
// origin is inside, and ok is false when the ray misses.
func (box aabb) entryDistance(origin, direction vec) (float64, bool) {
	enter, exit := 0.0, math.Inf(1)
	axes := [3][4]float64{
		{origin.x, direction.x, box.min.x, box.max.x},
		{origin.y, direction.y, box.min.y, box.max.y},
		{origin.z, direction.z, box.min.z, box.max.z},
	}
	for _, axis := range axes {
		o, d, lo, hi := axis[0], axis[1], axis[2], axis[3]
		if math.Abs(d) < 1e-12 {
			if o < lo || o > hi {
				return 0, false
			}
			continue
		}
		t0, t1 := (lo-o)/d, (hi-o)/d
		if t0 > t1 {
			t0, t1 = t1, t0
		}
		enter, exit = math.Max(enter, t0), math.Min(exit, t1)
		if enter > exit {
			return 0, false
		}
	}
	return enter, true
}
