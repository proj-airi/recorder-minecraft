package dev.mcdata.recorder.mixin;

import dev.mcdata.recorder.capture.ReplayScenePacketContract;
import net.casual.arcade.replay.io.writer.flashback.FlashbackWriter;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

import java.util.Collection;

/** Retains the client-visible scene packets that Flashback's presentation-oriented writer drops. */
@Mixin(value = FlashbackWriter.class, remap = false)
public abstract class FlashbackWriterScenePacketMixin {
    @Redirect(
        method = "canRecordPacket",
        at = @At(
            value = "INVOKE",
            target = "Lkotlin/collections/CollectionsKt;contains(Ljava/lang/Iterable;Ljava/lang/Object;)Z",
            remap = false
        ),
        remap = false,
        require = 1,
        expect = 1,
        allow = 1
    )
    private boolean mcRecorder$retainScenePacket(
        Iterable<?> ignoredPackets,
        Object packetType
    ) {
        boolean ignored = ((Collection<?>) ignoredPackets).contains(packetType);
        return ReplayScenePacketContract.shouldIgnoreAtFlashbackWriter(
            ignored,
            (Class<?>) packetType
        );
    }
}
