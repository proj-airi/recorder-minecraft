package perceptions

import (
	"math"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const epsilon = 1e-6

func assertVec(t *testing.T, expected, actual vec) {
	t.Helper()
	assert.InDelta(t, expected.x, actual.x, epsilon, "x")
	assert.InDelta(t, expected.y, actual.y, epsilon, "y")
	assert.InDelta(t, expected.z, actual.z, epsilon, "z")
}

// The vectors below are the ones Airicraft's ViewGeometryTest pins, so both
// implementations agree on Minecraft's yaw/pitch convention.
func TestForwardMatchesMinecraftYawPitch(t *testing.T) {
	assertVec(t, vec{0, 0, 1}, forward(0, 0))    // south = +Z
	assertVec(t, vec{-1, 0, 0}, forward(90, 0))  // west = -X
	assertVec(t, vec{1, 0, 0}, forward(-90, 0))  // east = +X
	assertVec(t, vec{0, 0, -1}, forward(180, 0)) // north = -Z
	assertVec(t, vec{0, 1, 0}, forward(0, -90))  // up
	assertVec(t, vec{0, -1, 0}, forward(0, 90))  // down
}

func TestCameraBasisIsOrthonormal(t *testing.T) {
	for _, yaw := range []float64{0, 45, -90, 135, 179, 720} {
		for _, pitch := range []float64{-80, -45, 0, 30, 89} {
			frame := cameraBasis(yaw, pitch)
			assert.InDelta(t, 1, frame.right.length(), epsilon)
			assert.InDelta(t, 1, frame.up.length(), epsilon)
			assert.InDelta(t, 1, frame.forward.length(), epsilon)
			assert.InDelta(t, 0, frame.right.dot(frame.forward), epsilon)
			assert.InDelta(t, 0, frame.up.dot(frame.forward), epsilon)
			assert.InDelta(t, 0, frame.right.dot(frame.up), epsilon)
		}
	}
}

func TestSouthFacingRightPointsWest(t *testing.T) {
	frame := cameraBasis(0, 0)
	assertVec(t, vec{-1, 0, 0}, frame.right)
	assertVec(t, vec{0, 1, 0}, frame.up)
	assertVec(t, vec{0, 0, 1}, frame.forward)
}

func TestDegeneratePitchKeepsValidBasis(t *testing.T) {
	frame := cameraBasis(0, -90)
	assertVec(t, vec{0, 1, 0}, frame.forward)
	assert.InDelta(t, 1, frame.right.length(), epsilon)
	assert.InDelta(t, 0, frame.right.dot(frame.forward), epsilon)
}

func TestProjectNDCPlacesPointsLikeAiricraft(t *testing.T) {
	cam := camera{eye: vec{0, 80, 0}, frame: cameraBasis(0, 0), projection: perspective(70, 854.0/480.0), near: nearPlaneBlocks}

	x, y, depth, ok := cam.projectNDC(vec{0, 80, 10})
	require.True(t, ok)
	assert.InDelta(t, 0, x, epsilon)
	assert.InDelta(t, 0, y, epsilon)
	assert.InDelta(t, 10, depth, epsilon)

	// -X is screen-right when facing south.
	right, sameY, _, ok := cam.projectNDC(vec{-2, 80, 10})
	require.True(t, ok)
	assert.Greater(t, right, 0.0)
	assert.InDelta(t, 0, sameY, epsilon)

	_, above, _, ok := cam.projectNDC(vec{0, 85, 10})
	require.True(t, ok)
	assert.Greater(t, above, 0.0)

	_, _, _, ok = cam.projectNDC(vec{0, 80, -5})
	assert.False(t, ok, "behind the camera projects to nothing")
}

func TestVanillaFOVIsVertical(t *testing.T) {
	// FOV 70 at 16:9 covers about 102.45 degrees horizontally.
	assert.InDelta(t, 102.448, horizontalFOV(70, 16.0/9.0), 1e-3)
	cam := camera{eye: vec{}, frame: cameraBasis(0, 0), projection: perspective(70, 16.0/9.0), near: nearPlaneBlocks}
	inside, outside := 34.5*math.Pi/180, 35.5*math.Pi/180
	assert.True(t, cam.inView(vec{0, math.Tan(inside), 1}), "just inside the top edge")
	assert.False(t, cam.inView(vec{0, math.Tan(outside), 1}), "just outside the top edge")
	assert.True(t, cam.inView(vec{-math.Tan(50.5 * math.Pi / 180), 0, 1}), "50.5 degrees right is inside the 102 degree width")
	assert.False(t, cam.inView(vec{-math.Tan(52 * math.Pi / 180), 0, 1}), "52 degrees right is outside")
	assert.False(t, cam.inView(vec{0, 0, 0.01}), "points before the near plane are clipped")
}

func TestBoxHelpers(t *testing.T) {
	box := aabb{min: vec{0, 0, 0}, max: vec{1, 2, 1}}
	assert.InDelta(t, 0, box.distanceTo(vec{0.5, 1, 0.5}), epsilon)
	assert.InDelta(t, 3, box.distanceTo(vec{4, 1, 0.5}), epsilon)
	assert.InDelta(t, 5, box.distanceTo(vec{4, 6, 0.5}), epsilon)

	points := box.samplePoints(0.5)
	require.Len(t, points, 9)
	assertVec(t, vec{0.5, 1, 0.5}, points[0])
	assertVec(t, vec{0.25, 0.5, 0.25}, points[1])
	assertVec(t, vec{0.75, 1.5, 0.75}, points[8])

	entry, ok := box.entryDistance(vec{-3, 1, 0.5}, vec{1, 0, 0})
	require.True(t, ok)
	assert.InDelta(t, 3, entry, epsilon)
	_, ok = box.entryDistance(vec{-3, 5, 0.5}, vec{1, 0, 0})
	assert.False(t, ok)
	entry, ok = box.entryDistance(vec{0.5, 1, 0.5}, vec{0, 1, 0})
	require.True(t, ok)
	assert.Zero(t, entry, "an origin inside the box enters at once")
}
