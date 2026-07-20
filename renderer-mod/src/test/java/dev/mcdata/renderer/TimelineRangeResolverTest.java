package dev.mcdata.renderer;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class TimelineRangeResolverTest {
    @Test
    void intersectsRequestedRangeWithExactMarkerCoverage() {
        TimelineRangeResolver.Resolution resolution = TimelineRangeResolver.resolve(
            RenderJobSpec.RangePolicy.INTERSECTION,
            90, 180, 100,
            new TimelineRangeResolver.Marker(100, 10),
            new TimelineRangeResolver.Marker(170, 80)
        );

        assertEquals(TimelineRangeResolver.Status.READY, resolution.status());
        assertEquals(90, resolution.globalTickOffset());
        assertEquals(10, resolution.replayStartTick());
        assertEquals(80, resolution.replayEndTick());
        assertEquals(100, resolution.globalStartTick());
        assertEquals(170, resolution.globalEndTick());
    }

    @Test
    void reportsNoCoverageWithoutInventingFrames() {
        TimelineRangeResolver.Resolution resolution = TimelineRangeResolver.resolve(
            RenderJobSpec.RangePolicy.INTERSECTION,
            200, 220, 100,
            new TimelineRangeResolver.Marker(100, 10),
            new TimelineRangeResolver.Marker(170, 80)
        );

        assertEquals(TimelineRangeResolver.Status.NO_COVERAGE, resolution.status());
        assertEquals(-1, resolution.replayStartTick());
        assertEquals(200, resolution.globalStartTick());
        assertEquals(170, resolution.globalEndTick());
    }

    @Test
    void strictPolicyRejectsPartialSegmentCoverage() {
        assertThrows(IllegalArgumentException.class, () -> TimelineRangeResolver.resolve(
            RenderJobSpec.RangePolicy.STRICT,
            90, 180, 100,
            new TimelineRangeResolver.Marker(100, 10),
            new TimelineRangeResolver.Marker(170, 80)
        ));
    }

    @Test
    void rejectsOffsetDriftBetweenFirstAndLastMarkers() {
        assertThrows(IllegalArgumentException.class, () -> TimelineRangeResolver.resolve(
            RenderJobSpec.RangePolicy.INTERSECTION,
            100, 170, 100,
            new TimelineRangeResolver.Marker(100, 10),
            new TimelineRangeResolver.Marker(171, 80)
        ));
    }

    @Test
    void legacyJobsRetainWholeReplayBoundsBehavior() {
        TimelineRangeResolver.Resolution resolution = TimelineRangeResolver.resolve(
            RenderJobSpec.RangePolicy.LEGACY_STRICT,
            95, 180, 100,
            new TimelineRangeResolver.Marker(100, 10),
            null
        );

        assertEquals(TimelineRangeResolver.Status.READY, resolution.status());
        assertEquals(5, resolution.replayStartTick());
        assertEquals(90, resolution.replayEndTick());
    }
}
