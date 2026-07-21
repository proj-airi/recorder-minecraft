package dev.mcdata.recorder.capture;

import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.game.ClientboundForgetLevelChunkPacket;
import net.minecraft.network.protocol.game.ClientboundMoveEntityPacket;
import net.minecraft.network.protocol.game.ClientboundMoveMinecartPacket;
import net.minecraft.network.protocol.game.ClientboundPlayerPositionPacket;
import net.minecraft.network.protocol.game.ClientboundSetEntityMotionPacket;
import net.minecraft.network.protocol.game.ClientboundTeleportEntityPacket;

import java.util.Set;

/**
 * Capture policy required to reconstruct client-visible scene state without simulating entity
 * physics. Mixins adapt Arcade's optimizer and Flashback writer to this deterministic policy.
 */
public final class ReplayScenePacketContract {
    public static final String FLASHBACK_CAPTURE_CONTRACT = "client_visible_scene_v1";

    private static final Set<Class<?>> FLASHBACK_WRITER_PACKETS = Set.of(
        ClientboundForgetLevelChunkPacket.class,
        ClientboundPlayerPositionPacket.class,
        ClientboundMoveMinecartPacket.class
    );

    private ReplayScenePacketContract() {
    }

    public static boolean shouldIgnoreAtFlashbackWriter(
        boolean upstreamDecision,
        Class<?> packetType
    ) {
        return upstreamDecision && !FLASHBACK_WRITER_PACKETS.contains(packetType);
    }

    public static boolean shouldIgnoreAtOptimizer(
        boolean upstreamDecision,
        Packet<?> packet
    ) {
        if (!upstreamDecision) {
            return false;
        }
        return !(
            packet instanceof ClientboundMoveEntityPacket ||
                packet instanceof ClientboundTeleportEntityPacket ||
                packet instanceof ClientboundSetEntityMotionPacket
        );
    }
}
