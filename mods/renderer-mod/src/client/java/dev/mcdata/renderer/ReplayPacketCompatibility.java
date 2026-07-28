package dev.mcdata.renderer;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;

/**
 * Owns the automated renderer's compatibility policy for packets that Flashback can decode but
 * cannot apply. Interactive Flashback playback remains strict because suppression is enabled only
 * while an explicit recorder-minecraft render job is active.
 */
public final class ReplayPacketCompatibility {
    public static final String POLICY = "ignore_flashback_unsupported_v1";

    private static final TreeMap<String, Long> SKIPPED_BY_TYPE = new TreeMap<>();
    private static boolean automatedRenderActive;
    private static long skippedTotal;

    private ReplayPacketCompatibility() {
    }

    public static synchronized void beginAutomatedRender() {
        SKIPPED_BY_TYPE.clear();
        skippedTotal = 0;
        automatedRenderActive = true;
    }

    public static synchronized Suppression suppressIfAutomated(String packetType) {
        if (!automatedRenderActive) {
            return null;
        }
        long typeCount = SKIPPED_BY_TYPE.merge(packetType, 1L, Long::sum);
        skippedTotal++;
        return new Suppression(packetType, typeCount, skippedTotal);
    }

    public static synchronized Snapshot snapshot() {
        return new Snapshot(
            POLICY,
            skippedTotal,
            Collections.unmodifiableMap(new LinkedHashMap<>(SKIPPED_BY_TYPE))
        );
    }

    public static synchronized void endAutomatedRender() {
        automatedRenderActive = false;
        SKIPPED_BY_TYPE.clear();
        skippedTotal = 0;
    }

    public record Suppression(String packetType, long packetTypeCount, long totalCount) {
    }

    public record Snapshot(String policy, long totalCount, Map<String, Long> packetTypes) {
    }
}
