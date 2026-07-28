package dev.mcdata.scene;

import dev.mcdata.scene.job.SceneJob;
import dev.mcdata.scene.replay.ReplayTimelinePayload;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.ModContainer;
import net.minecraft.server.MinecraftServer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

/** One-shot server entrypoint. Minecraft supplies registries; extraction never starts a client. */
public final class SceneExtractorMod implements ModInitializer {
    private static final Logger LOGGER = LoggerFactory.getLogger("recorder-minecraft-scene-extractor");
    private static final String ENVIRONMENT_JOB = "MC_RECORDER_SCENE_JOB";
    private static final String PROPERTY_JOB = "mc.recorder.sceneJob";

    private Path request;

    @Override
    public void onInitialize() {
        if (!FabricLoader.getInstance().isModLoaded("recorder-minecraft")) {
            PayloadTypeRegistry.playS2C().register(ReplayTimelinePayload.TYPE, ReplayTimelinePayload.STREAM_CODEC);
        }
        this.request = resolveRequestPath();
        ServerLifecycleEvents.SERVER_STARTED.register(this::startExtraction);
    }

    private void startExtraction(MinecraftServer server) {
        Thread worker = new Thread(() -> runExtraction(server), "recorder-minecraft-scene-extractor");
        worker.setDaemon(false);
        worker.start();
    }

    private void runExtraction(MinecraftServer server) {
        SceneJob job = null;
        try {
            job = new SceneExtractionRunner(server.registryAccess(), fabricRuntimeMods()).run(request);
        } catch (Throwable failure) {
            LOGGER.error("Scene extraction failed", failure);
        } finally {
            if (job == null || job.stopWhenDone()) {
                server.execute(() -> server.halt(false));
            }
        }
    }

    private static Map<String, String> fabricRuntimeMods() {
        Map<String, String> loaded = new HashMap<>();
        for (ModContainer container : FabricLoader.getInstance().getAllMods()) {
            loaded.put(
                container.getMetadata().getId(),
                container.getMetadata().getVersion().getFriendlyString()
            );
        }
        return Map.copyOf(loaded);
    }

    private static Path resolveRequestPath() {
        String environment = System.getenv(ENVIRONMENT_JOB);
        String property = System.getProperty(PROPERTY_JOB);
        boolean hasEnvironment = environment != null && !environment.isBlank();
        boolean hasProperty = property != null && !property.isBlank();
        if (hasEnvironment == hasProperty) {
            throw new IllegalStateException(
                "set exactly one of " + ENVIRONMENT_JOB + " or -D" + PROPERTY_JOB
            );
        }
        return Path.of(hasEnvironment ? environment : property);
    }
}
