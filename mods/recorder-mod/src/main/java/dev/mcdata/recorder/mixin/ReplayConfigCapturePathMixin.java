package dev.mcdata.recorder.mixin;

import com.mojang.authlib.GameProfile;
import dev.mcdata.recorder.capture.CaptureRuntime;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import java.nio.file.Path;

/** Routes ServerReplay's live Flashback writer directly into the allocated play capture. */
@Mixin(targets = "me.senseiwells.replay.config.ReplayConfig", remap = false)
public abstract class ReplayConfigCapturePathMixin {
    @Inject(method = "getPlayerRecordingLocation", at = @At("HEAD"), cancellable = true)
    private void mcRecorder$usePlayCapturePath(
        GameProfile profile,
        CallbackInfoReturnable<Path> callback
    ) {
        Path capturePath = CaptureRuntime.replayPathFor(profile);
        if (capturePath != null) {
            callback.setReturnValue(capturePath);
        }
    }
}
