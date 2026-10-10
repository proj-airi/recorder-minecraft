package perceptions

import (
	"math"
	"sort"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
)

// Target sampling factors. Entity rays end at the box center and at the eight
// corners pulled 20% toward the center, so a ray does not graze the floor cell
// under the feet. Block entity corners sit at 1/8 and 7/8 of the cell, inside
// a chest's 1/16..15/16 footprint and at its 14/16 lid height.
const (
	entityCornerFactor      = 0.8
	blockEntityCornerFactor = 0.75
)

// poseEyeHeights are vanilla player eye heights in blocks above the feet, read
// from Player.POSES and EntityType.PLAYER dimensions in Minecraft 1.21.8.
// Poses that Player.POSES does not list use the standing dimensions.
var poseEyeHeights = map[string]float64{
	"standing":    1.62,
	"crouching":   1.27,
	"swimming":    0.4,
	"fall_flying": 0.4,
	"spin_attack": 0.4,
	"sleeping":    0.2,
	// Dying shrinks the box to 0.2 but vanilla keeps the standing eye height.
	"dying": 1.62,
}

const defaultEyeHeight = 1.62

func eyeHeight(pose string) float64 {
	if height, ok := poseEyeHeights[pose]; ok {
		return height
	}
	return defaultEyeHeight
}

// verdict is the visibility decision for one target.
type verdict uint8

const (
	verdictHidden verdict = iota
	verdictVisible
	verdictUndetermined
)

// observe casts sight rays from the camera to every in-view sample point of a
// target box and decides visibility.
//
// One clear ray is enough to see a target: a person who sees part of an actor
// sees the actor. Without a clear ray, a ray stopped by an unknown cell makes
// the answer undetermined rather than hidden, because the unknown cell might
// be air. In-view and distance tests happen before any ray is cast.
func observe(world *voxelWorld, cam camera, dimension int32, box aabb, points []vec) (verdict, *artifactsv1.RaySupport) {
	support := &artifactsv1.RaySupport{Points: uint32(len(points))}
	for _, point := range points {
		if !cam.inView(point) {
			continue
		}
		support.InView++
		direction := point.sub(cam.eye).normalize()
		// Stop marching where the ray enters the target box: cells beyond it
		// are behind the visible surface. A ray toward an interior point always
		// enters the box; the fallback only guards degenerate boxes.
		end, ok := box.entryDistance(cam.eye, direction)
		if !ok {
			end = point.sub(cam.eye).length()
		}
		switch world.march(dimension, cam.eye, direction, end) {
		case rayClear:
			support.Clear++
		case rayBlocked:
			support.Blocked++
		case rayUnknown:
			support.Unknown++
		}
	}
	switch {
	case support.Clear > 0:
		return verdictVisible, support
	case support.Unknown > 0:
		return verdictUndetermined, support
	default:
		return verdictHidden, support
	}
}

// entityTarget is one entity version that can be seen.
type entityTarget struct {
	instanceID string
	networkID  *int64
	dimension  string
	typeID     string
	start, end int64
	box        aabb
	blobSHA256 string
}

// blockEntityTarget is one block entity version that can be seen.
type blockEntityTarget struct {
	dimension  string
	x, y, z    int64
	typeID     string
	start, end int64
}

func (target blockEntityTarget) box() aabb {
	low := vec{float64(target.x), float64(target.y), float64(target.z)}
	return aabb{min: low, max: low.add(vec{1, 1, 1})}
}

// activeSet tracks versions whose [start, end) range covers a moving tick.
// Ticks only move forward, so versions enter once from a start-ordered list
// and leave once their end passes.
type activeSet[T any] struct {
	pending []T
	active  []T
	start   func(T) int64
	end     func(T) int64
}

func newActiveSet[T any](values []T, start, end func(T) int64) *activeSet[T] {
	sort.SliceStable(values, func(i, j int) bool { return start(values[i]) < start(values[j]) })
	return &activeSet[T]{pending: values, start: start, end: end}
}

func (set *activeSet[T]) at(tick int64) []T {
	for len(set.pending) > 0 && set.start(set.pending[0]) <= tick {
		set.active = append(set.active, set.pending[0])
		set.pending = set.pending[1:]
	}
	kept := set.active[:0]
	for _, value := range set.active {
		if set.end(value) > tick {
			kept = append(kept, value)
		}
	}
	set.active = kept
	return kept
}

func roundDistance(value float64) float64 {
	return math.Round(value*1000) / 1000
}
