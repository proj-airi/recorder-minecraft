package dev.mcdata.scene.job;

import java.nio.file.Path;
import java.util.List;
import java.util.UUID;

/** Immutable, fully validated extraction request. */
public record SceneJob(
    Path requestPath,
    String jobId,
    String sessionId,
    UUID playerUuid,
    UUID connectionId,
    long globalStartTick,
    long globalEndTick,
    Path output,
    Path result,
    List<SourceReplay> sourceReplays,
    boolean stopWhenDone
) {
    public static final String SCOPE = "client_visible";
    public static final String METADATA_POLICY = "full_packet_metadata";

    public SceneJob {
        sourceReplays = List.copyOf(sourceReplays);
    }

    public record SourceReplay(
        UUID segmentId,
        int segmentOrdinal,
        Path path,
        String sha256,
        long sizeBytes
    ) { }
}
