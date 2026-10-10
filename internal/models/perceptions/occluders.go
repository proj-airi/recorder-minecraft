package perceptions

import (
	"fmt"
	"sort"
	"strings"
)

// occluderModelName names the sight-blocking rule recorded in the header.
const occluderModelName = "vanilla_solid_render_full_cube_v1"

// blockStateCondition is one property value that makes a block an opaque full
// cube, such as a double slab.
type blockStateCondition struct{ property, value string }

// occluderOverrides are blocks vanilla does not use for face culling but that
// a viewer still cannot see through. Vanilla skips them for culling because
// their shapes are not full cubes or they are rendered as fluids, not because
// they are transparent.
var occluderOverrides = map[string]string{
	// Lava renders as an opaque fluid surface; only water is see-through.
	"minecraft:lava": "opaque fluid",
	// Powder snow renders a full opaque texture; vanilla only disables culling
	// so a player standing inside it still sees the faces.
	"minecraft:powder_snow": "opaque texture",
}

// occludes reports whether a block state stops a sight ray.
//
// The rule is vanilla BlockState.isSolidRender(), generated from the 1.21.8
// block registry: opaque full cubes stop rays, while glass, leaves, water,
// plants, slabs, chests, and other non-full or see-through blocks let them
// pass. Leaves and other cutout blocks therefore assume fancy graphics.
//
// A block absent from the table (a mod block or a newer Minecraft block)
// occludes, so an unknown shape never manufactures visibility.
func occludes(name string, properties map[string]string) bool {
	if _, ok := occluderOverrides[name]; ok {
		return true
	}
	if _, ok := solidRenderBlocks[name]; ok {
		return true
	}
	if _, ok := openBlocks[name]; ok {
		return false
	}
	if condition, ok := conditionalSolidRenderBlocks[name]; ok {
		return properties[condition.property] == condition.value
	}
	return true
}

func occluderDescription() string {
	return fmt.Sprintf("A cell stops a sight ray when its block state is an opaque full cube under vanilla %s BlockState.isSolidRender(), "+
		"plus the listed overrides. Glass, leaves (fancy graphics), water, plants, slabs, stairs, fences, chests, and other non-full or "+
		"see-through blocks let rays pass. Cutout texels and partial shapes are not modeled.", occluderTableVersion)
}

func occluderOverrideNames() []string {
	names := make([]string, 0, len(occluderOverrides))
	for name, reason := range occluderOverrides {
		names = append(names, name+": "+reason)
	}
	sort.Strings(names)
	return names
}

// conditionalRuleSummary groups conditional blocks by their rule so the header
// stays short: every slab, for example, shares "type=double".
func conditionalRuleSummary() []string {
	byRule := map[string][]string{}
	for name, condition := range conditionalSolidRenderBlocks {
		rule := condition.property + "=" + condition.value
		byRule[rule] = append(byRule[rule], name)
	}
	rules := make([]string, 0, len(byRule))
	for rule, names := range byRule {
		sort.Strings(names)
		rules = append(rules, rule+": "+strings.Join(names, ","))
	}
	sort.Strings(rules)
	return rules
}
