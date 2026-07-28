package dev.mcdata.scene.job;

import dev.minerec.artifacts.v1.ArtifactFile;
import dev.minerec.artifacts.v1.Rotation;
import dev.minerec.artifacts.v1.SceneExtractionJob;
import dev.minerec.artifacts.v1.SceneSourceReplay;
import dev.minerec.artifacts.v1.SubjectPose;
import dev.minerec.artifacts.v1.TickRange;
import dev.minerec.artifacts.v1.Vector3;
import dev.mcdata.scene.io.Hashing;
import com.google.protobuf.util.JsonFormat;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class SceneJobLoaderTest {
    private static final String PLAYER = "11111111-1111-1111-1111-111111111111";
    private static final String CONNECTION = "22222222-2222-2222-2222-222222222222";

    @TempDir
    Path temporary;

    @Test
    void acceptsTheGeneratedContract() throws IOException {
        Path request = writeJob(false);
        SceneJob job = SceneJobLoader.load(request);
        assertEquals("job-1", job.jobId());
        assertEquals(2, job.sourceReplays().getFirst().segmentOrdinal());
        assertEquals(temporary.resolve("result.json"), job.result());
        assertEquals(11.25, job.subjectPoses().require(11).position().x());
        assertEquals(7, job.subjectPoses().require(11).entityId());
    }

    @Test
    void rejectsPolicyAndOutputViolations() throws IOException {
        Path request = writeJob(false);
        SceneExtractionJob value = parseJob(request);
        Files.writeString(request, JsonFormat.printer().print(value.toBuilder().setFlashbackCaptureContract("legacy").build()));
        Path legacy = request;
        assertThrows(IOException.class, () -> SceneJobLoader.load(legacy));

        request = writeJob(false);
        value = parseJob(request);
        Files.writeString(request, JsonFormat.printer().print(value.toBuilder().setOutputPath(temporary.getParent().resolve("stream").toString()).build()));
        Path escaped = request;
        assertThrows(IOException.class, () -> SceneJobLoader.load(escaped));
    }

    @Test
    void rejectsPoseIdentityMismatchAndDetectsPostLoadReplacement() throws IOException {
        Path mismatch = writeJob(true);
        assertThrows(IOException.class, () -> SceneJobLoader.load(mismatch));

        Path request = writeJob(false);
        SceneJob job = SceneJobLoader.load(request);
        Files.write(job.subjectPoses().path(), new byte[] {1}, StandardOpenOption.APPEND);
        assertThrows(IOException.class, () -> SceneJobLoader.verifySubjectPosesUnchanged(job));
    }

    private Path writeJob(boolean identityMismatch) throws IOException {
        Path source = temporary.resolve("source.zip");
        Files.write(source, new byte[] {1});
        Path poses = temporary.resolve("subject-poses.jsonl");
        try (var output = Files.newOutputStream(poses)) {
            for (long tick = 10; tick <= 12; tick++) {
                SubjectPose pose = SubjectPose.newBuilder()
                    .setServerTick(tick).setSessionId("session-1").setPlayerUuid(PLAYER)
                    .setConnectionId(identityMismatch && tick == 11 ? "99999999-9999-4999-8999-999999999999" : CONNECTION)
                    .setEntityId(7).setDimension("minecraft:overworld")
                    .setPosition(Vector3.newBuilder().setX(tick + .25).setY(64.5).setZ(-2))
                    .setVelocity(Vector3.newBuilder().setX(.1).setY(.2).setZ(.3))
                    .setRotation(Rotation.newBuilder().setYaw(12).setPitch(-4).setHeadYaw(13))
                    .setOnGround(true).build();
                output.write(JsonFormat.printer().omittingInsignificantWhitespace().print(pose).getBytes(java.nio.charset.StandardCharsets.UTF_8));
                output.write('\n');
            }
        }
        SceneExtractionJob job = SceneExtractionJob.newBuilder()
            .setJobId("job-1").setSessionId("session-1").setPlayerUuid(PLAYER).setConnectionId(CONNECTION)
            .setTicks(TickRange.newBuilder().setFirstTick(10).setLastTick(12))
            .setScope(SceneJob.SCOPE).setMetadataPolicy(SceneJob.METADATA_POLICY)
            .setFlashbackCaptureContract(SceneJob.FLASHBACK_CAPTURE_CONTRACT)
            .addSourceReplays(SceneSourceReplay.newBuilder().setSegmentId("33333333-3333-3333-3333-333333333333")
                .setSegmentOrdinal(2).setPath(source.toString()).setSha256(Hashing.sha256(source)).setSizeBytes(1).setFormat("flashback"))
            .setSubjectPoses(ArtifactFile.newBuilder().setPath(poses.toString()).setSha256(Hashing.sha256(poses)).setSizeBytes(Files.size(poses)))
            .setSourceEvents(ArtifactFile.newBuilder().setPath(temporary.resolve("events.jsonl").toString()).setSha256("b".repeat(64)).setSizeBytes(123))
            .setOutputPath(temporary.resolve("stream").toString()).setStopWhenDone(true).build();
        Path request = temporary.resolve("scene-job.json");
        Files.writeString(request, JsonFormat.printer().print(job));
        return request;
    }

    private static SceneExtractionJob parseJob(Path path) throws IOException {
        SceneExtractionJob.Builder value = SceneExtractionJob.newBuilder();
        JsonFormat.parser().merge(Files.readString(path), value);
        return value.build();
    }
}
