package perceptions

import (
	"fmt"
	"sort"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
)

// assumptions declares every modeling choice in the header, so a consumer
// can judge or reproduce a sample without reading this code.
func assumptions(options Options) *artifactsv1.PerceptionAssumptions {
	poses := make([]string, 0, len(poseEyeHeights))
	for pose := range poseEyeHeights {
		poses = append(poses, pose)
	}
	sort.Strings(poses)
	eyeHeights := make([]*artifactsv1.PoseEyeHeight, 0, len(poses))
	for _, pose := range poses {
		eyeHeights = append(eyeHeights, &artifactsv1.PoseEyeHeight{Pose: pose, EyeHeight: poseEyeHeights[pose]})
	}
	return &artifactsv1.PerceptionAssumptions{
		Camera: &artifactsv1.PerceptionCamera{
			VerticalFovDegrees:   options.VerticalFOVDegrees,
			AspectRatio:          options.AspectRatio,
			HorizontalFovDegrees: roundDistance(horizontalFOV(options.VerticalFOVDegrees, options.AspectRatio)),
			NearPlaneBlocks:      nearPlaneBlocks,
		},
		EyeHeights:       eyeHeights,
		DefaultEyeHeight: defaultEyeHeight,
		OccluderModel: &artifactsv1.OccluderModel{
			Name:                occluderModelName,
			Description:         occluderDescription(),
			OccluderOverrides:   occluderOverrideNames(),
			ConditionalRules:    conditionalRuleSummary(),
			UnlistedBlockPolicy: fmt.Sprintf("occluder: a block absent from the %s registry table stops rays", occluderTableVersion),
		},
		SamplingIntervalTicks:           uint32(options.IntervalTicks),
		MaxDistanceBlocks:               options.MaxDistanceBlocks,
		MaxDistanceCappedByViewDistance: true,
		UnknownCellPolicy:               artifactsv1.UnknownCellPolicy_UNKNOWN_CELL_POLICY_RAY_UNDETERMINED,
		ObserverSource:                  "scene.sqlite3 player_states: authoritative post-tick feet position, yaw, pitch, and pose of the recorded player",
		TargetSampling: &artifactsv1.TargetSampling{
			EntityPoints: fmt.Sprintf("bounding box center plus its 8 corners scaled %.2f toward the center; rays end where they enter the box", entityCornerFactor),
			BlockEntityPoints: fmt.Sprintf("block cell center plus its 8 corners scaled %.2f toward the center; rays end where they enter the cell",
				blockEntityCornerFactor),
			VisibilityRule: "only points inside the view frustum are cast; visible when at least one ray is clear; undetermined when no ray is clear " +
				"and at least one stopped at an unknown cell; otherwise not visible and omitted. The ray starts after the eye's own cell.",
		},
	}
}

// knownLimitations lists what the reconstruction does not model. Each entry
// is a stable token followed by an explanation.
func knownLimitations() []string {
	return []string{
		"fov_and_aspect_assumed: the player's FOV option and window aspect never reach the server; the configured values are assumed",
		"dynamic_fov_ignored: sprint, speed, flying, bow, and spyglass FOV changes are not applied",
		"rotation_sampled_per_tick: yaw and pitch come from the post-tick player state; head motion between ticks and client interpolation are lost",
		"eye_height_from_pose: vanilla player eye heights by pose; the scale attribute, crouch transition easing, and riding offsets are ignored",
		"full_cube_occluders_only: partial blocks (slabs, stairs, fences, panes, doors) never block sight, and cutout texels are not tested",
		"entities_do_not_occlude: other entities and block entities never block sight rays",
		"no_lighting_or_effects: darkness, light level, fog, blindness, darkness effect, and invisibility are not modeled",
		"single_distance_limit: vanilla scales entity render distance by entity size and the entity distance option; one limit applies to every target",
		"first_person_only: third-person views and spectator camera targets are not modeled",
		"client_visible_scene: targets and blocks come from the client-visible Scene Store; cells outside it, including above or below the build limit, are unknown",
	}
}
