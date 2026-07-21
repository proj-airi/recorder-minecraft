package dev.mcdata.scene.io;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneFrame;
import dev.mcdata.scene.job.SceneJob;

import java.io.BufferedWriter;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.zip.Deflater;
import java.util.zip.DeflaterOutputStream;

/** Effect boundary that durably writes the scene-stream-v1 spool. */
public final class SceneSpoolWriter implements AutoCloseable {
    public static final String FORMAT = "mc-recorder-scene-stream-v1";
    private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().create();

    private final Path finalOutput;
    private final Path temporaryOutput;
    private final Path blobs;
    private final Path framesPath;
    private final Path changesPath;
    private final BufferedWriter frames;
    private final BufferedWriter changes;
    private final Map<String, Long> blobSizes = new LinkedHashMap<>();

    private long changeSequence;
    private long frameCount;
    private long changeCount;
    private boolean committed;
    private boolean closed;

    public SceneSpoolWriter(Path finalOutput) throws IOException {
        this.finalOutput = finalOutput;
        this.temporaryOutput = finalOutput.resolveSibling("." + finalOutput.getFileName() + ".tmp-" + UUID.randomUUID());
        Files.createDirectory(temporaryOutput);
        this.blobs = Files.createDirectory(temporaryOutput.resolve("blobs"));
        this.framesPath = temporaryOutput.resolve("frames.jsonl");
        this.changesPath = temporaryOutput.resolve("changes.jsonl");
        this.frames = Files.newBufferedWriter(framesPath, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW);
        this.changes = Files.newBufferedWriter(changesPath, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW);
    }

    public void beginSegment(SceneJob.SourceReplay source) throws IOException {
        JsonObject data = new JsonObject();
        data.addProperty("segment_id", source.segmentId().toString());
        data.addProperty("segment_ordinal", source.segmentOrdinal());
        data.addProperty("sha256", source.sha256());
        data.addProperty("size_bytes", source.sizeBytes());
        writeChangeObject(-1, source, "segment_begin", data);
    }

    public void writeChange(int replayTick, SceneJob.SourceReplay source, SceneEvent event) throws IOException {
        String kind = eventKind(event);
        JsonElement data;
        if (event instanceof SceneEvent.SectionLoaded loaded) {
            JsonObject object = new JsonObject();
            object.addProperty("dimension", loaded.dimension());
            object.addProperty("chunk_x", loaded.chunkX());
            object.addProperty("chunk_z", loaded.chunkZ());
            object.addProperty("section_y", loaded.sectionY());
            object.addProperty("blob", writeSectionBlob(loaded.snapshot()));
            data = object;
        } else {
            data = GSON.toJsonTree(event);
        }
        writeChangeObject(replayTick, source, kind, data);
    }

    public void writeFrame(SceneFrame frame) throws IOException {
        JsonObject object = new JsonObject();
        object.addProperty("schema_version", 1);
        object.addProperty("global_tick", frame.globalTick());
        object.addProperty("replay_tick", frame.replayTick());
        object.addProperty("event_sequence", frame.eventSequence());
        object.addProperty("segment_id", frame.segmentId().toString());
        object.addProperty("segment_ordinal", frame.segmentOrdinal());
        object.addProperty("dimension", frame.dimension());
        object.addProperty("subject_entity_id", frame.subjectEntityId());
        if (frame.subjectPosition() == null) {
            object.add("subject_position", null);
        } else {
            object.add("subject_position", GSON.toJsonTree(frame.subjectPosition()));
        }
        object.addProperty("loaded_section_count", frame.loadedSectionCount());
        object.addProperty("entity_count", frame.entityCount());
        object.addProperty("complete", frame.complete());
        appendJsonLine(frames, object);
        frameCount++;
    }

    public OutputStats commit() throws IOException {
        ensureOpen();
        frames.close();
        changes.close();
        forceFile(framesPath);
        forceFile(changesPath);
        for (String blob : blobSizes.keySet()) {
            forceFile(blobs.resolve(blob + ".json.zlib"));
        }
        forceDirectory(blobs);
        forceDirectory(temporaryOutput);
        moveAtomically(temporaryOutput, finalOutput);
        forceDirectory(finalOutput.getParent());
        committed = true;
        closed = true;
        return new OutputStats(
            frameCount,
            changeCount,
            blobSizes.size(),
            blobSizes.values().stream().mapToLong(Long::longValue).sum(),
            Hashing.sha256(finalOutput.resolve("frames.jsonl")),
            Files.size(finalOutput.resolve("frames.jsonl")),
            Hashing.sha256(finalOutput.resolve("changes.jsonl")),
            Files.size(finalOutput.resolve("changes.jsonl"))
        );
    }

    @Override
    public void close() throws IOException {
        if (closed) {
            return;
        }
        IOException failure = null;
        try {
            frames.close();
        } catch (IOException exception) {
            failure = exception;
        }
        try {
            changes.close();
        } catch (IOException exception) {
            if (failure == null) {
                failure = exception;
            } else {
                failure.addSuppressed(exception);
            }
        }
        closed = true;
        if (!committed) {
            try {
                deleteTemporaryOutput();
            } catch (IOException exception) {
                if (failure == null) {
                    failure = exception;
                } else {
                    failure.addSuppressed(exception);
                }
            }
        }
        if (failure != null) {
            throw failure;
        }
    }

    private void writeChangeObject(
        int replayTick,
        SceneJob.SourceReplay source,
        String kind,
        JsonElement data
    ) throws IOException {
        JsonObject object = new JsonObject();
        object.addProperty("schema_version", 1);
        object.addProperty("sequence", changeSequence++);
        object.addProperty("replay_tick", replayTick);
        object.addProperty("segment_id", source.segmentId().toString());
        object.addProperty("segment_ordinal", source.segmentOrdinal());
        object.addProperty("kind", kind);
        object.add("data", data);
        appendJsonLine(changes, object);
        changeCount++;
    }

    private String writeSectionBlob(SceneEvent.SectionSnapshot snapshot) throws IOException {
        JsonObject object = new JsonObject();
        object.addProperty("schema_version", 1);
        object.addProperty("cell_order", "y_z_x");
        object.add("palette", GSON.toJsonTree(snapshot.palette()));
        object.add("indices", GSON.toJsonTree(snapshot.indices()));
        byte[] canonical = GSON.toJson(object).getBytes(StandardCharsets.UTF_8);
        String digest = Hashing.sha256(canonical);
        if (blobSizes.containsKey(digest)) {
            return digest;
        }

        byte[] compressed;
        try (ByteArrayOutputStream bytes = new ByteArrayOutputStream();
             DeflaterOutputStream output = new DeflaterOutputStream(bytes, new Deflater(6))) {
            output.write(canonical);
            output.finish();
            compressed = bytes.toByteArray();
        }
        Path target = blobs.resolve(digest + ".json.zlib");
        Path temporary = blobs.resolve("." + digest + ".tmp-" + UUID.randomUUID());
        Files.write(temporary, compressed, StandardOpenOption.CREATE_NEW);
        forceFile(temporary);
        moveAtomically(temporary, target);
        blobSizes.put(digest, (long) compressed.length);
        return digest;
    }

    private static String eventKind(SceneEvent event) {
        String name = event.getClass().getSimpleName();
        StringBuilder result = new StringBuilder(name.length() + 4);
        for (int index = 0; index < name.length(); index++) {
            char value = name.charAt(index);
            if (Character.isUpperCase(value) && index > 0) {
                result.append('_');
            }
            result.append(Character.toLowerCase(value));
        }
        return result.toString();
    }

    private static void appendJsonLine(BufferedWriter writer, JsonObject object) throws IOException {
        writer.write(GSON.toJson(object));
        writer.newLine();
    }

    private void ensureOpen() {
        if (closed) {
            throw new IllegalStateException("scene spool writer is closed");
        }
    }

    private static void forceFile(Path path) throws IOException {
        try (FileChannel channel = FileChannel.open(path, StandardOpenOption.WRITE)) {
            channel.force(true);
        }
    }

    private static void forceDirectory(Path path) throws IOException {
        try (FileChannel channel = FileChannel.open(path, StandardOpenOption.READ)) {
            channel.force(true);
        } catch (UnsupportedOperationException exception) {
            throw new IOException("filesystem cannot durably sync scene directory " + path, exception);
        }
    }

    private static void moveAtomically(Path source, Path target) throws IOException {
        try {
            Files.move(source, target, java.nio.file.StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException exception) {
            throw new IOException("scene job filesystem does not support required atomic publication", exception);
        }
    }

    private void deleteTemporaryOutput() throws IOException {
        if (!Files.exists(temporaryOutput)) {
            return;
        }
        try (var entries = Files.walk(temporaryOutput)) {
            for (Path path : entries.sorted(Comparator.reverseOrder()).toList()) {
                Files.deleteIfExists(path);
            }
        }
    }

    public record OutputStats(
        long frameCount,
        long changeCount,
        long blobCount,
        long blobBytes,
        String framesSha256,
        long framesBytes,
        String changesSha256,
        long changesBytes
    ) { }
}
