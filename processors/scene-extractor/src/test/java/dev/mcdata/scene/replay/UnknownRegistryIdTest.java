package dev.mcdata.scene.replay;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import net.minecraft.DetectedVersion;
import net.minecraft.SharedConstants;
import net.minecraft.core.BlockPos;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.VarInt;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.game.ClientGamePacketListener;
import net.minecraft.network.protocol.game.GameProtocols;
import net.minecraft.network.protocol.game.ClientboundAddEntityPacket;
import net.minecraft.network.protocol.game.ClientboundBlockEntityDataPacket;
import net.minecraft.network.protocol.game.ClientboundBlockUpdatePacket;
import net.minecraft.network.protocol.game.ClientboundSetEntityDataPacket;
import net.minecraft.network.syncher.EntityDataAccessor;
import net.minecraft.network.syncher.EntityDataSerializers;
import net.minecraft.network.syncher.SynchedEntityData;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.server.Bootstrap;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.Vec3;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * The replay validator accepts unknown mods because content from a mod cannot decode silently.
 * These tests pin that assumption: each encodes a valid vanilla value, replaces its registry ID
 * with the first ID after the vanilla registry, and expects extraction to reject it.
 */
final class UnknownRegistryIdTest {
    private static RegistryAccess registries;

    @BeforeAll
    static void bootstrapMinecraftRegistries() {
        SharedConstants.setVersion(DetectedVersion.BUILT_IN);
        Bootstrap.bootStrap();
        registries = RegistryAccess.fromRegistryOfRegistries(BuiltInRegistries.REGISTRY);
    }

    @Test
    void rejectsABlockStateFromAContentMod() {
        ClientboundBlockUpdatePacket packet = new ClientboundBlockUpdatePacket(BlockPos.ZERO, Blocks.STONE.defaultBlockState());
        // BlockPos is one long; the block state ID follows.
        requireRejected(ClientboundBlockUpdatePacket.STREAM_CODEC, packet, Long.BYTES, Block.BLOCK_STATE_REGISTRY.size());
    }

    @Test
    void rejectsAnEntityTypeFromAContentMod() {
        ClientboundAddEntityPacket packet = new ClientboundAddEntityPacket(
            1, UUID.randomUUID(), 0, 64, 0, 0, 0, EntityType.COW, 0, Vec3.ZERO, 0
        );
        StreamCodec<ByteBuf, Packet<? super ClientGamePacketListener>> codec = GameProtocols.CLIENTBOUND_TEMPLATE
            .bind(RegistryFriendlyByteBuf.decorator(registries)).codec();
        ByteBuf vanilla = Unpooled.buffer();
        codec.encode(vanilla, packet);
        // The packet ID VarInt, entity ID 1 as a one-byte VarInt, and a 16-byte UUID precede the type.
        int typeOffset = VarInt.getByteSize(new FriendlyByteBuf(vanilla.duplicate()).readVarInt()) + 1 + 16;
        ByteBuf modded = replaceVarInt(vanilla, typeOffset, BuiltInRegistries.ENTITY_TYPE.size());

        assertDoesNotThrow(() -> FlashbackSceneExtractor.requireKnownEntityType(vanilla));
        // The entity type registry is defaulted, so the vanilla codec turns the unknown ID into a pig.
        ClientboundAddEntityPacket decoded = (ClientboundAddEntityPacket) codec.decode(modded.duplicate());
        assertEquals(EntityType.PIG, decoded.getType());
        assertThrows(IllegalStateException.class, () -> FlashbackSceneExtractor.requireKnownEntityType(modded));
    }

    @Test
    void rejectsAnItemFromAContentMod() {
        // Count 1 is a one-byte VarInt; the item ID follows.
        requireRejected(ItemStack.OPTIONAL_STREAM_CODEC, new ItemStack(Items.STONE), 1, BuiltInRegistries.ITEM.size());
    }

    @Test
    void rejectsABlockEntityTypeFromAContentMod() {
        // The packet constructor is private, so write its fields: the position as one long, the
        // block entity type ID, and the tag.
        RegistryFriendlyByteBuf encoded = buffer(Unpooled.buffer());
        encoded.writeBlockPos(BlockPos.ZERO);
        encoded.writeVarInt(BuiltInRegistries.BLOCK_ENTITY_TYPE.getId(BlockEntityType.CHEST));
        encoded.writeNbt(new CompoundTag());
        requireRejected(
            ClientboundBlockEntityDataPacket.STREAM_CODEC, encoded, Long.BYTES, BuiltInRegistries.BLOCK_ENTITY_TYPE.size()
        );
    }

    @Test
    void rejectsAnEntityDataSerializerFromAContentMod() {
        ClientboundSetEntityDataPacket packet = new ClientboundSetEntityDataPacket(1, List.of(
            SynchedEntityData.DataValue.create(new EntityDataAccessor<>(0, EntityDataSerializers.BYTE), (byte) 0)
        ));
        // Entity ID 1 is a one-byte VarInt, then the one-byte data index; the serializer ID follows.
        // NOTICE: Vanilla registers fewer than 128 serializers, so 127 is past the vanilla range.
        requireRejected(ClientboundSetEntityDataPacket.STREAM_CODEC, packet, 2, 127);
    }

    private static <T> void requireRejected(
        StreamCodec<? super RegistryFriendlyByteBuf, T> codec,
        T value,
        int idOffset,
        int unknownId
    ) {
        RegistryFriendlyByteBuf encoded = buffer(Unpooled.buffer());
        codec.encode(encoded, value);
        requireRejected(codec, encoded, idOffset, unknownId);
    }

    private static <T> void requireRejected(
        StreamCodec<? super RegistryFriendlyByteBuf, T> codec,
        ByteBuf encoded,
        int idOffset,
        int unknownId
    ) {
        byte[] bytes = new byte[encoded.readableBytes()];
        encoded.readBytes(bytes);

        RegistryFriendlyByteBuf vanilla = buffer(Unpooled.wrappedBuffer(bytes));
        assertDoesNotThrow(() -> codec.decode(vanilla));
        assertFalse(vanilla.isReadable());

        ByteBuf modded = replaceVarInt(Unpooled.wrappedBuffer(bytes), idOffset, unknownId);
        assertThrows(RuntimeException.class, () -> codec.decode(buffer(modded)));
    }

    /** Copies an encoded value with the VarInt at {@code offset} replaced. */
    private static ByteBuf replaceVarInt(ByteBuf encoded, int offset, int value) {
        FriendlyByteBuf original = new FriendlyByteBuf(encoded.duplicate());
        ByteBuf result = Unpooled.buffer();
        result.writeBytes(original, offset);
        original.readVarInt();
        new FriendlyByteBuf(result).writeVarInt(value);
        result.writeBytes(original);
        return result;
    }

    private static RegistryFriendlyByteBuf buffer(ByteBuf bytes) {
        return new RegistryFriendlyByteBuf(bytes, registries);
    }
}
