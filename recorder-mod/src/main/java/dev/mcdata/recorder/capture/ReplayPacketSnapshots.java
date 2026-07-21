package dev.mcdata.recorder.capture;

import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.game.ClientboundSetPlayerInventoryPacket;

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
        return packet;
    }
}
