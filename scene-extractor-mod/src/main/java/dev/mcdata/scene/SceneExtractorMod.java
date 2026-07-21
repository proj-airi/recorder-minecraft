package dev.mcdata.scene;

import dev.mcdata.scene.io.ResultPublisher;
import dev.mcdata.scene.io.SceneSpoolWriter;
import dev.mcdata.scene.job.SceneJob;
import dev.mcdata.scene.job.SceneJobLoader;
import dev.mcdata.scene.replay.FlashbackSceneExtractor;
import dev.mcdata.scene.replay.ReplayArchiveValidator;
import dev.mcdata.scene.replay.ReplayTimelinePayload;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.server.MinecraftServer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** One-shot server entrypoint. Minecraft supplies registries; extraction never starts a client. */
public final class SceneExtractorMod implements ModInitializer {
    private static final Logger LOGGER = LoggerFactory.getLogger("mc-recorder-scene-extractor");
    private static final String ENVIRONMENT_JOB = "MC_RECORDER_SCENE_JOB";
    private static final String PROPERTY_JOB = "mc.recorder.sceneJob";

    private SceneJob job;

    @Override
    public void onInitialize() {
        if (!FabricLoader.getInstance().isModLoaded("mc-recorder")) {
            PayloadTypeRegistry.playS2C().register(ReplayTimelinePayload.TYPE, ReplayTimelinePayload.STREAM_CODEC);
        }
        Path request = resolveRequestPath();
        try {
            job = SceneJobLoader.load(request);
        } catch (IOException exception) {
            throw new IllegalStateException("refusing invalid scene extraction job " + request, exception);
        }
        ServerLifecycleEvents.SERVER_STARTED.register(this::startExtraction);
    }

    private void startExtraction(MinecraftServer server) {
        Thread worker = new Thread(() -> runExtraction(server), "mc-recorder-scene-extractor");
        worker.setDaemon(false);
        worker.start();
    }

    private void runExtraction(MinecraftServer server) {
        try {
            ReplayArchiveValidator validator = new ReplayArchiveValidator();
            List<ReplayArchiveValidator.VerifiedSource> verified = new ArrayList<>();
            for (SceneJob.SourceReplay source : job.sourceReplays()) {
                verified.add(validator.verify(job, source));
            }

            FlashbackSceneExtractor.ExtractionStats extraction;
            SceneSpoolWriter.OutputStats output;
            try (SceneSpoolWriter spool = new SceneSpoolWriter(job.output())) {
                FlashbackSceneExtractor extractor = new FlashbackSceneExtractor(job, server.registryAccess(), spool);
                extraction = extractor.extract(verified);
                for (ReplayArchiveValidator.VerifiedSource source : verified) {
                    validator.verifyUnchanged(source);
                }
                output = spool.commit();
            }
            ResultPublisher.complete(job, output, extraction);
            LOGGER.info("Scene extraction {} completed with {} frames", job.jobId(), output.frameCount());
        } catch (Throwable failure) {
            LOGGER.error("Scene extraction {} failed", job.jobId(), failure);
            try {
                ResultPublisher.failed(job, failure);
            } catch (IOException resultFailure) {
                failure.addSuppressed(resultFailure);
                LOGGER.error("Could not publish failed scene extraction result", resultFailure);
            }
        } finally {
            if (job.stopWhenDone()) {
                server.execute(() -> server.halt(false));
            }
        }
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
