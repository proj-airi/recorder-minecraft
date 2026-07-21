package dev.mcdata.recorder.mixin;

import dev.mcdata.recorder.capture.ReplayScenePacketContract;
import net.casual.arcade.replay.recorder.ReplayRecorder;
import net.casual.arcade.replay.util.ReplayOptimizerUtils;
import net.minecraft.network.protocol.Packet;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Retains explicit entity motion because the headless scene reducer does not simulate physics. */
@Mixin(value = ReplayOptimizerUtils.class, remap = false)
public abstract class ReplayOptimizerScenePacketMixin {
    @Inject(
        method = "shouldIgnorePacket",
        at = @At(value = "RETURN", ordinal = 0),
        cancellable = true,
        remap = false,
        require = 1,
        expect = 1,
        allow = 1
    )
    private void mcRecorder$retainSceneEntityMovement(
        ReplayRecorder recorder,
        Packet<?> packet,
        CallbackInfoReturnable<Boolean> callback
    ) {
        callback.setReturnValue(ReplayScenePacketContract.shouldIgnoreAtOptimizer(
            callback.getReturnValueZ(),
            packet
        ));
    }
}
