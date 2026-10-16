package dev.mcdata.renderer;

import com.mojang.blaze3d.pipeline.RenderTarget;
import com.mojang.blaze3d.systems.RenderSystem;
import dev.mcdata.renderer.mixin.LevelRendererAccessor;
import dev.mcdata.renderer.mixin.SectionOcclusionGraphAccessor;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.chunk.CompiledSectionMesh;
import net.minecraft.client.renderer.chunk.SectionRenderDispatcher;
import net.minecraft.core.SectionPos;
import net.minecraft.world.phys.Vec3;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.concurrent.Future;
import java.util.concurrent.locks.LockSupport;

/**
 * Holds each exported frame until the sections the camera can see are compiled and uploaded.
 *
 * <p>Flashback renders a frame with whatever sections happen to be meshed. Interactive users
 * browse a replay before exporting, so its sections are already compiled; an automated render
 * exports straight after opening the replay, so early frames, and frames after chunks arrive,
 * otherwise show only the clear colour.</p>
 */
public final class RenderReadiness {
    private static final Logger LOGGER = LoggerFactory.getLogger("recorder-minecraft-renderer");
    private static final long MAX_WAIT_NANOS = 10_000_000_000L;
    private static final int READY_PASSES = 2;

    private RenderReadiness() {
    }

    public static void renderUntilVisibleSectionsReady() {
        if (!ReplayPacketCompatibility.isAutomatedRenderActive()) {
            return;
        }
        Minecraft minecraft = Minecraft.getInstance();
        if (minecraft.level == null) {
            return;
        }
        long started = System.nanoTime();
        int readyPasses = 0;
        while (readyPasses < READY_PASSES) {
            if (System.nanoTime() - started > MAX_WAIT_NANOS) {
                LOGGER.warn(
                    "Exporting a frame with {} visible sections not compiled: {}",
                    pendingVisibleSections(minecraft), minecraft.levelRenderer.getSectionStatistics()
                );
                return;
            }
            // Same dummy pass Flashback's ExportJob runs while a screen is open: it schedules
            // compiles for dirty visible sections and uploads finished meshes.
            RenderTarget target = minecraft.getMainRenderTarget();
            RenderSystem.getDevice().createCommandEncoder().clearColorAndDepthTextures(
                target.getColorTexture(), 0, target.getDepthTexture(), 1.0
            );
            minecraft.gameRenderer.render(minecraft.getDeltaTracker(), true);
            while (minecraft.pollTask()) {
            }
            if (pendingVisibleSections(minecraft) == 0) {
                readyPasses++;
            } else {
                readyPasses = 0;
                LockSupport.parkNanos("waiting for visible sections to compile", 1_000_000L);
            }
        }
    }

    // The compile queue also holds sections outside the frustum, so only visible sections count.
    // A result of 0 means the frame shows every section the camera can see.
    private static int pendingVisibleSections(Minecraft minecraft) {
        SectionRenderDispatcher dispatcher = minecraft.levelRenderer.getSectionRenderDispatcher();
        if (dispatcher == null) {
            return 0;
        }
        LevelRendererAccessor levelRenderer = (LevelRendererAccessor) minecraft.levelRenderer;
        List<SectionRenderDispatcher.RenderSection> visible = levelRenderer.mcRecorder$visibleSections();
        // NOTICE: The occlusion graph finds visible sections on a worker thread. Until its first
        // full update lands the visible list is empty, which would pass every check below while
        // the frame still shows only the clear colour.
        SectionOcclusionGraphAccessor graph = (SectionOcclusionGraphAccessor) levelRenderer.mcRecorder$sectionOcclusionGraph();
        Future<?> fullUpdate = graph.mcRecorder$fullUpdateTask();
        if (graph.mcRecorder$needsFullUpdate() || (fullUpdate != null && !fullUpdate.isDone())) {
            return Math.max(1, visible.size());
        }
        Vec3 camera = minecraft.gameRenderer.getMainCamera().getPosition();
        boolean cameraChunkLoaded = minecraft.level.hasChunk(
            SectionPos.blockToSectionCoord(camera.x), SectionPos.blockToSectionCoord(camera.z)
        );
        if (cameraChunkLoaded && visible.isEmpty()) {
            return 1;
        }
        int pending = dispatcher.getToUpload();
        for (SectionRenderDispatcher.RenderSection section : visible) {
            // Mirrors LevelRenderer#compileSections: a section without a mesh compiles only once
            // all its neighbours exist, so an edge section with a missing neighbour never blocks.
            boolean uncompiled = section.getSectionMesh() == CompiledSectionMesh.UNCOMPILED;
            boolean compilable = !uncompiled || section.hasAllNeighbors();
            if (compilable && (section.isDirty() || uncompiled)) {
                pending++;
            }
        }
        return pending;
    }
}
