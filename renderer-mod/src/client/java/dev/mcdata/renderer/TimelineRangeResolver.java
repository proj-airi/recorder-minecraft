package dev.mcdata.renderer;

final class TimelineRangeResolver {
    private TimelineRangeResolver() {
    }

    static Resolution resolve(
        RenderJobSpec.RangePolicy policy,
        long requestedStart,
        long requestedEnd,
        int totalReplayTicks,
        Marker first,
        Marker last
    ) {
        if (first == null) {
            throw new IllegalArgumentException("A first matching timeline marker is required");
        }
        validateMarker(first, totalReplayTicks, "first");
        long offset = first.serverTick() - first.replayTick();

        if (policy == RenderJobSpec.RangePolicy.LEGACY_STRICT) {
            return ready(requestedStart, requestedEnd, offset, totalReplayTicks,
                first.serverTick(), offset + totalReplayTicks);
        }
        if (last == null) {
            throw new IllegalArgumentException("A last matching timeline marker is required");
        }
        validateMarker(last, totalReplayTicks, "last");
        if (last.replayTick() < first.replayTick() || last.serverTick() < first.serverTick()) {
            throw new IllegalArgumentException("Timeline marker coverage is reversed");
        }
        long lastOffset = last.serverTick() - last.replayTick();
        if (lastOffset != offset) {
            throw new IllegalArgumentException(
                "Timeline offset changed inside replay segment: " + offset + " != " + lastOffset
            );
        }

        long coverageStart = first.serverTick();
        long coverageEnd = last.serverTick();
        if (policy == RenderJobSpec.RangePolicy.STRICT) {
            if (requestedStart < coverageStart || requestedEnd > coverageEnd) {
                throw new IllegalArgumentException(
                    "Requested global ticks are not fully covered by this replay segment"
                );
            }
            return ready(requestedStart, requestedEnd, offset, totalReplayTicks, coverageStart, coverageEnd);
        }

        long effectiveStart = Math.max(requestedStart, coverageStart);
        long effectiveEnd = Math.min(requestedEnd, coverageEnd);
        if (effectiveStart > effectiveEnd) {
            return new Resolution(
                Status.NO_COVERAGE, offset, -1, -1,
                effectiveStart, effectiveEnd, coverageStart, coverageEnd
            );
        }
        return ready(effectiveStart, effectiveEnd, offset, totalReplayTicks, coverageStart, coverageEnd);
    }

    private static Resolution ready(
        long globalStart,
        long globalEnd,
        long offset,
        int totalReplayTicks,
        long coverageStart,
        long coverageEnd
    ) {
        long replayStart = globalStart - offset;
        long replayEnd = globalEnd - offset;
        if (replayStart < 0 || replayEnd < replayStart
            || replayEnd > totalReplayTicks || replayEnd > Integer.MAX_VALUE) {
            throw new IllegalArgumentException(
                "Requested global ticks do not fall inside this replay segment after exact marker alignment"
            );
        }
        return new Resolution(
            Status.READY, offset, (int) replayStart, (int) replayEnd,
            globalStart, globalEnd, coverageStart, coverageEnd
        );
    }

    private static void validateMarker(Marker marker, int totalReplayTicks, String label) {
        if (marker.serverTick() < 0 || marker.replayTick() < 0 || marker.replayTick() > totalReplayTicks) {
            throw new IllegalArgumentException("Invalid " + label + " timeline marker");
        }
    }

    record Marker(long serverTick, int replayTick) {
    }

    record Resolution(
        Status status,
        long globalTickOffset,
        int replayStartTick,
        int replayEndTick,
        long globalStartTick,
        long globalEndTick,
        long coverageStartTick,
        long coverageEndTick
    ) {
    }

    enum Status {
        READY,
        NO_COVERAGE
    }
}
