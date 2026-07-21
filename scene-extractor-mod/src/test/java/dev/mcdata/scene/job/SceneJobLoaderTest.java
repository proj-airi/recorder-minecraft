package dev.mcdata.scene.job;

import dev.mcdata.scene.io.Hashing;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class SceneJobLoaderTest {
    @TempDir
    Path temporary;

    @Test
    void acceptsTheExactContract() throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path poses = writePoses(temporary.resolve("subject-poses.jsonl"), validPoses());
        Path request = writeJob(source, poses, temporary.resolve("spool"), "");

        SceneJob job = SceneJobLoader.load(request);

        assertEquals("job-1", job.jobId());
        assertEquals(2, job.sourceReplays().getFirst().segmentOrdinal());
        assertEquals(temporary.resolve("result.json"), job.result());
        assertEquals(11.25, job.subjectPoses().require(11).position().x());
        assertEquals(7, job.subjectPoses().require(11).entityId());
    }

    @Test
    void rejectsUnknownKeys() throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path poses = writePoses(temporary.resolve("subject-poses.jsonl"), validPoses());
        Path request = writeJob(source, poses, temporary.resolve("spool"), ",\"surprise\":true");
        assertThrows(IOException.class, () -> SceneJobLoader.load(request));
    }

    @Test
    void rejectsOutputOutsideTheJobRoot() throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path poses = writePoses(temporary.resolve("subject-poses.jsonl"), validPoses());
        Path request = writeJob(source, poses, temporary.getParent().resolve("spool"), "");
        assertThrows(IOException.class, () -> SceneJobLoader.load(request));
    }

    @Test
    void rejectsPoseContainmentAndSymlinkViolations() throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path outside = writePoses(temporary.getParent().resolve("outside-poses.jsonl"), validPoses());
        Path escaped = writeJob(source, outside, temporary.resolve("spool"), "");
        assertThrows(IOException.class, () -> SceneJobLoader.load(escaped));

        Files.delete(escaped);
        Path link = temporary.resolve("subject-poses.jsonl");
        Files.createSymbolicLink(link, outside);
        Path linked = writeJob(source, link, temporary.resolve("spool"), "");
        assertThrows(IOException.class, () -> SceneJobLoader.load(linked));
        Files.deleteIfExists(outside);
    }

    @Test
    void rejectsPoseHashAndSizeTampering() throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path poses = writePoses(temporary.resolve("subject-poses.jsonl"), validPoses());
        Path request = writeJob(source, poses, temporary.resolve("spool"), "");

        Files.writeString(poses, "{}\n", java.nio.file.StandardOpenOption.APPEND);

        assertThrows(IOException.class, () -> SceneJobLoader.load(request));
    }

    @Test
    void rejectsNonContiguousCoverageAndNonFinitePoseValues() throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path poses = writePoses(
            temporary.resolve("subject-poses.jsonl"),
            List.of(poseLine(10), poseLine(12), poseLine(13))
        );
        Path request = writeJob(source, poses, temporary.resolve("spool"), "");
        Path nonContiguous = request;
        assertThrows(IOException.class, () -> SceneJobLoader.load(nonContiguous));

        Files.delete(request);
        Files.delete(poses);
        poses = writePoses(
            temporary.resolve("subject-poses.jsonl"),
            List.of(poseLine(10), poseLine(11).replace("11.25", "1e400"), poseLine(12))
        );
        request = writeJob(source, poses, temporary.resolve("spool"), "");
        Path invalid = request;
        assertThrows(IOException.class, () -> SceneJobLoader.load(invalid));
    }

    @Test
    void rejectsPoseIdentityMismatchAndDetectsPostLoadReplacement() throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path poses = writePoses(
            temporary.resolve("subject-poses.jsonl"),
            List.of(
                poseLine(10),
                poseLine(11).replace(
                    "22222222-2222-2222-2222-222222222222",
                    "99999999-9999-4999-8999-999999999999"
                ),
                poseLine(12)
            )
        );
        Path request = writeJob(source, poses, temporary.resolve("spool"), "");
        Path identityMismatch = request;
        assertThrows(IOException.class, () -> SceneJobLoader.load(identityMismatch));

        Files.delete(request);
        Files.delete(poses);
        poses = writePoses(temporary.resolve("subject-poses.jsonl"), validPoses());
        request = writeJob(source, poses, temporary.resolve("spool"), "");
        SceneJob job = SceneJobLoader.load(request);
        Files.writeString(poses, "tampered\n");
        assertThrows(IOException.class, () -> SceneJobLoader.verifySubjectPosesUnchanged(job));
    }

    private Path writeJob(Path source, Path poses, Path output, String extra) throws IOException {
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
              "subject_poses": {
                "format": "mc-recorder-subject-poses-v1",
                "path": "%s",
                "sha256": "%s",
                "size_bytes": %d,
                "record_count": 3,
                "first_tick": 10,
                "last_tick": 12,
                "source_epochs": [{
                  "epoch_index": 0,
                  "events_sha256": "%s",
                  "events_size_bytes": 123,
                  "record_count": 9
                }]
              },
              "output": "%s",
              "stop_when_done": true%s
            }
            """.formatted(
                source, "a".repeat(64), poses, Hashing.sha256(poses), Files.size(poses),
                "b".repeat(64), output, extra
            );
        Files.writeString(request, json);
        return request;
    }

    private static Path writePoses(Path path, List<String> records) throws IOException {
        Files.writeString(path, String.join("", records));
        return path;
    }

    private static List<String> validPoses() {
        return List.of(poseLine(10), poseLine(11), poseLine(12));
    }

    private static String poseLine(long tick) {
        return """
            {"schema_version":1,"server_tick":%d,"session_id":"session-1","player_uuid":"11111111-1111-1111-1111-111111111111","connection_id":"22222222-2222-2222-2222-222222222222","entity_id":7,"dimension":"minecraft:overworld","position":{"x":%s,"y":64.5,"z":-2.0},"velocity":{"x":0.1,"y":0.2,"z":0.3},"yaw":12.0,"pitch":-4.0,"head_yaw":13.0,"on_ground":true}
            """.formatted(tick, tick + ".25");
    }
}
