package dev.mcdata.renderer;

import com.google.gson.JsonObject;
import com.moulberry.flashback.Flashback;
import com.moulberry.flashback.combo_options.TrackingBodyPart;
import com.moulberry.flashback.combo_options.VideoContainer;
import com.moulberry.flashback.exporting.ExportJob;
import com.moulberry.flashback.exporting.ExportSettings;
import com.moulberry.flashback.keyframe.impl.TrackEntityKeyframe;
import com.moulberry.flashback.keyframe.interpolation.InterpolationType;
import com.moulberry.flashback.keyframe.types.TrackEntityKeyframeType;
import com.moulberry.flashback.playback.ReplayServer;
import com.moulberry.flashback.state.EditorScene;
import com.moulberry.flashback.state.EditorState;
import com.moulberry.flashback.state.KeyframeTrack;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.minecraft.client.Minecraft;
import net.minecraft.world.entity.Entity;
import org.joml.Vector3d;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.List;

public final class McRecorderRenderer implements ClientModInitializer {
    private static final Logger LOGGER = LoggerFactory.getLogger("mc-recorder-renderer");
    private static final String JOB_PROPERTY = "mc.recorder.renderJob";
    private static final String JOB_ENVIRONMENT = "MC_RECORDER_RENDER_JOB";
    private static final int LOAD_TIMEOUT_TICKS = 20 * 120;
    private static final int MAX_ANCHOR_SCAN_TICKS = 200;
    private static final int ANCHOR_SETTLE_CLIENT_TICKS = 3;
    private static final int VOXEL_SETTLE_CLIENT_TICKS = 2;

    private RenderJobSpec job;
    private Phase phase = Phase.DISABLED;
    private Throwable startupFailure;
    private int waitTicks;
    private int resolvedStartTick;
    private int resolvedEndTick;
    private long globalTickOffset;
    private int anchorScanTick;
    private boolean anchorScanRequested;
    private int anchorSettleTicks;
    private volatile TimelineObservation timelineObservation;
    private int voxelReplayTick;
    private int voxelSettleTicks;
    private boolean voxelsComplete;
    private final List<JsonObject> voxelIndexRows = new ArrayList<>();

    @Override
    public void onInitializeClient() {
        PayloadTypeRegistry.playS2C().register(ReplayTimelinePayload.TYPE, ReplayTimelinePayload.STREAM_CODEC);
        ClientPlayNetworking.registerGlobalReceiver(ReplayTimelinePayload.TYPE, (payload, context) -> {
            ReplayServer replayServer = Flashback.getReplayServer();
            if (replayServer != null) {
                this.timelineObservation = new TimelineObservation(payload, replayServer.getReplayTick());
            }
        });

        String jobValue = System.getProperty(JOB_PROPERTY);
        if (jobValue == null || jobValue.isBlank()) {
            jobValue = System.getenv(JOB_ENVIRONMENT);
        }
        if (jobValue == null || jobValue.isBlank()) {
            LOGGER.info("No automated render job configured; set -D{}=<job.json> or {}", JOB_PROPERTY, JOB_ENVIRONMENT);
            return;
        }

        ClientTickEvents.END_CLIENT_TICK.register(this::onClientTick);
        try {
            this.job = RenderJobSpec.read(Path.of(jobValue));
            this.validateInputs();
            this.phase = Phase.OPEN_REPLAY;
            LOGGER.info("Loaded render job {}", this.job.jobPath());
        } catch (Exception exception) {
            this.startupFailure = exception;
            this.phase = Phase.STARTUP_FAILED;
            LOGGER.error("Unable to initialize automated renderer", exception);
        }
    }

    private void onClientTick(Minecraft minecraft) {
        try {
            switch (this.phase) {
                case STARTUP_FAILED -> this.fail(minecraft, this.startupFailure);
                case OPEN_REPLAY -> this.openReplay();
                case WAIT_REPLAY -> this.waitForReplay(minecraft);
                case FIND_ANCHOR -> this.findTimelineAnchor();
                case CAPTURE_VOXELS -> this.captureVoxelTick(minecraft);
                case WAIT_TARGET -> this.waitForTargetAndExport(minecraft);
                case EXPORTING -> this.finishWhenExportCompletes(minecraft);
                default -> {
                }
            }
        } catch (Throwable throwable) {
            this.fail(minecraft, throwable);
        }
    }

    private void validateInputs() throws IOException {
        if (Files.isSymbolicLink(this.job.replay()) || !Files.isRegularFile(this.job.replay())) {
            throw new IOException("Replay does not exist: " + this.job.replay());
        }
        if (Files.isSymbolicLink(this.job.output())) {
            throw new IOException("Renderer output cannot be a symlink: " + this.job.output());
        }
        Files.createDirectories(this.job.output());
        try (DirectoryStream<Path> stream = Files.newDirectoryStream(this.job.output(), "frame_*.png")) {
            if (stream.iterator().hasNext()) {
                throw new IOException("Output already contains frame_*.png files: " + this.job.output());
            }
        }
        if (Files.exists(this.job.output().resolve("frames.jsonl"))) {
            throw new IOException("Output already contains frames.jsonl: " + this.job.output());
        }
        if (this.job.capturesVoxels()
            && (Files.exists(this.job.output().resolve("voxels.jsonl"))
                || Files.exists(this.job.output().resolve("voxels")))) {
            throw new IOException("Output already contains voxel artifacts: " + this.job.output());
        }
    }

    private void openReplay() {
        this.phase = Phase.WAIT_REPLAY;
        Flashback.openReplayWorld(this.job.replay());
        LOGGER.info("Opening Flashback replay {}", this.job.replay());
    }

    private void waitForReplay(Minecraft minecraft) {
        ReplayServer replayServer = Flashback.getReplayServer();
        if (replayServer == null || minecraft.level == null || minecraft.player == null) {
            this.checkTimeout("replay server");
            return;
        }

        replayServer.replayPaused = true;
        this.anchorScanTick = 0;
        this.anchorScanRequested = false;
        this.anchorSettleTicks = 0;
        this.timelineObservation = null;
        this.phase = Phase.FIND_ANCHOR;
        this.waitTicks = 0;
    }

    private void findTimelineAnchor() {
        ReplayServer replayServer = Flashback.getReplayServer();
        if (replayServer == null) {
            this.checkTimeout("replay server while finding timeline marker");
            return;
        }

        TimelineObservation observation = this.timelineObservation;
        if (observation != null && this.matchesJob(observation.payload())) {
            this.globalTickOffset = observation.payload().serverTick() - observation.replayTick();
            long start = this.job.globalStartTick() - this.globalTickOffset;
            long end = this.job.globalEndTick() - this.globalTickOffset;
            if (start < 0 || end < start || end > replayServer.getTotalReplayTicks() || end > Integer.MAX_VALUE) {
                throw new IllegalArgumentException(
                    "Requested global ticks do not fall inside this replay segment after exact marker alignment"
                );
            }
            this.resolvedStartTick = (int) start;
            this.resolvedEndTick = (int) end;
            replayServer.goToReplayTick(this.resolvedStartTick);
            replayServer.replayPaused = true;
            this.phase = Phase.WAIT_TARGET;
            this.waitTicks = 0;
            LOGGER.info(
                "Aligned replay tick {} to global server tick {} for connection {}",
                observation.replayTick(), observation.payload().serverTick(), this.job.connectionId()
            );
            return;
        }

        if (this.anchorScanRequested) {
            this.anchorSettleTicks++;
            if (this.anchorSettleTicks < ANCHOR_SETTLE_CLIENT_TICKS) {
                return;
            }
            this.anchorScanTick++;
            this.anchorScanRequested = false;
        }

        int scanLimit = Math.min(MAX_ANCHOR_SCAN_TICKS, replayServer.getTotalReplayTicks());
        if (this.anchorScanTick > scanLimit) {
            throw new IllegalStateException(
                "No matching mc_recorder:timeline marker found in the first " + scanLimit + " replay ticks"
            );
        }

        this.timelineObservation = null;
        replayServer.goToReplayTick(this.anchorScanTick);
        replayServer.replayPaused = true;
        this.anchorScanRequested = true;
        this.anchorSettleTicks = 0;
    }

    private boolean matchesJob(ReplayTimelinePayload payload) {
        return payload.sessionId().equals(this.job.sessionId())
            && payload.connectionId().equals(this.job.connectionId());
    }

    private void waitForTargetAndExport(Minecraft minecraft) throws IOException {
        if (minecraft.level == null || minecraft.player == null) {
            this.checkTimeout("client level");
            return;
        }
        Entity target = null;
        for (Entity candidate : minecraft.level.entitiesForRendering()) {
            if (candidate.getUUID().equals(this.job.playerId())) {
                target = candidate;
                break;
            }
        }
        if (target == null) {
            this.checkTimeout("target player " + this.job.playerId());
            return;
        }

        if (this.job.capturesVoxels() && !this.voxelsComplete) {
            this.beginVoxelCapture();
            return;
        }

        EditorState editorState = this.firstPersonEditorState();
        ExportSettings settings = new ExportSettings(
            "mc-recorder-" + this.job.playerId(),
            editorState,
            minecraft.player.position(), minecraft.player.getYRot(), minecraft.player.getXRot(),
            this.job.width(), this.job.height(), this.resolvedStartTick, this.resolvedEndTick,
            this.job.framesPerSecond(), true,
            VideoContainer.PNG_SEQUENCE, null, null, 0, false, false, this.job.noGui(),
            false, false, null,
            this.job.output(), "frame_%06d"
        );

        this.writeStatus("running", null);
        Flashback.EXPORT_JOB = new ExportJob(settings);
        this.phase = Phase.EXPORTING;
        LOGGER.info(
            "Starting {}x{} export for player {} from replay ticks {}..{} / global ticks {}..{}",
            this.job.width(), this.job.height(), this.job.playerId(),
            this.resolvedStartTick, this.resolvedEndTick,
            this.job.globalStartTick(), this.job.globalEndTick()
        );
    }

    private void beginVoxelCapture() throws IOException {
        ReplayServer replayServer = Flashback.getReplayServer();
        if (replayServer == null) {
            throw new IOException("Replay server disappeared before voxel conversion");
        }
        this.voxelReplayTick = this.resolvedStartTick;
        this.voxelSettleTicks = 0;
        this.voxelIndexRows.clear();
        this.writeStatus("running", null);
        replayServer.goToReplayTick(this.voxelReplayTick);
        replayServer.replayPaused = true;
        this.phase = Phase.CAPTURE_VOXELS;
        this.waitTicks = 0;
        LOGGER.info(
            "Starting voxel conversion for replay ticks {}..{} with radii xz={} y={}",
            this.resolvedStartTick, this.resolvedEndTick,
            this.job.voxelHorizontalRadius(), this.job.voxelVerticalRadius()
        );
    }

    private void captureVoxelTick(Minecraft minecraft) throws IOException {
        ReplayServer replayServer = Flashback.getReplayServer();
        if (replayServer == null || minecraft.level == null) {
            this.checkTimeout("replay world during voxel conversion");
            return;
        }
        replayServer.replayPaused = true;
        if (replayServer.getReplayTick() != this.voxelReplayTick) {
            this.checkTimeout("replay tick " + this.voxelReplayTick + " during voxel conversion");
            return;
        }
        if (++this.voxelSettleTicks < VOXEL_SETTLE_CLIENT_TICKS) {
            return;
        }

        Entity target = this.findTarget(minecraft);
        if (target == null) {
            this.checkTimeout("target player during voxel conversion at replay tick " + this.voxelReplayTick);
            return;
        }
        long serverTick = this.globalTickOffset + this.voxelReplayTick;
        this.voxelIndexRows.add(
            VoxelSnapshotWriter.write(minecraft.level, target, this.job, this.voxelReplayTick, serverTick)
        );
        this.waitTicks = 0;

        if (this.voxelReplayTick >= this.resolvedEndTick) {
            this.writeVoxelIndex();
            this.voxelsComplete = true;
            replayServer.goToReplayTick(this.resolvedStartTick);
            replayServer.replayPaused = true;
            this.phase = Phase.WAIT_TARGET;
            LOGGER.info("Completed {} voxel snapshots", this.voxelIndexRows.size());
            return;
        }

        this.voxelReplayTick++;
        this.voxelSettleTicks = 0;
        replayServer.goToReplayTick(this.voxelReplayTick);
        replayServer.replayPaused = true;
    }

    private Entity findTarget(Minecraft minecraft) {
        if (minecraft.level == null) {
            return null;
        }
        for (Entity candidate : minecraft.level.entitiesForRendering()) {
            if (candidate.getUUID().equals(this.job.playerId())) {
                return candidate;
            }
        }
        return null;
    }

    private void writeVoxelIndex() throws IOException {
        Path index = this.job.output().resolve("voxels.jsonl");
        Path partial = index.resolveSibling(index.getFileName() + ".inprogress");
        Files.deleteIfExists(partial);
        try (BufferedWriter writer = Files.newBufferedWriter(partial, StandardCharsets.UTF_8,
            StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)) {
            for (JsonObject row : this.voxelIndexRows) {
                writer.write(row.toString());
                writer.newLine();
            }
        }
        forceFile(partial);
        atomicMove(partial, index);
    }

    private EditorState firstPersonEditorState() {
        EditorState editorState = new EditorState();
        KeyframeTrack track = new KeyframeTrack(TrackEntityKeyframeType.INSTANCE);

        TrackEntityKeyframe firstPerson = new TrackEntityKeyframe(
            this.job.playerId(), TrackingBodyPart.HEAD,
            0.0f, 0.0f, new Vector3d(), new Vector3d(), 0.0f,
            InterpolationType.HOLD
        );
        track.keyframesByTick.put(this.resolvedStartTick, firstPerson);
        track.keyframesByTick.put(this.resolvedEndTick, firstPerson.copy());

        long stamp = editorState.acquireWrite();
        try {
            EditorScene scene = editorState.getCurrentScene(stamp);
            scene.keyframeTracks.add(track);
            editorState.markDirty();
        } finally {
            editorState.release(stamp);
        }
        return editorState;
    }

    private void finishWhenExportCompletes(Minecraft minecraft) throws IOException {
        if (Flashback.EXPORT_JOB != null) {
            return;
        }

        int expectedFrames = this.resolvedEndTick - this.resolvedStartTick + 1;
        if (this.job.capturesVoxels() && this.voxelIndexRows.size() != expectedFrames) {
            throw new IOException(
                "Expected " + expectedFrames + " voxel snapshots, found " + this.voxelIndexRows.size()
            );
        }
        int actualFrames = this.writeFrameIndex();
        if (actualFrames != expectedFrames) {
            throw new IOException("Expected " + expectedFrames + " frames, found " + actualFrames);
        }

        this.writeStatus("complete", null);
        this.phase = Phase.COMPLETE;
        LOGGER.info("Completed render job with {} frames in {}", actualFrames, this.job.output());
        if (this.job.stopWhenDone()) {
            minecraft.stop();
        }
    }

    private int writeFrameIndex() throws IOException {
        int frame = 0;
        Path index = this.job.output().resolve("frames.jsonl");
        Path partial = index.resolveSibling(index.getFileName() + ".inprogress");
        Files.deleteIfExists(partial);
        try (BufferedWriter writer = Files.newBufferedWriter(partial, StandardCharsets.UTF_8,
            StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)) {
            while (true) {
                int number = frame + 1;
                Path image = this.job.output().resolve(String.format("frame_%06d.png", number));
                if (!Files.isRegularFile(image)) {
                    break;
                }
                int replayTick = this.resolvedStartTick + frame;
                long serverTick = this.globalTickOffset + replayTick;
                JsonObject row = new JsonObject();
                row.addProperty("frame", number);
                row.addProperty("server_tick", serverTick);
                row.addProperty("replay_tick", replayTick);
                row.addProperty("partial_tick", 0.0);
                row.addProperty("session_id", this.job.sessionId());
                row.addProperty("connection_id", this.job.connectionId());
                row.addProperty("player_uuid", this.job.playerId().toString());
                row.addProperty("path", image.getFileName().toString());
                writer.write(row.toString());
                writer.newLine();
                frame++;
            }
        }
        forceFile(partial);
        atomicMove(partial, index);
        return frame;
    }

    private void writeStatus(String status, Throwable failure) throws IOException {
        JsonObject result = new JsonObject();
        result.addProperty("status", status);
        result.addProperty("replay", this.job.replay().toString());
        result.addProperty("output", this.job.output().toString());
        result.addProperty("session_id", this.job.sessionId());
        result.addProperty("connection_id", this.job.connectionId());
        result.addProperty("player_uuid", this.job.playerId().toString());
        result.addProperty("global_start_tick", this.job.globalStartTick());
        result.addProperty("global_end_tick", this.job.globalEndTick());
        result.addProperty("replay_start_tick", this.resolvedStartTick);
        result.addProperty("replay_end_tick", this.resolvedEndTick);
        result.addProperty("global_tick_offset", this.globalTickOffset);
        result.addProperty("fps", this.job.framesPerSecond());
        result.addProperty("voxel_snapshots", this.voxelIndexRows.size());
        if (this.job.capturesVoxels()) {
            result.addProperty("voxel_index", this.job.output().resolve("voxels.jsonl").toString());
            result.addProperty("voxel_horizontal_radius", this.job.voxelHorizontalRadius());
            result.addProperty("voxel_vertical_radius", this.job.voxelVerticalRadius());
        }
        if (failure != null) {
            result.addProperty("error", failure.getClass().getSimpleName() + ": " + failure.getMessage());
        }
        atomicWriteString(this.job.result(), result.toString() + System.lineSeparator());
    }

    private void checkTimeout(String waitingFor) {
        this.waitTicks++;
        if (this.waitTicks > LOAD_TIMEOUT_TICKS) {
            throw new IllegalStateException("Timed out waiting for " + waitingFor);
        }
    }

    private void fail(Minecraft minecraft, Throwable throwable) {
        this.phase = Phase.FAILED;
        LOGGER.error("Automated render failed", throwable);
        try {
            if (this.job != null) {
                this.writeStatus("failed", throwable);
            }
        } catch (IOException statusFailure) {
            LOGGER.error("Unable to write render failure status", statusFailure);
        }
        if (this.job == null || this.job.stopWhenDone()) {
            minecraft.stop();
        }
    }

    private static void atomicWriteString(Path destination, String value) throws IOException {
        Files.createDirectories(destination.getParent());
        Path partial = destination.resolveSibling(destination.getFileName() + ".inprogress");
        Files.writeString(partial, value, StandardCharsets.UTF_8,
            StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE);
        forceFile(partial);
        atomicMove(partial, destination);
    }

    private static void forceFile(Path path) throws IOException {
        try (FileChannel channel = FileChannel.open(path, StandardOpenOption.WRITE)) {
            channel.force(true);
        }
    }

    private static void atomicMove(Path source, Path destination) throws IOException {
        try {
            Files.move(source, destination, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (AtomicMoveNotSupportedException ignored) {
            Files.move(source, destination, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    private record TimelineObservation(ReplayTimelinePayload payload, int replayTick) {
    }

    private enum Phase {
        DISABLED,
        STARTUP_FAILED,
        OPEN_REPLAY,
        WAIT_REPLAY,
        FIND_ANCHOR,
        CAPTURE_VOXELS,
        WAIT_TARGET,
        EXPORTING,
        COMPLETE,
        FAILED
    }
}
