package dev.mcdata.scene.io;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneFrame;
import dev.mcdata.scene.core.SceneSnapshot;
import dev.mcdata.scene.job.SceneJob;

import java.io.BufferedWriter;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;
import java.util.zip.Deflater;
import java.util.zip.DeflaterOutputStream;

/** Effect boundary that durably writes the normalized scene-stream-v1 spool. */
public final class SceneSpoolWriter implements AutoCloseable {
    public static final String FORMAT = "mc-recorder-scene-stream-v1";
    private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().serializeNulls().create();

    private final Path finalOutput;
    private final Path temporaryOutput;
    private final Path blobs;
    private final Path framesPath;
    private final Path changesPath;
    private final BufferedWriter frames;
    private final BufferedWriter changes;
    private final Map<String, Long> blobSizes = new LinkedHashMap<>();
    private Map<SectionIdentity, String> previousSections = Map.of();
    private Map<String, String> previousEntities = Map.of();
    private Map<BlockEntityIdentity, String> previousBlockEntities = Map.of();

    private SceneJob.SourceReplay activeSegment;
    private boolean segmentBeginPending;
    private long changeSequence;
    private long frameCount;
    private long changeCount;
    private boolean committed;
    private boolean closed;

    public SceneSpoolWriter(Path finalOutput) throws IOException {
        this.finalOutput = finalOutput;
        this.temporaryOutput = finalOutput.resolveSibling(
            "." + finalOutput.getFileName() + ".tmp-" + UUID.randomUUID()
        );
        Files.createDirectory(temporaryOutput);
        this.blobs = Files.createDirectory(temporaryOutput.resolve("blobs"));
        this.framesPath = temporaryOutput.resolve("frames.jsonl");
        this.changesPath = temporaryOutput.resolve("changes.jsonl");
        this.frames = Files.newBufferedWriter(
            framesPath, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW
        );
        this.changes = Files.newBufferedWriter(
            changesPath, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW
        );
    }

    /** Defers provenance emission until the segment's first selected global tick is known. */
    public void beginSegment(SceneJob.SourceReplay source) {
        ensureOpen();
        activeSegment = source;
        segmentBeginPending = true;
    }

    /**
     * Canonicalizes one complete snapshot without advancing the output state.
     *
     * <p>This lets the replay adapter compare overlapping segment snapshots before deciding which
     * single frame owns the global tick. Blob creation is content-addressed and therefore remains
     * deterministic when an equal overlap is suppressed.</p>
     */
    public PreparedSnapshot prepareSnapshot(SceneFrame frame, SceneSnapshot snapshot) throws IOException {
        ensureOpen();
        SceneJob.SourceReplay source = requireActiveSegment(frame);
        Map<SectionIdentity, String> currentSections = new LinkedHashMap<>();
        for (SceneSnapshot.Section section : snapshot.sections()) {
            SectionIdentity identity = new SectionIdentity(
                section.dimension(), section.x(), section.y(), section.z()
            );
            currentSections.put(identity, writeSectionBlob(section.snapshot()));
        }

        Map<String, String> currentEntities = new LinkedHashMap<>();
        for (SceneSnapshot.Entity entity : snapshot.entities()) {
            currentEntities.put(instanceId(source, entity), writeEntityBlob(entity));
        }

        Map<BlockEntityIdentity, String> currentBlockEntities = new LinkedHashMap<>();
        for (SceneSnapshot.BlockEntity blockEntity : snapshot.blockEntities()) {
            BlockEntityIdentity identity = new BlockEntityIdentity(
                blockEntity.dimension(), blockEntity.x(), blockEntity.y(), blockEntity.z()
            );
            currentBlockEntities.put(identity, writeBlockEntityBlob(blockEntity));
        }

        return new PreparedSnapshot(
            source.segmentId(), currentSections, currentEntities, currentBlockEntities,
            snapshotSha256(
                frame.dimension(), currentSections, currentEntities, currentBlockEntities, source
            )
        );
    }

    /** Emits source provenance for a segment that owns any selected marker, including an overlap. */
    public void writeSegmentBegin(SceneFrame frame) throws IOException {
        ensureOpen();
        writeSegmentBegin(frame, requireActiveSegment(frame));
    }

    /** Writes one selected frame and the normalized state delta effective at its global tick. */
    public void writeFrame(SceneFrame frame, SceneSnapshot snapshot) throws IOException {
        writeFrame(frame, prepareSnapshot(frame, snapshot));
    }

    /** Writes one unique selected frame from a snapshot prepared for overlap comparison. */
    public void writeFrame(SceneFrame frame, PreparedSnapshot snapshot) throws IOException {
        ensureOpen();
        SceneJob.SourceReplay source = requireActiveSegment(frame);
        if (!snapshot.segmentId.equals(source.segmentId())) {
            throw new IllegalStateException("prepared scene snapshot belongs to a different replay segment");
        }
        writeSegmentBegin(frame, source);
        String snapshotSha256 = writeSnapshotChanges(frame, source, snapshot);

        JsonObject object = new JsonObject();
        object.addProperty("complete", frame.complete());
        object.addProperty("dimension", frame.dimension());
        object.addProperty("frame_id", frame.segmentId() + ":" + frame.eventSequence());
        JsonObject metadata = new JsonObject();
        metadata.addProperty("entity_count", frame.entityCount());
        metadata.addProperty("event_sequence", frame.eventSequence());
        metadata.addProperty("loaded_section_count", frame.loadedSectionCount());
        metadata.addProperty("metadata_policy", SceneJob.METADATA_POLICY);
        metadata.addProperty("scene_snapshot_sha256", snapshotSha256);
        metadata.addProperty("scope", SceneJob.SCOPE);
        metadata.addProperty("subject_entity_id", frame.subjectEntityId());
        object.add("metadata", metadata);
        object.addProperty("replay_tick", frame.replayTick());
        object.add("reasons", new JsonArray());
        object.addProperty("schema_version", 1);
        object.addProperty("segment_id", frame.segmentId().toString());
        object.addProperty("segment_ordinal", frame.segmentOrdinal());
        object.addProperty("server_tick", frame.globalTick());
        object.add("subject_position", vector(frame.subjectPosition()));
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
            forceFile(blobs.resolve(blob + ".zlib"));
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

    private SceneJob.SourceReplay requireActiveSegment(SceneFrame frame) {
        if (activeSegment == null || !activeSegment.segmentId().equals(frame.segmentId())) {
            throw new IllegalStateException("scene frame does not belong to the active replay segment");
        }
        return activeSegment;
    }

    private void writeSegmentBegin(SceneFrame frame, SceneJob.SourceReplay source) throws IOException {
        if (!segmentBeginPending) {
            return;
        }
        JsonObject fields = baseChange(frame, source, "segment_begin");
        fields.addProperty("sha256", source.sha256());
        fields.addProperty("size_bytes", source.sizeBytes());
        writeChangeObject(fields);
        segmentBeginPending = false;
    }

    private String writeSnapshotChanges(
        SceneFrame frame,
        SceneJob.SourceReplay source,
        PreparedSnapshot snapshot
    ) throws IOException {
        Map<SectionIdentity, String> currentSections = snapshot.sections;
        for (SectionIdentity identity : sortedDifference(previousSections, currentSections)) {
            writeSectionChange(frame, source, "section_unload", identity, null);
        }
        for (Map.Entry<SectionIdentity, String> entry : currentSections.entrySet()) {
            if (!entry.getValue().equals(previousSections.get(entry.getKey()))) {
                writeSectionChange(frame, source, "section_set", entry.getKey(), entry.getValue());
            }
        }

        Map<String, String> currentEntities = snapshot.entities;
        for (String instanceId : sortedDifference(previousEntities, currentEntities)) {
            JsonObject fields = baseChange(frame, source, "entity_remove");
            fields.addProperty("instance_id", instanceId);
            writeChangeObject(fields);
        }
        for (Map.Entry<String, String> entry : currentEntities.entrySet()) {
            if (!entry.getValue().equals(previousEntities.get(entry.getKey()))) {
                JsonObject fields = baseChange(frame, source, "entity_set");
                fields.addProperty("blob_sha256", entry.getValue());
                fields.addProperty("instance_id", entry.getKey());
                writeChangeObject(fields);
            }
        }

        Map<BlockEntityIdentity, String> currentBlockEntities = snapshot.blockEntities;
        for (BlockEntityIdentity identity : sortedDifference(previousBlockEntities, currentBlockEntities)) {
            writeBlockEntityChange(frame, source, "block_entity_remove", identity, null);
        }
        for (Map.Entry<BlockEntityIdentity, String> entry : currentBlockEntities.entrySet()) {
            if (!entry.getValue().equals(previousBlockEntities.get(entry.getKey()))) {
                writeBlockEntityChange(
                    frame, source, "block_entity_set", entry.getKey(), entry.getValue()
                );
            }
        }

        previousSections = Map.copyOf(currentSections);
        previousEntities = Map.copyOf(currentEntities);
        previousBlockEntities = Map.copyOf(currentBlockEntities);
        return snapshot.sha256;
    }

    private void writeSectionChange(
        SceneFrame frame,
        SceneJob.SourceReplay source,
        String type,
        SectionIdentity identity,
        String digest
    ) throws IOException {
        JsonObject fields = baseChange(frame, source, type);
        if (digest != null) {
            fields.addProperty("blob_sha256", digest);
        }
        fields.addProperty("dimension", identity.dimension);
        fields.addProperty("x", identity.x);
        fields.addProperty("y", identity.y);
        fields.addProperty("z", identity.z);
        writeChangeObject(fields);
    }

    private void writeBlockEntityChange(
        SceneFrame frame,
        SceneJob.SourceReplay source,
        String type,
        BlockEntityIdentity identity,
        String digest
    ) throws IOException {
        JsonObject fields = baseChange(frame, source, type);
        if (digest != null) {
            fields.addProperty("blob_sha256", digest);
        }
        fields.addProperty("dimension", identity.dimension);
        fields.addProperty("x", identity.x);
        fields.addProperty("y", identity.y);
        fields.addProperty("z", identity.z);
        writeChangeObject(fields);
    }

    private JsonObject baseChange(
        SceneFrame frame,
        SceneJob.SourceReplay source,
        String type
    ) {
        JsonObject object = new JsonObject();
        object.addProperty("replay_tick", frame.replayTick());
        object.addProperty("schema_version", 1);
        object.addProperty("segment_id", source.segmentId().toString());
        object.addProperty("segment_ordinal", source.segmentOrdinal());
        object.addProperty("sequence", changeSequence++);
        object.addProperty("server_tick", frame.globalTick());
        object.addProperty("type", type);
        return object;
    }

    private void writeChangeObject(JsonObject object) throws IOException {
        appendJsonLine(changes, object);
        changeCount++;
    }

    private String writeSectionBlob(SceneEvent.SectionSnapshot snapshot) throws IOException {
        List<String> encodedStates = snapshot.palette().stream()
            .map(SceneSpoolWriter::blockState)
            .map(SceneSpoolWriter::canonicalJson)
            .toList();
        TreeMap<String, JsonElement> uniqueStates = new TreeMap<>();
        for (int index = 0; index < encodedStates.size(); index++) {
            uniqueStates.put(encodedStates.get(index), blockState(snapshot.palette().get(index)));
        }
        Map<String, Integer> canonicalIndices = new HashMap<>();
        JsonArray palette = new JsonArray();
        for (Map.Entry<String, JsonElement> entry : uniqueStates.entrySet()) {
            canonicalIndices.put(entry.getKey(), palette.size());
            palette.add(entry.getValue());
        }
        int[] sourceIndices = snapshot.indices();
        ByteBuffer packed = ByteBuffer.allocate(4096 * 2).order(ByteOrder.LITTLE_ENDIAN);
        for (int sourceIndex : sourceIndices) {
            int canonicalIndex = canonicalIndices.get(encodedStates.get(sourceIndex));
            packed.putShort((short) canonicalIndex);
        }
        JsonObject object = new JsonObject();
        object.addProperty("format", "mc-recorder-section-v1");
        object.addProperty("index_order", "x_fastest_then_z_then_y");
        object.addProperty("indices_le_u16_b64", Base64.getEncoder().encodeToString(packed.array()));
        object.add("palette", palette);
        JsonArray shape = new JsonArray();
        shape.add(16);
        shape.add(16);
        shape.add(16);
        object.add("shape", shape);
        return writeBlob(object);
    }

    private String writeEntityBlob(SceneSnapshot.Entity entity) throws IOException {
        JsonObject object = new JsonObject();
        double halfWidth = entity.width() / 2.0;
        JsonArray aabb = new JsonArray();
        aabb.add(entity.position().x() - halfWidth);
        aabb.add(entity.position().y());
        aabb.add(entity.position().z() - halfWidth);
        aabb.add(entity.position().x() + halfWidth);
        aabb.add(entity.position().y() + entity.height());
        aabb.add(entity.position().z() + halfWidth);
        object.add("aabb", aabb);
        object.add("attributes", attributes(entity.attributes()));
        object.addProperty("dimension", entity.dimension());
        object.add("effects", effects(entity.effects()));
        object.add("equipment", equipment(entity.equipment()));
        object.addProperty("head_yaw", entity.headYaw());
        object.addProperty("height", entity.height());
        if (entity.leashDestinationNetworkId() == null) {
            object.add("leash_destination_network_id", JsonNull.INSTANCE);
        } else {
            object.addProperty("leash_destination_network_id", entity.leashDestinationNetworkId());
        }
        object.add("metadata", metadata(entity.metadata()));
        object.addProperty("network_id", entity.networkId());
        object.addProperty("on_ground", entity.onGround());
        object.add("passengers", GSON.toJsonTree(entity.passengers()));
        if (entity.playerInfo() == null) {
            object.add("player_info", JsonNull.INSTANCE);
        } else {
            JsonObject info = new JsonObject();
            info.add("actions", GSON.toJsonTree(entity.playerInfo().actions()));
            info.add("packet", encodedValue(entity.playerInfo().packet()));
            object.add("player_info", info);
        }
        object.add("position", vector(entity.position()));
        JsonArray rotation = new JsonArray();
        rotation.add(entity.yaw());
        rotation.add(entity.pitch());
        object.add("rotation", rotation);
        object.addProperty("spawn_data", entity.spawnData());
        object.addProperty("subject", entity.subject());
        object.addProperty("type_id", entity.typeId());
        if (entity.uuid() == null) {
            object.add("uuid", JsonNull.INSTANCE);
        } else {
            object.addProperty("uuid", entity.uuid().toString());
        }
        object.add("velocity", vector(entity.velocity()));
        object.addProperty("width", entity.width());
        return writeBlob(object);
    }

    private static String snapshotSha256(
        String dimension,
        Map<SectionIdentity, String> sections,
        Map<String, String> entities,
        Map<BlockEntityIdentity, String> blockEntities,
        SceneJob.SourceReplay source
    ) {
        JsonObject object = new JsonObject();
        JsonObject blockEntityValues = new JsonObject();
        new TreeMap<>(blockEntities).forEach((key, digest) ->
            blockEntityValues.addProperty(key.logicalKey(), digest)
        );
        object.add("block_entities", blockEntityValues);
        object.addProperty("dimension", dimension);
        JsonObject entityValues = new JsonObject();
        new TreeMap<>(entities).forEach((instanceId, digest) -> {
            String prefix = source.segmentId() + ":";
            String logicalId = instanceId.startsWith(prefix) ? instanceId.substring(prefix.length()) : instanceId;
            entityValues.addProperty(logicalId, digest);
        });
        object.add("entities", entityValues);
        JsonObject sectionValues = new JsonObject();
        new TreeMap<>(sections).forEach((key, digest) ->
            sectionValues.addProperty(key.logicalKey(), digest)
        );
        object.add("sections", sectionValues);
        return Hashing.sha256(canonicalJson(object).getBytes(StandardCharsets.UTF_8));
    }

    private String writeBlockEntityBlob(SceneSnapshot.BlockEntity blockEntity) throws IOException {
        JsonObject object = new JsonObject();
        object.addProperty("dimension", blockEntity.dimension());
        object.add("nbt", encodedValue(blockEntity.nbt()));
        JsonArray position = new JsonArray();
        position.add(blockEntity.x());
        position.add(blockEntity.y());
        position.add(blockEntity.z());
        object.add("position", position);
        object.addProperty("type_id", blockEntity.typeId());
        return writeBlob(object);
    }

    private String writeBlob(JsonObject value) throws IOException {
        byte[] canonical = canonicalJson(value).getBytes(StandardCharsets.UTF_8);
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
        Path target = blobs.resolve(digest + ".zlib");
        Path temporary = blobs.resolve("." + digest + ".tmp-" + UUID.randomUUID());
        Files.write(temporary, compressed, StandardOpenOption.CREATE_NEW);
        forceFile(temporary);
        moveAtomically(temporary, target);
        blobSizes.put(digest, (long) compressed.length);
        return digest;
    }

    private static JsonObject blockState(SceneEvent.BlockState state) {
        JsonObject object = new JsonObject();
        object.addProperty("name", state.name());
        JsonObject properties = new JsonObject();
        new TreeMap<>(state.properties()).forEach(properties::addProperty);
        object.add("properties", properties);
        return object;
    }

    private static JsonObject encodedValue(SceneEvent.EncodedValue value) {
        JsonObject object = new JsonObject();
        object.addProperty("base64", value.base64());
        object.addProperty("codec_id", value.codecId());
        object.addProperty("encoding", value.encoding());
        object.addProperty("logical_type", value.logicalType());
        return object;
    }

    private static JsonObject metadata(Map<Integer, SceneEvent.EncodedValue> values) {
        TreeMap<String, SceneEvent.EncodedValue> sorted = new TreeMap<>();
        values.forEach((key, value) -> sorted.put(Integer.toString(key), value));
        JsonObject object = new JsonObject();
        sorted.forEach((key, value) -> object.add(key, encodedValue(value)));
        return object;
    }

    private static JsonArray equipment(Map<String, SceneEvent.EquipmentValue> values) {
        JsonArray array = new JsonArray();
        new TreeMap<>(values).forEach((slot, value) -> {
            JsonObject object = new JsonObject();
            object.addProperty("count", value.count());
            object.add("encoded_stack", encodedValue(value.encodedStack()));
            object.addProperty("item", value.item());
            object.addProperty("slot", slot);
            array.add(object);
        });
        return array;
    }

    private static JsonArray attributes(Map<String, SceneEvent.AttributeValue> values) {
        JsonArray array = new JsonArray();
        new TreeMap<>(values).forEach((name, value) -> {
            JsonObject object = new JsonObject();
            object.addProperty("attribute", name);
            object.addProperty("base", value.base());
            JsonArray modifiers = new JsonArray();
            value.modifiers().stream().sorted(Comparator.comparing(SceneEvent.AttributeModifierValue::id))
                .forEach(modifier -> {
                    JsonObject encoded = new JsonObject();
                    encoded.addProperty("amount", modifier.amount());
                    encoded.addProperty("id", modifier.id());
                    encoded.addProperty("operation", modifier.operation());
                    modifiers.add(encoded);
                });
            object.add("modifiers", modifiers);
            array.add(object);
        });
        return array;
    }

    private static JsonArray effects(Map<String, SceneSnapshot.Effect> values) {
        JsonArray array = new JsonArray();
        new TreeMap<>(values).forEach((name, value) -> {
            JsonObject object = new JsonObject();
            object.addProperty("amplifier", value.amplifier());
            object.addProperty("ambient", value.ambient());
            object.addProperty("blend", value.blend());
            object.addProperty("duration_ticks", value.durationTicks());
            object.addProperty("effect", name);
            object.addProperty("show_icon", value.showIcon());
            object.addProperty("visible", value.visible());
            array.add(object);
        });
        return array;
    }

    private static JsonArray vector(SceneEvent.Vec3 value) {
        if (value == null) {
            return null;
        }
        JsonArray array = new JsonArray();
        array.add(value.x());
        array.add(value.y());
        array.add(value.z());
        return array;
    }

    private static String instanceId(SceneJob.SourceReplay source, SceneSnapshot.Entity entity) {
        return source.segmentId() + ":" + entity.networkId() + ":" + entity.generation();
    }

    private static <K extends Comparable<? super K>> List<K> sortedDifference(
        Map<K, String> previous,
        Map<K, String> current
    ) {
        List<K> values = new ArrayList<>();
        for (K key : previous.keySet()) {
            if (!current.containsKey(key)) {
                values.add(key);
            }
        }
        values.sort(Comparator.naturalOrder());
        return values;
    }

    private static String canonicalJson(JsonElement element) {
        return GSON.toJson(canonicalize(element));
    }

    private static JsonElement canonicalize(JsonElement element) {
        if (element.isJsonObject()) {
            JsonObject sorted = new JsonObject();
            TreeMap<String, JsonElement> values = new TreeMap<>();
            element.getAsJsonObject().entrySet().forEach(entry -> values.put(entry.getKey(), entry.getValue()));
            values.forEach((key, value) -> sorted.add(key, canonicalize(value)));
            return sorted;
        }
        if (element.isJsonArray()) {
            JsonArray array = new JsonArray();
            element.getAsJsonArray().forEach(value -> array.add(canonicalize(value)));
            return array;
        }
        return element.deepCopy();
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

    private record SectionIdentity(String dimension, int x, int y, int z)
        implements Comparable<SectionIdentity> {
        private String logicalKey() {
            return dimension + ":" + x + ":" + y + ":" + z;
        }

        @Override
        public int compareTo(SectionIdentity other) {
            int compared = dimension.compareTo(other.dimension);
            if (compared != 0) {
                return compared;
            }
            compared = Integer.compare(x, other.x);
            if (compared != 0) {
                return compared;
            }
            compared = Integer.compare(y, other.y);
            return compared != 0 ? compared : Integer.compare(z, other.z);
        }
    }

    private record BlockEntityIdentity(String dimension, int x, int y, int z)
        implements Comparable<BlockEntityIdentity> {
        private String logicalKey() {
            return dimension + ":" + x + ":" + y + ":" + z;
        }

        @Override
        public int compareTo(BlockEntityIdentity other) {
            int compared = dimension.compareTo(other.dimension);
            if (compared != 0) {
                return compared;
            }
            compared = Integer.compare(x, other.x);
            if (compared != 0) {
                return compared;
            }
            compared = Integer.compare(y, other.y);
            return compared != 0 ? compared : Integer.compare(z, other.z);
        }
    }

    /** Opaque canonical snapshot prepared for one active replay segment. */
    public static final class PreparedSnapshot {
        private final UUID segmentId;
        private final Map<SectionIdentity, String> sections;
        private final Map<String, String> entities;
        private final Map<BlockEntityIdentity, String> blockEntities;
        private final String sha256;

        private PreparedSnapshot(
            UUID segmentId,
            Map<SectionIdentity, String> sections,
            Map<String, String> entities,
            Map<BlockEntityIdentity, String> blockEntities,
            String sha256
        ) {
            this.segmentId = segmentId;
            // Preserve reducer insertion order because it also fixes deterministic change ordering.
            this.sections = Collections.unmodifiableMap(new LinkedHashMap<>(sections));
            this.entities = Collections.unmodifiableMap(new LinkedHashMap<>(entities));
            this.blockEntities = Collections.unmodifiableMap(new LinkedHashMap<>(blockEntities));
            this.sha256 = sha256;
        }

        public String sha256() {
            return sha256;
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
