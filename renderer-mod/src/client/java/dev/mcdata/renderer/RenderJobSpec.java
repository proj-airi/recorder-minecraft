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
    Path output,
    String sessionId,
    String connectionId,
    UUID playerId,
    long globalStartTick,
    long globalEndTick,
    int width,
    int height,
    double framesPerSecond,
    int voxelHorizontalRadius,
    int voxelVerticalRadius,
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
        Path output = resolve(base, requiredString(json, "output"));
        String sessionId = requiredString(json, "session_id");
        String connectionId = requiredString(json, "connection_id");
        UUID playerId = UUID.fromString(requiredString(json, "player_uuid"));
        long globalStartTick = requiredLong(json, "global_start_tick");
        long globalEndTick = requiredLong(json, "global_end_tick");
        int width = integer(json, "width", 640);
        int height = integer(json, "height", 360);
        double fps = decimal(json, "fps", 20.0);
        int voxelHorizontalRadius = integer(json, "voxel_horizontal_radius", 0);
        int voxelVerticalRadius = integer(json, "voxel_vertical_radius", 0);
        boolean noGui = bool(json, "no_gui", true);
        boolean stop = bool(json, "stop_when_done", true);
        Path result = json.has("result")
            ? resolve(base, json.get("result").getAsString())
            : output.resolve("render-result.json");

        if (!output.equals(base.resolve("frames").toAbsolutePath().normalize())
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
        if ((voxelHorizontalRadius == 0) != (voxelVerticalRadius == 0)) {
            throw new IllegalArgumentException(
                "voxel_horizontal_radius and voxel_vertical_radius must both be zero or both be positive"
            );
        }
        if (voxelHorizontalRadius < 0 || voxelHorizontalRadius > 64
            || voxelVerticalRadius < 0 || voxelVerticalRadius > 64) {
            throw new IllegalArgumentException("Voxel radii must be between 0 and 64 blocks");
        }
        long voxelCount = (2L * voxelHorizontalRadius + 1L)
            * (2L * voxelHorizontalRadius + 1L)
            * (2L * voxelVerticalRadius + 1L);
        if (voxelHorizontalRadius > 0 && voxelCount > 2_000_000L) {
            throw new IllegalArgumentException("Voxel crop is too large; maximum is 2,000,000 cells per tick");
        }
        return new RenderJobSpec(normalizedJob, replay, output, sessionId, connectionId, playerId,
            globalStartTick, globalEndTick,
            width, height, fps, voxelHorizontalRadius, voxelVerticalRadius, noGui, stop, result);
    }

    boolean capturesVoxels() {
        return this.voxelHorizontalRadius > 0;
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
