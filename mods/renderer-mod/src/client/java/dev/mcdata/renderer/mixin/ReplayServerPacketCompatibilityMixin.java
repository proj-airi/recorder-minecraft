package dev.mcdata.renderer.mixin;

import com.moulberry.flashback.exception.UnsupportedPacketException;
import com.moulberry.flashback.playback.ReplayServer;
import dev.mcdata.renderer.ReplayPacketCompatibility;
import net.minecraft.network.PacketListener;
import net.minecraft.network.protocol.Packet;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

/** Keeps automated RGB playback moving when Flashback explicitly rejects a decoded packet. */
@Mixin(value = ReplayServer.class, remap = false)
public abstract class ReplayServerPacketCompatibilityMixin {
    private static final Logger LOGGER = LoggerFactory.getLogger("mc-recorder-renderer");

    @Redirect(
        method = "handleGamePacket",
        at = @At(
            value = "INVOKE",
            target = "Lnet/minecraft/network/protocol/Packet;handle(Lnet/minecraft/network/PacketListener;)V",
            remap = true
        ),
        remap = false,
        require = 1,
        expect = 1,
        allow = 1
    )
    @SuppressWarnings({"rawtypes", "unchecked"})
    private void mcRecorder$ignoreUnsupportedPacket(Packet<?> packet, PacketListener listener) {
        try {
            ((Packet) packet).handle(listener);
        } catch (UnsupportedPacketException exception) {
            String packetType = packet.type().id().toString();
            ReplayPacketCompatibility.Suppression suppression =
                ReplayPacketCompatibility.suppressIfAutomated(packetType);
            if (suppression == null) {
                throw exception;
            }
            if (suppression.packetTypeCount() == 1) {
                LOGGER.warn(
                    "Ignoring Flashback-unsupported packet {} during automated RGB playback; "
                        + "the final render result will record all skipped packet types and counts",
                    packetType
                );
            }
        }
    }
}
