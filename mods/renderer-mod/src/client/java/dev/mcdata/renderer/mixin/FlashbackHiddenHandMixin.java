package dev.mcdata.renderer.mixin;

import com.moulberry.flashback.ext.ItemInHandRendererExt;
import com.moulberry.flashback.state.EditorState;
import com.moulberry.flashback.state.EditorStateManager;
import dev.mcdata.renderer.McRecorderRenderer;
import net.minecraft.client.player.AbstractClientPlayer;
import net.minecraft.client.renderer.ItemInHandRenderer;
import org.spongepowered.asm.mixin.Dynamic;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.UUID;

/** Keeps the tracked world model hidden without suppressing its first-person hand. */
@Mixin(ItemInHandRenderer.class)
public abstract class FlashbackHiddenHandMixin {
    @Unique
    private UUID mcRecorder$temporarilyVisiblePlayer;

    /**
     * Removes Flashback's world-model hiding flag while it renders the recorded hand.
     *
     * <p>Triggering workflow:</p>
     *
     * <p>{@link ItemInHandRendererExt#flashback$renderHandsWithItems}
     *   -> {@link #mcRecorder$showPresentationHand(CallbackInfo)}
     *     -> {@link EditorState#hideDuringExport}</p>
     *
     * <p>Downstream:</p>
     * <ul><li>{@link #mcRecorder$restoreHiddenPlayer(CallbackInfo)} restores the export invariant.</li></ul>
     */
    @Dynamic("Method is added to ItemInHandRenderer by Flashback's playback mixin")
    @Inject(method = "flashback$renderHandsWithItems", at = @At("HEAD"), remap = false)
    private void mcRecorder$showPresentationHand(CallbackInfo callback) {
        AbstractClientPlayer player = McRecorderRenderer.presentationPlayerOverride();
        EditorState editorState = EditorStateManager.getCurrent();
        if (player != null && editorState != null
            && editorState.hideDuringExport.remove(player.getUUID())) {
            // NOTICE: Flashback 0.39.5 uses one hide set for both world models and hands.
            // The tracked player must remain hidden from the head-mounted export camera, but
            // its hand must still render. Restore the set at method return. Source:
            // `https://github.com/Moulberry/Flashback/blob/9117b1351228cfefdfdff6f502d5427a31ec0d3a/src/main/java/com/moulberry/flashback/mixin/playback/MixinItemInHandRenderer.java#L99-L104`
            this.mcRecorder$temporarilyVisiblePlayer = player.getUUID();
        }
    }

    /**
     * Restores the tracked player's world-model hiding flag after hand rendering.
     *
     * <p>Triggering workflow:</p>
     *
     * <p>{@link #mcRecorder$showPresentationHand(CallbackInfo)}
     *   -> {@link ItemInHandRendererExt#flashback$renderHandsWithItems}
     *     -> {@link #mcRecorder$restoreHiddenPlayer(CallbackInfo)}</p>
     *
     * <p>Downstream:</p>
     * <ul><li>{@link EditorState#hideDuringExport} again hides the tracked world model.</li></ul>
     */
    @Dynamic("Method is added to ItemInHandRenderer by Flashback's playback mixin")
    @Inject(method = "flashback$renderHandsWithItems", at = @At("RETURN"), remap = false)
    private void mcRecorder$restoreHiddenPlayer(CallbackInfo callback) {
        if (this.mcRecorder$temporarilyVisiblePlayer == null) {
            return;
        }
        EditorState editorState = EditorStateManager.getCurrent();
        if (editorState != null) {
            editorState.hideDuringExport.add(this.mcRecorder$temporarilyVisiblePlayer);
        }
        this.mcRecorder$temporarilyVisiblePlayer = null;
    }
}
