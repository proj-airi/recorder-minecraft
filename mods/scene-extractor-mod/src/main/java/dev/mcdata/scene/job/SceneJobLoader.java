package dev.mcdata.scene.job;

import dev.minerec.artifacts.v1.SceneExtractionJob;
import dev.minerec.artifacts.v1.SceneSourceReplay;
import dev.minerec.artifacts.v1.SubjectPose;
import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.io.Hashing;
import com.google.protobuf.util.JsonFormat;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/** Strict protobuf parser and filesystem boundary for one owned scene extraction job. */
public final class SceneJobLoader {
    private static final long MAX_JOB_BYTES = 4L * 1024 * 1024;
    private static final long MAX_POSE_BYTES = 256L * 1024 * 1024;

    private SceneJobLoader() { }

    public static SceneJob load(Path requestPath) throws IOException {
        Path request = requestPath.toAbsolutePath().normalize();
        Path root = request.getParent();
        if (!request.equals(root.resolve("scene-job.json"))) {
            throw new IOException("scene job must be the canonical scene-job.json file");
        }
        BasicFileAttributes requestAttributes = regularFile(request, MAX_JOB_BYTES, "scene job");
        SceneExtractionJob value;
        try (var reader = Files.newBufferedReader(request, StandardCharsets.UTF_8)) {
            SceneExtractionJob.Builder builder = SceneExtractionJob.newBuilder();
            JsonFormat.parser().merge(reader, builder);
            value = builder.build();
        }
        verifyUnchanged(request, requestAttributes, "scene job");

        UUID player = canonicalUuid(value.getPlayerUuid(), "player_uuid");
        UUID connection = canonicalUuid(value.getConnectionId(), "connection_id");
        if (value.getJobId().isBlank() || value.getSessionId().isBlank()) {
            throw new IOException("scene job identity must not be blank");
        }
        if (!value.hasTicks() || value.getTicks().getLastTick() < value.getTicks().getFirstTick()) {
            throw new IOException("scene job has an invalid tick range");
        }
        if (!SceneJob.SCOPE.equals(value.getScope()) || !SceneJob.METADATA_POLICY.equals(value.getMetadataPolicy())) {
            throw new IOException("scene job declares unsupported extraction policy");
        }
        if (!SceneJob.FLASHBACK_CAPTURE_CONTRACT.equals(value.getFlashbackCaptureContract())) {
            throw new IOException("scene job declares an unsupported Flashback capture contract");
        }
        Path output = Path.of(value.getOutputPath()).toAbsolutePath().normalize();
        if (!output.equals(root.resolve("stream"))) {
            throw new IOException("scene output must be the owned stream directory");
        }
        Path result = root.resolve("result.json");
        List<SceneJob.SourceReplay> sources = new ArrayList<>();
        for (SceneSourceReplay source : value.getSourceReplaysList()) {
            Path path = Path.of(source.getPath()).toAbsolutePath().normalize();
            BasicFileAttributes attributes = regularFile(path, Long.MAX_VALUE, "source replay");
            if (attributes.size() != source.getSizeBytes() || !Hashing.sha256(path).equals(source.getSha256())) {
                throw new IOException("source replay integrity does not match the scene job");
            }
            sources.add(new SceneJob.SourceReplay(
                canonicalUuid(source.getSegmentId(), "segment_id"), source.getSegmentOrdinal(), path,
                source.getSha256(), source.getSizeBytes()
            ));
        }
        if (sources.isEmpty()) {
            throw new IOException("scene job has no source replay");
        }
        SceneJob.SubjectPoseInput poses = loadPoses(value, root, player, connection);
        return new SceneJob(
            request, value.getJobId(), value.getSessionId(), player, connection,
            value.getTicks().getFirstTick(), value.getTicks().getLastTick(), output, result,
            sources, poses, value.getStopWhenDone()
        );
    }

    public static void verifySubjectPosesUnchanged(SceneJob job) throws IOException {
        SceneJob.SubjectPoseInput input = job.subjectPoses();
        BasicFileAttributes current = regularFile(input.path(), MAX_POSE_BYTES, "subject poses");
        String key = String.valueOf(current.fileKey());
        if (!key.equals(input.fileIdentity().fileKey())
            || current.lastModifiedTime().toMillis() != input.fileIdentity().lastModifiedMillis()
            || current.size() != input.sizeBytes()
            || !Hashing.sha256(input.path()).equals(input.sha256())) {
            throw new IOException("subject pose stream changed after validation");
        }
    }

    private static SceneJob.SubjectPoseInput loadPoses(
        SceneExtractionJob job,
        Path root,
        UUID player,
        UUID connection
    ) throws IOException {
        Path path = Path.of(job.getSubjectPoses().getPath()).toAbsolutePath().normalize();
        if (!path.equals(root.resolve("subject-poses.jsonl"))) {
            throw new IOException("subject poses must be the owned subject-poses.jsonl file");
        }
        BasicFileAttributes attributes = regularFile(path, MAX_POSE_BYTES, "subject poses");
        if (attributes.size() != job.getSubjectPoses().getSizeBytes()
            || !Hashing.sha256(path).equals(job.getSubjectPoses().getSha256())) {
            throw new IOException("subject pose integrity does not match the scene job");
        }
        List<SubjectPose> values = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String line;
            while ((line = reader.readLine()) != null) {
                SubjectPose.Builder pose = SubjectPose.newBuilder();
                JsonFormat.parser().merge(line, pose);
                values.add(pose.build());
            }
        }
        long first = job.getTicks().getFirstTick();
        long last = job.getTicks().getLastTick();
        if (values.size() != last - first + 1) {
            throw new IOException("subject pose count does not cover the requested tick range");
        }
        int count = values.size();
        int[] entityIds = new int[count];
        String[] dimensions = new String[count];
        double[] px = new double[count];
        double[] py = new double[count];
        double[] pz = new double[count];
        double[] vx = new double[count];
        double[] vy = new double[count];
        double[] vz = new double[count];
        float[] yaw = new float[count];
        float[] pitch = new float[count];
        float[] headYaw = new float[count];
        boolean[] onGround = new boolean[count];
        for (int index = 0; index < count; index++) {
            SubjectPose pose = values.get(index);
            long expectedTick = first + index;
            if (pose.getServerTick() != expectedTick || !pose.getSessionId().equals(job.getSessionId())
                || !pose.getPlayerUuid().equals(player.toString()) || !pose.getConnectionId().equals(connection.toString())) {
                throw new IOException("subject pose identity or timeline does not match the scene job");
            }
            entityIds[index] = pose.getEntityId();
            dimensions[index] = pose.getDimension();
            px[index] = pose.getPosition().getX(); py[index] = pose.getPosition().getY(); pz[index] = pose.getPosition().getZ();
            vx[index] = pose.getVelocity().getX(); vy[index] = pose.getVelocity().getY(); vz[index] = pose.getVelocity().getZ();
            yaw[index] = (float) pose.getRotation().getYaw();
            pitch[index] = (float) pose.getRotation().getPitch();
            headYaw[index] = (float) pose.getRotation().getHeadYaw();
            onGround[index] = pose.getOnGround();
        }
        var timeline = new SceneJob.SubjectPoseTimeline(
            first, job.getSessionId(), player, connection, entityIds, dimensions,
            px, py, pz, vx, vy, vz, yaw, pitch, headYaw, onGround
        );
        var source = job.getSourceEvents();
        return new SceneJob.SubjectPoseInput(
            "application/jsonl", path, job.getSubjectPoses().getSha256(), attributes.size(), count,
            first, last, new SceneJob.SourceEvents(source.getSha256(), source.getSizeBytes(), 0),
            new SceneJob.SubjectPoseFileIdentity(String.valueOf(attributes.fileKey()), attributes.lastModifiedTime().toMillis()), timeline
        );
    }

    private static BasicFileAttributes regularFile(Path path, long maxBytes, String label) throws IOException {
        BasicFileAttributes attributes = Files.readAttributes(path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
        if (!attributes.isRegularFile() || Files.isSymbolicLink(path) || attributes.size() > maxBytes) {
            throw new IOException(label + " must be a bounded non-symlinked regular file: " + path);
        }
        return attributes;
    }

    private static void verifyUnchanged(Path path, BasicFileAttributes before, String label) throws IOException {
        BasicFileAttributes after = regularFile(path, MAX_JOB_BYTES, label);
        if (before.size() != after.size() || before.lastModifiedTime().toMillis() != after.lastModifiedTime().toMillis()
            || !String.valueOf(before.fileKey()).equals(String.valueOf(after.fileKey()))) {
            throw new IOException(label + " changed while it was being read");
        }
    }

    private static UUID canonicalUuid(String value, String label) throws IOException {
        try {
            UUID parsed = UUID.fromString(value);
            if (!parsed.toString().equals(value)) {
                throw new IllegalArgumentException();
            }
            return parsed;
        } catch (IllegalArgumentException exception) {
            throw new IOException(label + " must be a canonical UUID", exception);
        }
    }
}
