package perceptions

import (
	"errors"
	"fmt"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// syntheticWorld knows every section within radius sections of the origin in
// dimension 0 for ticks 0..99. Listed cells are occluders, and listed
// sections are left out so they read as unknown.
func syntheticWorld(radius int32, solid [][3]int, missing ...sectionCoord) *voxelWorld {
	sections := map[sectionCoord]*occupancy{}
	for x := -radius; x <= radius; x++ {
		for y := -radius; y <= radius; y++ {
			for z := -radius; z <= radius; z++ {
				sections[sectionCoord{0, x, y, z}] = &occupancy{}
			}
		}
	}
	for _, cell := range solid {
		coord := sectionCoord{0, int32(cell[0] >> 4), int32(cell[1] >> 4), int32(cell[2] >> 4)}
		sections[coord].set((cell[1]&15)*256 + (cell[2]&15)*16 + (cell[0] & 15))
	}
	for _, coord := range missing {
		delete(sections, coord)
	}
	blobs := map[string]*occupancy{}
	world := newVoxelWorld(func(blob string) (*occupancy, error) {
		if value, ok := blobs[blob]; ok {
			return value, nil
		}
		return nil, errors.New("missing blob " + blob)
	})
	for coord, value := range sections {
		blob := fmt.Sprint(coord)
		blobs[blob] = value
		world.addSpan(coord, sectionSpan{start: 0, end: 100, blob: blob})
	}
	world.seal()
	return world
}

func wall(x0, x1, y0, y1, z int) [][3]int {
	var cells [][3]int
	for x := x0; x <= x1; x++ {
		for y := y0; y <= y1; y++ {
			cells = append(cells, [3]int{x, y, z})
		}
	}
	return cells
}

func TestMarchStopsAtOccluder(t *testing.T) {
	world := syntheticWorld(1, wall(-2, 2, 0, 2, 3))
	origin := vec{0.5, 1.5, 0.5}
	assert.Equal(t, rayBlocked, world.march(0, origin, vec{0, 0, 1}, 6), "wall at z=3 is in the way")
	assert.Equal(t, rayClear, world.march(0, origin, vec{0, 0, 1}, 2.5), "target before the wall")
	assert.Equal(t, rayClear, world.march(0, origin, vec{0, 0, -1}, 6), "the other direction is open")
	assert.Equal(t, rayClear, world.march(0, origin, vec{0, 0, 1}, 2.5000000001), "the wall cell itself is entered at the end")
}

func TestMarchHandlesNegativeCoordinatesAndDiagonals(t *testing.T) {
	// The occluder sits in section (-1, 0, -1); a diagonal ray toward
	// negative x and z must find it at the right local offset.
	world := syntheticWorld(1, [][3]int{{-3, 1, -3}})
	origin := vec{0.5, 1.5, 0.5}
	direction := vec{-1, 0, -1}.normalize()
	assert.Equal(t, rayBlocked, world.march(0, origin, direction, 10))
	assert.Equal(t, rayClear, world.march(0, origin, vec{-1, 0, -0.5}.normalize(), 10), "a ray that passes beside the block")
}

func TestMarchSkipsTheEyeCell(t *testing.T) {
	world := syntheticWorld(1, [][3]int{{0, 1, 0}})
	assert.Equal(t, rayClear, world.march(0, vec{0.5, 1.5, 0.5}, vec{0, 0, 1}, 5), "an eye inside an occluder still looks out")
}

func TestMarchReportsUnknownSections(t *testing.T) {
	world := syntheticWorld(1, nil, sectionCoord{0, 0, 0, 1})
	origin := vec{0.5, 1.5, 0.5}
	assert.Equal(t, rayUnknown, world.march(0, origin, vec{0, 0, 1}, 20), "unknown cells are not air")
	assert.Equal(t, rayClear, world.march(0, origin, vec{0, 0, 1}, 10), "the target lies before the unknown section")

	blocked := syntheticWorld(1, [][3]int{{0, 1, 12}}, sectionCoord{0, 0, 0, 1})
	assert.Equal(t, rayBlocked, blocked.march(0, origin, vec{0, 0, 1}, 30), "an occluder before the unknown section decides")
	assert.Equal(t, rayUnknown, world.march(0, origin, vec{0, 1, 0}, 40), "cells above the known sections are unknown")
}

func TestSectionVersionsFollowTheSampleTick(t *testing.T) {
	open, closed := &occupancy{}, &occupancy{}
	for index := range 4096 {
		closed.set(index)
	}
	world := newVoxelWorld(func(blob string) (*occupancy, error) {
		if blob == "closed" {
			return closed, nil
		}
		return open, nil
	})
	coord := sectionCoord{0, 0, 0, 1}
	world.addSpan(coord, sectionSpan{start: 20, end: 30, blob: "closed"})
	world.addSpan(coord, sectionSpan{start: 0, end: 10, blob: "open"})
	world.addSpan(sectionCoord{0, 0, 0, 0}, sectionSpan{start: 0, end: 100, blob: "open"})
	world.seal()
	origin, direction := vec{0.5, 1.5, 0.5}, vec{0, 0, 1}

	for _, tc := range []struct {
		tick int64
		want rayOutcome
	}{{0, rayClear}, {9, rayClear}, {10, rayUnknown}, {19, rayUnknown}, {20, rayBlocked}, {29, rayBlocked}, {30, rayUnknown}} {
		world.at(tc.tick)
		assert.Equal(t, tc.want, world.march(0, origin, direction, 20), "tick %d", tc.tick)
	}
	require.NoError(t, world.err)
	assert.Len(t, world.decoded, 2, "decoded sections are cached by blob")
}

func TestDecodeFailureSurfacesAsError(t *testing.T) {
	world := newVoxelWorld(func(string) (*occupancy, error) { return nil, errors.New("corrupt") })
	world.addSpan(sectionCoord{0, 0, 0, 0}, sectionSpan{start: 0, end: 10, blob: "bad"})
	world.seal()
	assert.Equal(t, rayUnknown, world.march(0, vec{0.5, 1.5, 0.5}, vec{0, 0, 1}, 5))
	assert.EqualError(t, world.err, "corrupt")
}
