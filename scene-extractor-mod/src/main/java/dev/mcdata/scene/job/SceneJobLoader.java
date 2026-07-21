package dev.mcdata.scene.job;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.io.Reader;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

/** Strict parser and path boundary for scene-job.json. */
public final class SceneJobLoader {
    private static final long MAX_JOB_BYTES = 1_048_576;
    private static final Pattern OPAQUE_ID = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,159}");
    private static final Pattern SHA256 = Pattern.compile("[0-9a-f]{64}");
    private static final Set<String> ROOT_KEYS = Set.of(
        "schema_version", "job_id", "session_id", "subject", "global_start_tick",
        "global_end_tick", "scope", "metadata_policy", "source_replays", "output",
        "stop_when_done"
    );
    private static final Set<String> SUBJECT_KEYS = Set.of("player_uuid", "connection_id");
    private static final Set<String> SOURCE_KEYS = Set.of(
        "segment_id", "segment_ordinal", "path", "sha256", "size_bytes", "format"
    );

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
        boolean stopWhenDone = requiredBoolean(root, "stop_when_done");
        if (!stopWhenDone) {
            throw new IOException("scene extractor only accepts stop_when_done=true");
        }

        Path jobRoot = normalizedRequest.getParent();
        if (jobRoot == null || !Files.isDirectory(jobRoot, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("scene job must have an existing non-symlink parent directory");
        }
        jobRoot = jobRoot.toRealPath(LinkOption.NOFOLLOW_LINKS);
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
            output, result, parsedSources, true
        );
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
