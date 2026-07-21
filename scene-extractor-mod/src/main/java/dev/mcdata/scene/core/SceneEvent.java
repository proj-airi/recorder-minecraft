package dev.mcdata.scene.core;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Stable values translated from live Minecraft packet objects at the adapter boundary. */
public sealed interface SceneEvent permits
    SceneEvent.DimensionChanged,
    SceneEvent.SectionLoaded,
    SceneEvent.ChunkUnloaded,
    SceneEvent.BlockChanged,
    SceneEvent.BlockEntityChanged,
    SceneEvent.EntitySpawned,
    SceneEvent.EntitiesRemoved,
    SceneEvent.EntityMoved,
    SceneEvent.EntityTeleported,
    SceneEvent.EntityVelocityChanged,
    SceneEvent.EntityHeadRotated,
    SceneEvent.EntityMetadataChanged,
    SceneEvent.EntityEquipmentChanged,
    SceneEvent.EntityPassengersChanged,
    SceneEvent.EntityLeashChanged,
    SceneEvent.EntityAttributesChanged,
    SceneEvent.EntityEffectChanged,
    SceneEvent.EntityEffectRemoved,
    SceneEvent.PlayerInfoChanged,
    SceneEvent.PlayerInfoRemoved {

    record Vec3(double x, double y, double z) { }

    record Rotation(float yaw, float pitch, float headYaw) { }

    record BlockState(String name, Map<String, String> properties) {
        public BlockState {
            properties = Map.copyOf(properties);
        }
    }

    /** The 4096 indices use Minecraft's y-z-x section order. */
    record SectionSnapshot(List<BlockState> palette, int[] indices) {
        public SectionSnapshot {
            palette = List.copyOf(palette);
            indices = indices.clone();
            if (indices.length != 4096) {
                throw new IllegalArgumentException("section indices must contain exactly 4096 cells");
            }
        }

        @Override
        public int[] indices() {
            return indices.clone();
        }
    }

    /** Exact packet value bytes with the codec/registry identity needed to decode them. */
    record EncodedValue(String logicalType, String encoding, int codecId, String base64) { }

    record EquipmentValue(String slot, String item, int count, EncodedValue encodedStack) { }

    record AttributeModifierValue(String id, double amount, String operation) { }

    record AttributeValue(String attribute, double base, List<AttributeModifierValue> modifiers) {
        public AttributeValue {
            modifiers = List.copyOf(modifiers);
        }
    }

    record DimensionChanged(String dimension, int subjectEntityId, int minY, int height) implements SceneEvent { }

    record SectionLoaded(
        String dimension,
        int chunkX,
        int chunkZ,
        int sectionY,
        SectionSnapshot snapshot
    ) implements SceneEvent { }

    record ChunkUnloaded(String dimension, int chunkX, int chunkZ) implements SceneEvent { }

    record BlockChanged(String dimension, int x, int y, int z, BlockState state) implements SceneEvent { }

    record BlockEntityChanged(
        String dimension,
        int x,
        int y,
        int z,
        String blockEntityType,
        EncodedValue nbt
    ) implements SceneEvent { }

    record EntitySpawned(
        int entityId,
        UUID uuid,
        String entityType,
        Vec3 position,
        Vec3 velocity,
        Rotation rotation,
        double width,
        double height,
        int spawnData,
        boolean subject
    ) implements SceneEvent { }

    record EntitiesRemoved(List<Integer> entityIds) implements SceneEvent {
        public EntitiesRemoved {
            entityIds = List.copyOf(entityIds);
        }
    }

    record EntityMoved(
        int entityId,
        Vec3 delta,
        Float yaw,
        Float pitch,
        boolean onGround
    ) implements SceneEvent { }

    record EntityTeleported(
        int entityId,
        Vec3 position,
        Vec3 velocity,
        float yaw,
        float pitch,
        Set<String> relatives,
        boolean onGround
    ) implements SceneEvent {
        public EntityTeleported {
            relatives = Set.copyOf(relatives);
        }
    }

    record EntityVelocityChanged(int entityId, Vec3 velocity) implements SceneEvent { }

    record EntityHeadRotated(int entityId, float headYaw) implements SceneEvent { }

    record EntityMetadataChanged(int entityId, Map<Integer, EncodedValue> values) implements SceneEvent {
        public EntityMetadataChanged {
            values = Map.copyOf(values);
        }
    }

    record EntityEquipmentChanged(int entityId, List<EquipmentValue> equipment) implements SceneEvent {
        public EntityEquipmentChanged {
            equipment = List.copyOf(equipment);
        }
    }

    record EntityPassengersChanged(int vehicleId, List<Integer> passengers) implements SceneEvent {
        public EntityPassengersChanged {
            passengers = List.copyOf(passengers);
        }
    }

    record EntityLeashChanged(int sourceId, int destinationId) implements SceneEvent { }

    record EntityAttributesChanged(int entityId, List<AttributeValue> attributes) implements SceneEvent {
        public EntityAttributesChanged {
            attributes = List.copyOf(attributes);
        }
    }

    record EntityEffectChanged(
        int entityId,
        String effect,
        int amplifier,
        int durationTicks,
        boolean ambient,
        boolean visible,
        boolean showIcon,
        boolean blend
    ) implements SceneEvent { }

    record EntityEffectRemoved(int entityId, String effect) implements SceneEvent { }

    record PlayerInfoChanged(
        List<UUID> profileIds,
        Set<String> actions,
        EncodedValue packet
    ) implements SceneEvent {
        public PlayerInfoChanged {
            profileIds = List.copyOf(profileIds);
            actions = Set.copyOf(actions);
        }
    }

    record PlayerInfoRemoved(List<UUID> profileIds) implements SceneEvent {
        public PlayerInfoRemoved {
            profileIds = List.copyOf(profileIds);
        }
    }
}
