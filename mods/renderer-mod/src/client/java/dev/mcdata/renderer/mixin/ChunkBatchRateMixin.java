package dev.mcdata.renderer.mixin;

import dev.mcdata.renderer.ReplayPacketCompatibility;
import net.minecraft.client.multiplayer.ChunkBatchSizeCalculator;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Keeps the replay server streaming chunks at full rate during an automated render. */
@Mixin(ChunkBatchSizeCalculator.class)
public abstract class ChunkBatchRateMixin {
    private static final float MAX_CHUNKS_PER_TICK = 64.0f;

    /**
     * Reports the highest chunk rate the server accepts while a render job is active.
     *
     * <p>Triggering workflow:</p>
     *
     * <p>{@code ClientPacketListener#handleChunkBatchFinished}
     *   -> {@link ChunkBatchSizeCalculator#getDesiredChunksPerTick()}
     *     -> {@link #mcRecorder$fullRate(CallbackInfoReturnable)}</p>
     *
     * <p>Downstream:</p>
     * <ul><li>The replay server's {@code PlayerChunkSender} sends the next batches at once.</li></ul>
     */
    @Inject(method = "getDesiredChunksPerTick", at = @At("RETURN"), cancellable = true)
    private void mcRecorder$fullRate(CallbackInfoReturnable<Float> callback) {
        if (ReplayPacketCompatibility.isAutomatedRenderActive()) {
            // NOTICE: The calculator times each batch from its start packet to its finish packet.
            // The renderer pauses the client between those packets while it seeks and settles, so
            // the measured time grew without bound and the requested rate fell to the minimum:
            // renders then kept the 25 chunks loaded at the start for the whole export.
            callback.setReturnValue(MAX_CHUNKS_PER_TICK);
        }
    }
}
