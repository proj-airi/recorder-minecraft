package dev.mcdata.renderer;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class TimelineRangeResolverTest {
    @Test
    void waitsForTheResolvedStartMarkerAfterSeekingBackFromTheCoverageScan() {
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
    void boundsCoverageByTheReplayTailAtTheFirstMarkerOffset() {
        TimelineRangeResolver.Marker first = new TimelineRangeResolver.Marker(17, 3, 40);

        TimelineRangeResolver.Marker bound = TimelineRangeResolver.tailBound(first, 1300);

        assertEquals(1314, bound.serverTick());
        assertEquals(1300, bound.replayTick());
        TimelineRangeResolver.Resolution resolution = TimelineRangeResolver.resolve(17, 1315, 1300, first, bound);
        assertEquals(3, resolution.replayStartTick());
        assertEquals(1300, resolution.replayEndTick());
        assertEquals(1314, resolution.globalEndTick());
    }

    @Test
    void keepsTheRenderedRangeWhenMarkersCoverIt() {
        TimelineRangeResolver.Resolution rendered = TimelineRangeResolver.resolve(
            200, 240, 1300,
            new TimelineRangeResolver.Marker(17, 3),
            new TimelineRangeResolver.Marker(1314, 1300)
        );

        TimelineRangeResolver.Resolution trimmed = TimelineRangeResolver.trimToCoverageEnd(rendered, 1314);

        assertEquals(TimelineRangeResolver.Status.READY, trimmed.status());
        assertEquals(240, trimmed.globalEndTick());
        assertEquals(226, trimmed.replayEndTick());
        assertEquals(1314, trimmed.coverageEndTick());
    }

    @Test
    void trimsFramesPastTheLastAppliedMarker() {
        TimelineRangeResolver.Resolution rendered = TimelineRangeResolver.resolve(
            17, 1315, 1300,
            new TimelineRangeResolver.Marker(17, 3),
            new TimelineRangeResolver.Marker(1314, 1300)
        );

        TimelineRangeResolver.Resolution trimmed = TimelineRangeResolver.trimToCoverageEnd(rendered, 1290);

        assertEquals(TimelineRangeResolver.Status.READY, trimmed.status());
        assertEquals(17, trimmed.globalStartTick());
        assertEquals(1290, trimmed.globalEndTick());
        assertEquals(3, trimmed.replayStartTick());
        assertEquals(1276, trimmed.replayEndTick());
        assertEquals(1290, trimmed.coverageEndTick());
    }

    @Test
    void reportsNoCoverageWhenMarkersEndBeforeTheRenderedStart() {
        TimelineRangeResolver.Resolution rendered = TimelineRangeResolver.resolve(
            200, 240, 1300,
            new TimelineRangeResolver.Marker(17, 3),
            new TimelineRangeResolver.Marker(1314, 1300)
        );

        TimelineRangeResolver.Resolution trimmed = TimelineRangeResolver.trimToCoverageEnd(rendered, 150);

        assertEquals(TimelineRangeResolver.Status.NO_COVERAGE, trimmed.status());
        assertEquals(-1, trimmed.replayEndTick());
    }

    @Test
    void rejectsOffsetDriftBetweenFirstAndLastMarkers() {
        assertThrows(IllegalArgumentException.class, () -> TimelineRangeResolver.resolve(
            100, 170, 100,
            new TimelineRangeResolver.Marker(100, 10),
            new TimelineRangeResolver.Marker(171, 80)
        ));
    }
}
