package dev.mcdata.renderer.mixin;

import it.unimi.dsi.fastutil.objects.ObjectArrayList;
import net.minecraft.client.renderer.LevelRenderer;
import net.minecraft.client.renderer.SectionOcclusionGraph;
import net.minecraft.client.renderer.chunk.SectionRenderDispatcher;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** Exposes the visible sections and their occlusion graph for the per-frame readiness check. */
@Mixin(LevelRenderer.class)
public interface LevelRendererAccessor {
    @Accessor("visibleSections")
    ObjectArrayList<SectionRenderDispatcher.RenderSection> mcRecorder$visibleSections();

    @Accessor("sectionOcclusionGraph")
    SectionOcclusionGraph mcRecorder$sectionOcclusionGraph();
}
