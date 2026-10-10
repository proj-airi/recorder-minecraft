package perceptions

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestOccluderRule(t *testing.T) {
	for _, tc := range []struct {
		name       string
		properties map[string]string
		want       bool
	}{
		{"minecraft:stone", nil, true},
		{"minecraft:grass_block", map[string]string{"snowy": "false"}, true},
		{"minecraft:furnace", map[string]string{"facing": "north", "lit": "false"}, true},
		{"minecraft:air", nil, false},
		{"minecraft:cave_air", nil, false},
		{"minecraft:glass", nil, false},
		{"minecraft:white_stained_glass_pane", nil, false},
		{"minecraft:oak_leaves", map[string]string{"persistent": "true"}, false},
		{"minecraft:water", map[string]string{"level": "0"}, false},
		{"minecraft:short_grass", nil, false},
		{"minecraft:torch", nil, false},
		{"minecraft:chest", map[string]string{"type": "single"}, false},
		{"minecraft:lava", map[string]string{"level": "0"}, true},
		{"minecraft:powder_snow", nil, true},
		{"minecraft:oak_slab", map[string]string{"type": "double", "waterlogged": "false"}, true},
		{"minecraft:oak_slab", map[string]string{"type": "bottom", "waterlogged": "false"}, false},
		{"minecraft:snow", map[string]string{"layers": "8"}, true},
		{"minecraft:snow", map[string]string{"layers": "3"}, false},
		{"minecraft:piston", map[string]string{"extended": "false", "facing": "up"}, true},
		{"minecraft:piston", map[string]string{"extended": "true", "facing": "up"}, false},
		{"examplemod:mystery_block", nil, true},
	} {
		assert.Equal(t, tc.want, occludes(tc.name, tc.properties), "%s %v", tc.name, tc.properties)
	}
	assert.Equal(t, "1.21.8", occluderTableVersion)
}

func TestEyeHeightFollowsPose(t *testing.T) {
	assert.Equal(t, 1.62, eyeHeight("standing"))
	assert.Equal(t, 1.27, eyeHeight("crouching"))
	assert.Equal(t, 0.4, eyeHeight("swimming"))
	assert.Equal(t, 0.4, eyeHeight("fall_flying"))
	assert.Equal(t, 0.2, eyeHeight("sleeping"))
	assert.Equal(t, 1.62, eyeHeight("sitting"), "poses without player dimensions use standing")
}

// The scene below is S3 of the live check in miniature: the observer looks
// south along +Z, a stone wall spans x -2..2 at z 6, and targets stand behind
// it, beside it, and behind the observer.
func lookingSouth() camera {
	return camera{eye: vec{0.5, 1.62, 0.5}, frame: cameraBasis(0, 0), projection: perspective(70, 16.0/9.0), near: nearPlaneBlocks}
}

func standingPlayer(x, z float64) aabb {
	return aabb{min: vec{x - 0.3, 0, z - 0.3}, max: vec{x + 0.3, 1.8, z + 0.3}}
}

func TestObserveSeesAnUnobstructedEntity(t *testing.T) {
	world := syntheticWorld(1, nil)
	box := standingPlayer(0.5, 8)
	decision, support := observe(world, lookingSouth(), 0, box, box.samplePoints(entityCornerFactor))
	assert.Equal(t, verdictVisible, decision)
	assert.EqualValues(t, 9, support.GetPoints())
	assert.EqualValues(t, 9, support.GetInView())
	assert.EqualValues(t, 9, support.GetClear())
}

func TestObserveCullsTargetsOutsideTheFrustum(t *testing.T) {
	world := syntheticWorld(1, nil)
	box := standingPlayer(0.5, -6)
	decision, support := observe(world, lookingSouth(), 0, box, box.samplePoints(entityCornerFactor))
	assert.Equal(t, verdictHidden, decision, "behind the observer")
	assert.Zero(t, support.GetInView())

	turned := lookingSouth()
	turned.frame = cameraBasis(180, 0)
	box = standingPlayer(0.5, 8)
	decision, _ = observe(world, turned, 0, box, box.samplePoints(entityCornerFactor))
	assert.Equal(t, verdictHidden, decision, "observer turned away")
}

func TestObserveHidesTargetsBehindAWall(t *testing.T) {
	world := syntheticWorld(1, wall(-2, 2, 0, 2, 6))
	actor := standingPlayer(0.5, 8)
	decision, support := observe(world, lookingSouth(), 0, actor, actor.samplePoints(entityCornerFactor))
	assert.Equal(t, verdictHidden, decision)
	assert.EqualValues(t, 9, support.GetBlocked())

	chest := blockEntityTarget{x: 0, y: 0, z: 9}.box()
	decision, _ = observe(world, lookingSouth(), 0, chest, chest.samplePoints(blockEntityCornerFactor))
	assert.Equal(t, verdictHidden, decision, "chest behind the wall")

	control := standingPlayer(6.5, 8)
	decision, support = observe(world, lookingSouth(), 0, control, control.samplePoints(entityCornerFactor))
	assert.Equal(t, verdictVisible, decision, "target beside the wall stays visible")
	assert.Positive(t, support.GetClear())
}

func TestObservePartialOcclusionStillSees(t *testing.T) {
	// A one-block-high wall hides the legs but not the head.
	world := syntheticWorld(1, wall(-2, 2, 0, 0, 6))
	actor := standingPlayer(0.5, 8)
	decision, support := observe(world, lookingSouth(), 0, actor, actor.samplePoints(entityCornerFactor))
	assert.Equal(t, verdictVisible, decision)
	assert.Positive(t, support.GetBlocked())
	assert.Positive(t, support.GetClear())
}

func TestObserveReportsUnknownInsteadOfHidden(t *testing.T) {
	// Section z=0 ends at z=15; the target stands in an unknown section.
	world := syntheticWorld(1, nil, sectionCoord{0, 0, 0, 1})
	actor := standingPlayer(0.5, 20)
	decision, support := observe(world, lookingSouth(), 0, actor, actor.samplePoints(entityCornerFactor))
	assert.Equal(t, verdictUndetermined, decision)
	assert.EqualValues(t, 9, support.GetUnknown())

	blocked := syntheticWorld(1, wall(-3, 3, 0, 3, 6), sectionCoord{0, 0, 0, 1})
	decision, _ = observe(blocked, lookingSouth(), 0, actor, actor.samplePoints(entityCornerFactor))
	assert.Equal(t, verdictHidden, decision, "a known wall decides before the unknown section")
}

func TestActiveSetTracksVersionRanges(t *testing.T) {
	type version struct{ start, end int64 }
	set := newActiveSet([]version{{5, 10}, {0, 3}, {2, 8}},
		func(v version) int64 { return v.start }, func(v version) int64 { return v.end })
	assert.ElementsMatch(t, []version{{0, 3}}, set.at(0))
	assert.ElementsMatch(t, []version{{0, 3}, {2, 8}}, set.at(2))
	assert.ElementsMatch(t, []version{{2, 8}, {5, 10}}, set.at(6))
	assert.ElementsMatch(t, []version{{5, 10}}, set.at(9))
	assert.Empty(t, set.at(10))
}

func TestViewDistanceUsesTheSmallerSetting(t *testing.T) {
	timeline := viewDistanceTimeline{}
	timeline.client.record(10, 12)
	timeline.client.record(20, 4)
	timeline.server.record(5, 10)
	timeline.server.record(6, 10)
	_, ok := timeline.at(4)
	assert.False(t, ok)
	value, _ := timeline.at(5)
	assert.EqualValues(t, 10, value, "server only")
	value, _ = timeline.at(15)
	assert.EqualValues(t, 10, value, "client 12 is clamped by the server")
	value, _ = timeline.at(25)
	assert.EqualValues(t, 4, value, "a later client setting applies from its tick")
	assert.Len(t, timeline.server, 1, "unchanged values are not repeated")
}
