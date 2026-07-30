package dev.mcdata.scene.replay;

import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneFrame;
import dev.mcdata.scene.job.SceneJob;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Validates selected replay markers and chooses one canonical owner for each global tick. */
final class FrameOverlapTracker {
    enum Decision {
        EMIT,
        SUPPRESS_EQUAL_OVERLAP
    }

    private final Map<Long, LogicalFrame> frames = new HashMap<>();
    private final Set<MarkerKey> markers = new HashSet<>();
    private final Set<UUID> contributingSegmentIds = new HashSet<>();
    private final List<SceneJob.SourceReplay> contributingSources = new ArrayList<>();

    Decision observe(
        SceneJob.SourceReplay source,
        SceneFrame frame,
        String snapshotSha256
    ) throws IOException {
        if (!source.segmentId().equals(frame.segmentId())
            || source.segmentOrdinal() != frame.segmentOrdinal()) {
            throw new IOException("selected scene frame does not match its replay segment");
        }
        MarkerKey marker = new MarkerKey(source.segmentId(), frame.globalTick());
        if (!markers.add(marker)) {
            throw new IOException(
                "duplicate timeline marker in one replay segment at global tick " + frame.globalTick()
            );
        }
        if (contributingSegmentIds.add(source.segmentId())) {
            contributingSources.add(source);
        }

        LogicalFrame logical = LogicalFrame.from(frame, snapshotSha256);
        LogicalFrame existing = frames.putIfAbsent(frame.globalTick(), logical);
        if (existing == null) {
            return Decision.EMIT;
        }
        if (!existing.equals(logical)) {
            throw new IOException(
                "overlapping scene frames disagree at global tick " + frame.globalTick()
            );
        }
        return Decision.SUPPRESS_EQUAL_OVERLAP;
    }

    void requireCoverage(long firstTick, long lastTick) throws IOException {
        long tick = firstTick;
        while (true) {
            if (!frames.containsKey(tick)) {
                throw new IOException("scene replay coverage is missing global tick " + tick);
            }
            if (tick == lastTick) {
                return;
            }
            tick++;
        }
    }

    long coveredTickCount() {
        return frames.size();
    }

    List<SceneJob.SourceReplay> contributingSources() {
        return List.copyOf(contributingSources);
    }

    private record MarkerKey(UUID segmentId, long globalTick) { }

    private record LogicalFrame(
        long eventSequence,
        String dimension,
        int subjectEntityId,
        SceneEvent.Vec3 subjectPosition,
        int loadedSectionCount,
        int entityCount,
        boolean complete,
        String snapshotSha256
    ) {
        private static LogicalFrame from(SceneFrame frame, String snapshotSha256) {
            return new LogicalFrame(
                frame.eventSequence(), frame.dimension(), frame.subjectEntityId(), frame.subjectPosition(),
                frame.loadedSectionCount(), frame.entityCount(), frame.complete(), snapshotSha256
            );
        }
    }
}
