package dev.mcdata.recorder.mixin;

import dev.mcdata.recorder.capture.CaptureRuntime;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.MenuProvider;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import java.util.OptionalInt;

/**
 * Brackets `openMenu` so container views sent while it runs can be linked to the block that
 * provided the menu. The open-screen and initial contents packets are sent before the new menu is
 * assigned to the player, so the link can only be resolved at return.
 */
@Mixin(ServerPlayer.class)
public abstract class ServerPlayerMenuMixin {
    @Inject(method = "openMenu(Lnet/minecraft/world/MenuProvider;)Ljava/util/OptionalInt;", at = @At("HEAD"))
    private void mcRecorder$beginMenuOpen(MenuProvider provider, CallbackInfoReturnable<OptionalInt> callback) {
        CaptureRuntime.menuOpening((ServerPlayer) (Object) this);
    }

    @Inject(method = "openMenu(Lnet/minecraft/world/MenuProvider;)Ljava/util/OptionalInt;", at = @At("RETURN"))
    private void mcRecorder$finishMenuOpen(MenuProvider provider, CallbackInfoReturnable<OptionalInt> callback) {
        // A null provider returns early without opening anything; pending views still flush.
        CaptureRuntime.menuOpened((ServerPlayer) (Object) this, provider);
    }
}
