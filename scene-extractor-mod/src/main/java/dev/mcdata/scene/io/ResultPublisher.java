package dev.mcdata.scene.io;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dev.mcdata.scene.job.SceneJob;
import dev.mcdata.scene.replay.FlashbackSceneExtractor;

import java.io.IOException;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Atomically publishes the orchestration terminal result only after spool durability. */
public final class ResultPublisher {
    public static final String RESULT_TYPE = "mc-recorder-scene-extraction-result-v1";
    private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().create();
    private static final int MAX_ERROR_CHARS = 4096;

    private ResultPublisher() { }

    public static void complete(
        SceneJob job,
        SceneSpoolWriter.OutputStats output,
        FlashbackSceneExtractor.ExtractionStats extraction
    ) throws IOException {
        JsonObject result = common(job, "complete", extraction.sourceReplays());
        JsonObject stream = new JsonObject();
        stream.addProperty("format", SceneSpoolWriter.FORMAT);
        stream.addProperty("path", job.output().toString());
        stream.addProperty("frames_index", "frames.jsonl");
        stream.addProperty("changes_index", "changes.jsonl");
        stream.addProperty("blobs_directory", "blobs");
        stream.addProperty("frame_count", output.frameCount());
        stream.addProperty("change_count", output.changeCount());
        stream.addProperty("blob_count", output.blobCount());
        stream.addProperty("blob_bytes", output.blobBytes());
        stream.addProperty("frames_sha256", output.framesSha256());
        stream.addProperty("frames_size_bytes", output.framesBytes());
        stream.addProperty("changes_sha256", output.changesSha256());
        stream.addProperty("changes_size_bytes", output.changesBytes());
        result.add("stream", stream);

        JsonObject ignored = new JsonObject();
        for (Map.Entry<String, Long> entry : extraction.ignoredPacketCounts().entrySet()) {
            ignored.addProperty(entry.getKey(), entry.getValue());
        }
        result.add("ignored_packet_counts", ignored);
        result.addProperty("covered_tick_count", extraction.coveredTickCount());
        publish(job.result(), result);
    }

    public static void failed(SceneJob job, Throwable failure) throws IOException {
        JsonObject result = common(job, "failed", job.sourceReplays());
        String message = failure.getClass().getSimpleName() + ": " + String.valueOf(failure.getMessage());
        if (message.length() > MAX_ERROR_CHARS) {
            message = message.substring(0, MAX_ERROR_CHARS);
        }
        result.addProperty("error", message);
        publish(job.result(), result);
    }

    private static JsonObject common(
        SceneJob job,
        String status,
        List<SceneJob.SourceReplay> sourceReplays
    ) {
        JsonObject result = new JsonObject();
        result.addProperty("schema_version", 1);
        result.addProperty("result_type", RESULT_TYPE);
        result.addProperty("status", status);
        result.addProperty("job_id", job.jobId());
        result.addProperty("session_id", job.sessionId());
        result.addProperty("player_uuid", job.playerUuid().toString());
        result.addProperty("connection_id", job.connectionId().toString());
        result.addProperty("global_start_tick", job.globalStartTick());
        result.addProperty("global_end_tick", job.globalEndTick());
        result.addProperty("scope", SceneJob.SCOPE);
        result.addProperty("metadata_policy", SceneJob.METADATA_POLICY);
        result.add("source_replays", sourceReplays(sourceReplays));
        return result;
    }

    private static JsonArray sourceReplays(List<SceneJob.SourceReplay> sourceReplays) {
        JsonArray sources = new JsonArray();
        for (SceneJob.SourceReplay source : sourceReplays) {
            JsonObject value = new JsonObject();
            value.addProperty("segment_id", source.segmentId().toString());
            value.addProperty("segment_ordinal", source.segmentOrdinal());
            value.addProperty("path", source.path().toString());
            value.addProperty("sha256", source.sha256());
            value.addProperty("size_bytes", source.sizeBytes());
            value.addProperty("format", "flashback");
            sources.add(value);
        }
        return sources;
    }

    private static void publish(Path target, JsonObject value) throws IOException {
        byte[] bytes = (GSON.toJson(value) + "\n").getBytes(StandardCharsets.UTF_8);
        Path temporary = target.resolveSibling(".result.json.tmp-" + UUID.randomUUID());
        Files.write(temporary, bytes, StandardOpenOption.CREATE_NEW);
        try (FileChannel channel = FileChannel.open(temporary, StandardOpenOption.WRITE)) {
            channel.force(true);
        }
        try {
            Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException exception) {
            Files.deleteIfExists(temporary);
            throw new IOException("scene result filesystem does not support required atomic publication", exception);
        }
        try (FileChannel channel = FileChannel.open(target.getParent(), StandardOpenOption.READ)) {
            channel.force(true);
        }
    }
}
