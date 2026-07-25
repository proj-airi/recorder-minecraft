package dev.mcdata.scene.job;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dev.mcdata.scene.core.SceneEvent;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.Reader;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

/** Strict parser and path boundary for scene-job.json. */
public final class SceneJobLoader {
    private static final long MAX_JOB_BYTES = 1_048_576;
    private static final long MAX_SUBJECT_POSE_BYTES = 256L * 1024 * 1024;
    private static final int MAX_SUBJECT_POSE_RECORD_BYTES = 16 * 1024;
    private static final Pattern OPAQUE_ID = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,159}");
    private static final Pattern SHA256 = Pattern.compile("[0-9a-f]{64}");
    private static final Set<String> ROOT_KEYS = Set.of(
        "schema_version", "job_id", "session_id", "subject", "global_start_tick",
        "global_end_tick", "scope", "metadata_policy", "source_replays", "output",
        "subject_poses", "flashback_capture_contract", "stop_when_done"
    );
    private static final Set<String> SUBJECT_KEYS = Set.of("player_uuid", "connection_id");
    private static final Set<String> SOURCE_KEYS = Set.of(
        "segment_id", "segment_ordinal", "path", "sha256", "size_bytes", "format"
    );
    private static final Set<String> SUBJECT_POSE_KEYS = Set.of(
        "format", "path", "sha256", "size_bytes", "record_count", "first_tick",
        "last_tick", "source_events"
    );
    private static final Set<String> SUBJECT_POSE_EVENT_SOURCE_KEYS = Set.of(
        "events_sha256", "events_size_bytes", "record_count"
    );
    private static final Set<String> SUBJECT_POSE_RECORD_KEYS = Set.of(
        "schema_version", "server_tick", "session_id", "player_uuid", "connection_id",
        "entity_id", "dimension", "position", "velocity", "yaw", "pitch", "head_yaw",
        "on_ground"
    );
    private static final Set<String> VECTOR_KEYS = Set.of("x", "y", "z");
    private static final Pattern RESOURCE_LOCATION = Pattern.compile("[a-z0-9_.-]+:[a-z0-9/._-]+");

    private SceneJobLoader() { }

    public static SceneJob load(Path request) throws IOException {
        Path normalizedRequest = requireAbsoluteNormalized(request, "scene job");
        if (!Files.isRegularFile(normalizedRequest, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("scene job must be a regular non-symlink file: " + normalizedRequest);
        }
        long size = Files.size(normalizedRequest);
        if (size <= 0 || size > MAX_JOB_BYTES) {
            throw new IOException("scene job size must be in 1.." + MAX_JOB_BYTES + " bytes");
        }

        JsonObject root;
        try (Reader reader = Files.newBufferedReader(normalizedRequest)) {
            JsonElement parsed = JsonParser.parseReader(reader);
            if (!parsed.isJsonObject()) {
                throw new IOException("scene job root must be an object");
            }
            root = parsed.getAsJsonObject();
        } catch (RuntimeException exception) {
            throw new IOException("scene job is not valid JSON", exception);
        }
        requireExactKeys(root, ROOT_KEYS, "scene job");
        if (requiredInt(root, "schema_version") != 1) {
            throw new IOException("scene job schema_version must be 1");
        }

        String jobId = requiredOpaque(root, "job_id");
        String sessionId = requiredOpaque(root, "session_id");
        JsonObject subject = requiredObject(root, "subject");
        requireExactKeys(subject, SUBJECT_KEYS, "scene job subject");
        UUID playerUuid = requiredUuid(subject, "player_uuid");
        UUID connectionId = requiredUuid(subject, "connection_id");
        long start = requiredLong(root, "global_start_tick");
        long end = requiredLong(root, "global_end_tick");
        if (start < 0 || end < start) {
            throw new IOException("scene job global tick range is invalid");
        }
        if (end - start > 10_000_000L) {
            throw new IOException("scene job global tick range exceeds the 10,000,001 frame safety limit");
        }
        requireLiteral(root, "scope", SceneJob.SCOPE);
        requireLiteral(root, "metadata_policy", SceneJob.METADATA_POLICY);
        requireLiteral(
            root, "flashback_capture_contract", SceneJob.FLASHBACK_CAPTURE_CONTRACT
        );
        boolean stopWhenDone = requiredBoolean(root, "stop_when_done");
        if (!stopWhenDone) {
            throw new IOException("scene extractor only accepts stop_when_done=true");
        }

        Path jobRoot = normalizedRequest.getParent();
        if (jobRoot == null || !Files.isDirectory(jobRoot, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("scene job must have an existing non-symlink parent directory");
        }
        jobRoot = jobRoot.toRealPath(LinkOption.NOFOLLOW_LINKS);
        SceneJob.SubjectPoseInput subjectPoses = loadSubjectPoses(
            root, jobRoot, sessionId, playerUuid, connectionId, start, end
        );
        Path output = requireAbsoluteNormalized(Path.of(requiredString(root, "output")), "output");
        if (!output.getParent().equals(jobRoot)) {
            throw new IOException("scene output must be a direct child of the scene job directory");
        }
        if (Files.exists(output, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("scene output already exists: " + output);
        }
        Path result = jobRoot.resolve("result.json");
        if (Files.exists(result, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("scene result already exists: " + result);
        }

        JsonArray sources = requiredArray(root, "source_replays");
        if (sources.isEmpty()) {
            throw new IOException("scene job requires at least one source replay");
        }
        List<SceneJob.SourceReplay> parsedSources = new ArrayList<>(sources.size());
        Set<UUID> ids = new HashSet<>();
        Set<Integer> ordinals = new HashSet<>();
        int previousOrdinal = -1;
        for (int index = 0; index < sources.size(); index++) {
            JsonElement element = sources.get(index);
            if (!element.isJsonObject()) {
                throw new IOException("source_replays[" + index + "] must be an object");
            }
            JsonObject source = element.getAsJsonObject();
            requireExactKeys(source, SOURCE_KEYS, "source_replays[" + index + "]");
            requireLiteral(source, "format", "flashback");
            UUID segmentId = requiredUuid(source, "segment_id");
            int ordinal = requiredInt(source, "segment_ordinal");
            if (ordinal < 0 || ordinal <= previousOrdinal || !ordinals.add(ordinal) || !ids.add(segmentId)) {
                throw new IOException("source replays must have unique, strictly increasing non-negative ordinals");
            }
            previousOrdinal = ordinal;
            String digest = requiredString(source, "sha256");
            if (!SHA256.matcher(digest).matches()) {
                throw new IOException("source replay sha256 must be lowercase hexadecimal");
            }
            long sourceSize = requiredLong(source, "size_bytes");
            if (sourceSize <= 0) {
                throw new IOException("source replay size_bytes must be positive");
            }
            Path sourcePath = requireAbsoluteNormalized(Path.of(requiredString(source, "path")), "source replay");
            if (!Files.isRegularFile(sourcePath, LinkOption.NOFOLLOW_LINKS)) {
                throw new IOException("source replay must be a regular non-symlink file: " + sourcePath);
            }
            parsedSources.add(new SceneJob.SourceReplay(segmentId, ordinal, sourcePath, digest, sourceSize));
        }

        return new SceneJob(
            normalizedRequest, jobId, sessionId, playerUuid, connectionId, start, end,
            output, result, parsedSources, subjectPoses, true
        );
    }

    public static void verifySubjectPosesUnchanged(SceneJob job) throws IOException {
        SceneJob.SubjectPoseInput input = job.subjectPoses();
        BasicFileAttributes attributes = subjectPoseAttributes(input.path());
        String fileKey = String.valueOf(attributes.fileKey());
        if (
            attributes.size() != input.sizeBytes()
                || attributes.lastModifiedTime().toMillis() != input.fileIdentity().lastModifiedMillis()
                || !fileKey.equals(input.fileIdentity().fileKey())
                || !hashSubjectPoseFile(input.path()).equals(input.sha256())
        ) {
            throw new IOException("subject pose stream changed during scene extraction");
        }
    }

    private static SceneJob.SubjectPoseInput loadSubjectPoses(
        JsonObject root,
        Path jobRoot,
        String sessionId,
        UUID playerUuid,
        UUID connectionId,
        long start,
        long end
    ) throws IOException {
        JsonObject envelope = requiredObject(root, "subject_poses");
        requireExactKeys(envelope, SUBJECT_POSE_KEYS, "scene job subject_poses");
        requireLiteral(envelope, "format", "mc-recorder-subject-poses-v1");
        Path path = requireAbsoluteNormalized(
            Path.of(requiredString(envelope, "path")), "subject pose stream"
        );
        if (!path.equals(jobRoot.resolve("subject-poses.jsonl"))) {
            throw new IOException(
                "subject pose stream must be the owned subject-poses.jsonl in the scene job directory"
            );
        }
        BasicFileAttributes before = subjectPoseAttributes(path);
        long sizeBytes = requiredLong(envelope, "size_bytes");
        if (sizeBytes <= 0 || sizeBytes > MAX_SUBJECT_POSE_BYTES || before.size() != sizeBytes) {
            throw new IOException(
                "subject pose stream size must match its envelope and be in 1.."
                    + MAX_SUBJECT_POSE_BYTES + " bytes"
            );
        }
        String sha256 = requiredString(envelope, "sha256");
        if (!SHA256.matcher(sha256).matches()) {
            throw new IOException("subject pose stream sha256 must be lowercase hexadecimal");
        }
        long expectedCount = end - start + 1;
        long recordCount = requiredLong(envelope, "record_count");
        if (
            recordCount != expectedCount
                || recordCount > Integer.MAX_VALUE
                || requiredLong(envelope, "first_tick") != start
                || requiredLong(envelope, "last_tick") != end
        ) {
            throw new IOException("subject pose stream coverage does not exactly match the scene job");
        }

        JsonObject source = requiredObject(envelope, "source_events");
        requireExactKeys(source, SUBJECT_POSE_EVENT_SOURCE_KEYS, "subject_poses source_events");
        long eventsSize = requiredLong(source, "events_size_bytes");
        long sourceRecordCount = requiredLong(source, "record_count");
        String eventsSha256 = requiredString(source, "events_sha256");
        if (
            eventsSize <= 0
                || sourceRecordCount <= 0
                || !SHA256.matcher(eventsSha256).matches()
        ) {
            throw new IOException("subject pose source events integrity is invalid");
        }
        SceneJob.SourceEvents sourceEvents = new SceneJob.SourceEvents(
            eventsSha256, eventsSize, sourceRecordCount
        );

        SceneJob.SubjectPoseTimeline timeline = readSubjectPoseTimeline(
            path, sha256, sizeBytes, (int) recordCount, start,
            sessionId, playerUuid, connectionId
        );
        BasicFileAttributes after = subjectPoseAttributes(path);
        if (!sameFile(before, after)) {
            throw new IOException("subject pose stream changed while it was being loaded");
        }
        return new SceneJob.SubjectPoseInput(
            "mc-recorder-subject-poses-v1",
            path,
            sha256,
            sizeBytes,
            recordCount,
            start,
            end,
            sourceEvents,
            new SceneJob.SubjectPoseFileIdentity(
                String.valueOf(after.fileKey()), after.lastModifiedTime().toMillis()
            ),
            timeline
        );
    }

    private static BasicFileAttributes subjectPoseAttributes(Path path) throws IOException {
        BasicFileAttributes attributes = Files.readAttributes(
            path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS
        );
        if (!attributes.isRegularFile() || Files.isSymbolicLink(path)) {
            throw new IOException("subject pose stream must be a regular non-symlink file: " + path);
        }
        return attributes;
    }

    private static boolean sameFile(BasicFileAttributes before, BasicFileAttributes after) {
        return before.size() == after.size()
            && before.lastModifiedTime().equals(after.lastModifiedTime())
            && String.valueOf(before.fileKey()).equals(String.valueOf(after.fileKey()));
    }

    private static SceneJob.SubjectPoseTimeline readSubjectPoseTimeline(
        Path path,
        String expectedSha256,
        long expectedSize,
        int expectedCount,
        long firstTick,
        String sessionId,
        UUID playerUuid,
        UUID connectionId
    ) throws IOException {
        PoseArrays values = new PoseArrays(expectedCount);
        MessageDigest digest = sha256Digest();
        long size = 0;
        int count = 0;
        ByteArrayOutputStream line = new ByteArrayOutputStream(512);
        Set<OpenOption> options = Set.of(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS);
        try (SeekableByteChannel channel = Files.newByteChannel(path, options)) {
            if (channel.size() != expectedSize) {
                throw new IOException("subject pose stream size does not match its envelope");
            }
            ByteBuffer buffer = ByteBuffer.allocate(64 * 1024);
            byte[] chunk = new byte[buffer.capacity()];
            while (channel.read(buffer) >= 0) {
                buffer.flip();
                int length = buffer.remaining();
                if (length == 0) {
                    buffer.clear();
                    continue;
                }
                buffer.get(chunk, 0, length);
                digest.update(chunk, 0, length);
                size += length;
                if (size > MAX_SUBJECT_POSE_BYTES) {
                    throw new IOException("subject pose stream exceeds its size limit");
                }
                for (int index = 0; index < length; index++) {
                    int value = chunk[index] & 0xFF;
                    if (value == '\n') {
                        if (line.size() == 0) {
                            throw new IOException("subject pose stream contains an empty record");
                        }
                        if (count >= expectedCount) {
                            throw new IOException("subject pose stream contains too many records");
                        }
                        parseSubjectPose(
                            line.toByteArray(), count, firstTick + count, sessionId,
                            playerUuid, connectionId, values
                        );
                        count++;
                        line.reset();
                    } else {
                        if (value == '\r') {
                            throw new IOException("subject pose stream must use LF line endings");
                        }
                        if (line.size() >= MAX_SUBJECT_POSE_RECORD_BYTES) {
                            throw new IOException("subject pose record exceeds the size limit");
                        }
                        line.write(value);
                    }
                }
                buffer.clear();
            }
            if (channel.size() != expectedSize) {
                throw new IOException("subject pose stream changed while it was being loaded");
            }
        }
        if (line.size() != 0) {
            throw new IOException("subject pose stream must end with a newline");
        }
        if (size != expectedSize || count != expectedCount) {
            throw new IOException("subject pose stream size or record count does not match its envelope");
        }
        if (!HexFormat.of().formatHex(digest.digest()).equals(expectedSha256)) {
            throw new IOException("subject pose stream SHA-256 does not match its envelope");
        }
        return values.timeline(firstTick, sessionId, playerUuid, connectionId);
    }

    private static void parseSubjectPose(
        byte[] bytes,
        int index,
        long expectedTick,
        String sessionId,
        UUID playerUuid,
        UUID connectionId,
        PoseArrays values
    ) throws IOException {
        String text;
        try {
            text = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes))
                .toString();
        } catch (CharacterCodingException exception) {
            throw new IOException("subject pose record is not valid UTF-8", exception);
        }
        JsonObject pose;
        try {
            JsonElement parsed = JsonParser.parseString(text);
            if (!parsed.isJsonObject()) {
                throw new IOException("subject pose record must be an object");
            }
            pose = parsed.getAsJsonObject();
        } catch (RuntimeException exception) {
            throw new IOException("subject pose record is not valid JSON", exception);
        }
        requireExactKeys(pose, SUBJECT_POSE_RECORD_KEYS, "subject pose record");
        if (requiredInt(pose, "schema_version") != 1) {
            throw new IOException("subject pose schema_version must be 1");
        }
        if (requiredLong(pose, "server_tick") != expectedTick) {
            throw new IOException("subject pose ticks must exactly and contiguously cover the job");
        }
        if (!requiredString(pose, "session_id").equals(sessionId)) {
            throw new IOException("subject pose session_id does not match the scene job");
        }
        if (!requiredUuid(pose, "player_uuid").equals(playerUuid)) {
            throw new IOException("subject pose player_uuid does not match the scene job");
        }
        if (!requiredUuid(pose, "connection_id").equals(connectionId)) {
            throw new IOException("subject pose connection_id does not match the scene job");
        }
        int entityId = requiredInt(pose, "entity_id");
        if (entityId < 0) {
            throw new IOException("subject pose entity_id must be non-negative");
        }
        String dimension = requiredString(pose, "dimension");
        if (dimension.length() > 32_767 || !RESOURCE_LOCATION.matcher(dimension).matches()) {
            throw new IOException("subject pose dimension is not a resource location");
        }
        JsonObject position = requiredObject(pose, "position");
        JsonObject velocity = requiredObject(pose, "velocity");
        requireExactKeys(position, VECTOR_KEYS, "subject pose position");
        requireExactKeys(velocity, VECTOR_KEYS, "subject pose velocity");
        values.entityIds[index] = entityId;
        values.dimensions[index] = values.canonicalDimensions.computeIfAbsent(
            dimension, ignored -> dimension
        );
        values.positionX[index] = requiredFiniteDouble(position, "x");
        values.positionY[index] = requiredFiniteDouble(position, "y");
        values.positionZ[index] = requiredFiniteDouble(position, "z");
        values.velocityX[index] = requiredFiniteDouble(velocity, "x");
        values.velocityY[index] = requiredFiniteDouble(velocity, "y");
        values.velocityZ[index] = requiredFiniteDouble(velocity, "z");
        values.yaw[index] = requiredFiniteFloat(pose, "yaw");
        values.pitch[index] = requiredFiniteFloat(pose, "pitch");
        values.headYaw[index] = requiredFiniteFloat(pose, "head_yaw");
        values.onGround[index] = requiredBoolean(pose, "on_ground");
    }

    private static double requiredFiniteDouble(JsonObject object, String name) throws IOException {
        JsonElement value = object.get(name);
        try {
            if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()) {
                throw new NumberFormatException();
            }
            double result = value.getAsDouble();
            if (!Double.isFinite(result)) {
                throw new NumberFormatException();
            }
            return result;
        } catch (NumberFormatException exception) {
            throw new IOException(name + " must be a finite number", exception);
        }
    }

    private static float requiredFiniteFloat(JsonObject object, String name) throws IOException {
        double value = requiredFiniteDouble(object, name);
        if (value < -Float.MAX_VALUE || value > Float.MAX_VALUE) {
            throw new IOException(name + " is outside the finite float range");
        }
        return (float) value;
    }

    private static MessageDigest sha256Digest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new AssertionError("SHA-256 is required by the Java runtime", exception);
        }
    }

    private static String hashSubjectPoseFile(Path path) throws IOException {
        MessageDigest digest = sha256Digest();
        Set<OpenOption> options = Set.of(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS);
        try (SeekableByteChannel channel = Files.newByteChannel(path, options)) {
            ByteBuffer buffer = ByteBuffer.allocate(1024 * 1024);
            while (channel.read(buffer) >= 0) {
                buffer.flip();
                digest.update(buffer);
                buffer.clear();
            }
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    private static final class PoseArrays {
        private final int[] entityIds;
        private final String[] dimensions;
        private final double[] positionX;
        private final double[] positionY;
        private final double[] positionZ;
        private final double[] velocityX;
        private final double[] velocityY;
        private final double[] velocityZ;
        private final float[] yaw;
        private final float[] pitch;
        private final float[] headYaw;
        private final boolean[] onGround;
        private final java.util.Map<String, String> canonicalDimensions = new HashMap<>();

        private PoseArrays(int count) {
            entityIds = new int[count];
            dimensions = new String[count];
            positionX = new double[count];
            positionY = new double[count];
            positionZ = new double[count];
            velocityX = new double[count];
            velocityY = new double[count];
            velocityZ = new double[count];
            yaw = new float[count];
            pitch = new float[count];
            headYaw = new float[count];
            onGround = new boolean[count];
        }

        private SceneJob.SubjectPoseTimeline timeline(
            long firstTick,
            String sessionId,
            UUID playerUuid,
            UUID connectionId
        ) {
            return new SceneJob.SubjectPoseTimeline(
                firstTick, sessionId, playerUuid, connectionId, entityIds, dimensions,
                positionX, positionY, positionZ, velocityX, velocityY, velocityZ,
                yaw, pitch, headYaw, onGround
            );
        }
    }

    private static Path requireAbsoluteNormalized(Path path, String label) throws IOException {
        if (!path.isAbsolute() || !path.equals(path.normalize())) {
            throw new IOException(label + " path must be absolute and normalized");
        }
        return path;
    }

    private static void requireExactKeys(JsonObject object, Set<String> expected, String label) throws IOException {
        if (!object.keySet().equals(expected)) {
            Set<String> missing = new HashSet<>(expected);
            missing.removeAll(object.keySet());
            Set<String> extra = new HashSet<>(object.keySet());
            extra.removeAll(expected);
            throw new IOException(label + " keys do not match contract; missing=" + missing + ", extra=" + extra);
        }
    }

    private static JsonObject requiredObject(JsonObject object, String name) throws IOException {
        JsonElement value = object.get(name);
        if (value == null || !value.isJsonObject()) {
            throw new IOException(name + " must be an object");
        }
        return value.getAsJsonObject();
    }

    private static JsonArray requiredArray(JsonObject object, String name) throws IOException {
        JsonElement value = object.get(name);
        if (value == null || !value.isJsonArray()) {
            throw new IOException(name + " must be an array");
        }
        return value.getAsJsonArray();
    }

    private static String requiredString(JsonObject object, String name) throws IOException {
        JsonElement value = object.get(name);
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) {
            throw new IOException(name + " must be a string");
        }
        String result = value.getAsString();
        if (result.isEmpty() || result.indexOf('\0') >= 0) {
            throw new IOException(name + " must be a non-empty string without NUL");
        }
        return result;
    }

    private static String requiredOpaque(JsonObject object, String name) throws IOException {
        String value = requiredString(object, name);
        if (!OPAQUE_ID.matcher(value).matches()) {
            throw new IOException(name + " must be an opaque path-free identifier");
        }
        return value;
    }

    private static UUID requiredUuid(JsonObject object, String name) throws IOException {
        try {
            UUID value = UUID.fromString(requiredString(object, name));
            if (!value.toString().equals(object.get(name).getAsString())) {
                throw new IllegalArgumentException("not canonical");
            }
            return value;
        } catch (IllegalArgumentException exception) {
            throw new IOException(name + " must be a canonical lowercase UUID", exception);
        }
    }

    private static long requiredLong(JsonObject object, String name) throws IOException {
        JsonElement value = object.get(name);
        try {
            if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()) {
                throw new NumberFormatException();
            }
            long result = value.getAsLong();
            if (value.getAsBigDecimal().scale() > 0 || value.getAsBigDecimal().longValueExact() != result) {
                throw new NumberFormatException();
            }
            return result;
        } catch (ArithmeticException | NumberFormatException exception) {
            throw new IOException(name + " must be an exact integer", exception);
        }
    }

    private static int requiredInt(JsonObject object, String name) throws IOException {
        long value = requiredLong(object, name);
        if (value < Integer.MIN_VALUE || value > Integer.MAX_VALUE) {
            throw new IOException(name + " is outside the 32-bit integer range");
        }
        return (int) value;
    }

    private static boolean requiredBoolean(JsonObject object, String name) throws IOException {
        JsonElement value = object.get(name);
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isBoolean()) {
            throw new IOException(name + " must be a boolean");
        }
        return value.getAsBoolean();
    }

    private static void requireLiteral(JsonObject object, String name, String expected) throws IOException {
        if (!requiredString(object, name).equals(expected)) {
            throw new IOException(name + " must be exactly " + expected);
        }
    }
}
