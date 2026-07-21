package dev.mcdata.renderer;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class StructuredHudTimelineTest {
    @TempDir
    Path temporary;

    @Test
    void loadsAnIntegrityBoundContiguousTimeline() throws Exception {
        RenderJobSpec job = writeJob(sidecar(10, 11));

        StructuredHudTimeline timeline = StructuredHudTimeline.load(job);

        assertEquals(2, timeline.resultEnvelope().get("records").getAsLong());
        assertEquals(10, timeline.resultEnvelope().get("start_server_tick").getAsLong());
        assertEquals(11, timeline.resultEnvelope().get("end_server_tick").getAsLong());
        assertEquals(
            "cccccccccccccccccccccccccccccccc",
            timeline.resultEnvelope().get("dataset_id").getAsString()
        );
    }

    @Test
    void rejectsAHashValidSidecarWithMissingTicks() throws Exception {
        RenderJobSpec job = writeJob(sidecar(10, 12));

        assertThrows(java.io.IOException.class, () -> StructuredHudTimeline.load(job));
    }

    @Test
    void rejectsSidecarMutationAfterJobPreparation() throws Exception {
        RenderJobSpec job = writeJob(sidecar(10, 11));
        Files.writeString(temporary.resolve("hud-states.jsonl"), "tampered\n");

        assertThrows(java.io.IOException.class, () -> StructuredHudTimeline.load(job));
    }

    @Test
    void acceptsTheLastMinecraftInventoryEquipmentSlot() throws Exception {
        RenderJobSpec job = writeJob(sidecarWithInventorySlot(42));

        StructuredHudTimeline timeline = StructuredHudTimeline.load(job);

        assertEquals(2, timeline.resultEnvelope().get("records").getAsLong());
    }

    @Test
    void rejectsInventorySlotsOutsideMinecraftInventory() throws Exception {
        RenderJobSpec job = writeJob(sidecarWithInventorySlot(43));

        assertThrows(java.io.IOException.class, () -> StructuredHudTimeline.load(job));
    }

    @Test
    void keepsReplayViewerVitalsOutOfTheProjectionPlan() {
        assertEquals(
            new StructuredHudTimeline.ProjectionPlan(true, true, false),
            StructuredHudTimeline.decideProjection(
                StructuredHudTimeline.CameraKind.REQUESTED_PLAYER, true
            )
        );
        assertEquals(
            new StructuredHudTimeline.ProjectionPlan(false, false, false),
            StructuredHudTimeline.decideProjection(
                StructuredHudTimeline.CameraKind.REPLAY_VIEWER, false
            )
        );
        assertEquals(
            new StructuredHudTimeline.ProjectionPlan(false, false, false),
            StructuredHudTimeline.decideProjection(
                StructuredHudTimeline.CameraKind.OTHER, false
            )
        );
        assertEquals(
            new StructuredHudTimeline.ProjectionPlan(false, false, true),
            StructuredHudTimeline.decideProjection(
                StructuredHudTimeline.CameraKind.REPLAY_VIEWER, true
            )
        );
        assertEquals(
            new StructuredHudTimeline.ProjectionPlan(false, false, true),
            StructuredHudTimeline.decideProjection(
                StructuredHudTimeline.CameraKind.OTHER, true
            )
        );
    }

    private String sidecar(long... ticks) {
        StringBuilder value = new StringBuilder();
        for (long tick : ticks) {
            value.append("""
                {"schema_version":1,"sidecar_type":"mc-recorder-structured-hud-v1","session_id":"session","server_tick":%d,"player_uuid":"11111111-1111-1111-1111-111111111111","connection_id":"22222222-2222-2222-2222-222222222222","state":{"health":20,"max_health":20,"absorption":0,"air":300,"max_air":300,"food_level":20,"saturation":5,"experience_progress":0,"experience_level":0,"total_experience":0,"selected_slot":0,"inventory":[]}}
                """.formatted(tick));
        }
        return value.toString();
    }

    private String sidecarWithInventorySlot(int slot) {
        String value = sidecar(10, 11);
        return value.replace(
            "\"inventory\":[]",
            "\"inventory\":[{\"slot\":" + slot
                + ",\"stack_snbt\":\"{id:\\\"minecraft:stone\\\",count:1}\""
                + ",\"item\":\"minecraft:stone\",\"count\":1,\"damage\":0,\"max_damage\":0}]"
        );
    }

    private RenderJobSpec writeJob(String sidecar) throws Exception {
        Path replay = temporary.resolve("replay.zip");
        Files.write(replay, new byte[0]);
        Files.createDirectories(temporary.resolve("frames"));
        byte[] sidecarBytes = sidecar.getBytes(StandardCharsets.UTF_8);
        Files.write(temporary.resolve("hud-states.jsonl"), sidecarBytes);
        String sidecarSha256 = HexFormat.of().formatHex(
            MessageDigest.getInstance("SHA-256").digest(sidecarBytes)
        );
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
              "global_end_tick":11,
              "no_gui":false,
              "presentation_contract":"flashback_server_spectate_structured_hud_v1",
              "structured_hud":{
                "schema_version":1,
                "type":"mc-recorder-structured-hud-v1",
                "path":"hud-states.jsonl",
                "format":"jsonl",
                "sha256":"%s",
                "size_bytes":%d,
                "records":2,
                "start_server_tick":10,
                "end_server_tick":11,
                "dataset_id":"cccccccccccccccccccccccccccccccc",
                "dataset_manifest_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                "samples_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                "session_id":"session",
                "player_uuid":"11111111-1111-1111-1111-111111111111",
                "connection_id":"22222222-2222-2222-2222-222222222222"
              }
            }
            """.formatted(sidecarSha256, sidecarBytes.length));
        return RenderJobSpec.read(job);
    }
}
