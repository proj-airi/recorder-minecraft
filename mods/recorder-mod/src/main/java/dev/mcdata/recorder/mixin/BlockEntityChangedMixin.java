package dev.mcdata.recorder.mixin;

import dev.mcdata.recorder.capture.CaptureRuntime;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(BlockEntity.class)
public abstract class BlockEntityChangedMixin {
    /**
     * Instance {@code setChanged()} delegates here, and several vanilla block entities (hoppers,
     * furnaces) call this static helper directly after mutating their own slots. Hooking the
     * static form therefore observes both paths. Only the position is recorded; container
     * filtering and content reads happen once per position at the end of the server tick.
     */
    @Inject(
        method = "setChanged(Lnet/minecraft/world/level/Level;Lnet/minecraft/core/BlockPos;Lnet/minecraft/world/level/block/state/BlockState;)V",
        at = @At("HEAD")
    )
    private static void mcRecorder$markWorldContainerChanged(
        Level level,
        BlockPos pos,
        BlockState state,
        CallbackInfo callback
    ) {
        if (level instanceof ServerLevel serverLevel) {
            CaptureRuntime.blockEntityChanged(serverLevel, pos);
        }
    }
}
