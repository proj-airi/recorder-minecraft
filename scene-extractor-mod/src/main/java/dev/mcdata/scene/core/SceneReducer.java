package dev.mcdata.scene.core;

import dev.mcdata.scene.job.SceneJob;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Single-owner deterministic scene state machine. It contains no filesystem, replay, server, or
 * codec effects; adapters translate packets into {@link SceneEvent} values before applying them.
 */
public final class SceneReducer {
    private final SceneJob job;
    private final Map<Integer, MutableEntity> entities = new HashMap<>();
    private final Set<SectionKey> sections = new HashSet<>();
    private final Map<Integer, Integer> spawnGenerations = new HashMap<>();

    private String dimension;
    private int subjectEntityId = -1;
    private int minY;
    private int height;

    public SceneReducer(SceneJob job) {
        this.job = job;
    }

    public void beginSegment() {
        entities.clear();
        sections.clear();
        dimension = null;
        subjectEntityId = -1;
        minY = 0;
        height = 0;
    }

    public void apply(SceneEvent event) {
        if (event instanceof SceneEvent.DimensionChanged changed) {
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
            return;
        }
        requireDimension();
        if (event instanceof SceneEvent.SectionLoaded loaded) {
            requireCurrentDimension(loaded.dimension());
            requireSectionY(loaded.sectionY());
            sections.add(new SectionKey(loaded.chunkX(), loaded.sectionY(), loaded.chunkZ()));
            return;
        }
        if (event instanceof SceneEvent.ChunkUnloaded unloaded) {
            requireCurrentDimension(unloaded.dimension());
            sections.removeIf(key -> key.chunkX == unloaded.chunkX() && key.chunkZ == unloaded.chunkZ());
            return;
        }
        if (event instanceof SceneEvent.BlockChanged changed) {
            requireCurrentDimension(changed.dimension());
            int sectionY = Math.floorDiv(changed.y(), 16);
            SectionKey key = new SectionKey(Math.floorDiv(changed.x(), 16), sectionY, Math.floorDiv(changed.z(), 16));
            if (!sections.contains(key)) {
                throw new SceneStateException("block update targets an unknown client-visible section " + key);
            }
            return;
        }
        if (event instanceof SceneEvent.BlockEntityChanged changed) {
            requireCurrentDimension(changed.dimension());
            return;
        }
        if (event instanceof SceneEvent.EntitySpawned spawned) {
            int generation = spawnGenerations.merge(spawned.entityId(), 1, Integer::sum);
            entities.put(spawned.entityId(), new MutableEntity(
                spawned.position(), spawned.velocity(), spawned.rotation().yaw(), spawned.rotation().pitch(),
                spawned.rotation().headYaw(), generation
            ));
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
            return;
        }
        if (event instanceof SceneEvent.EntityTeleported teleported) {
            MutableEntity entity = requireEntity(teleported.entityId());
            entity.position = resolveVector(entity.position, teleported.position(), teleported.relatives(), "X", "Y", "Z");
            entity.velocity = resolveVector(
                entity.velocity, teleported.velocity(), teleported.relatives(), "DELTA_X", "DELTA_Y", "DELTA_Z"
            );
            entity.yaw = teleported.relatives().contains("Y_ROT") ? entity.yaw + teleported.yaw() : teleported.yaw();
            entity.pitch = teleported.relatives().contains("X_ROT") ? entity.pitch + teleported.pitch() : teleported.pitch();
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
            requireEntity(changed.entityId());
            return;
        }
        if (event instanceof SceneEvent.EntityEquipmentChanged changed) {
            requireEntity(changed.entityId());
            return;
        }
        if (event instanceof SceneEvent.EntityPassengersChanged changed) {
            requireEntity(changed.vehicleId());
            for (int passenger : changed.passengers()) {
                requireEntity(passenger);
            }
            return;
        }
        if (event instanceof SceneEvent.EntityLeashChanged changed) {
            requireEntity(changed.sourceId());
            if (changed.destinationId() != 0) {
                requireEntity(changed.destinationId());
            }
            return;
        }
        if (event instanceof SceneEvent.EntityAttributesChanged changed) {
            requireEntity(changed.entityId());
            return;
        }
        if (event instanceof SceneEvent.EntityEffectChanged changed) {
            requireEntity(changed.entityId());
            return;
        }
        if (event instanceof SceneEvent.EntityEffectRemoved changed) {
            requireEntity(changed.entityId());
            return;
        }
        if (event instanceof SceneEvent.PlayerInfoChanged || event instanceof SceneEvent.PlayerInfoRemoved) {
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

    public int subjectEntityId() {
        return subjectEntityId;
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

    private static final class MutableEntity {
        private SceneEvent.Vec3 position;
        private SceneEvent.Vec3 velocity;
        private float yaw;
        private float pitch;
        private float headYaw;
        @SuppressWarnings("unused")
        private final int generation;

        private MutableEntity(
            SceneEvent.Vec3 position,
            SceneEvent.Vec3 velocity,
            float yaw,
            float pitch,
            float headYaw,
            int generation
        ) {
            this.position = position;
            this.velocity = velocity;
            this.yaw = yaw;
            this.pitch = pitch;
            this.headYaw = headYaw;
            this.generation = generation;
        }
    }

    public static final class SceneStateException extends IllegalStateException {
        public SceneStateException(String message) {
            super(message);
        }
    }
}
