package dev.mcdata.recorder.mixin;

import dev.mcdata.recorder.capture.CaptureRuntime;
import dev.mcdata.recorder.capture.ContainerViews;
import io.netty.channel.ChannelFutureListener;
import net.minecraft.network.protocol.Packet;
import net.minecraft.server.network.ServerCommonPacketListenerImpl;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Observes container packets as the server hands them to a play connection.
 *
 * Both `send` overloads funnel into this one, for dedicated-server sockets and for the
 * integrated-server host's in-memory connection alike. Arcade's own listeners on this method can
 * replace or cancel packets; the recorder registers none for container packets, so what is
 * observed here is what is written to the connection.
 */
@Mixin(ServerCommonPacketListenerImpl.class)
public abstract class ServerCommonPacketListenerImplMixin {
    @Inject(
        method = "send(Lnet/minecraft/network/protocol/Packet;Lio/netty/channel/ChannelFutureListener;)V",
        at = @At("HEAD")
    )
    private void mcRecorder$observeContainerView(
        Packet<?> packet,
        ChannelFutureListener listener,
        CallbackInfo callback
    ) {
        // Filter before entering the synchronized coordinator; every chunk and entity packet passes here.
        if (!ContainerViews.isObserved(packet)) {
            return;
        }
        if ((Object) this instanceof ServerGamePacketListenerImpl gameListener) {
            CaptureRuntime.clientboundPacket(gameListener.player, packet);
        }
    }
}
