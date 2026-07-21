package dev.mcdata.scene.mixin;

import net.minecraft.network.protocol.game.ClientboundRotateHeadPacket;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

@Mixin(ClientboundRotateHeadPacket.class)
public interface RotateHeadPacketAccessor {
    @Accessor("entityId")
    int mcRecorderSceneEntityId();
}
