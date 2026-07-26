package dev.mcdata.renderer.mixin;

import com.moulberry.flashback.Flashback;
import dev.mcdata.renderer.McRecorderRenderer;
import net.minecraft.client.player.AbstractClientPlayer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Separates the recorded HUD/hand source from Flashback's export camera entity. */
@Mixin(value = Flashback.class, remap = false)
public abstract class FlashbackPresentationPlayerMixin {
    /**
     * Supplies the recorded player when Flashback resolves first-person presentation state.
     *
     * <p>Triggering workflow:</p>
     *
     * <p>{@link Flashback#getSpectatingPlayer()}
     *   -> {@link McRecorderRenderer#presentationPlayerOverride()}
     *     -> {@link #mcRecorder$usePresentationPlayer(CallbackInfoReturnable)}</p>
     *
     * <p>Downstream:</p>
     * <ul><li>Flashback's HUD and item-in-hand render mixins receive the recorded player.</li></ul>
     */
    @Inject(method = "getSpectatingPlayer", at = @At("HEAD"), cancellable = true, remap = false)
    private static void mcRecorder$usePresentationPlayer(
        CallbackInfoReturnable<AbstractClientPlayer> callback
    ) {
        AbstractClientPlayer player = McRecorderRenderer.presentationPlayerOverride();
        if (player != null) {
            // NOTICE: Flashback 0.39.5 derives hand/HUD presentation from the camera entity,
            // but its ExportJob moves a separate replay viewer for entity-tracked camera paths.
            // Keep those roles separate for automated first-person rendering. Source:
            // `https://github.com/Moulberry/Flashback/blob/9117b1351228cfefdfdff6f502d5427a31ec0d3a/src/main/java/com/moulberry/flashback/Flashback.java#L1062-L1073`
            callback.setReturnValue(player);
        }
    }
}
