package dev.mcdata.recorder.mixin;

import net.minecraft.server.level.ChunkHolder;
import net.minecraft.server.level.ChunkMap;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

@Mixin(ChunkMap.class)
public interface ChunkMapAccessor {
    // Visible chunk holders; used once at world stream start to enumerate already-loaded containers.
    @Invoker("getChunks")
    Iterable<ChunkHolder> mcRecorderVisibleChunks();
}
