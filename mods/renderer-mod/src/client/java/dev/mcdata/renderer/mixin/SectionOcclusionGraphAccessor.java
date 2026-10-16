package dev.mcdata.renderer.mixin;

import net.minecraft.client.renderer.SectionOcclusionGraph;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

import java.util.concurrent.Future;

/** Exposes whether the visible-section search is still running, for the readiness check. */
@Mixin(SectionOcclusionGraph.class)
public interface SectionOcclusionGraphAccessor {
    @Accessor("needsFullUpdate")
    boolean mcRecorder$needsFullUpdate();

    @Accessor("fullUpdateTask")
    Future<?> mcRecorder$fullUpdateTask();
}
