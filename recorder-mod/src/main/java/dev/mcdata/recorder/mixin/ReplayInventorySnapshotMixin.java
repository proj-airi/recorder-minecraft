package dev.mcdata.recorder.mixin;

import dev.mcdata.recorder.capture.ReplayPacketSnapshots;
import net.casual.arcade.replay.recorder.ReplayRecorder;
import net.minecraft.network.protocol.Packet;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyVariable;

/**
 * Freezes mutable packet values at ServerReplay's last synchronous boundary, before its replay
 * writer can encode them asynchronously after the live game state has changed.
 */
@Mixin(value = ReplayRecorder.class, remap = false)
public abstract class ReplayInventorySnapshotMixin {
    @ModifyVariable(
        method = "record",
        at = @At("HEAD"),
        argsOnly = true,
        ordinal = 0,
        remap = false,
        require = 1,
        expect = 1,
        allow = 1
    )
    private Packet<?> mcRecorder$freezeMutablePacket(Packet<?> packet) {
        return ReplayPacketSnapshots.freezeMutableValues(packet);
    }
}
