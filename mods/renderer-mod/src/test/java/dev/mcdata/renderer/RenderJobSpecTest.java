package dev.mcdata.renderer;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class RenderJobSpecTest {
    @TempDir
    Path temporary;

    @Test
    void preservesLegacyJobsWithoutSegmentFields() throws Exception {
        Path job = writeJob("");
        RenderJobSpec spec = RenderJobSpec.read(job);

        assertEquals(RenderJobSpec.RangePolicy.LEGACY_STRICT, spec.rangePolicy());
        assertNull(spec.segmentId());
        assertNull(spec.segmentOrdinal());
        assertTrue(spec.noGui());
        assertNull(spec.presentationContract());
        assertEquals(temporary.resolve("progress.json").toAbsolutePath().normalize(), spec.progress());
    }

    @Test
    void acceptsAnExplicitFullClientGuiJob() throws Exception {
        Path job = writeJob(",\"no_gui\":false");

        RenderJobSpec spec = RenderJobSpec.read(job);

        assertFalse(spec.noGui());
        assertNull(spec.presentationContract());
    }

    @Test
    void acceptsCurrentPresentationContractForGuiJob() throws Exception {
        Path job = writeJob(currentPresentationFields());

        RenderJobSpec spec = RenderJobSpec.read(job);

        assertEquals(ReplayPresentation.FULL_CLIENT_PRESENTATION_CONTRACT, spec.presentationContract());
        assertEquals(11, spec.structuredHud().records());
        assertEquals(
            temporary.resolve("hud-states.jsonl").toAbsolutePath().normalize(),
            spec.structuredHud().path()
        );
    }

    @Test
    void rejectsCurrentPresentationContractWithoutStructuredHud() throws Exception {
        Path job = writeJob(
            ",\"no_gui\":false,\"presentation_contract\":\""
                + ReplayPresentation.FULL_CLIENT_PRESENTATION_CONTRACT + "\""
        );

        assertThrows(IllegalArgumentException.class, () -> RenderJobSpec.read(job));
    }

    @Test
    void rejectsUnsupportedPresentationContractForGuiJob() throws Exception {
        Path job = writeJob(",\"no_gui\":false,\"presentation_contract\":\"direct_camera_v0\"");

        assertThrows(IllegalArgumentException.class, () -> RenderJobSpec.read(job));
    }

    @Test
    void rejectsPresentationContractForNoGuiJob() throws Exception {
        Path job = writeJob(
            ",\"presentation_contract\":\""
                + ReplayPresentation.FULL_CLIENT_PRESENTATION_CONTRACT + "\""
        );

        assertThrows(IllegalArgumentException.class, () -> RenderJobSpec.read(job));
    }

    @Test
    void acceptsPortableSegmentIntersectionFields() throws Exception {
        Path job = writeJob("""
            ,"timeline":{"range_policy":"intersection","newer_cutoff":15}
            """);
        String value = Files.readString(job).replace(
            "\"size_bytes\":0",
            "\"size_bytes\":0,\"segment_id\":\"segment-0001\",\"segment_ordinal\":7"
        );
        Files.writeString(job, value);
        RenderJobSpec spec = RenderJobSpec.read(job);

        assertEquals(RenderJobSpec.RangePolicy.INTERSECTION, spec.rangePolicy());
        assertEquals("segment-0001", spec.segmentId());
        assertEquals(7L, spec.segmentOrdinal());
        assertEquals(15L, spec.newerCutoff());
        assertEquals(14L, spec.effectiveGlobalEndTick());
    }

    @Test
    void rejectsPartialSegmentIdentity() throws Exception {
        Path job = writeJob(",\"segment_id\":\"44444444-4444-4444-4444-444444444444\"");
        assertThrows(IllegalArgumentException.class, () -> RenderJobSpec.read(job));
    }

    private Path writeJob(String extra) throws Exception {
        Path replay = temporary.resolve("replay.zip");
        Files.write(replay, new byte[0]);
        Files.createDirectories(temporary.resolve("frames"));
        Path job = temporary.resolve("job.json");
        Files.writeString(job, """
            {
              "owner":"mc-recorder",
              "job_type":"mc-recorder-first-person-render-v1",
              "replay":"replay.zip",
              "source_replay":{
                "path":"replay.zip",
                "sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "size_bytes":0
              },
              "output":"frames",
              "result":"result.json",
              "session_id":"session",
              "connection_id":"22222222-2222-2222-2222-222222222222",
              "player_uuid":"11111111-1111-1111-1111-111111111111",
              "global_start_tick":10,
              "global_end_tick":20
              %s
            }
            """.formatted(extra));
        return job;
    }

    private String currentPresentationFields() {
        return """
            ,"no_gui":false,
             "presentation_contract":"%s",
             "structured_hud":{
               "schema_version":1,
               "type":"mc-recorder-structured-hud-v1",
               "path":"hud-states.jsonl",
               "format":"jsonl",
               "sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
               "size_bytes":123,
               "records":11,
               "start_server_tick":10,
               "end_server_tick":20,
               "dataset_id":"cccccccccccccccccccccccccccccccc",
               "dataset_manifest_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
               "samples_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
               "session_id":"session",
               "player_uuid":"11111111-1111-1111-1111-111111111111",
               "connection_id":"22222222-2222-2222-2222-222222222222"
             }
            """.formatted(ReplayPresentation.FULL_CLIENT_PRESENTATION_CONTRACT);
    }
}
