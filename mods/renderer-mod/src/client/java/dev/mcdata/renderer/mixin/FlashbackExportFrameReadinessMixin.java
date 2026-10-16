package dev.mcdata.renderer.mixin;

import com.moulberry.flashback.exporting.ExportJob;
import dev.mcdata.renderer.RenderReadiness;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Holds every exported frame until the camera's visible sections are meshed. */
@Mixin(value = ExportJob.class, remap = false)
public abstract class FlashbackExportFrameReadinessMixin {
    /**
     * Runs the readiness wait right before Flashback renders the frame it captures.
     *
     * <p>Triggering workflow:</p>
     *
     * <p>{@code ExportJob#doExport}
     *   -> {@code PerfectFrames#waitUntilFrameReady}
     *     -> {@link #mcRecorder$waitForVisibleSections(CallbackInfo)}</p>
     *
     * <p>Downstream:</p>
     * <ul><li>{@link RenderReadiness#renderUntilVisibleSectionsReady()} renders dummy passes.</li></ul>
     */
    @Inject(
        method = "doExport",
        at = @At(
            value = "INVOKE",
            target = "Lcom/moulberry/flashback/exporting/PerfectFrames;waitUntilFrameReady()V"
        ),
        remap = false
    )
    private void mcRecorder$waitForVisibleSections(CallbackInfo callback) {
        // NOTICE: Flashback 0.39.5 PerfectFrames waits only for player skins; its synchronous
        // section compile is commented out. Source:
        // `https://github.com/Moulberry/Flashback/blob/9117b1351228cfefdfdff6f502d5427a31ec0d3a/src/main/java/com/moulberry/flashback/mixin/MixinLevelRenderer.java#L246-L273`
        RenderReadiness.renderUntilVisibleSectionsReady();
    }
}
