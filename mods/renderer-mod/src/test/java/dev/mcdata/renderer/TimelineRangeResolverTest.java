package dev.mcdata.renderer;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class TimelineRangeResolverTest {
    @Test
    void waitsForTheResolvedStartMarkerAfterSeekingBackFromTheCoverageScan() {
        assertTrue(TimelineRangeResolver.requiresResolvedStartMarker(
            RenderJobSpec.RangePolicy.INTERSECTION
        ));
        assertFalse(TimelineRangeResolver.requiresResolvedStartMarker(
            RenderJobSpec.RangePolicy.LEGACY_STRICT
        ));
        assertFalse(TimelineRangeResolver.matchesResolvedStart(null, 3, 1200));
        assertFalse(TimelineRangeResolver.matchesResolvedStart(
            new TimelineRangeResolver.Marker(1890, 693), 3, 1200
        ));
        assertFalse(TimelineRangeResolver.matchesResolvedStart(
            new TimelineRangeResolver.Marker(1200, 4), 3, 1200
        ));
        assertTrue(TimelineRangeResolver.matchesResolvedStart(
            new TimelineRangeResolver.Marker(1200, 3), 3, 1200
        ));
        assertTrue(TimelineRangeResolver.matchesResolvedStart(
            new TimelineRangeResolver.Marker(1200, 3, 2419), 3, 1200
        ));
    }

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
    void alignsTheRecordedConnectionRangeWithoutSeekingToReplayEnd() {
        TimelineRangeResolver.Resolution resolution = TimelineRangeResolver.resolve(
            RenderJobSpec.RangePolicy.INTERSECTION,
            1200, 1890, 1357,
            new TimelineRangeResolver.Marker(1200, 3),
            new TimelineRangeResolver.Marker(1890, 693)
        );

        assertEquals(TimelineRangeResolver.Status.READY, resolution.status());
        assertEquals(1197, resolution.globalTickOffset());
        assertEquals(3, resolution.replayStartTick());
        assertEquals(693, resolution.replayEndTick());
    }

    @Test
    void retainsTheRealMarkerWhenAForwardSeekReplaysItAgain() {
        TimelineRangeResolver.Marker first = new TimelineRangeResolver.Marker(1200, 3, 2419);
        TimelineRangeResolver.Marker last = new TimelineRangeResolver.Marker(1892, 695, 9002);

        TimelineRangeResolver.Marker accepted = TimelineRangeResolver.extendForwardCoverage(
            first, last, new TimelineRangeResolver.Marker(1892, 1357, 9002)
        );

        assertEquals(last, accepted);
    }

    @Test
    void ignoresAnOlderMarkerReplayedByAForwardSeek() {
        TimelineRangeResolver.Marker first = new TimelineRangeResolver.Marker(1200, 3, 2419);
        TimelineRangeResolver.Marker last = new TimelineRangeResolver.Marker(1500, 303, 6000);

        TimelineRangeResolver.Marker accepted = TimelineRangeResolver.extendForwardCoverage(
            first, last, new TimelineRangeResolver.Marker(1499, 450, 5990)
        );

        assertEquals(last, accepted);
    }

    @Test
    void rejectsARegressedServerTickWithANewerSequence() {
        assertThrows(IllegalArgumentException.class, () -> TimelineRangeResolver.extendForwardCoverage(
            new TimelineRangeResolver.Marker(1200, 3, 2419),
            new TimelineRangeResolver.Marker(1500, 303, 6000),
            new TimelineRangeResolver.Marker(1499, 450, 6001)
        ));
    }

    @Test
    void acceptsAnAdvancingMarkerWithTheExactOffset() {
        TimelineRangeResolver.Marker candidate = new TimelineRangeResolver.Marker(1202, 5, 2437);

        TimelineRangeResolver.Marker accepted = TimelineRangeResolver.extendForwardCoverage(
            new TimelineRangeResolver.Marker(1200, 3, 2419),
            new TimelineRangeResolver.Marker(1201, 4, 2428),
            candidate
        );

        assertEquals(candidate, accepted);
    }

    @Test
    void distinguishesAnIntermediateTailSeekMarkerFromAnAlignedMarker() {
        TimelineRangeResolver.Marker first = new TimelineRangeResolver.Marker(6898, 3, 2419);

        assertFalse(TimelineRangeResolver.hasSameOffset(
            first, new TimelineRangeResolver.Marker(6924, 682, 2600)
        ));
        assertTrue(TimelineRangeResolver.hasSameOffset(
            first, new TimelineRangeResolver.Marker(7577, 682, 9002)
        ));
    }

    @Test
    void rejectsAnAdvancingMarkerWithOffsetDriftDuringForwardScan() {
        assertThrows(IllegalArgumentException.class, () -> TimelineRangeResolver.extendForwardCoverage(
            new TimelineRangeResolver.Marker(1200, 3, 2419),
            new TimelineRangeResolver.Marker(1201, 4, 2428),
            new TimelineRangeResolver.Marker(1202, 6, 2437)
        ));
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
