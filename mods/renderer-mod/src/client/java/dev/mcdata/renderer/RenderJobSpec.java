package dev.mcdata.renderer;

import com.google.protobuf.util.JsonFormat;
import dev.minerec.artifacts.v1.RenderJob;
import dev.minerec.artifacts.v1.RenderJobStatus;
import dev.minerec.artifacts.v1.RenderRangePolicy;
import dev.minerec.artifacts.v1.RenderReplaySource;

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
    Path result,
    Path progress
) {
    static RenderJobSpec read(Path jobPath) throws IOException {
        RenderJob.Builder builder = RenderJob.newBuilder();
        JsonFormat.parser().merge(Files.readString(jobPath), builder);
        RenderJob job = builder.build();
        Path normalizedJob = jobPath.toAbsolutePath().normalize();
        Path base = normalizedJob.getParent();

        if (job.getSchemaVersion() != 1
            || !"mc-recorder".equals(job.getOwner())
            || !"mc-recorder-first-person-render-v1".equals(job.getJobType())
            || job.getStatus() != RenderJobStatus.RENDER_JOB_STATUS_PREPARED) {
            throw new IllegalArgumentException("Render job is not owned by the mc-recorder v1 framework");
        }
        if (!job.hasReplay() || !job.hasGlobalTicks()) {
            throw new IllegalArgumentException("Render job is missing replay or tick coverage");
        }
        RenderReplaySource source = job.getReplay();
        Path replay = resolve(base, source.getPath());
        String replaySha256 = source.getSha256();
        long replayBytes = source.getSizeBytes();
        if (!replaySha256.matches("[0-9a-f]{64}") || replayBytes < 0 || !"flashback".equals(source.getFormat())) {
            throw new IllegalArgumentException("Invalid replay integrity envelope");
        }
        String replayId = canonicalUuid(source.getReplayId(), "replay_id");
        String sessionId = required(job.getSessionId(), "session_id");
        String connectionId = canonicalUuid(job.getConnectionId(), "connection_id");
        UUID playerId = UUID.fromString(canonicalUuid(job.getPlayerUuid(), "player_uuid"));
        Path output = resolve(base, job.getOutputPath());
        Path result = resolve(base, job.getResultPath());
        Path progress = resolve(base, job.getProgressPath());
        if (!output.equals(base.resolve("fpv_frames").toAbsolutePath().normalize())
            || !result.equals(base.resolve("result.json").toAbsolutePath().normalize())
            || !progress.equals(base.resolve("progress.json").toAbsolutePath().normalize())) {
            throw new IllegalArgumentException("Renderer outputs must remain inside the owned job directory");
        }
        long globalStartTick = job.getGlobalTicks().getFirstTick();
        long globalEndTick = job.getGlobalTicks().getLastTick();
        int width = job.getWidth();
        int height = job.getHeight();
        double fps = job.getFramesPerSecond();
        if (globalStartTick < 0 || globalEndTick < globalStartTick) {
            throw new IllegalArgumentException("Invalid global tick interval");
        }
        if (width < 64 || height < 64 || width > 16384 || height > 16384) {
            throw new IllegalArgumentException("Invalid render resolution");
        }
        if (Math.abs(fps - 20.0) > 0.0001) {
            throw new IllegalArgumentException("Renderer v1 requires 20 FPS");
        }
        if (job.getRangePolicy() != RenderRangePolicy.RENDER_RANGE_POLICY_INTERSECTION) {
            throw new IllegalArgumentException("Unsupported render range policy");
        }
        return new RenderJobSpec(normalizedJob, replay, replaySha256, replayBytes, output,
            sessionId, connectionId, playerId, replayId, RangePolicy.INTERSECTION,
            globalStartTick, globalEndTick, width, height, fps, job.getNoGui(),
            job.getStopWhenDone(), result, progress);
    }

    enum RangePolicy {
        INTERSECTION
    }

    private static Path resolve(Path base, String value) {
        Path path = Path.of(required(value, "path"));
        return (path.isAbsolute() ? path : base.resolve(path)).toAbsolutePath().normalize();
    }

    private static String required(String value, String label) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("Missing required field: " + label);
        }
        return value;
    }

    private static String canonicalUuid(String value, String label) {
        String required = required(value, label);
        UUID parsed = UUID.fromString(required);
        if (!parsed.toString().equals(required)) {
            throw new IllegalArgumentException(label + " must use canonical UUID spelling");
        }
        return required;
    }
}
