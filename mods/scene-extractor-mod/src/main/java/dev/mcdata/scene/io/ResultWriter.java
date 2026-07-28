package dev.mcdata.scene.io;

import dev.minerec.artifacts.v1.SceneExtractionResult;
import dev.minerec.artifacts.v1.SceneSourceReplay;
import dev.minerec.artifacts.v1.SceneStreamResult;
import dev.minerec.artifacts.v1.TickRange;
import dev.mcdata.scene.job.SceneJob;
import dev.mcdata.scene.replay.FlashbackSceneExtractor;
import com.google.protobuf.util.JsonFormat;

import java.io.IOException;
import java.nio.channels.FileChannel;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.List;
import java.util.UUID;

/** Atomically writes the generated protobuf terminal result only after spool durability. */
public final class ResultWriter {
    private static final int MAX_ERROR_CHARS = 4096;

    private ResultWriter() { }

    public static void complete(
        SceneJob job,
        SceneSpoolWriter.OutputStats output,
        FlashbackSceneExtractor.ExtractionStats extraction
    ) throws IOException {
        SceneExtractionResult.Builder result = common(job, SceneExtractionResult.Status.STATUS_COMPLETE, extraction.sourceReplays());
        result.setStream(SceneStreamResult.newBuilder()
            .setPath(job.output().toString())
            .setFrames("frames.jsonl")
            .setChanges("changes.jsonl")
            .setBlobsDirectory("blobs")
            .setFrameCount(output.frameCount())
            .setChangeCount(output.changeCount())
            .setBlobCount(output.blobCount())
            .setBlobBytes(output.blobBytes())
            .setFramesSha256(output.framesSha256())
            .setFramesSizeBytes(output.framesBytes())
            .setChangesSha256(output.changesSha256())
            .setChangesSizeBytes(output.changesBytes()));
        result.putAllIgnoredPacketCounts(extraction.ignoredPacketCounts());
        result.setCoveredTickCount(extraction.coveredTickCount());
        write(job.result(), result.build());
    }

    public static void failed(SceneJob job, Throwable failure) throws IOException {
        String message = failure.getClass().getSimpleName() + ": " + String.valueOf(failure.getMessage());
        if (message.length() > MAX_ERROR_CHARS) {
            message = message.substring(0, MAX_ERROR_CHARS);
        }
        SceneExtractionResult result = common(job, SceneExtractionResult.Status.STATUS_FAILED, job.sourceReplays())
            .setError(message)
            .build();
        write(job.result(), result);
    }

    private static SceneExtractionResult.Builder common(
        SceneJob job,
        SceneExtractionResult.Status status,
        List<SceneJob.SourceReplay> sources
    ) {
        SceneExtractionResult.Builder result = SceneExtractionResult.newBuilder()
            .setStatus(status)
            .setJobId(job.jobId())
            .setSessionId(job.sessionId())
            .setPlayerUuid(job.playerUuid().toString())
            .setConnectionId(job.connectionId().toString())
            .setTicks(TickRange.newBuilder().setFirstTick(job.globalStartTick()).setLastTick(job.globalEndTick()))
            .setScope(SceneJob.SCOPE)
            .setMetadataPolicy(SceneJob.METADATA_POLICY);
        for (SceneJob.SourceReplay source : sources) {
            result.addSourceReplays(SceneSourceReplay.newBuilder()
                .setSegmentId(source.segmentId().toString())
                .setSegmentOrdinal(source.segmentOrdinal())
                .setPath(source.path().toString())
                .setSha256(source.sha256())
                .setSizeBytes(source.sizeBytes())
                .setFormat("flashback"));
        }
        return result;
    }

    private static void write(Path target, SceneExtractionResult value) throws IOException {
        Path temporary = target.resolveSibling(".result.json.tmp-" + UUID.randomUUID());
        byte[] encoded = (JsonFormat.printer().print(value) + "\n").getBytes(java.nio.charset.StandardCharsets.UTF_8);
        Files.write(temporary, encoded, StandardOpenOption.CREATE_NEW);
        try (FileChannel channel = FileChannel.open(temporary, StandardOpenOption.WRITE)) {
            channel.force(true);
        }
        try {
            Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException exception) {
            Files.deleteIfExists(temporary);
            throw new IOException("scene result filesystem does not support the required atomic write", exception);
        }
        try (FileChannel channel = FileChannel.open(target.getParent(), StandardOpenOption.READ)) {
            channel.force(true);
        }
    }
}
