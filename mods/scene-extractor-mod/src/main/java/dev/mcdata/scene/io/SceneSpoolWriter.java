package dev.mcdata.scene.io;

import dev.minerec.artifacts.v1.AttributeModifier;
import dev.minerec.artifacts.v1.BlockEntityBlob;
import dev.minerec.artifacts.v1.BlockEntityChange;
import dev.minerec.artifacts.v1.BlockState;
import dev.minerec.artifacts.v1.Bounds;
import dev.minerec.artifacts.v1.EncodedValue;
import dev.minerec.artifacts.v1.EntityAttribute;
import dev.minerec.artifacts.v1.EntityBlob;
import dev.minerec.artifacts.v1.EntityChange;
import dev.minerec.artifacts.v1.EntityEffect;
import dev.minerec.artifacts.v1.EntityMetadata;
import dev.minerec.artifacts.v1.Equipment;
import dev.minerec.artifacts.v1.PlayerListInfo;
import dev.minerec.artifacts.v1.Rotation;
import dev.minerec.artifacts.v1.SceneBlobPayload;
import dev.minerec.artifacts.v1.SceneChangeRecord;
import dev.minerec.artifacts.v1.SceneFrameRecord;
import dev.minerec.artifacts.v1.SectionBlob;
import dev.minerec.artifacts.v1.SectionChange;
import dev.minerec.artifacts.v1.SegmentBegin;
import dev.minerec.artifacts.v1.Vector3;
import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneFrame;
import dev.mcdata.scene.core.SceneSnapshot;
import dev.mcdata.scene.job.SceneJob;
import com.google.protobuf.MessageOrBuilder;
import com.google.protobuf.util.JsonFormat;

import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
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

/** Effect boundary that durably writes generated, length-delimited protobuf scene records. */
public final class SceneSpoolWriter implements AutoCloseable {
    private static final JsonFormat.Printer JSON = JsonFormat.printer().omittingInsignificantWhitespace();
    private final Path finalOutput;
    private final Path temporaryOutput;
    private final Path blobs;
    private final Path framesPath;
    private final Path changesPath;
    private final OutputStream frames;
    private final OutputStream changes;
    private final Map<String, Long> blobSizes = new LinkedHashMap<>();
    private final Map<SectionIdentity, CachedSectionBlob> sectionBlobCache = new HashMap<>();
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
        temporaryOutput = finalOutput.resolveSibling("." + finalOutput.getFileName() + ".tmp-" + UUID.randomUUID());
        Files.createDirectory(temporaryOutput);
        blobs = Files.createDirectory(temporaryOutput.resolve("blobs"));
        framesPath = temporaryOutput.resolve("frames.jsonl");
        changesPath = temporaryOutput.resolve("changes.jsonl");
        frames = new BufferedOutputStream(Files.newOutputStream(framesPath, StandardOpenOption.CREATE_NEW));
        changes = new BufferedOutputStream(Files.newOutputStream(changesPath, StandardOpenOption.CREATE_NEW));
    }

    public void beginSegment(SceneJob.SourceReplay source) {
        ensureOpen();
        activeSegment = source;
        segmentBeginPending = true;
    }

    public PreparedSnapshot prepareSnapshot(SceneFrame frame, SceneSnapshot snapshot) throws IOException {
        ensureOpen();
        SceneJob.SourceReplay source = requireActiveSegment(frame);
        Map<SectionIdentity, String> currentSections = new LinkedHashMap<>();
        for (SceneSnapshot.Section section : snapshot.sections()) {
            SectionIdentity identity = new SectionIdentity(section.dimension(), section.x(), section.y(), section.z());
            SceneEvent.SectionSnapshot sectionSnapshot = section.snapshot();
            CachedSectionBlob cached = sectionBlobCache.get(identity);
            String digest;
            if (cached != null && cached.snapshot == sectionSnapshot) {
                digest = cached.digest;
            } else {
                digest = writeSectionBlob(sectionSnapshot);
                sectionBlobCache.put(identity, new CachedSectionBlob(sectionSnapshot, digest));
            }
            currentSections.put(identity, digest);
        }
        sectionBlobCache.keySet().retainAll(currentSections.keySet());
        Map<String, String> currentEntities = new LinkedHashMap<>();
        for (SceneSnapshot.Entity entity : snapshot.entities()) {
            currentEntities.put(instanceId(source, entity), writeEntityBlob(entity));
        }
        Map<BlockEntityIdentity, String> currentBlockEntities = new LinkedHashMap<>();
        for (SceneSnapshot.BlockEntity blockEntity : snapshot.blockEntities()) {
            BlockEntityIdentity identity = new BlockEntityIdentity(blockEntity.dimension(), blockEntity.x(), blockEntity.y(), blockEntity.z());
            currentBlockEntities.put(identity, writeBlockEntityBlob(blockEntity));
        }
        return new PreparedSnapshot(source.segmentId(), currentSections, currentEntities, currentBlockEntities,
            snapshotSha256(frame.dimension(), currentSections, currentEntities, currentBlockEntities, source));
    }

    public void writeSegmentBegin(SceneFrame frame) throws IOException {
        writeSegmentBegin(frame, requireActiveSegment(frame));
    }

    public void writeFrame(SceneFrame frame, SceneSnapshot snapshot) throws IOException {
        writeFrame(frame, prepareSnapshot(frame, snapshot));
    }

    public void writeFrame(SceneFrame frame, PreparedSnapshot snapshot) throws IOException {
        ensureOpen();
        SceneJob.SourceReplay source = requireActiveSegment(frame);
        if (!snapshot.segmentId.equals(source.segmentId())) {
            throw new IllegalStateException("prepared scene snapshot belongs to a different replay segment");
        }
        writeSegmentBegin(frame, source);
        String snapshotSha256 = writeSnapshotChanges(frame, source, snapshot);
        SceneFrameRecord value = SceneFrameRecord.newBuilder()
            .setServerTick(frame.globalTick())
            .setFrameId(frame.segmentId() + ":" + frame.eventSequence())
            .setReplayTick(frame.replayTick())
            .setDimension(frame.dimension())
            .setSubjectPosition(vector(frame.subjectPosition()))
            .setCoverageComplete(frame.complete())
            .setSegmentId(frame.segmentId().toString())
            .setSegmentOrdinal(frame.segmentOrdinal())
            .setEventSequence(frame.eventSequence())
            .setEntityCount(frame.entityCount())
            .setLoadedSectionCount(frame.loadedSectionCount())
            .setMetadataPolicy(SceneJob.METADATA_POLICY)
            .setSceneSnapshotSha256(snapshotSha256)
            .setScope(SceneJob.SCOPE)
            .setSubjectEntityId(frame.subjectEntityId())
            .build();
        writeJsonLine(frames, value);
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
        return new OutputStats(frameCount, changeCount, blobSizes.size(), blobSizes.values().stream().mapToLong(Long::longValue).sum(),
            Hashing.sha256(finalOutput.resolve("frames.jsonl")), Files.size(finalOutput.resolve("frames.jsonl")),
            Hashing.sha256(finalOutput.resolve("changes.jsonl")), Files.size(finalOutput.resolve("changes.jsonl")));
    }

    @Override
    public void close() throws IOException {
        if (closed) return;
        IOException failure = null;
        try { frames.close(); } catch (IOException exception) { failure = exception; }
        try { changes.close(); } catch (IOException exception) {
            if (failure == null) failure = exception; else failure.addSuppressed(exception);
        }
        closed = true;
        if (!committed) {
            try { deleteTemporaryOutput(); } catch (IOException exception) {
                if (failure == null) failure = exception; else failure.addSuppressed(exception);
            }
        }
        if (failure != null) throw failure;
    }

    private SceneJob.SourceReplay requireActiveSegment(SceneFrame frame) {
        if (activeSegment == null || !activeSegment.segmentId().equals(frame.segmentId())) {
            throw new IllegalStateException("scene frame does not belong to the active replay segment");
        }
        return activeSegment;
    }

    private SceneChangeRecord.Builder baseChange(SceneFrame frame, SceneJob.SourceReplay source) {
        return SceneChangeRecord.newBuilder().setReplayTick(frame.replayTick()).setSegmentId(source.segmentId().toString())
            .setSegmentOrdinal(source.segmentOrdinal()).setSequence(changeSequence++).setServerTick(frame.globalTick());
    }

    private void writeSegmentBegin(SceneFrame frame, SceneJob.SourceReplay source) throws IOException {
        if (!segmentBeginPending) return;
        writeChange(baseChange(frame, source).setSegmentBegin(SegmentBegin.newBuilder().setSha256(source.sha256()).setSizeBytes(source.sizeBytes())).build());
        segmentBeginPending = false;
    }

    private String writeSnapshotChanges(SceneFrame frame, SceneJob.SourceReplay source, PreparedSnapshot snapshot) throws IOException {
        for (SectionIdentity identity : sortedDifference(previousSections, snapshot.sections)) {
            writeChange(baseChange(frame, source).setSectionUnload(sectionChange(identity, null)).build());
        }
        for (Map.Entry<SectionIdentity, String> entry : snapshot.sections.entrySet()) {
            if (!entry.getValue().equals(previousSections.get(entry.getKey()))) {
                writeChange(baseChange(frame, source).setSectionSet(sectionChange(entry.getKey(), entry.getValue())).build());
            }
        }
        for (String instance : sortedDifference(previousEntities, snapshot.entities)) {
            writeChange(baseChange(frame, source).setEntityRemove(EntityChange.newBuilder().setInstanceId(instance)).build());
        }
        for (Map.Entry<String, String> entry : snapshot.entities.entrySet()) {
            if (!entry.getValue().equals(previousEntities.get(entry.getKey()))) {
                writeChange(baseChange(frame, source).setEntitySet(EntityChange.newBuilder().setInstanceId(entry.getKey()).setBlobSha256(entry.getValue())).build());
            }
        }
        for (BlockEntityIdentity identity : sortedDifference(previousBlockEntities, snapshot.blockEntities)) {
            writeChange(baseChange(frame, source).setBlockEntityRemove(blockChange(identity, null)).build());
        }
        for (Map.Entry<BlockEntityIdentity, String> entry : snapshot.blockEntities.entrySet()) {
            if (!entry.getValue().equals(previousBlockEntities.get(entry.getKey()))) {
                writeChange(baseChange(frame, source).setBlockEntitySet(blockChange(entry.getKey(), entry.getValue())).build());
            }
        }
        previousSections = Map.copyOf(snapshot.sections);
        previousEntities = Map.copyOf(snapshot.entities);
        previousBlockEntities = Map.copyOf(snapshot.blockEntities);
        return snapshot.sha256;
    }

    private static SectionChange.Builder sectionChange(SectionIdentity value, String digest) {
        SectionChange.Builder result = SectionChange.newBuilder().setDimension(value.dimension).setX(value.x).setY(value.y).setZ(value.z);
        if (digest != null) result.setBlobSha256(digest);
        return result;
    }

    private static BlockEntityChange.Builder blockChange(BlockEntityIdentity value, String digest) {
        BlockEntityChange.Builder result = BlockEntityChange.newBuilder().setDimension(value.dimension).setX(value.x).setY(value.y).setZ(value.z);
        if (digest != null) result.setBlobSha256(digest);
        return result;
    }

    private void writeChange(SceneChangeRecord value) throws IOException {
        writeJsonLine(changes, value);
        changeCount++;
    }

    private String writeSectionBlob(SceneEvent.SectionSnapshot snapshot) throws IOException {
        Map<String, SceneEvent.BlockState> unique = new TreeMap<>();
        List<String> keys = snapshot.palette().stream().map(SceneSpoolWriter::blockStateKey).toList();
        for (int index = 0; index < keys.size(); index++) unique.put(keys.get(index), snapshot.palette().get(index));
        Map<String, Integer> canonical = new HashMap<>();
        SectionBlob.Builder value = SectionBlob.newBuilder();
        for (Map.Entry<String, SceneEvent.BlockState> entry : unique.entrySet()) {
            canonical.put(entry.getKey(), value.getPaletteCount());
            BlockState.Builder state = BlockState.newBuilder().setName(entry.getValue().name());
            state.putAllProperties(new TreeMap<>(entry.getValue().properties()));
            value.addPalette(state);
        }
        ByteBuffer packed = ByteBuffer.allocate(4096 * 2).order(ByteOrder.LITTLE_ENDIAN);
        for (int sourceIndex : snapshot.indices()) packed.putShort((short) (int) canonical.get(keys.get(sourceIndex)));
        value.setIndicesLeU16(com.google.protobuf.ByteString.copyFrom(packed.array()));
        return writeBlob(SceneBlobPayload.newBuilder().setSection(value).build());
    }

    private String writeEntityBlob(SceneSnapshot.Entity entity) throws IOException {
        double halfWidth = entity.width() / 2.0;
        EntityBlob.Builder value = EntityBlob.newBuilder()
            .setBounds(Bounds.newBuilder().setMinX(entity.position().x() - halfWidth).setMinY(entity.position().y()).setMinZ(entity.position().z() - halfWidth)
                .setMaxX(entity.position().x() + halfWidth).setMaxY(entity.position().y() + entity.height()).setMaxZ(entity.position().z() + halfWidth))
            .setDimension(entity.dimension()).setNetworkId(entity.networkId()).setTypeId(entity.typeId())
            .setPosition(vector(entity.position())).setVelocity(vector(entity.velocity()))
            .setRotation(Rotation.newBuilder().setYaw(entity.yaw()).setPitch(entity.pitch()).setHeadYaw(entity.headYaw()))
            .setWidth(entity.width()).setHeight(entity.height()).setOnGround(entity.onGround()).setSubject(entity.subject()).setSpawnData(entity.spawnData())
            .addAllPassengers(entity.passengers());
        if (entity.uuid() != null) value.setUuid(entity.uuid().toString());
        if (entity.leashDestinationNetworkId() != null) value.setLeashDestinationNetworkId(entity.leashDestinationNetworkId());
        new TreeMap<>(entity.metadata()).forEach((index, encoded) -> value.addMetadata(EntityMetadata.newBuilder().setIndex(index).setValue(encoded(encoded))));
        new TreeMap<>(entity.equipment()).forEach((slot, equipment) -> value.addEquipment(Equipment.newBuilder().setSlot(slot).setItemId(equipment.item()).setCount(equipment.count()).setEncodedStack(encoded(equipment.encodedStack()))));
        new TreeMap<>(entity.attributes()).forEach((name, attribute) -> {
            EntityAttribute.Builder item = EntityAttribute.newBuilder().setAttributeId(name).setBase(attribute.base());
            attribute.modifiers().stream().sorted(Comparator.comparing(SceneEvent.AttributeModifierValue::id)).forEach(modifier ->
                item.addModifiers(AttributeModifier.newBuilder().setId(modifier.id()).setAmount(modifier.amount()).setOperation(modifier.operation())));
            value.addAttributes(item);
        });
        new TreeMap<>(entity.effects()).forEach((name, effect) -> value.addEffects(EntityEffect.newBuilder().setEffectId(name)
            .setAmplifier(effect.amplifier()).setDurationTicks(effect.durationTicks()).setAmbient(effect.ambient())
            .setVisible(effect.visible()).setShowIcon(effect.showIcon()).setBlend(effect.blend())));
        if (entity.playerInfo() != null) {
            value.setPlayerInfo(PlayerListInfo.newBuilder().addAllActions(entity.playerInfo().actions()).setPacket(encoded(entity.playerInfo().packet())));
        }
        return writeBlob(SceneBlobPayload.newBuilder().setEntity(value).build());
    }

    private String writeBlockEntityBlob(SceneSnapshot.BlockEntity value) throws IOException {
        return writeBlob(SceneBlobPayload.newBuilder().setBlockEntity(BlockEntityBlob.newBuilder()
            .setDimension(value.dimension()).setX(value.x()).setY(value.y()).setZ(value.z()).setTypeId(value.typeId()).setNbt(encoded(value.nbt()))).build());
    }

    private String writeBlob(SceneBlobPayload value) throws IOException {
        byte[] raw = JSON.print(value).getBytes(java.nio.charset.StandardCharsets.UTF_8);
        String digest = Hashing.sha256(raw);
        if (blobSizes.containsKey(digest)) return digest;
        byte[] compressed;
        try (ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DeflaterOutputStream output = new DeflaterOutputStream(bytes, new Deflater(6))) {
            output.write(raw); output.finish(); compressed = bytes.toByteArray();
        }
        Path target = blobs.resolve(digest + ".zlib");
        Path temporary = blobs.resolve("." + digest + ".tmp-" + UUID.randomUUID());
        Files.write(temporary, compressed, StandardOpenOption.CREATE_NEW);
        moveAtomically(temporary, target);
        blobSizes.put(digest, (long) compressed.length);
        return digest;
    }

    private static EncodedValue encoded(SceneEvent.EncodedValue value) {
        return EncodedValue.newBuilder().setLogicalType(value.logicalType()).setCodecId(value.codecId()).setEncoding(value.encoding())
            .setData(com.google.protobuf.ByteString.copyFrom(Base64.getDecoder().decode(value.base64()))).build();
    }

    private static Vector3 vector(SceneEvent.Vec3 value) {
        return Vector3.newBuilder().setX(value.x()).setY(value.y()).setZ(value.z()).build();
    }

    private static String blockStateKey(SceneEvent.BlockState value) {
        StringBuilder key = new StringBuilder(value.name());
        new TreeMap<>(value.properties()).forEach((name, item) -> key.append('\u0000').append(name).append('=').append(item));
        return key.toString();
    }

    private static String snapshotSha256(String dimension, Map<SectionIdentity, String> sections, Map<String, String> entities,
                                         Map<BlockEntityIdentity, String> blockEntities, SceneJob.SourceReplay source) {
        try {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            DataOutputStream output = new DataOutputStream(bytes);
            output.writeUTF(dimension);
            for (Map.Entry<SectionIdentity, String> entry : new TreeMap<>(sections).entrySet()) { output.writeUTF(entry.getKey().logicalKey()); output.writeUTF(entry.getValue()); }
            for (Map.Entry<String, String> entry : new TreeMap<>(entities).entrySet()) {
                String prefix = source.segmentId() + ":";
                output.writeUTF(entry.getKey().startsWith(prefix) ? entry.getKey().substring(prefix.length()) : entry.getKey()); output.writeUTF(entry.getValue());
            }
            for (Map.Entry<BlockEntityIdentity, String> entry : new TreeMap<>(blockEntities).entrySet()) { output.writeUTF(entry.getKey().logicalKey()); output.writeUTF(entry.getValue()); }
            output.flush();
            return Hashing.sha256(bytes.toByteArray());
        } catch (IOException exception) { throw new AssertionError(exception); }
    }

    private static String instanceId(SceneJob.SourceReplay source, SceneSnapshot.Entity entity) {
        return source.segmentId() + ":" + entity.networkId() + ":" + entity.generation();
    }

    private static void writeJsonLine(OutputStream output, MessageOrBuilder value) throws IOException {
        output.write(JSON.print(value).getBytes(java.nio.charset.StandardCharsets.UTF_8));
        output.write('\n');
    }

    private static <K extends Comparable<? super K>> List<K> sortedDifference(Map<K, String> previous, Map<K, String> current) {
        List<K> values = new ArrayList<>();
        for (K key : previous.keySet()) if (!current.containsKey(key)) values.add(key);
        values.sort(Comparator.naturalOrder());
        return values;
    }

    private void ensureOpen() { if (closed) throw new IllegalStateException("scene spool writer is closed"); }
    private static void forceFile(Path path) throws IOException { try (FileChannel channel = FileChannel.open(path, StandardOpenOption.WRITE)) { channel.force(true); } }
    private static void forceDirectory(Path path) throws IOException { try (FileChannel channel = FileChannel.open(path, StandardOpenOption.READ)) { channel.force(true); } }
    private static void moveAtomically(Path source, Path target) throws IOException {
        try { Files.move(source, target, java.nio.file.StandardCopyOption.ATOMIC_MOVE); }
        catch (AtomicMoveNotSupportedException exception) { throw new IOException("scene job filesystem does not support required atomic publication", exception); }
    }
    private void deleteTemporaryOutput() throws IOException {
        if (!Files.exists(temporaryOutput)) return;
        try (var entries = Files.walk(temporaryOutput)) { for (Path path : entries.sorted(Comparator.reverseOrder()).toList()) Files.deleteIfExists(path); }
    }

    private record SectionIdentity(String dimension, int x, int y, int z) implements Comparable<SectionIdentity> {
        private String logicalKey() { return dimension + ":" + x + ":" + y + ":" + z; }
        public int compareTo(SectionIdentity other) { int c = dimension.compareTo(other.dimension); if (c != 0) return c; c = Integer.compare(x, other.x); if (c != 0) return c; c = Integer.compare(y, other.y); return c != 0 ? c : Integer.compare(z, other.z); }
    }
    private record CachedSectionBlob(SceneEvent.SectionSnapshot snapshot, String digest) { }
    private record BlockEntityIdentity(String dimension, int x, int y, int z) implements Comparable<BlockEntityIdentity> {
        private String logicalKey() { return dimension + ":" + x + ":" + y + ":" + z; }
        public int compareTo(BlockEntityIdentity other) { int c = dimension.compareTo(other.dimension); if (c != 0) return c; c = Integer.compare(x, other.x); if (c != 0) return c; c = Integer.compare(y, other.y); return c != 0 ? c : Integer.compare(z, other.z); }
    }

    public static final class PreparedSnapshot {
        private final UUID segmentId;
        private final Map<SectionIdentity, String> sections;
        private final Map<String, String> entities;
        private final Map<BlockEntityIdentity, String> blockEntities;
        private final String sha256;
        private PreparedSnapshot(UUID segmentId, Map<SectionIdentity, String> sections, Map<String, String> entities, Map<BlockEntityIdentity, String> blockEntities, String sha256) {
            this.segmentId = segmentId; this.sections = Collections.unmodifiableMap(new LinkedHashMap<>(sections));
            this.entities = Collections.unmodifiableMap(new LinkedHashMap<>(entities)); this.blockEntities = Collections.unmodifiableMap(new LinkedHashMap<>(blockEntities)); this.sha256 = sha256;
        }
        public String sha256() { return sha256; }
    }

    public record OutputStats(long frameCount, long changeCount, long blobCount, long blobBytes, String framesSha256,
                              long framesBytes, String changesSha256, long changesBytes) { }
}
