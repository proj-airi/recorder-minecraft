package dev.mcdata.scene.job;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class SceneJobLoaderTest {
    @TempDir
    Path temporary;

    @Test
    void acceptsTheExactContract() throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path request = writeJob(source, temporary.resolve("spool"), "");

        SceneJob job = SceneJobLoader.load(request);

        assertEquals("job-1", job.jobId());
        assertEquals(2, job.sourceReplays().getFirst().segmentOrdinal());
        assertEquals(temporary.resolve("result.json"), job.result());
    }

    @Test
    void rejectsUnknownKeys() throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path request = writeJob(source, temporary.resolve("spool"), ",\"surprise\":true");
        assertThrows(IOException.class, () -> SceneJobLoader.load(request));
    }

    @Test
    void rejectsOutputOutsideTheJobRoot() throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path request = writeJob(source, temporary.getParent().resolve("spool"), "");
        assertThrows(IOException.class, () -> SceneJobLoader.load(request));
    }

    private Path writeJob(Path source, Path output, String extra) throws IOException {
        Path request = temporary.resolve("scene-job.json");
        String json = """
            {
              "schema_version": 1,
              "job_id": "job-1",
              "session_id": "session-1",
              "subject": {
                "player_uuid": "11111111-1111-1111-1111-111111111111",
                "connection_id": "22222222-2222-2222-2222-222222222222"
              },
              "global_start_tick": 10,
              "global_end_tick": 12,
              "scope": "client_visible",
              "metadata_policy": "full_packet_metadata",
              "source_replays": [{
                "segment_id": "33333333-3333-3333-3333-333333333333",
                "segment_ordinal": 2,
                "path": "%s",
                "sha256": "%s",
                "size_bytes": 1,
                "format": "flashback"
              }],
              "output": "%s",
              "stop_when_done": true%s
            }
            """.formatted(source, "a".repeat(64), output, extra);
        Files.writeString(request, json);
        return request;
    }
}
