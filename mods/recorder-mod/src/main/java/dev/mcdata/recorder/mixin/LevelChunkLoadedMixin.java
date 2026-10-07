package dev.mcdata.recorder.mixin;

import dev.mcdata.recorder.capture.CaptureRuntime;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.chunk.LevelChunk;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(LevelChunk.class)
public abstract class LevelChunkLoadedMixin {
    /**
     * Distinguishes chunk unloads from block entity destruction for the world stream.
     *
     * In 1.21.8 {@code ChunkMap} unloads a chunk as {@code setLoaded(false)}, then {@code save}
     * (where Fabric fires CHUNK_UNLOAD and BLOCK_ENTITY_UNLOAD), then {@code ServerLevel.unload},
     * which calls {@code setRemoved()} on every block entity. Destruction through
     * {@code removeBlockEntity} fires BLOCK_ENTITY_UNLOAD before {@code setRemoved()}. So
     * {@code BlockEntity.isRemoved()} is false at the event in both cases and cannot tell them
     * apart; the chunk's loaded flag flipping just before the event can.
     */
    @Inject(method = "setLoaded(Z)V", at = @At("HEAD"))
    private void mcRecorder$trackChunkUnload(boolean loaded, CallbackInfo callback) {
        LevelChunk chunk = (LevelChunk) (Object) this;
        if (chunk.getLevel() instanceof ServerLevel serverLevel) {
            CaptureRuntime.chunkLoadedChanged(serverLevel, chunk.getPos().toLong(), loaded);
        }
    }
}
