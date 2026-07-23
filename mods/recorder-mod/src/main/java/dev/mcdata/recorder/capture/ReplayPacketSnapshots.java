package dev.mcdata.recorder.capture;

import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.game.ClientboundMoveMinecartPacket;
import net.minecraft.network.protocol.game.ClientboundPlayerPositionPacket;
import net.minecraft.network.protocol.game.ClientboundSetPlayerInventoryPacket;
import net.minecraft.network.protocol.game.ClientboundTeleportEntityPacket;

import java.util.List;
import java.util.Set;

/**
 * Freezes packet values that ServerReplay otherwise retains by mutable reference until its
 * asynchronous writer encodes them.
 */
public final class ReplayPacketSnapshots {
    public static final String HOTBAR_SNAPSHOT_CONTRACT = "item_stack_copy_v1";

    private ReplayPacketSnapshots() {
    }

    public static Packet<?> freezeMutableValues(Packet<?> packet) {
        if (packet instanceof ClientboundSetPlayerInventoryPacket inventory) {
            return new ClientboundSetPlayerInventoryPacket(
                inventory.slot(),
                inventory.contents().copy()
            );
        }
        if (packet instanceof ClientboundMoveMinecartPacket minecart) {
            return new ClientboundMoveMinecartPacket(
                minecart.entityId(),
                List.copyOf(minecart.lerpSteps())
            );
        }
        if (packet instanceof ClientboundPlayerPositionPacket playerPosition) {
            return new ClientboundPlayerPositionPacket(
                playerPosition.id(),
                playerPosition.change(),
                Set.copyOf(playerPosition.relatives())
            );
        }
        if (packet instanceof ClientboundTeleportEntityPacket teleport) {
            return new ClientboundTeleportEntityPacket(
                teleport.id(),
                teleport.change(),
                Set.copyOf(teleport.relatives()),
                teleport.onGround()
            );
        }
        return packet;
    }
}
