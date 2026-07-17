package dev.mcdata.recorder.mixin;

import dev.mcdata.recorder.capture.CaptureRuntime;
import net.minecraft.network.PacketListener;
import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.PacketUtils;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import net.minecraft.util.thread.BlockableEventLoop;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(PacketUtils.class)
public abstract class PacketUtilsMixin {
    @Inject(
        method = "ensureRunningOnSameThread(Lnet/minecraft/network/protocol/Packet;Lnet/minecraft/network/PacketListener;Lnet/minecraft/util/thread/BlockableEventLoop;)V",
        at = @At("RETURN")
    )
    private static <T extends PacketListener> void mcRecorder$stampMainThreadApplication(
        Packet<T> packet,
        T listener,
        BlockableEventLoop<?> executor,
        CallbackInfo callback
    ) {
        if (listener instanceof ServerGamePacketListenerImpl gameListener) {
            CaptureRuntime.packetApply(gameListener.player, packet);
        }
    }
}
