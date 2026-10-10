package dev.mcdata.scene;

import dev.mcdata.scene.io.ResultWriter;
import dev.mcdata.scene.io.SceneSpoolWriter;
import dev.mcdata.scene.job.SceneJob;
import dev.mcdata.scene.job.SceneJobLoader;
import dev.mcdata.scene.replay.ExtractorRuntime;
import dev.mcdata.scene.replay.FlashbackSceneExtractor;
import dev.mcdata.scene.replay.ReplayArchiveValidator;
import net.minecraft.core.RegistryAccess;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Runs one extraction job without owning the process launch mechanism. */
public final class SceneExtractionRunner {
    private static final Logger LOGGER = LoggerFactory.getLogger("recorder-minecraft-scene-extractor");

    private final RegistryAccess registries;
    private final ExtractorRuntime runtime;

    public SceneExtractionRunner(RegistryAccess registries, ExtractorRuntime runtime) {
        this.registries = registries;
        this.runtime = runtime;
    }

    public SceneJob run(Path request) throws IOException {
        SceneJob job = SceneJobLoader.load(request);
        try {
            SceneJobLoader.verifySubjectPosesUnchanged(job);
            ReplayArchiveValidator validator = new ReplayArchiveValidator(runtime);
            List<ReplayArchiveValidator.VerifiedSource> verified = new ArrayList<>();
            for (SceneJob.SourceReplay source : job.sourceReplays()) {
                verified.add(validator.verify(job, source));
            }

            FlashbackSceneExtractor.ExtractionStats extraction;
            SceneSpoolWriter.OutputStats output;
            try (SceneSpoolWriter spool = new SceneSpoolWriter(job.output())) {
                FlashbackSceneExtractor extractor = new FlashbackSceneExtractor(job, registries, spool);
                extraction = extractor.extract(verified);
                for (ReplayArchiveValidator.VerifiedSource source : verified) {
                    validator.verifyUnchanged(source);
                }
                SceneJobLoader.verifySubjectPosesUnchanged(job);
                output = spool.commit();
            }
            ResultWriter.complete(job, output, extraction, verified);
            LOGGER.info("Scene extraction {} completed with {} frames", job.jobId(), output.frameCount());
            return job;
        } catch (Throwable failure) {
            LOGGER.error("Scene extraction {} failed", job.jobId(), failure);
            try {
                ResultWriter.failed(job, failure);
            } catch (IOException resultFailure) {
                failure.addSuppressed(resultFailure);
                LOGGER.error("Could not write failed scene extraction result", resultFailure);
            }
            if (failure instanceof IOException exception) {
                throw exception;
            }
            throw new IOException("scene extraction failed: " + failure.getMessage(), failure);
        }
    }
}
