package dev.mcdata.renderer;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;
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
    String presentationContract,
    StructuredHudSpec structuredHud,
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
        String presentationContract = optionalString(json, "presentation_contract");
        StructuredHudSpec structuredHud = structuredHud(
            base, json, sessionId, playerId, connectionId
        );
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
        if (structuredHud != null
            && (structuredHud.startServerTick() > globalStartTick
                || structuredHud.endServerTick() < globalEndTick)) {
            throw new IllegalArgumentException(
                "structured_hud tick range must cover the requested global range"
            );
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
        if (presentationContract != null) {
            if (!ReplayPresentation.FULL_CLIENT_PRESENTATION_CONTRACT.equals(presentationContract)) {
                throw new IllegalArgumentException(
                    "Unsupported presentation_contract for GUI render job: " + presentationContract
                );
            }
            if (noGui) {
                throw new IllegalArgumentException("presentation_contract requires no_gui=false");
            }
            if (structuredHud == null) {
                throw new IllegalArgumentException("presentation_contract requires structured_hud");
            }
        } else if (structuredHud != null) {
            throw new IllegalArgumentException("structured_hud requires presentation_contract");
        }
        return new RenderJobSpec(normalizedJob, replay, replaySha256, replayBytes,
            output, sessionId, connectionId, playerId, segmentId, segmentOrdinal, rangePolicy, newerCutoff,
            globalStartTick, globalEndTick,
            width, height, fps, voxelHorizontalRadius, voxelVerticalRadius, noGui, presentationContract,
            structuredHud,
            stop, result);
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

    private static StructuredHudSpec structuredHud(
        Path base,
        JsonObject job,
        String sessionId,
        UUID playerId,
        String connectionId
    ) {
        if (!job.has("structured_hud") || job.get("structured_hud").isJsonNull()) {
            return null;
        }
        if (!job.get("structured_hud").isJsonObject()) {
            throw new IllegalArgumentException("structured_hud must be an object");
        }
        JsonObject value = job.getAsJsonObject("structured_hud");
        if (!value.keySet().equals(Set.of(
            "schema_version", "type", "path", "format", "sha256", "size_bytes",
            "records", "start_server_tick", "end_server_tick", "dataset_id",
            "dataset_manifest_sha256", "samples_sha256", "session_id", "player_uuid",
            "connection_id"
        ))) {
            throw new IllegalArgumentException("structured_hud has unsupported or missing fields");
        }
        Path path = resolve(base, requiredString(value, "path"));
        Path expectedPath = base.resolve("hud-states.jsonl").toAbsolutePath().normalize();
        long schemaVersion = requiredLong(value, "schema_version");
        String type = requiredString(value, "type");
        String format = requiredString(value, "format");
        String sha256 = requiredString(value, "sha256").toLowerCase();
        long sizeBytes = requiredLong(value, "size_bytes");
        long records = requiredLong(value, "records");
        long startTick = requiredLong(value, "start_server_tick");
        long endTick = requiredLong(value, "end_server_tick");
        String datasetId = requiredString(value, "dataset_id");
        String datasetManifestSha256 = requiredString(
            value, "dataset_manifest_sha256"
        ).toLowerCase();
        String samplesSha256 = requiredString(value, "samples_sha256").toLowerCase();
        String sidecarSessionId = requiredString(value, "session_id");
        UUID sidecarPlayerId = UUID.fromString(requiredString(value, "player_uuid"));
        String sidecarConnectionId = requiredString(value, "connection_id");
        UUID.fromString(sidecarConnectionId);
        if (!path.equals(expectedPath)
            || schemaVersion != 1
            || !StructuredHudTimeline.SIDECAR_TYPE.equals(type)
            || !"jsonl".equals(format)
            || !sha256.matches("[0-9a-f]{64}")
            || !datasetId.matches("[0-9a-f]{32}")
            || !datasetManifestSha256.matches("[0-9a-f]{64}")
            || !samplesSha256.matches("[0-9a-f]{64}")
            || !sidecarSessionId.equals(sessionId)
            || !sidecarPlayerId.equals(playerId)
            || !sidecarConnectionId.equals(connectionId)
            || sizeBytes <= 0
            || records <= 0
            || startTick < 0
            || endTick < startTick
            || endTick - startTick + 1 != records) {
            throw new IllegalArgumentException("Invalid structured_hud integrity envelope");
        }
        return new StructuredHudSpec(
            path, schemaVersion, type, format, sha256, sizeBytes, records, startTick, endTick,
            datasetId, datasetManifestSha256, samplesSha256,
            sidecarSessionId, sidecarPlayerId, sidecarConnectionId
        );
    }

    record StructuredHudSpec(
        Path path,
        long schemaVersion,
        String type,
        String format,
        String sha256,
        long sizeBytes,
        long records,
        long startServerTick,
        long endServerTick,
        String datasetId,
        String datasetManifestSha256,
        String samplesSha256,
        String sessionId,
        UUID playerId,
        String connectionId
    ) {
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
