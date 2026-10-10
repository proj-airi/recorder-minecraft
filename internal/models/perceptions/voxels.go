package perceptions

import (
	"math"
	"sort"
)

// cellState is what a sight ray learns from one block cell.
type cellState uint8

const (
	cellOpen cellState = iota
	cellOccluder
	// cellUnknown means the scene has no block state for the cell at the
	// sample tick: its section was never sent or was unloaded. It is not air.
	cellUnknown
)

// rayOutcome is the first decisive cell a sight ray met.
type rayOutcome uint8

const (
	rayClear rayOutcome = iota
	rayBlocked
	rayUnknown
)

// occupancy marks the occluding cells of one 16x16x16 section. Bit i stands
// for the cell at local index y*256 + z*16 + x, the order SectionBlob uses.
type occupancy [64]uint64

func (o *occupancy) set(index int)           { o[index>>6] |= 1 << (index & 63) }
func (o *occupancy) occludes(index int) bool { return o[index>>6]&(1<<(index&63)) != 0 }

// sectionCoord addresses a section by interned dimension and section
// coordinates (block coordinate >> 4 on each axis).
type sectionCoord struct {
	dimension int32
	x, y, z   int32
}

// sectionSpan is one section version: its occupancy applies to
// start <= tick < end, matching Scene Store version ranges.
type sectionSpan struct {
	start, end int64
	blob       string
}

// voxelWorld answers "what is in this cell at this tick" for ray marching.
//
// Decoded occupancy is cached by blob digest. Scene blobs are content
// addressed, so one cache entry serves every (dimension, section, version)
// with identical blocks, for example all-air sky sections.
type voxelWorld struct {
	spans   map[sectionCoord][]sectionSpan
	decode  func(blob string) (*occupancy, error)
	decoded map[string]*occupancy

	// resolved caches section lookups for the current tick only. A nil value
	// records that the section is unknown at that tick.
	tick     int64
	resolved map[sectionCoord]*occupancy
	err      error
}

func newVoxelWorld(decode func(string) (*occupancy, error)) *voxelWorld {
	return &voxelWorld{spans: map[sectionCoord][]sectionSpan{}, decode: decode, decoded: map[string]*occupancy{}, resolved: map[sectionCoord]*occupancy{}}
}

func (w *voxelWorld) addSpan(coord sectionCoord, span sectionSpan) {
	w.spans[coord] = append(w.spans[coord], span)
}

// seal orders every section's versions by start tick so lookups can binary
// search. Call it once after all spans are added.
func (w *voxelWorld) seal() {
	for _, spans := range w.spans {
		sort.Slice(spans, func(i, j int) bool { return spans[i].start < spans[j].start })
	}
}

// at moves the per-tick cache to tick. Samples only move forward, but the
// cache is simply dropped on any change because versions may differ.
func (w *voxelWorld) at(tick int64) {
	if tick != w.tick {
		w.tick = tick
		clear(w.resolved)
	}
}

func (w *voxelWorld) section(coord sectionCoord) *occupancy {
	if cached, ok := w.resolved[coord]; ok {
		return cached
	}
	var found *occupancy
	spans := w.spans[coord]
	// First version that ends after the tick; it applies only if it has
	// already started. Versions of one section never overlap.
	index := sort.Search(len(spans), func(i int) bool { return spans[i].end > w.tick })
	if index < len(spans) && spans[index].start <= w.tick {
		blob := spans[index].blob
		found = w.decoded[blob]
		if found == nil {
			decoded, err := w.decode(blob)
			if err != nil {
				// Keep the first failure for the caller; the cell reads as
				// unknown so marching can stop without fabricating air.
				if w.err == nil {
					w.err = err
				}
				w.resolved[coord] = nil
				return nil
			}
			w.decoded[blob] = decoded
			found = decoded
		}
	}
	w.resolved[coord] = found
	return found
}

func (w *voxelWorld) cell(dimension int32, x, y, z int) cellState {
	// Arithmetic shifts floor negative coordinates, and &15 gives the
	// non-negative local offset that matches them.
	section := w.section(sectionCoord{dimension, int32(x >> 4), int32(y >> 4), int32(z >> 4)})
	if section == nil {
		return cellUnknown
	}
	if section.occludes((y&15)*256 + (z&15)*16 + (x & 15)) {
		return cellOccluder
	}
	return cellOpen
}

// march walks the cells a ray crosses from origin along the unit direction
// until distance end, using the Amanatides-Woo voxel traversal. The origin
// cell is skipped: the eye sits in it, and an eye placed exactly on a block
// face must not be occluded by the block it touches.
//
// The ray is clear when it reaches end without entering an occluding or
// unknown cell. Cells entered at or after end belong to the target and are
// not tested.
func (w *voxelWorld) march(dimension int32, origin, direction vec, end float64) rayOutcome {
	x, y, z := int(math.Floor(origin.x)), int(math.Floor(origin.y)), int(math.Floor(origin.z))
	stepX, nextX, deltaX := axisStep(origin.x, direction.x, x)
	stepY, nextY, deltaY := axisStep(origin.y, direction.y, y)
	stepZ, nextZ, deltaZ := axisStep(origin.z, direction.z, z)
	const epsilon = 1e-9
	for {
		var t float64
		// Cross whichever cell face the ray reaches first.
		switch {
		case nextX <= nextY && nextX <= nextZ:
			t, x, nextX = nextX, x+stepX, nextX+deltaX
		case nextY <= nextZ:
			t, y, nextY = nextY, y+stepY, nextY+deltaY
		default:
			t, z, nextZ = nextZ, z+stepZ, nextZ+deltaZ
		}
		if t >= end-epsilon {
			return rayClear
		}
		switch w.cell(dimension, x, y, z) {
		case cellOccluder:
			return rayBlocked
		case cellUnknown:
			return rayUnknown
		}
	}
}

// axisStep returns the cell step direction, the ray distance to the first
// face crossing on this axis, and the distance between successive crossings.
// An axis the ray does not move along never crosses a face.
func axisStep(origin, direction float64, cell int) (step int, next, delta float64) {
	switch {
	case direction > 0:
		return 1, (float64(cell+1) - origin) / direction, 1 / direction
	case direction < 0:
		return -1, (origin - float64(cell)) / -direction, -1 / direction
	default:
		return 0, math.Inf(1), math.Inf(1)
	}
}
