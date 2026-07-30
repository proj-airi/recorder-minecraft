package dev.mcdata.scene.core;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Immutable client-visible state captured at one timeline marker. */
public record SceneSnapshot(
    List<Section> sections,
    List<Entity> entities,
    List<BlockEntity> blockEntities
) {
    public SceneSnapshot {
        sections = List.copyOf(sections);
        entities = List.copyOf(entities);
        blockEntities = List.copyOf(blockEntities);
    }

    public record Section(
        String dimension,
        int x,
        int y,
        int z,
        SceneEvent.SectionSnapshot snapshot
    ) { }

    public record PlayerInfo(
        List<String> actions,
        SceneEvent.EncodedValue packet
    ) {
        public PlayerInfo {
            actions = List.copyOf(actions);
        }
    }

    public record Effect(
        String effect,
        int amplifier,
        int durationTicks,
        boolean ambient,
        boolean visible,
        boolean showIcon,
        boolean blend
    ) { }

    public record Entity(
        int networkId,
        int generation,
        UUID uuid,
        String typeId,
        String dimension,
        SceneEvent.Vec3 position,
        SceneEvent.Vec3 velocity,
        float yaw,
        float pitch,
        float headYaw,
        double width,
        double height,
        int spawnData,
        boolean subject,
        boolean onGround,
        Map<Integer, SceneEvent.EncodedValue> metadata,
        Map<String, SceneEvent.EquipmentValue> equipment,
        List<Integer> passengers,
        Integer leashDestinationNetworkId,
        Map<String, SceneEvent.AttributeValue> attributes,
        Map<String, Effect> effects,
        PlayerInfo playerInfo
    ) {
        public Entity {
            metadata = Map.copyOf(metadata);
            equipment = Map.copyOf(equipment);
            passengers = List.copyOf(passengers);
            attributes = Map.copyOf(attributes);
            effects = Map.copyOf(effects);
        }
    }

    public record BlockEntity(
        String dimension,
        int x,
        int y,
        int z,
        String typeId,
        SceneEvent.EncodedValue nbt
    ) { }
}
