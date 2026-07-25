package dev.mcdata.renderer;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;

record RenderJobSpec(
    Path jobPath,
    Path replay,
    String replaySha256,
    long replayBytes,
    Path output,
    String sessionId,
    String connectionId,
    UUID playerId,
    String replayId,
    RangePolicy rangePolicy,
    long globalStartTick,
    long globalEndTick,
    int width,
    int height,
    double framesPerSecond,
    boolean noGui,
    boolean stopWhenDone,
    Path result
) {
    static RenderJobSpec read(Path jobPath) throws IOException {
        JsonObject json = JsonParser.parseString(Files.readString(jobPath)).getAsJsonObject();
        Path normalizedJob = jobPath.toAbsolutePath().normalize();
        Path base = normalizedJob.getParent();

        if (!"mc-recorder".equals(requiredString(json, "owner"))
            || !"mc-recorder-first-person-render-v1".equals(requiredString(json, "job_type"))) {
            throw new IllegalArgumentException("Render job is not owned by the mc-recorder v1 framework");
        }

        Path replay = resolve(base, requiredString(json, "replay"));
        if (!json.has("source_replay") || !json.get("source_replay").isJsonObject()) {
            throw new IllegalArgumentException("Missing required object: source_replay");
        }
        JsonObject sourceReplay = json.getAsJsonObject("source_replay");
        Path sourceReplayPath = resolve(base, requiredString(sourceReplay, "path"));
        String replaySha256 = requiredString(sourceReplay, "sha256").toLowerCase();
        long replayBytes = requiredLong(sourceReplay, "size_bytes");
        if (!sourceReplayPath.equals(replay)
            || !replaySha256.matches("[0-9a-f]{64}")
            || replayBytes < 0) {
            throw new IllegalArgumentException("Invalid source_replay integrity envelope");
        }
        Path output = resolve(base, requiredString(json, "output"));
        String sessionId = requiredString(json, "session_id");
        String connectionId = requiredString(json, "connection_id");
        UUID playerId = UUID.fromString(requiredString(json, "player_uuid"));
        String replayId = requiredString(json, "replay_id");
        UUID parsedReplayId = UUID.fromString(replayId);
        if (!parsedReplayId.toString().equals(replayId)) {
            throw new IllegalArgumentException("replay_id must use canonical UUID spelling");
        }
        if (!replayId.equals(requiredString(sourceReplay, "replay_id"))) {
            throw new IllegalArgumentException("source_replay identity does not match the render job");
        }
        JsonObject timeline = json.has("timeline") && json.get("timeline").isJsonObject()
            ? json.getAsJsonObject("timeline") : null;
        if (timeline == null) {
            throw new IllegalArgumentException("Missing required object: timeline");
        }
        RangePolicy rangePolicy = RangePolicy.parse(requiredString(timeline, "range_policy"));
        long globalStartTick = requiredLong(json, "global_start_tick");
        long globalEndTick = requiredLong(json, "global_end_tick");
        int width = integer(json, "width", 640);
        int height = integer(json, "height", 360);
        double fps = decimal(json, "fps", 20.0);
        boolean noGui = bool(json, "no_gui", true);
        boolean stop = bool(json, "stop_when_done", true);
        Path result = json.has("result")
            ? resolve(base, json.get("result").getAsString())
            : output.resolve("render-result.json");

        if (!output.equals(base.resolve("fpv_frames").toAbsolutePath().normalize())
            || !result.equals(base.resolve("result.json").toAbsolutePath().normalize())) {
            throw new IllegalArgumentException("Renderer output and result must remain inside the owned job directory");
        }
        if (sessionId.length() > 128 || connectionId.length() > 64) {
            throw new IllegalArgumentException("Session or connection identifier is too long");
        }
        UUID.fromString(connectionId);

        if (globalStartTick < 0 || globalEndTick < globalStartTick) {
            throw new IllegalArgumentException("Invalid global_start_tick/global_end_tick interval");
        }
        if (width <= 0 || height <= 0 || width > 16384 || height > 16384) {
            throw new IllegalArgumentException("Invalid render resolution");
        }
        if (Math.abs(fps - 20.0) > 0.0001) {
            throw new IllegalArgumentException("Renderer v1 requires fps=20");
        }
        return new RenderJobSpec(normalizedJob, replay, replaySha256, replayBytes,
            output, sessionId, connectionId, playerId, replayId, rangePolicy,
            globalStartTick, globalEndTick,
            width, height, fps, noGui, stop, result);
    }

    Path progress() {
        return this.jobPath.getParent().resolve("progress.json");
    }

    enum RangePolicy {
        INTERSECTION;

        static RangePolicy parse(String serialized) {
            return switch (serialized) {
                case "intersection" -> INTERSECTION;
                default -> throw new IllegalArgumentException("Unsupported range_policy: " + serialized);
            };
        }

        String serialized() {
            return "intersection";
        }
    }

    private static Path resolve(Path base, String value) {
        Path path = Path.of(value);
        return (path.isAbsolute() ? path : base.resolve(path)).toAbsolutePath().normalize();
    }

    private static String requiredString(JsonObject json, String key) {
        if (!json.has(key) || json.get(key).getAsString().isBlank()) {
            throw new IllegalArgumentException("Missing required field: " + key);
        }
        return json.get(key).getAsString();
    }

    private static int integer(JsonObject json, String key, int fallback) {
        return json.has(key) ? json.get(key).getAsInt() : fallback;
    }

    private static long requiredLong(JsonObject json, String key) {
        if (!json.has(key)) {
            throw new IllegalArgumentException("Missing required field: " + key);
        }
        return json.get(key).getAsLong();
    }

    private static double decimal(JsonObject json, String key, double fallback) {
        return json.has(key) ? json.get(key).getAsDouble() : fallback;
    }

    private static boolean bool(JsonObject json, String key, boolean fallback) {
        return json.has(key) ? json.get(key).getAsBoolean() : fallback;
    }
}
