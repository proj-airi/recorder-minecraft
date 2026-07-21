package dev.mcdata.scene.replay;

import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.ResourceLocation;

public record ReplayTimelinePayload(
    String sessionId,
    String connectionId,
    long serverTick,
    long eventSequence
) implements CustomPacketPayload {
    public static final Type<ReplayTimelinePayload> TYPE = new Type<>(
        ResourceLocation.fromNamespaceAndPath("mc_recorder", "timeline")
    );

    public static final StreamCodec<RegistryFriendlyByteBuf, ReplayTimelinePayload> STREAM_CODEC =
        new StreamCodec<>() {
            @Override
            public ReplayTimelinePayload decode(RegistryFriendlyByteBuf buffer) {
                return new ReplayTimelinePayload(
                    buffer.readUtf(128), buffer.readUtf(64), buffer.readVarLong(), buffer.readVarLong()
                );
            }

            @Override
            public void encode(RegistryFriendlyByteBuf buffer, ReplayTimelinePayload payload) {
                buffer.writeUtf(payload.sessionId(), 128);
                buffer.writeUtf(payload.connectionId(), 64);
                buffer.writeVarLong(payload.serverTick());
                buffer.writeVarLong(payload.eventSequence());
            }
        };

    @Override
    public Type<? extends CustomPacketPayload> type() {
        return TYPE;
    }
}
