package dev.mcdata.scene.replay;

import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneFrame;
import dev.mcdata.scene.job.SceneJob;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class FrameOverlapTrackerTest {
    private static final SceneJob.SourceReplay FIRST = source(
        "11111111-1111-1111-1111-111111111111", 0
    );
    private static final SceneJob.SourceReplay SECOND = source(
        "22222222-2222-2222-2222-222222222222", 1
    );

    @Test
    void suppressesAnEqualOverlapButRetainsBothContributingSources() throws IOException {
        FrameOverlapTracker tracker = new FrameOverlapTracker();

        assertEquals(
            FrameOverlapTracker.Decision.EMIT,
            tracker.observe(FIRST, frame(FIRST, 100, 4, 10), "a".repeat(64))
        );
        assertEquals(
            FrameOverlapTracker.Decision.SUPPRESS_EQUAL_OVERLAP,
            tracker.observe(SECOND, frame(SECOND, 100, 99, 10), "a".repeat(64))
        );

        assertEquals(1, tracker.coveredTickCount());
        assertEquals(List.of(FIRST, SECOND), tracker.contributingSources());
    }

    @Test
    void rejectsAnOverlapWithDifferentCanonicalSnapshotState() throws IOException {
        FrameOverlapTracker tracker = new FrameOverlapTracker();
        tracker.observe(FIRST, frame(FIRST, 100, 4, 10), "a".repeat(64));

        IOException failure = assertThrows(
            IOException.class,
            () -> tracker.observe(SECOND, frame(SECOND, 100, 99, 10), "b".repeat(64))
        );

        assertEquals("overlapping scene frames disagree at global tick 100", failure.getMessage());
    }

    @Test
    void excludesAnUnobservedTrailingSourceFromProvenance() throws IOException {
        FrameOverlapTracker tracker = new FrameOverlapTracker();
        tracker.observe(FIRST, frame(FIRST, 100, 4, 10), "a".repeat(64));
        tracker.requireCoverage(100, 100);

        assertEquals(List.of(FIRST), tracker.contributingSources());
    }

    @Test
    void coverageCheckTerminatesAtTheLargestTick() throws IOException {
        FrameOverlapTracker tracker = new FrameOverlapTracker();
        tracker.observe(FIRST, frame(FIRST, Long.MAX_VALUE, 4, 10), "a".repeat(64));

        tracker.requireCoverage(Long.MAX_VALUE, Long.MAX_VALUE);
    }

    private static SceneFrame frame(
        SceneJob.SourceReplay source,
        long globalTick,
        int replayTick,
        long eventSequence
    ) {
        return new SceneFrame(
            globalTick, replayTick, eventSequence, source.segmentId(), source.segmentOrdinal(),
            "minecraft:overworld", 7, new SceneEvent.Vec3(1, 64, 2), 3, 2, true
        );
    }

    private static SceneJob.SourceReplay source(String segmentId, int ordinal) {
        return new SceneJob.SourceReplay(
            UUID.fromString(segmentId), ordinal, Path.of("segment-" + ordinal + ".zip"),
            Integer.toString(ordinal).repeat(64), ordinal + 1
        );
    }
}
