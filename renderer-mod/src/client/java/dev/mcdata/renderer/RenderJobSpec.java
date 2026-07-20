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
    String segmentId,
    Long segmentOrdinal,
    RangePolicy rangePolicy,
    Long newerCutoff,
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
        String segmentId = json.has("segment_id")
            ? requiredString(json, "segment_id")
            : optionalString(sourceReplay, "segment_id");
        Long segmentOrdinal = json.has("segment_ordinal")
            ? Long.valueOf(requiredLong(json, "segment_ordinal"))
            : optionalLong(sourceReplay, "segment_ordinal");
        if ((segmentId == null) != (segmentOrdinal == null)) {
            throw new IllegalArgumentException("segment_id and segment_ordinal must be provided together");
        }
        if (segmentId != null) {
            if (!segmentId.matches("[A-Za-z0-9][A-Za-z0-9._-]{0,159}")) {
                throw new IllegalArgumentException("segment_id must be an opaque path-free identifier");
            }
            if (segmentOrdinal < 0) {
                throw new IllegalArgumentException("segment_ordinal must not be negative");
            }
        }
        JsonObject timeline = json.has("timeline") && json.get("timeline").isJsonObject()
            ? json.getAsJsonObject("timeline") : null;
        String serializedRangePolicy = json.has("range_policy")
            ? requiredString(json, "range_policy")
            : optionalString(timeline, "range_policy");
        RangePolicy rangePolicy = RangePolicy.parse(
            serializedRangePolicy
        );
        long globalStartTick = requiredLong(json, "global_start_tick");
        long globalEndTick = requiredLong(json, "global_end_tick");
        Long newerCutoff = json.has("newer_cutoff")
            ? Long.valueOf(requiredLong(json, "newer_cutoff"))
            : optionalLong(timeline, "newer_cutoff");
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
        if (newerCutoff != null) {
            boolean atEndExclusive = globalEndTick < Long.MAX_VALUE && newerCutoff == globalEndTick + 1;
            if (rangePolicy != RangePolicy.INTERSECTION
                || newerCutoff < globalStartTick
                || (newerCutoff > globalEndTick && !atEndExclusive)) {
                throw new IllegalArgumentException(
                    "newer_cutoff is valid only inside an intersection range"
                );
            }
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
        return new RenderJobSpec(normalizedJob, replay, replaySha256, replayBytes,
            output, sessionId, connectionId, playerId, segmentId, segmentOrdinal, rangePolicy, newerCutoff,
            globalStartTick, globalEndTick,
            width, height, fps, voxelHorizontalRadius, voxelVerticalRadius, noGui, stop, result);
    }

    boolean capturesVoxels() {
        return this.voxelHorizontalRadius > 0;
    }

    Path progress() {
        return this.jobPath.getParent().resolve("progress.json");
    }

    long effectiveGlobalEndTick() {
        return this.newerCutoff == null
            ? this.globalEndTick
            : Math.min(this.globalEndTick, this.newerCutoff - 1);
    }

    enum RangePolicy {
        LEGACY_STRICT,
        STRICT,
        INTERSECTION;

        static RangePolicy parse(String serialized) {
            if (serialized == null) {
                return LEGACY_STRICT;
            }
            return switch (serialized) {
                case "strict" -> STRICT;
                case "exact" -> STRICT;
                case "intersection" -> INTERSECTION;
                default -> throw new IllegalArgumentException("Unsupported range_policy: " + serialized);
            };
        }

        String serialized() {
            return switch (this) {
                case LEGACY_STRICT -> "legacy_strict";
                case STRICT -> "exact";
                case INTERSECTION -> "intersection";
            };
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

    private static String optionalString(JsonObject json, String key) {
        return json != null && json.has(key) && !json.get(key).isJsonNull()
            ? requiredString(json, key) : null;
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

    private static Long optionalLong(JsonObject json, String key) {
        return json != null && json.has(key) && !json.get(key).isJsonNull()
            ? requiredLong(json, key) : null;
    }

    private static double decimal(JsonObject json, String key, double fallback) {
        return json.has(key) ? json.get(key).getAsDouble() : fallback;
    }

    private static boolean bool(JsonObject json, String key, boolean fallback) {
        return json.has(key) ? json.get(key).getAsBoolean() : fallback;
    }
}
