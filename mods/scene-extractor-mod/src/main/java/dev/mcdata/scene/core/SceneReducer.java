package dev.mcdata.scene.core;

import dev.mcdata.scene.job.SceneJob;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.OptionalInt;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;

/**
 * Single-owner deterministic scene state machine. It contains no filesystem, replay, server, or
 * codec effects; adapters translate packets into {@link SceneEvent} values before applying them.
 */
public final class SceneReducer {
    private static final Comparator<SectionKey> SECTION_ORDER = Comparator
        .comparingInt(SectionKey::chunkX)
        .thenComparingInt(SectionKey::sectionY)
        .thenComparingInt(SectionKey::chunkZ);
    private static final Comparator<BlockPosition> BLOCK_POSITION_ORDER = Comparator
        .comparingInt(BlockPosition::x)
        .thenComparingInt(BlockPosition::y)
        .thenComparingInt(BlockPosition::z);

    private final SceneJob job;
    private final Map<Integer, MutableEntity> entities = new HashMap<>();
    private final Map<SectionKey, MutableSection> sections = new HashMap<>();
    private final Map<BlockPosition, SceneSnapshot.BlockEntity> blockEntities = new HashMap<>();
    private final Map<Integer, Integer> spawnGenerations = new HashMap<>();
    private final Map<UUID, SceneSnapshot.PlayerInfo> playerInfo = new HashMap<>();

    private String dimension;
    private int subjectEntityId = -1;
    private int minY;
    private int height;

    public SceneReducer(SceneJob job) {
        this.job = job;
    }

    public void beginSegment() {
        beginSnapshot();
    }

    /**
     * Replace the reducer state before consuming a complete Flashback snapshot.
     *
     * <p>Flashback may insert full snapshots between chunks of one replay. Those
     * snapshots replay entity-spawn packets for entities that are already live;
     * retaining {@code spawnGenerations} would therefore manufacture a new
     * logical entity lifetime at every forced snapshot.</p>
     */
    public void beginSnapshot() {
        entities.clear();
        sections.clear();
        blockEntities.clear();
        spawnGenerations.clear();
        playerInfo.clear();
        dimension = null;
        subjectEntityId = -1;
        minY = 0;
        height = 0;
    }

    public void apply(SceneEvent event) {
        if (event instanceof SceneEvent.DimensionChanged changed) {
            applyDimensionChange(changed);
            return;
        }
        requireDimension();
        if (event instanceof SceneEvent.ChunkReplaced replaced) {
            requireCurrentDimension(replaced.dimension());
            removeChunkState(replaced.chunkX(), replaced.chunkZ());
            return;
        }
        if (event instanceof SceneEvent.SectionLoaded loaded) {
            requireCurrentDimension(loaded.dimension());
            requireSectionY(loaded.sectionY());
            sections.put(
                new SectionKey(loaded.chunkX(), loaded.sectionY(), loaded.chunkZ()),
                new MutableSection(loaded.snapshot())
            );
            return;
        }
        if (event instanceof SceneEvent.ChunkUnloaded unloaded) {
            requireCurrentDimension(unloaded.dimension());
            removeChunkState(unloaded.chunkX(), unloaded.chunkZ());
            return;
        }
        if (event instanceof SceneEvent.BlockChanged changed) {
            applyBlockChange(changed);
            return;
        }
        if (event instanceof SceneEvent.BlockEntityChanged changed) {
            requireCurrentDimension(changed.dimension());
            BlockPosition position = new BlockPosition(changed.x(), changed.y(), changed.z());
            blockEntities.put(position, new SceneSnapshot.BlockEntity(
                dimension, changed.x(), changed.y(), changed.z(), changed.blockEntityType(), changed.nbt()
            ));
            return;
        }
        if (event instanceof SceneEvent.EntitySpawned spawned) {
            int generation = spawnGenerations.merge(spawned.entityId(), 1, Integer::sum);
            entities.put(spawned.entityId(), new MutableEntity(spawned, generation));
            if (spawned.subject()) {
                subjectEntityId = spawned.entityId();
            }
            return;
        }
        if (event instanceof SceneEvent.EntitiesRemoved removed) {
            for (int id : removed.entityIds()) {
                entities.remove(id);
            }
            return;
        }
        if (event instanceof SceneEvent.EntityMoved moved) {
            MutableEntity entity = requireEntity(moved.entityId());
            entity.position = add(entity.position, moved.delta());
            if (moved.yaw() != null) {
                entity.yaw = moved.yaw();
            }
            if (moved.pitch() != null) {
                entity.pitch = moved.pitch();
            }
            entity.onGround = moved.onGround();
            return;
        }
        if (event instanceof SceneEvent.EntityTeleported teleported) {
            MutableEntity entity = requireEntity(teleported.entityId());
            entity.position = resolveVector(
                entity.position, teleported.position(), teleported.relatives(), "X", "Y", "Z"
            );
            entity.velocity = resolveVector(
                entity.velocity, teleported.velocity(), teleported.relatives(),
                "DELTA_X", "DELTA_Y", "DELTA_Z"
            );
            entity.yaw = teleported.relatives().contains("Y_ROT")
                ? entity.yaw + teleported.yaw() : teleported.yaw();
            entity.pitch = teleported.relatives().contains("X_ROT")
                ? entity.pitch + teleported.pitch() : teleported.pitch();
            entity.onGround = teleported.onGround();
            return;
        }
        if (event instanceof SceneEvent.EntityMinecartMoved moved) {
            MutableEntity entity = requireEntity(moved.entityId());
            entity.position = moved.position();
            entity.velocity = moved.velocity();
            entity.yaw = moved.yaw();
            entity.pitch = moved.pitch();
            return;
        }
        if (event instanceof SceneEvent.EntityVehicleMoved moved) {
            MutableEntity entity = requireEntity(moved.entityId());
            entity.position = moved.position();
            entity.yaw = moved.yaw();
            entity.pitch = moved.pitch();
            return;
        }
        if (event instanceof SceneEvent.EntityRotated rotated) {
            MutableEntity entity = requireEntity(rotated.entityId());
            entity.yaw = rotated.yaw();
            entity.pitch = rotated.pitch();
            return;
        }
        if (event instanceof SceneEvent.EntityVelocityChanged changed) {
            requireEntity(changed.entityId()).velocity = changed.velocity();
            return;
        }
        if (event instanceof SceneEvent.EntityHeadRotated rotated) {
            requireEntity(rotated.entityId()).headYaw = rotated.headYaw();
            return;
        }
        if (event instanceof SceneEvent.EntityMetadataChanged changed) {
            requireEntity(changed.entityId()).metadata.putAll(changed.values());
            return;
        }
        if (event instanceof SceneEvent.EntityEquipmentChanged changed) {
            MutableEntity entity = requireEntity(changed.entityId());
            for (SceneEvent.EquipmentValue equipment : changed.equipment()) {
                entity.equipment.put(equipment.slot(), equipment);
            }
            return;
        }
        if (event instanceof SceneEvent.EntityPassengersChanged changed) {
            MutableEntity vehicle = requireEntity(changed.vehicleId());
            for (int passenger : changed.passengers()) {
                requireEntity(passenger);
            }
            vehicle.passengers = List.copyOf(changed.passengers());
            return;
        }
        if (event instanceof SceneEvent.EntityLeashChanged changed) {
            MutableEntity source = requireEntity(changed.sourceId());
            if (changed.destinationId() != 0) {
                requireEntity(changed.destinationId());
                source.leashDestination = changed.destinationId();
            } else {
                source.leashDestination = null;
            }
            return;
        }
        if (event instanceof SceneEvent.EntityAttributesChanged changed) {
            MutableEntity entity = requireEntity(changed.entityId());
            for (SceneEvent.AttributeValue attribute : changed.attributes()) {
                entity.attributes.put(attribute.attribute(), attribute);
            }
            return;
        }
        if (event instanceof SceneEvent.EntityEffectChanged changed) {
            MutableEntity entity = requireEntity(changed.entityId());
            entity.effects.put(changed.effect(), new SceneSnapshot.Effect(
                changed.effect(), changed.amplifier(), changed.durationTicks(), changed.ambient(),
                changed.visible(), changed.showIcon(), changed.blend()
            ));
            return;
        }
        if (event instanceof SceneEvent.EntityEffectRemoved changed) {
            requireEntity(changed.entityId()).effects.remove(changed.effect());
            return;
        }
        if (event instanceof SceneEvent.PlayerInfoChanged changed) {
            List<String> actions = changed.actions().stream().sorted().toList();
            SceneSnapshot.PlayerInfo value = new SceneSnapshot.PlayerInfo(actions, changed.packet());
            for (UUID profileId : changed.profileIds()) {
                playerInfo.put(profileId, value);
            }
            return;
        }
        if (event instanceof SceneEvent.PlayerInfoRemoved removed) {
            for (UUID profileId : removed.profileIds()) {
                playerInfo.remove(profileId);
            }
            return;
        }
        throw new SceneStateException("unhandled scene event " + event.getClass().getName());
    }

    public Optional<SceneFrame> frame(
        TimelineMarker marker,
        int replayTick,
        SceneJob.SourceReplay source
    ) {
        if (!marker.sessionId().equals(job.sessionId()) || !marker.connectionId().equals(job.connectionId())) {
            throw new SceneStateException("timeline payload identity does not match the scene job");
        }
        if (marker.globalTick() < job.globalStartTick() || marker.globalTick() > job.globalEndTick()) {
            return Optional.empty();
        }
        requireDimension();
        MutableEntity subject = entities.get(subjectEntityId);
        boolean complete = subject != null;
        return Optional.of(new SceneFrame(
            marker.globalTick(), replayTick, marker.eventSequence(), source.segmentId(), source.segmentOrdinal(),
            dimension, subjectEntityId, subject == null ? null : subject.position,
            sections.size(), entities.size(), complete
        ));
    }

    /** Replaces only authoritative subject pose fields at a selected dataset tick. */
    public void applyCanonicalSubjectPose(SceneJob.SubjectPose pose) {
        requireDimension();
        if (!pose.sessionId().equals(job.sessionId())
            || !pose.playerUuid().equals(job.playerUuid())
            || !pose.connectionId().equals(job.connectionId())) {
            throw new SceneStateException("canonical subject pose identity does not match the scene job");
        }
        if (pose.entityId() != subjectEntityId) {
            throw new SceneStateException(
                "canonical subject pose entity_id " + pose.entityId()
                    + " does not match replay subject " + subjectEntityId
            );
        }
        if (!pose.dimension().equals(dimension)) {
            throw new SceneStateException(
                "canonical subject pose dimension " + pose.dimension()
                    + " does not match replay dimension " + dimension
            );
        }
        MutableEntity subject = requireEntity(subjectEntityId);
        if (!subject.subject || !subject.uuid.equals(job.playerUuid())) {
            throw new SceneStateException("replay subject identity does not match the scene job");
        }
        subject.position = pose.position();
        subject.velocity = pose.velocity();
        subject.yaw = pose.yaw();
        subject.pitch = pose.pitch();
        subject.headYaw = pose.headYaw();
        subject.onGround = pose.onGround();
    }

    public SceneSnapshot snapshot() {
        requireDimension();
        List<SceneSnapshot.Section> sectionValues = sections.entrySet().stream()
            .sorted(Map.Entry.comparingByKey(SECTION_ORDER))
            .map(entry -> new SceneSnapshot.Section(
                dimension, entry.getKey().chunkX, entry.getKey().sectionY, entry.getKey().chunkZ,
                entry.getValue().snapshot()
            ))
            .toList();
        List<SceneSnapshot.Entity> entityValues = entities.entrySet().stream()
            .sorted(Map.Entry.comparingByKey())
            .map(entry -> entry.getValue().snapshot(entry.getKey(), dimension, playerInfo.get(entry.getValue().uuid)))
            .toList();
        List<SceneSnapshot.BlockEntity> blockEntityValues = blockEntities.entrySet().stream()
            .sorted(Map.Entry.comparingByKey(BLOCK_POSITION_ORDER))
            .map(Map.Entry::getValue)
            .toList();
        return new SceneSnapshot(sectionValues, entityValues, blockEntityValues);
    }

    public int subjectEntityId() {
        return subjectEntityId;
    }

    public boolean hasEntity(int entityId) {
        return entities.containsKey(entityId);
    }

    /** Returns the subject's outermost mounted vehicle, or empty when the subject is not mounted. */
    public OptionalInt subjectRootVehicleId() {
        int current = subjectEntityId;
        if (!entities.containsKey(current)) {
            return OptionalInt.empty();
        }
        boolean mounted = false;
        Set<Integer> visited = new java.util.HashSet<>();
        visited.add(current);
        while (true) {
            Integer parent = null;
            for (Map.Entry<Integer, MutableEntity> candidate : entities.entrySet()) {
                if (!candidate.getValue().passengers.contains(current)) {
                    continue;
                }
                if (parent != null) {
                    throw new SceneStateException("subject is attached to multiple client-visible vehicles");
                }
                parent = candidate.getKey();
            }
            if (parent == null) {
                return mounted ? OptionalInt.of(current) : OptionalInt.empty();
            }
            if (!visited.add(parent)) {
                throw new SceneStateException("client-visible passenger graph contains a cycle");
            }
            mounted = true;
            current = parent;
        }
    }

    public String dimension() {
        requireDimension();
        return dimension;
    }

    public int minY() {
        requireDimension();
        return minY;
    }

    public int height() {
        requireDimension();
        return height;
    }

    private void applyDimensionChange(SceneEvent.DimensionChanged changed) {
        if (changed.height() <= 0 || changed.height() % 16 != 0 || changed.minY() % 16 != 0) {
            throw new SceneStateException("dimension height/minY is not section aligned");
        }
        MutableEntity existingSubject = entities.get(subjectEntityId);
        dimension = changed.dimension();
        subjectEntityId = changed.subjectEntityId();
        minY = changed.minY();
        height = changed.height();
        entities.clear();
        if (existingSubject != null) {
            entities.put(subjectEntityId, existingSubject);
        }
        sections.clear();
        blockEntities.clear();
    }

    private void applyBlockChange(SceneEvent.BlockChanged changed) {
        requireCurrentDimension(changed.dimension());
        int sectionY = Math.floorDiv(changed.y(), 16);
        SectionKey key = new SectionKey(
            Math.floorDiv(changed.x(), 16), sectionY, Math.floorDiv(changed.z(), 16)
        );
        MutableSection section = sections.get(key);
        if (section == null) {
            throw new SceneStateException("block update targets an unknown client-visible section " + key);
        }
        SceneEvent.BlockState previous = section.set(
            Math.floorMod(changed.x(), 16), Math.floorMod(changed.y(), 16),
            Math.floorMod(changed.z(), 16), changed.state()
        );
        BlockPosition position = new BlockPosition(changed.x(), changed.y(), changed.z());
        SceneSnapshot.BlockEntity existing = blockEntities.get(position);
        if (existing != null && (
            !previous.name().equals(changed.state().name())
                || !changed.state().compatibleBlockEntityTypes().contains(existing.typeId())
        )) {
            blockEntities.remove(position);
        }
    }

    private void removeChunkState(int chunkX, int chunkZ) {
        sections.keySet().removeIf(key -> key.chunkX == chunkX && key.chunkZ == chunkZ);
        blockEntities.keySet().removeIf(position ->
            Math.floorDiv(position.x, 16) == chunkX
                && Math.floorDiv(position.z, 16) == chunkZ
        );
    }

    private void requireDimension() {
        if (dimension == null) {
            throw new SceneStateException("scene packet arrived before login/respawn dimension state");
        }
    }

    private void requireCurrentDimension(String value) {
        if (!dimension.equals(value)) {
            throw new SceneStateException("event dimension " + value + " does not match current " + dimension);
        }
    }

    private void requireSectionY(int sectionY) {
        int first = Math.floorDiv(minY, 16);
        int count = height / 16;
        if (sectionY < first || sectionY >= first + count) {
            throw new SceneStateException("section Y lies outside current dimension: " + sectionY);
        }
    }

    private MutableEntity requireEntity(int id) {
        MutableEntity entity = entities.get(id);
        if (entity == null) {
            throw new SceneStateException("entity update targets unknown client-visible entity " + id);
        }
        return entity;
    }

    private static SceneEvent.Vec3 add(SceneEvent.Vec3 left, SceneEvent.Vec3 right) {
        return new SceneEvent.Vec3(left.x() + right.x(), left.y() + right.y(), left.z() + right.z());
    }

    private static SceneEvent.Vec3 resolveVector(
        SceneEvent.Vec3 previous,
        SceneEvent.Vec3 update,
        Set<String> relatives,
        String xFlag,
        String yFlag,
        String zFlag
    ) {
        return new SceneEvent.Vec3(
            relatives.contains(xFlag) ? previous.x() + update.x() : update.x(),
            relatives.contains(yFlag) ? previous.y() + update.y() : update.y(),
            relatives.contains(zFlag) ? previous.z() + update.z() : update.z()
        );
    }

    private record SectionKey(int chunkX, int sectionY, int chunkZ) { }

    private record BlockPosition(int x, int y, int z) { }

    private static final class MutableSection {
        private final List<SceneEvent.BlockState> palette;
        private final int[] indices;
        private SceneEvent.SectionSnapshot cachedSnapshot;

        private MutableSection(SceneEvent.SectionSnapshot snapshot) {
            this.palette = new ArrayList<>(snapshot.palette());
            this.indices = snapshot.indices();
            this.cachedSnapshot = snapshot;
        }

        private SceneEvent.BlockState set(int x, int y, int z, SceneEvent.BlockState state) {
            int offset = y * 256 + z * 16 + x;
            SceneEvent.BlockState previous = palette.get(indices[offset]);
            if (previous.equals(state)) {
                return previous;
            }
            int paletteIndex = palette.indexOf(state);
            if (paletteIndex < 0) {
                paletteIndex = palette.size();
                palette.add(state);
            }
            indices[offset] = paletteIndex;
            cachedSnapshot = null;
            return previous;
        }

        private SceneEvent.SectionSnapshot snapshot() {
            if (cachedSnapshot == null) {
                cachedSnapshot = new SceneEvent.SectionSnapshot(palette, indices);
            }
            return cachedSnapshot;
        }
    }

    private static final class MutableEntity {
        private final UUID uuid;
        private final String typeId;
        private final double width;
        private final double height;
        private final int spawnData;
        private final boolean subject;
        private final int generation;
        private final Map<Integer, SceneEvent.EncodedValue> metadata = new TreeMap<>();
        private final Map<String, SceneEvent.EquipmentValue> equipment = new TreeMap<>();
        private final Map<String, SceneEvent.AttributeValue> attributes = new TreeMap<>();
        private final Map<String, SceneSnapshot.Effect> effects = new TreeMap<>();
        private SceneEvent.Vec3 position;
        private SceneEvent.Vec3 velocity;
        private float yaw;
        private float pitch;
        private float headYaw;
        private boolean onGround;
        private List<Integer> passengers = List.of();
        private Integer leashDestination;

        private MutableEntity(SceneEvent.EntitySpawned spawned, int generation) {
            this.uuid = spawned.uuid();
            this.typeId = spawned.entityType();
            this.width = spawned.width();
            this.height = spawned.height();
            this.spawnData = spawned.spawnData();
            this.subject = spawned.subject();
            this.generation = generation;
            this.position = spawned.position();
            this.velocity = spawned.velocity();
            this.yaw = spawned.rotation().yaw();
            this.pitch = spawned.rotation().pitch();
            this.headYaw = spawned.rotation().headYaw();
        }

        private SceneSnapshot.Entity snapshot(
            int networkId,
            String dimension,
            SceneSnapshot.PlayerInfo info
        ) {
            return new SceneSnapshot.Entity(
                networkId, generation, uuid, typeId, dimension, position, velocity,
                yaw, pitch, headYaw, width, height, spawnData, subject, onGround,
                metadata, equipment, passengers, leashDestination, attributes, effects, info
            );
        }
    }

    public static final class SceneStateException extends IllegalStateException {
        public SceneStateException(String message) {
            super(message);
        }
    }
}
