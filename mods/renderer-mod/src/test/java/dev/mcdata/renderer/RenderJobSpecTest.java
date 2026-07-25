package dev.mcdata.renderer;

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
        Path job = writeJob("");
        RenderJobSpec spec = RenderJobSpec.read(job);

        assertEquals(RenderJobSpec.RangePolicy.INTERSECTION, spec.rangePolicy());
        assertEquals("33333333-3333-4333-8333-333333333333", spec.replayId());
        assertTrue(spec.noGui());
        assertEquals(temporary.resolve("progress.json").toAbsolutePath().normalize(), spec.progress());
    }

    @Test
    void acceptsAnExplicitFullClientGuiJob() throws Exception {
        Path job = writeJob(",\"no_gui\":false");

        RenderJobSpec spec = RenderJobSpec.read(job);

        assertFalse(spec.noGui());
    }

    @Test
    void rejectsMismatchedReplayIdentity() throws Exception {
        Path job = writeJob("");
        Files.writeString(job, Files.readString(job).replaceFirst(
            "\\\"replay_id\\\":\\\"33333333-3333-4333-8333-333333333333\\\"",
            "\\\"replay_id\\\":\\\"44444444-4444-4444-8444-444444444444\\\""
        ));
        assertThrows(IllegalArgumentException.class, () -> RenderJobSpec.read(job));
    }

    private Path writeJob(String extra) throws Exception {
        Path replay = temporary.resolve("replay.zip");
        Files.write(replay, new byte[0]);
        Files.createDirectories(temporary.resolve("fpv_frames"));
        Path job = temporary.resolve("job.json");
        Files.writeString(job, """
            {
              "owner":"mc-recorder",
              "job_type":"mc-recorder-first-person-render-v1",
              "replay":"replay.zip",
              "source_replay":{
                "path":"replay.zip",
                "sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "size_bytes":0,
                "replay_id":"33333333-3333-4333-8333-333333333333"
              },
              "output":"fpv_frames",
              "result":"result.json",
              "session_id":"session",
              "connection_id":"22222222-2222-2222-2222-222222222222",
              "player_uuid":"11111111-1111-1111-1111-111111111111",
              "replay_id":"33333333-3333-4333-8333-333333333333",
              "global_start_tick":10,
              "global_end_tick":20,
              "timeline":{"range_policy":"intersection"}
              %s
            }
            """.formatted(extra));
        return job;
    }
}
