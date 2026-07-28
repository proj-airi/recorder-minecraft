package dev.mcdata.renderer;

import com.google.protobuf.util.JsonFormat;
import dev.minerec.artifacts.v1.RenderJob;
import dev.minerec.artifacts.v1.RenderJobStatus;
import dev.minerec.artifacts.v1.RenderRangePolicy;
import dev.minerec.artifacts.v1.RenderReplaySource;
import dev.minerec.artifacts.v1.TickRange;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class RenderJobSpecTest {
    @TempDir
    Path temporary;

    @Test
    void acceptsCurrentReplayJob() throws Exception {
        Path job = writeJob(true);
        RenderJobSpec spec = RenderJobSpec.read(job);

        assertEquals(RenderJobSpec.RangePolicy.INTERSECTION, spec.rangePolicy());
        assertEquals("33333333-3333-4333-8333-333333333333", spec.replayId());
        assertTrue(spec.noGui());
        assertEquals(temporary.resolve("progress.json").toAbsolutePath().normalize(), spec.progress());
    }

    @Test
    void acceptsAnExplicitFullClientGuiJob() throws Exception {
        RenderJobSpec spec = RenderJobSpec.read(writeJob(false));

        assertFalse(spec.noGui());
    }

    @Test
    void rejectsOutputOutsideOwnedDirectory() throws Exception {
        Path job = writeJob(true);
        RenderJob.Builder value = RenderJob.newBuilder();
        JsonFormat.parser().merge(Files.readString(job), value);
        Files.writeString(job, JsonFormat.printer().print(value.setOutputPath(temporary.resolve("outside").toString()).build()));

        assertThrows(IllegalArgumentException.class, () -> RenderJobSpec.read(job));
    }

    private Path writeJob(boolean noGui) throws Exception {
        Path replay = temporary.resolve("replay.zip");
        Files.write(replay, new byte[0]);
        Files.createDirectories(temporary.resolve("fpv_frames"));
        RenderJob value = RenderJob.newBuilder()
            .setSchemaVersion(1)
            .setOwner("mc-recorder")
            .setJobType("mc-recorder-first-person-render-v1")
            .setStatus(RenderJobStatus.RENDER_JOB_STATUS_PREPARED)
            .setSessionId("session")
            .setConnectionId("22222222-2222-4222-8222-222222222222")
            .setPlayerUuid("11111111-1111-4111-8111-111111111111")
            .setReplay(RenderReplaySource.newBuilder()
                .setReplayId("33333333-3333-4333-8333-333333333333")
                .setPath(replay.toString())
                .setFormat("flashback")
                .setSha256("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
            .setGlobalTicks(TickRange.newBuilder().setFirstTick(10).setLastTick(20))
            .setRangePolicy(RenderRangePolicy.RENDER_RANGE_POLICY_INTERSECTION)
            .setWidth(640)
            .setHeight(360)
            .setFramesPerSecond(20)
            .setNoGui(noGui)
            .setStopWhenDone(true)
            .setOutputPath(temporary.resolve("fpv_frames").toString())
            .setResultPath(temporary.resolve("result.json").toString())
            .setProgressPath(temporary.resolve("progress.json").toString())
            .build();
        Path job = temporary.resolve("job.json");
        Files.writeString(job, JsonFormat.printer().print(value));
        return job;
    }
}
