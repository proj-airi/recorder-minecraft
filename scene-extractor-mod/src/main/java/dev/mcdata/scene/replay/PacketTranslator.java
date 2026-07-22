package dev.mcdata.scene.replay;

import com.mojang.datafixers.util.Pair;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneReducer;
import dev.mcdata.scene.core.TimelineMarker;
import dev.mcdata.scene.mixin.MoveEntityPacketAccessor;
import dev.mcdata.scene.mixin.RotateHeadPacketAccessor;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.Registry;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.NbtIo;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.common.ClientboundCustomPayloadPacket;
import net.minecraft.network.protocol.game.ClientboundAddEntityPacket;
import net.minecraft.network.protocol.game.ClientboundBlockEntityDataPacket;
import net.minecraft.network.protocol.game.ClientboundBlockUpdatePacket;
import net.minecraft.network.protocol.game.ClientboundEntityPositionSyncPacket;
import net.minecraft.network.protocol.game.ClientboundForgetLevelChunkPacket;
import net.minecraft.network.protocol.game.ClientboundLevelChunkWithLightPacket;
import net.minecraft.network.protocol.game.ClientboundLoginPacket;
import net.minecraft.network.protocol.game.ClientboundMoveEntityPacket;
import net.minecraft.network.protocol.game.ClientboundMoveMinecartPacket;
import net.minecraft.network.protocol.game.ClientboundMoveVehiclePacket;
import net.minecraft.network.protocol.game.ClientboundPlayerPositionPacket;
import net.minecraft.network.protocol.game.ClientboundPlayerRotationPacket;
import net.minecraft.network.protocol.game.ClientboundPlayerInfoRemovePacket;
import net.minecraft.network.protocol.game.ClientboundPlayerInfoUpdatePacket;
import net.minecraft.network.protocol.game.ClientboundRemoveEntitiesPacket;
import net.minecraft.network.protocol.game.ClientboundRemoveMobEffectPacket;
import net.minecraft.network.protocol.game.ClientboundRespawnPacket;
import net.minecraft.network.protocol.game.ClientboundRotateHeadPacket;
import net.minecraft.network.protocol.game.ClientboundSectionBlocksUpdatePacket;
import net.minecraft.network.protocol.game.ClientboundSetEntityDataPacket;
import net.minecraft.network.protocol.game.ClientboundSetEntityLinkPacket;
import net.minecraft.network.protocol.game.ClientboundSetEntityMotionPacket;
import net.minecraft.network.protocol.game.ClientboundSetEquipmentPacket;
import net.minecraft.network.protocol.game.ClientboundSetPassengersPacket;
import net.minecraft.network.protocol.game.ClientboundTeleportEntityPacket;
import net.minecraft.network.protocol.game.ClientboundUpdateAttributesPacket;
import net.minecraft.network.protocol.game.ClientboundUpdateMobEffectPacket;
import net.minecraft.network.syncher.EntityDataSerializer;
import net.minecraft.network.syncher.EntityDataSerializers;
import net.minecraft.network.syncher.SynchedEntityData;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.effect.MobEffect;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.PositionMoveRotation;
import net.minecraft.world.entity.Relative;
import net.minecraft.world.entity.ai.attributes.Attribute;
import net.minecraft.world.entity.ai.attributes.AttributeModifier;
import net.minecraft.world.entity.vehicle.NewMinecartBehavior;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;
import net.minecraft.world.level.chunk.LevelChunkSection;
import net.minecraft.world.phys.Vec3;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/** Converts registry-bound Minecraft packet objects to stable scene values exactly once. */
public final class PacketTranslator {
    private final RegistryAccess registries;
    private final SceneReducer reducer;
    private final Map<BlockState, Set<String>> compatibleBlockEntityTypes = new HashMap<>();

    public PacketTranslator(RegistryAccess registries, SceneReducer reducer) {
        this.registries = registries;
        this.reducer = reducer;
    }

    public Translation translate(Packet<?> packet) throws IOException {
        if (packet instanceof ClientboundCustomPayloadPacket custom
            && custom.payload().type().id().equals(ReplayTimelinePayload.TYPE.id())) {
            TimelineValues timeline = timelineValues(custom.payload());
            UUID connection;
            try {
                connection = UUID.fromString(timeline.connectionId());
            } catch (IllegalArgumentException exception) {
                throw new IOException("timeline payload has an invalid connection UUID", exception);
            }
            return Translation.timeline(new TimelineMarker(
                timeline.sessionId(), connection, timeline.serverTick(), timeline.eventSequence()
            ));
        }
        if (packet instanceof ClientboundLoginPacket login) {
            return Translation.events(List.of(dimensionChange(login.commonPlayerSpawnInfo(), login.playerId())));
        }
        if (packet instanceof ClientboundRespawnPacket respawn) {
            return Translation.events(List.of(dimensionChange(respawn.commonPlayerSpawnInfo(), reducer.subjectEntityId())));
        }
        if (packet instanceof ClientboundLevelChunkWithLightPacket chunk) {
            return Translation.events(chunkSnapshot(chunk));
        }
        if (packet instanceof ClientboundForgetLevelChunkPacket forgotten) {
            return Translation.events(List.of(new SceneEvent.ChunkUnloaded(
                reducer.dimension(), forgotten.pos().x, forgotten.pos().z
            )));
        }
        if (packet instanceof ClientboundBlockUpdatePacket block) {
            return Translation.events(List.of(blockChange(block.getPos(), block.getBlockState())));
        }
        if (packet instanceof ClientboundSectionBlocksUpdatePacket section) {
            List<SceneEvent> events = new ArrayList<>();
            section.runUpdates((position, state) -> events.add(blockChange(position, state)));
            return Translation.events(events);
        }
        if (packet instanceof ClientboundBlockEntityDataPacket blockEntity) {
            return Translation.events(List.of(blockEntityChange(
                blockEntity.getPos(), blockEntity.getType(), blockEntity.getTag()
            )));
        }
        if (packet instanceof ClientboundAddEntityPacket add) {
            return Translation.events(List.of(entitySpawn(add)));
        }
        if (packet instanceof ClientboundRemoveEntitiesPacket remove) {
            return Translation.events(List.of(new SceneEvent.EntitiesRemoved(
                remove.getEntityIds().intStream().boxed().toList()
            )));
        }
        if (packet instanceof ClientboundMoveEntityPacket move) {
            int id = ((MoveEntityPacketAccessor) move).mcRecorderSceneEntityId();
            return Translation.events(List.of(new SceneEvent.EntityMoved(
                id,
                new SceneEvent.Vec3(move.getXa() / 4096.0, move.getYa() / 4096.0, move.getZa() / 4096.0),
                move.hasRotation() ? move.getYRot() : null,
                move.hasRotation() ? move.getXRot() : null,
                move.isOnGround()
            )));
        }
        if (packet instanceof ClientboundMoveMinecartPacket move) {
            List<NewMinecartBehavior.MinecartStep> steps = move.lerpSteps();
            if (steps.isEmpty()) {
                return Translation.events(List.of());
            }
            NewMinecartBehavior.MinecartStep finalStep = steps.get(steps.size() - 1);
            return Translation.events(List.of(new SceneEvent.EntityMinecartMoved(
                move.entityId(), vector(finalStep.position()), vector(finalStep.movement()),
                finalStep.yRot(), finalStep.xRot()
            )));
        }
        if (packet instanceof ClientboundMoveVehiclePacket move) {
            var vehicle = reducer.subjectRootVehicleId();
            if (vehicle.isEmpty()) {
                return Translation.events(List.of());
            }
            return Translation.events(List.of(new SceneEvent.EntityVehicleMoved(
                vehicle.getAsInt(), vector(move.position()), move.yRot(), move.xRot()
            )));
        }
        if (packet instanceof ClientboundPlayerRotationPacket rotation) {
            return Translation.events(List.of(new SceneEvent.EntityRotated(
                reducer.subjectEntityId(), rotation.yRot(), rotation.xRot()
            )));
        }
        if (packet instanceof ClientboundTeleportEntityPacket teleport) {
            return Translation.events(List.of(teleport(
                teleport.id(), teleport.change(), teleport.relatives(), teleport.onGround()
            )));
        }
        if (packet instanceof ClientboundEntityPositionSyncPacket sync) {
            return Translation.events(List.of(teleport(sync.id(), sync.values(), Set.of(), sync.onGround())));
        }
        if (packet instanceof ClientboundPlayerPositionPacket playerPosition) {
            return Translation.events(List.of(teleport(
                reducer.subjectEntityId(), playerPosition.change(), playerPosition.relatives(), false
            )));
        }
        if (packet instanceof ClientboundSetEntityMotionPacket motion) {
            return Translation.events(List.of(new SceneEvent.EntityVelocityChanged(
                motion.getId(), new SceneEvent.Vec3(motion.getXa(), motion.getYa(), motion.getZa())
            )));
        }
        if (packet instanceof ClientboundRotateHeadPacket rotate) {
            return Translation.events(List.of(new SceneEvent.EntityHeadRotated(
                ((RotateHeadPacketAccessor) rotate).mcRecorderSceneEntityId(), rotate.getYHeadRot()
            )));
        }
        if (packet instanceof ClientboundSetEntityDataPacket metadata) {
            Map<Integer, SceneEvent.EncodedValue> values = new LinkedHashMap<>();
            for (SynchedEntityData.DataValue<?> value : metadata.packedItems()) {
                values.put(value.id(), encodeMetadata(value));
            }
            return Translation.events(List.of(new SceneEvent.EntityMetadataChanged(metadata.id(), values)));
        }
        if (packet instanceof ClientboundSetEquipmentPacket equipment) {
            List<SceneEvent.EquipmentValue> values = new ArrayList<>();
            for (Pair<EquipmentSlot, ItemStack> value : equipment.getSlots()) {
                ItemStack stack = value.getSecond();
                values.add(new SceneEvent.EquipmentValue(
                    value.getFirst().getSerializedName(),
                    BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(),
                    stack.getCount(),
                    encodeStack(stack)
                ));
            }
            return Translation.events(List.of(new SceneEvent.EntityEquipmentChanged(equipment.getEntity(), values)));
        }
        if (packet instanceof ClientboundSetPassengersPacket passengers) {
            return Translation.events(List.of(new SceneEvent.EntityPassengersChanged(
                passengers.getVehicle(), java.util.Arrays.stream(passengers.getPassengers()).boxed().toList()
            )));
        }
        if (packet instanceof ClientboundSetEntityLinkPacket link) {
            return Translation.events(List.of(new SceneEvent.EntityLeashChanged(
                link.getSourceId(), link.getDestId()
            )));
        }
        if (packet instanceof ClientboundUpdateAttributesPacket attributes) {
            return Translation.events(List.of(attributes(attributes)));
        }
        if (packet instanceof ClientboundUpdateMobEffectPacket effect) {
            return Translation.events(List.of(new SceneEvent.EntityEffectChanged(
                effect.getEntityId(), holderName(effect.getEffect()), effect.getEffectAmplifier(),
                effect.getEffectDurationTicks(), effect.isEffectAmbient(), effect.isEffectVisible(),
                effect.effectShowsIcon(), effect.shouldBlend()
            )));
        }
        if (packet instanceof ClientboundRemoveMobEffectPacket effect) {
            return Translation.events(List.of(new SceneEvent.EntityEffectRemoved(
                effect.entityId(), holderName(effect.effect())
            )));
        }
        if (packet instanceof ClientboundPlayerInfoUpdatePacket playerInfo) {
            return Translation.events(List.of(new SceneEvent.PlayerInfoChanged(
                playerInfo.entries().stream().map(ClientboundPlayerInfoUpdatePacket.Entry::profileId).toList(),
                playerInfo.actions().stream().map(Enum::name).collect(java.util.stream.Collectors.toUnmodifiableSet()),
                encodeValue(
                    "minecraft:player_info_update_packet", -1,
                    ClientboundPlayerInfoUpdatePacket.STREAM_CODEC, playerInfo
                )
            )));
        }
        if (packet instanceof ClientboundPlayerInfoRemovePacket playerInfo) {
            return Translation.events(List.of(new SceneEvent.PlayerInfoRemoved(playerInfo.profileIds())));
        }
        return Translation.unhandled(packet.type().id().toString());
    }

    public SceneEvent.EntitySpawned createLocalPlayer(
        int entityId,
        UUID uuid,
        double x,
        double y,
        double z,
        float pitch,
        float yaw,
        float headYaw,
        Vec3 velocity
    ) {
        return new SceneEvent.EntitySpawned(
            entityId, uuid, EntityType.getKey(EntityType.PLAYER).toString(),
            vector(x, y, z), vector(velocity), new SceneEvent.Rotation(yaw, pitch, headYaw),
            EntityType.PLAYER.getWidth(), EntityType.PLAYER.getHeight(), 0, true
        );
    }

    private SceneEvent.DimensionChanged dimensionChange(
        net.minecraft.network.protocol.game.CommonPlayerSpawnInfo spawn,
        int subjectEntityId
    ) {
        var type = spawn.dimensionType().value();
        return new SceneEvent.DimensionChanged(
            spawn.dimension().location().toString(), subjectEntityId, type.minY(), type.height()
        );
    }

    private List<SceneEvent> chunkSnapshot(ClientboundLevelChunkWithLightPacket chunk) throws IOException {
        FriendlyByteBuf buffer = chunk.getChunkData().getReadBuffer();
        int sectionCount = reducer.height() / 16;
        int firstSectionY = Math.floorDiv(reducer.minY(), 16);
        Registry<net.minecraft.world.level.biome.Biome> biomes = registries.lookupOrThrow(Registries.BIOME);
        List<SceneEvent> events = new ArrayList<>(sectionCount + 9);
        events.add(new SceneEvent.ChunkReplaced(reducer.dimension(), chunk.getX(), chunk.getZ()));
        try {
            for (int index = 0; index < sectionCount; index++) {
                LevelChunkSection section = new LevelChunkSection(biomes);
                section.read(buffer);
                events.add(new SceneEvent.SectionLoaded(
                    reducer.dimension(), chunk.getX(), chunk.getZ(), firstSectionY + index, sectionSnapshot(section)
                ));
            }
            if (buffer.isReadable()) {
                throw new IOException("chunk section buffer has " + buffer.readableBytes() + " trailing bytes");
            }
        } catch (RuntimeException exception) {
            throw new IOException("failed to decode chunk sections at " + chunk.getX() + "," + chunk.getZ(), exception);
        } finally {
            buffer.release();
        }
        try {
            chunk.getChunkData().getBlockEntitiesTagsConsumer(chunk.getX(), chunk.getZ()).accept(
                (position, type, tag) -> {
                    try {
                        events.add(blockEntityChange(position, type, tag));
                    } catch (IOException exception) {
                        throw new BlockEntityEncodingException(exception);
                    }
                }
            );
        } catch (BlockEntityEncodingException exception) {
            throw (IOException) exception.getCause();
        }
        return events;
    }

    private SceneEvent.SectionSnapshot sectionSnapshot(LevelChunkSection section) {
        List<SceneEvent.BlockState> palette = new ArrayList<>();
        Map<SceneEvent.BlockState, Integer> paletteIndices = new LinkedHashMap<>();
        int[] indices = new int[4096];
        int offset = 0;
        for (int y = 0; y < 16; y++) {
            for (int z = 0; z < 16; z++) {
                for (int x = 0; x < 16; x++) {
                    SceneEvent.BlockState state = blockState(section.getBlockState(x, y, z));
                    int paletteIndex = paletteIndices.computeIfAbsent(state, ignored -> {
                        palette.add(state);
                        return palette.size() - 1;
                    });
                    indices[offset++] = paletteIndex;
                }
            }
        }
        return new SceneEvent.SectionSnapshot(palette, indices);
    }

    private SceneEvent.BlockChanged blockChange(BlockPos position, BlockState state) {
        return new SceneEvent.BlockChanged(
            reducer.dimension(), position.getX(), position.getY(), position.getZ(), blockState(state)
        );
    }

    private SceneEvent.BlockEntityChanged blockEntityChange(
        BlockPos position,
        BlockEntityType<?> type,
        CompoundTag tag
    ) throws IOException {
        return new SceneEvent.BlockEntityChanged(
            reducer.dimension(), position.getX(), position.getY(), position.getZ(),
            BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(type).toString(), encodeNbt(tag)
        );
    }

    private SceneEvent.EntitySpawned entitySpawn(ClientboundAddEntityPacket packet) {
        EntityType<?> type = packet.getType();
        return new SceneEvent.EntitySpawned(
            packet.getId(), packet.getUUID(), EntityType.getKey(type).toString(),
            vector(packet.getX(), packet.getY(), packet.getZ()),
            vector(packet.getXa(), packet.getYa(), packet.getZa()),
            new SceneEvent.Rotation(packet.getYRot(), packet.getXRot(), packet.getYHeadRot()),
            type.getWidth(), type.getHeight(), packet.getData(), packet.getId() == reducer.subjectEntityId()
        );
    }

    private SceneEvent.EntityTeleported teleport(
        int entityId,
        PositionMoveRotation change,
        Set<Relative> relatives,
        boolean onGround
    ) {
        return new SceneEvent.EntityTeleported(
            entityId, vector(change.position()), vector(change.deltaMovement()), change.yRot(), change.xRot(),
            relatives.stream().map(Enum::name).collect(java.util.stream.Collectors.toUnmodifiableSet()), onGround
        );
    }

    private SceneEvent.EntityAttributesChanged attributes(ClientboundUpdateAttributesPacket packet) {
        List<SceneEvent.AttributeValue> values = new ArrayList<>();
        for (ClientboundUpdateAttributesPacket.AttributeSnapshot snapshot : packet.getValues()) {
            List<SceneEvent.AttributeModifierValue> modifiers = snapshot.modifiers().stream()
                .map(modifier -> new SceneEvent.AttributeModifierValue(
                    modifier.id().toString(), modifier.amount(), modifier.operation().getSerializedName()
                ))
                .toList();
            values.add(new SceneEvent.AttributeValue(holderName(snapshot.attribute()), snapshot.base(), modifiers));
        }
        return new SceneEvent.EntityAttributesChanged(packet.getEntityId(), values);
    }

    private SceneEvent.BlockState blockState(BlockState state) {
        Map<String, String> properties = new java.util.TreeMap<>();
        for (Map.Entry<Property<?>, Comparable<?>> value : state.getValues().entrySet()) {
            properties.put(value.getKey().getName(), propertyName(value.getKey(), value.getValue()));
        }
        Block block = state.getBlock();
        Set<String> blockEntityTypes = compatibleBlockEntityTypes.computeIfAbsent(state, candidate ->
            BuiltInRegistries.BLOCK_ENTITY_TYPE.stream()
                .filter(type -> type.isValid(candidate))
                .map(type -> BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(type).toString())
                .collect(java.util.stream.Collectors.toUnmodifiableSet())
        );
        return new SceneEvent.BlockState(
            BuiltInRegistries.BLOCK.getKey(block).toString(), properties, blockEntityTypes
        );
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static String propertyName(Property property, Comparable value) {
        return property.getName(value);
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private SceneEvent.EncodedValue encodeMetadata(SynchedEntityData.DataValue<?> value) {
        EntityDataSerializer serializer = value.serializer();
        int id = EntityDataSerializers.getSerializedId(serializer);
        return encodeValue("minecraft:entity_metadata", id, serializer.codec(), value.value());
    }

    private SceneEvent.EncodedValue encodeStack(ItemStack stack) {
        return encodeValue("minecraft:item_stack", -1, ItemStack.OPTIONAL_STREAM_CODEC, stack);
    }

    private <T> SceneEvent.EncodedValue encodeValue(
        String type,
        int codecId,
        net.minecraft.network.codec.StreamCodec<? super RegistryFriendlyByteBuf, T> codec,
        T value
    ) {
        ByteBuf bytes = Unpooled.buffer();
        RegistryFriendlyByteBuf buffer = new RegistryFriendlyByteBuf(bytes, registries);
        try {
            codec.encode(buffer, value);
            byte[] encoded = new byte[buffer.readableBytes()];
            buffer.getBytes(buffer.readerIndex(), encoded);
            return new SceneEvent.EncodedValue(
                type, "minecraft_registry_packet_base64", codecId, Base64.getEncoder().encodeToString(encoded)
            );
        } finally {
            buffer.release();
        }
    }

    private static SceneEvent.EncodedValue encodeNbt(CompoundTag tag) throws IOException {
        if (tag == null) {
            return new SceneEvent.EncodedValue(
                "minecraft:compound_nbt", "absent", -1, ""
            );
        }
        try (ByteArrayOutputStream bytes = new ByteArrayOutputStream();
             DataOutputStream output = new DataOutputStream(bytes)) {
            NbtIo.write(tag, output);
            output.flush();
            return new SceneEvent.EncodedValue(
                "minecraft:compound_nbt", "minecraft_nbt_binary_base64", -1,
                Base64.getEncoder().encodeToString(bytes.toByteArray())
            );
        }
    }

    private static String holderName(Holder<?> holder) {
        return holder.unwrapKey().map(key -> key.location().toString()).orElseGet(holder::getRegisteredName);
    }

    private static TimelineValues timelineValues(
        net.minecraft.network.protocol.common.custom.CustomPacketPayload payload
    ) throws IOException {
        if (payload instanceof ReplayTimelinePayload timeline) {
            return new TimelineValues(
                timeline.sessionId(), timeline.connectionId(), timeline.serverTick(), timeline.eventSequence()
            );
        }
        try {
            Class<?> type = payload.getClass();
            String sessionId = (String) type.getMethod("sessionId").invoke(payload);
            String connectionId = (String) type.getMethod("connectionId").invoke(payload);
            long serverTick = ((Number) type.getMethod("serverTick").invoke(payload)).longValue();
            long eventSequence = ((Number) type.getMethod("eventSequence").invoke(payload)).longValue();
            return new TimelineValues(sessionId, connectionId, serverTick, eventSequence);
        } catch (ReflectiveOperationException | ClassCastException exception) {
            throw new IOException(
                "mc_recorder:timeline payload is registered to an incompatible runtime type "
                    + payload.getClass().getName(),
                exception
            );
        }
    }

    private static SceneEvent.Vec3 vector(Vec3 value) {
        return vector(value.x, value.y, value.z);
    }

    private static SceneEvent.Vec3 vector(double x, double y, double z) {
        return new SceneEvent.Vec3(x, y, z);
    }

    public record Translation(List<SceneEvent> events, Optional<TimelineMarker> timeline, String unhandledType) {
        public Translation {
            events = List.copyOf(events);
        }

        public static Translation events(List<SceneEvent> events) {
            return new Translation(events, Optional.empty(), null);
        }

        public static Translation timeline(TimelineMarker timeline) {
            return new Translation(List.of(), Optional.of(timeline), null);
        }

        public static Translation unhandled(String type) {
            return new Translation(List.of(), Optional.empty(), type);
        }
    }

    private record TimelineValues(String sessionId, String connectionId, long serverTick, long eventSequence) { }

    private static final class BlockEntityEncodingException extends RuntimeException {
        private BlockEntityEncodingException(IOException cause) {
            super(cause);
        }
    }
}
