package dev.mcdata.renderer;

import com.google.protobuf.Timestamp;
import com.google.protobuf.util.JsonFormat;
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
import dev.minerec.artifacts.v1.RenderArtifact;
import dev.minerec.artifacts.v1.RenderFrameIndex;
import dev.minerec.artifacts.v1.RenderProgress;
import dev.minerec.artifacts.v1.RenderProgressStatus;
import dev.minerec.artifacts.v1.RenderReplaySource;
import dev.minerec.artifacts.v1.RenderResult;
import dev.minerec.artifacts.v1.RenderResultStatus;
import dev.minerec.artifacts.v1.TickRange;
import dev.minerec.artifacts.v1.UnsupportedPacketCount;
import dev.minerec.artifacts.v1.UnsupportedPackets;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.minecraft.client.CameraType;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.AbstractClientPlayer;
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
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;

public final class McRecorderRenderer implements ClientModInitializer {
    private static final Logger LOGGER = LoggerFactory.getLogger("mc-recorder-renderer");
    private static final JsonFormat.Printer JSON = JsonFormat.printer().omittingInsignificantWhitespace();
    private static final String JOB_PROPERTY = "mc.recorder.renderJob";
    private static final String JOB_ENVIRONMENT = "MC_RECORDER_RENDER_JOB";
    private static final int LOAD_TIMEOUT_TICKS = 20 * 120;
    private static final int MAX_ANCHOR_SCAN_TICKS = 200;
    // NOTICE: ReplayServer applies goToReplayTick on its server thread. Three client ticks
    // intermittently inspected the old timeline state and rejected healthy captures before
    // the tail marker arrived, so allow roughly one second for the cross-thread seek to settle.
    private static final int ANCHOR_SETTLE_CLIENT_TICKS = 20;
    private static final long EXPORT_SETTLE_NANOS = 1_000_000_000L;
    private static volatile AbstractClientPlayer presentationPlayerOverride;

    private RenderJobSpec job;
    private volatile Phase phase = Phase.DISABLED;
    private Throwable startupFailure;
    private int waitTicks;
    private int resolvedStartTick = -1;
    private int resolvedEndTick = -1;
    private long globalTickOffset;
    private long resolvedGlobalStartTick;
    private long resolvedGlobalEndTick;
    private long segmentCoverageStartTick;
    private long segmentCoverageEndTick;
    private int anchorScanTick;
    private boolean anchorScanRequested;
    private int anchorSettleTicks;
    private volatile TimelineObservation timelineObservation;
    private TimelineObservation firstTimelineObservation;
    private TimelineObservation lastTimelineObservation;
    private int progressTicks;
    private boolean clientPresentationActive;
    private AbstractClientPlayer clientPresentationTarget;
    private Entity previousCameraEntity;
    private CameraType previousCameraType;
    private boolean previousHideGui;
    private long exportSettleStartedNanos;

    public static AbstractClientPlayer presentationPlayerOverride() {
        return presentationPlayerOverride;
    }

    @Override
    public void onInitializeClient() {
        PayloadTypeRegistry.playS2C().register(ReplayTimelinePayload.TYPE, ReplayTimelinePayload.STREAM_CODEC);
        ClientPlayNetworking.registerGlobalReceiver(ReplayTimelinePayload.TYPE, (payload, context) -> {
            ReplayServer replayServer = Flashback.getReplayServer();
            if (replayServer != null && this.job != null && this.matchesJob(payload)) {
                this.timelineObservation = new TimelineObservation(payload, replayServer.getReplayTick());
                try {
                    boolean countsTowardRender = this.phase == Phase.EXPORTING
                        && payload.serverTick() >= this.resolvedGlobalStartTick
                        && payload.serverTick() <= this.resolvedGlobalEndTick;
                    if (countsTowardRender) {
                        this.maintainClientPresentation(context.client(), true);
                    }
                } catch (Throwable throwable) {
                    this.startupFailure = throwable;
                    this.phase = Phase.STARTUP_FAILED;
                }
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
            ReplayPacketCompatibility.beginAutomatedRender();
            this.writeProgress(RenderProgressStatus.RENDER_PROGRESS_STATUS_PREPARED, 0, 0);
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
                case OPEN_REPLAY -> this.scheduleOpenReplay(minecraft);
                case WAIT_REPLAY -> this.waitForReplay(minecraft);
                case FIND_FIRST_ANCHOR -> this.findFirstTimelineAnchor(minecraft);
                case FIND_LAST_ANCHOR -> this.findLastTimelineAnchor(minecraft);
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
        this.validateReplayIntegrity();
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
    }

    private void scheduleOpenReplay(Minecraft minecraft) {
        if (minecraft.screen == null || minecraft.getOverlay() != null) {
            this.checkTimeout("Minecraft startup screen");
            return;
        }
        if (++this.waitTicks < 20) {
            return;
        }
        this.phase = Phase.WAIT_REPLAY;
        this.waitTicks = 0;
        this.writeProgressUnchecked(RenderProgressStatus.RENDER_PROGRESS_STATUS_OPENING_REPLAY, 0, 0);
        LOGGER.info("Minecraft startup settled on {}; scheduling replay open", minecraft.screen.getClass().getSimpleName());
        Thread.startVirtualThread(() -> minecraft.submit(() -> {
            try {
                Flashback.openReplayWorld(this.job.replay());
                LOGGER.info("Opening Flashback replay {}", this.job.replay());
            } catch (Throwable throwable) {
                this.startupFailure = throwable;
                this.phase = Phase.STARTUP_FAILED;
            }
        }));
    }

    private void waitForReplay(Minecraft minecraft) throws IOException {
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
        this.firstTimelineObservation = null;
        this.lastTimelineObservation = null;
        this.phase = Phase.FIND_FIRST_ANCHOR;
        this.waitTicks = 0;
        this.writeProgress(RenderProgressStatus.RENDER_PROGRESS_STATUS_FINDING_COVERAGE, 0, 0);
    }

    private void findFirstTimelineAnchor(Minecraft minecraft) throws IOException {
        ReplayServer replayServer = Flashback.getReplayServer();
        if (replayServer == null) {
            this.checkTimeout("replay server while finding timeline marker");
            return;
        }

        TimelineObservation observation = this.timelineObservation;
        if (observation != null && this.matchesJob(observation.payload())) {
            this.firstTimelineObservation = observation;
            this.lastTimelineObservation = observation;
            if (observation.payload().serverTick() >= this.job.globalEndTick()) {
                this.resolveTimelineRange(replayServer, observation, observation);
                if (this.phase == Phase.NO_COVERAGE) {
                    this.completeNoCoverage(minecraft);
                }
                return;
            }
            this.anchorScanRequested = false;
            this.anchorSettleTicks = 0;
            this.timelineObservation = null;
            this.phase = Phase.FIND_LAST_ANCHOR;
            return;
        }

        int scanLimit = Math.min(MAX_ANCHOR_SCAN_TICKS, replayServer.getTotalReplayTicks());
        if (!this.advanceAnchorScan(replayServer, 1, scanLimit)) {
            throw new IllegalStateException(
                "No matching mc_recorder:timeline marker found in the first " + scanLimit + " replay ticks"
            );
        }
    }

    private void findLastTimelineAnchor(Minecraft minecraft) throws IOException {
        ReplayServer replayServer = Flashback.getReplayServer();
        if (replayServer == null) {
            this.checkTimeout("replay server while finding final timeline marker");
            return;
        }

        if (!this.seekAndSettleAtReplayTick(replayServer, replayServer.getTotalReplayTicks())) {
            return;
        }

        TimelineObservation observation = this.timelineObservation;
        if (observation != null && this.matchesJob(observation.payload())) {
            TimelineRangeResolver.Marker first = marker(this.firstTimelineObservation);
            TimelineRangeResolver.Marker candidate = marker(observation);
            if (!TimelineRangeResolver.hasSameOffset(first, candidate)) {
                this.checkTimeout("aligned final timeline marker after replay tail seek");
                return;
            }
            TimelineRangeResolver.Marker previous = marker(this.lastTimelineObservation);
            TimelineRangeResolver.Marker accepted = TimelineRangeResolver.extendForwardCoverage(
                first, previous, candidate
            );
            if (!accepted.equals(previous)) {
                this.lastTimelineObservation = observation;
            }
            this.resolveTimelineRange(
                replayServer, this.firstTimelineObservation, this.lastTimelineObservation
            );
            if (this.phase == Phase.NO_COVERAGE) {
                this.completeNoCoverage(minecraft);
            }
            return;
        }

        throw new IllegalStateException(
            "No matching mc_recorder:timeline marker was applied at the replay tail"
        );
    }

    private boolean seekAndSettleAtReplayTick(ReplayServer replayServer, int replayTick) {
        if (!this.anchorScanRequested) {
            this.timelineObservation = null;
            replayServer.goToReplayTick(replayTick);
            replayServer.replayPaused = true;
            this.anchorScanRequested = true;
            this.anchorSettleTicks = 0;
            LOGGER.info("Seeking directly to replay tail tick {} to establish timeline coverage", replayTick);
            return false;
        }

        replayServer.replayPaused = true;
        return ++this.anchorSettleTicks >= ANCHOR_SETTLE_CLIENT_TICKS;
    }

    private boolean advanceAnchorScan(ReplayServer replayServer, int step, int inclusiveLimit) {
        if (this.anchorScanRequested) {
            this.anchorSettleTicks++;
            if (this.anchorSettleTicks < ANCHOR_SETTLE_CLIENT_TICKS) {
                return true;
            }
            this.anchorScanTick += step;
            this.anchorScanRequested = false;
        }

        if ((step > 0 && this.anchorScanTick > inclusiveLimit)
            || (step < 0 && this.anchorScanTick < inclusiveLimit)) {
            return false;
        }

        this.timelineObservation = null;
        replayServer.goToReplayTick(this.anchorScanTick);
        replayServer.replayPaused = true;
        this.anchorScanRequested = true;
        this.anchorSettleTicks = 0;
        return true;
    }

    private void resolveTimelineRange(
        ReplayServer replayServer,
        TimelineObservation first,
        TimelineObservation last
    ) {
        TimelineRangeResolver.Resolution resolution = TimelineRangeResolver.resolve(
            this.job.globalStartTick(), this.job.globalEndTick(), replayServer.getTotalReplayTicks(),
            marker(first), last == null ? null : marker(last)
        );
        this.globalTickOffset = resolution.globalTickOffset();
        this.resolvedGlobalStartTick = resolution.globalStartTick();
        this.resolvedGlobalEndTick = resolution.globalEndTick();
        this.segmentCoverageStartTick = resolution.coverageStartTick();
        this.segmentCoverageEndTick = resolution.coverageEndTick();
        if (resolution.status() == TimelineRangeResolver.Status.NO_COVERAGE) {
            this.resolvedStartTick = -1;
            this.resolvedEndTick = -1;
            this.phase = Phase.NO_COVERAGE;
            return;
        }
        this.resolvedStartTick = resolution.replayStartTick();
        this.resolvedEndTick = resolution.replayEndTick();
        if (!TimelineRangeResolver.matchesResolvedStart(
            this.timelineObservation == null ? null : marker(this.timelineObservation),
            this.resolvedStartTick,
            this.resolvedGlobalStartTick
        )) {
            this.timelineObservation = null;
        }
        replayServer.goToReplayTick(this.resolvedStartTick);
        replayServer.replayPaused = true;
        this.phase = Phase.WAIT_TARGET;
        this.waitTicks = 0;
        LOGGER.info(
            "Aligned segment coverage {}..{} to requested {}..{}; rendering global ticks {}..{} for connection {}",
            this.segmentCoverageStartTick, this.segmentCoverageEndTick,
            this.job.globalStartTick(), this.job.globalEndTick(),
            this.resolvedGlobalStartTick, this.resolvedGlobalEndTick, this.job.connectionId()
        );
    }

    private static TimelineRangeResolver.Marker marker(TimelineObservation observation) {
        return new TimelineRangeResolver.Marker(
            observation.payload().serverTick(), observation.replayTick(), observation.payload().eventSequence()
        );
    }

    private void completeNoCoverage(Minecraft minecraft) throws IOException {
        this.writeStatus(RenderResultStatus.RENDER_RESULT_STATUS_NO_COVERAGE, null, 0);
        this.writeProgress(RenderProgressStatus.RENDER_PROGRESS_STATUS_NO_COVERAGE, 0, 0);
        ReplayPacketCompatibility.endAutomatedRender();
        this.phase = Phase.COMPLETE;
        LOGGER.info(
            "Replay {} has no coverage for requested global ticks {}..{}",
            this.job.replayId(), this.job.globalStartTick(), this.job.globalEndTick()
        );
        if (this.job.stopWhenDone()) {
            minecraft.stop();
        }
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
        TimelineObservation observation = this.timelineObservation;
        if (!TimelineRangeResolver.matchesResolvedStart(
            observation == null ? null : marker(observation),
            this.resolvedStartTick,
            this.resolvedGlobalStartTick
        )) {
            this.checkTimeout("resolved start timeline marker");
            return;
        }
        AbstractClientPlayer target = this.findPresentPresentationTarget(minecraft);
        if (target == null) {
            this.checkTimeout("target player " + this.job.playerId());
            return;
        }

        if (!this.activateClientPresentation(minecraft, target)) {
            this.checkTimeout("Flashback replay-server spectate for player " + this.job.playerId());
            return;
        }
        this.waitTicks = 0;
        if (this.exportSettleStartedNanos == 0L) {
            this.exportSettleStartedNanos = System.nanoTime();
            return;
        }
        if (System.nanoTime() - this.exportSettleStartedNanos < EXPORT_SETTLE_NANOS) {
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

        this.writeProgress(RenderProgressStatus.RENDER_PROGRESS_STATUS_RENDERING, 0, this.resolvedEndTick - this.resolvedStartTick + 1);
        Flashback.EXPORT_JOB = new ExportJob(settings);
        this.phase = Phase.EXPORTING;
        LOGGER.info(
            "Starting {}x{} export for player {} from replay ticks {}..{} / global ticks {}..{}",
            this.job.width(), this.job.height(), this.job.playerId(),
            this.resolvedStartTick, this.resolvedEndTick,
            this.resolvedGlobalStartTick, this.resolvedGlobalEndTick
        );
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

    private EditorState firstPersonEditorState() {
        EditorState editorState = new EditorState();
        ReplayPresentation.configureClientGui(editorState.replayVisuals, this.job.noGui());

        long stamp = editorState.acquireWrite();
        try {
            EditorScene scene = editorState.getCurrentScene(stamp);
            // NOTICE: Flashback's ExportJob evaluates EditorState camera tracks independently
            // from the recorded player used for hand and HUD presentation. Always keep this
            // track or GUI renders freeze at the replay spectator position with partially
            // meshed chunks.
            KeyframeTrack track = new KeyframeTrack(TrackEntityKeyframeType.INSTANCE);
            TrackEntityKeyframe firstPerson = new TrackEntityKeyframe(
                this.job.playerId(), TrackingBodyPart.HEAD,
                0.0f, 0.0f, new Vector3d(), new Vector3d(), 0.0f,
                InterpolationType.HOLD
            );
            track.keyframesByTick.put(this.resolvedStartTick, firstPerson);
            track.keyframesByTick.put(this.resolvedEndTick, firstPerson.copy());
            scene.keyframeTracks.add(track);
            // The tracked entity occupies the camera's head position. Hiding its world model
            // prevents the skin and name tag from covering every exported frame; the mixin
            // presentation override still supplies that player's first-person hand and HUD.
            editorState.hideDuringExport.add(this.job.playerId());
            editorState.markDirty();
        } finally {
            editorState.release(stamp);
        }
        return editorState;
    }

    private boolean activateClientPresentation(Minecraft minecraft, Entity target) {
        if (!ReplayPresentation.useRecordedPlayerPresentation(this.job.noGui())) {
            return true;
        }
        if (!(target instanceof AbstractClientPlayer replayPlayer) || replayPlayer == minecraft.player) {
            throw new IllegalStateException(
                "Recorded player cannot be used as the first-person replay camera: " + this.job.playerId()
            );
        }
        this.clientPresentationTarget = replayPlayer;
        presentationPlayerOverride = replayPlayer;
        if (!this.clientPresentationActive) {
            this.previousCameraEntity = minecraft.getCameraEntity();
            this.previousCameraType = minecraft.options.getCameraType();
            this.previousHideGui = minecraft.options.hideGui;
            this.clientPresentationActive = true;
            LOGGER.info("Activated player {} for first-person hand and HUD presentation", this.job.playerId());
        }
        minecraft.options.setCameraType(CameraType.FIRST_PERSON);
        minecraft.options.hideGui = false;
        return true;
    }

    private void maintainClientPresentation(Minecraft minecraft, boolean countsTowardRender) throws IOException {
        if (!ReplayPresentation.useRecordedPlayerPresentation(this.job.noGui())) {
            return;
        }
        if (minecraft.level == null || minecraft.player == null) {
            throw new IOException("Replay client world is unavailable for a counted render frame");
        }

        AbstractClientPlayer presentTarget = this.findPresentPresentationTarget(minecraft);
        if (presentTarget != null) {
            this.clientPresentationTarget = presentTarget;
            presentationPlayerOverride = presentTarget;
        } else if (this.clientPresentationTarget == null && countsTowardRender) {
            throw new IOException(
                "Requested replay player is unavailable for healthy counted frame "
                    + this.job.playerId()
            );
        }
        minecraft.options.setCameraType(CameraType.FIRST_PERSON);
        minecraft.options.hideGui = false;
        if (countsTowardRender && presentationPlayerOverride == null) {
            throw new IOException(
                "Unable to retain requested replay player for hand and HUD presentation "
                    + this.job.playerId()
            );
        }
    }

    private AbstractClientPlayer findPresentPresentationTarget(Minecraft minecraft) {
        Entity target = minecraft.level == null
            ? null : minecraft.level.getEntity(this.job.playerId());
        if (target instanceof AbstractClientPlayer replayPlayer
            && replayPlayer != minecraft.player
            && !replayPlayer.isRemoved()) {
            return replayPlayer;
        }
        return null;
    }

    private void restoreClientPresentation(Minecraft minecraft) {
        if (!this.clientPresentationActive) {
            return;
        }
        this.clientPresentationActive = false;
        this.clientPresentationTarget = null;
        presentationPlayerOverride = null;
        minecraft.setCameraEntity(
            this.previousCameraEntity != null ? this.previousCameraEntity : minecraft.player
        );
        if (this.previousCameraType != null) {
            minecraft.options.setCameraType(this.previousCameraType);
        }
        minecraft.options.hideGui = this.previousHideGui;
        this.previousCameraEntity = null;
        this.previousCameraType = null;
    }

    private void finishWhenExportCompletes(Minecraft minecraft) throws IOException {
        if (Flashback.EXPORT_JOB != null) {
            if (++this.progressTicks >= 20) {
                this.progressTicks = 0;
                this.writeProgress(
                    RenderProgressStatus.RENDER_PROGRESS_STATUS_RENDERING,
                    this.countRenderedFrames(),
                    this.resolvedEndTick - this.resolvedStartTick + 1
                );
            }
            return;
        }

        int expectedFrames = this.resolvedEndTick - this.resolvedStartTick + 1;
        this.restoreClientPresentation(minecraft);
        int actualFrames = this.writeFrameIndex();
        if (actualFrames != expectedFrames) {
            throw new IOException("Expected " + expectedFrames + " frames, found " + actualFrames);
        }

        this.validateReplayIntegrity();

        this.writeStatus(RenderResultStatus.RENDER_RESULT_STATUS_COMPLETE, null, actualFrames);
        this.writeProgress(RenderProgressStatus.RENDER_PROGRESS_STATUS_COMPLETE, actualFrames, expectedFrames);
        ReplayPacketCompatibility.endAutomatedRender();
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
                RenderFrameIndex row = RenderFrameIndex.newBuilder()
                    .setOrdinal(number)
                    .setServerTick(serverTick)
                    .setReplayTick(replayTick)
                    .setPartialTick(0.0)
                    .setSessionId(this.job.sessionId())
                    .setConnectionId(this.job.connectionId())
                    .setPlayerUuid(this.job.playerId().toString())
                    .setReplayId(this.job.replayId())
                    .setPath(image.getFileName().toString())
                    .build();
                writer.write(JSON.print(row));
                writer.newLine();
                frame++;
            }
        }
        forceFile(partial);
        atomicMove(partial, index);
        return frame;
    }

    private void writeStatus(RenderResultStatus status, Throwable failure, int frameCount) throws IOException {
        RenderResult.Builder result = RenderResult.newBuilder()
            .setSchemaVersion(1)
            .setStatus(status)
            .setSessionId(this.job.sessionId())
            .setConnectionId(this.job.connectionId())
            .setPlayerUuid(this.job.playerId().toString())
            .setReplay(RenderReplaySource.newBuilder()
                .setReplayId(this.job.replayId())
                .setPath(this.job.replay().toString())
                .setFormat("flashback")
                .setSha256(this.job.replaySha256())
                .setSizeBytes(this.job.replayBytes()))
            .setOutputPath(this.job.output().toString())
            .setRequestedGlobalTicks(ticks(this.job.globalStartTick(), this.job.globalEndTick()))
            .setFramesPerSecond(this.job.framesPerSecond())
            .setWidth(this.job.width())
            .setHeight(this.job.height())
            .setNoGui(this.job.noGui())
            .setFrameCount(frameCount)
            .setUnsupportedPackets(unsupportedPacketEnvelope());
        if (status == RenderResultStatus.RENDER_RESULT_STATUS_COMPLETE) {
            result.setGlobalTicks(ticks(this.resolvedGlobalStartTick, this.resolvedGlobalEndTick));
            result.setReplayTicks(ticks(this.resolvedStartTick, this.resolvedEndTick));
            result.setGlobalTickOffset(this.globalTickOffset);
            result.setFrameIndex(artifact(this.job.output().resolve("frames.jsonl"), "application/jsonl"));
        }
        if (this.firstTimelineObservation != null) {
            result.setSegmentCoverageTicks(ticks(this.segmentCoverageStartTick, this.segmentCoverageEndTick));
        }
        if (failure != null) {
            result.setError(failure.getClass().getSimpleName() + ": " + failure.getMessage());
        }
        atomicWriteProto(this.job.result(), result.build());
    }

    private int countRenderedFrames() throws IOException {
        int count = 0;
        try (DirectoryStream<Path> stream = Files.newDirectoryStream(this.job.output(), "frame_*.png")) {
            for (Path ignored : stream) count++;
        }
        return count;
    }

    private void writeProgress(RenderProgressStatus status, int completedUnits, int totalUnits) throws IOException {
        RenderProgress progress = RenderProgress.newBuilder()
            .setSchemaVersion(1)
            .setStatus(status)
            .setSessionId(this.job.sessionId())
            .setConnectionId(this.job.connectionId())
            .setPlayerUuid(this.job.playerId().toString())
            .setReplayId(this.job.replayId())
            .setCompletedUnits(completedUnits)
            .setTotalUnits(totalUnits)
            .setUpdatedAt(timestamp(Instant.now()))
            .build();
        atomicWriteProto(this.job.progress(), progress);
    }

    private void writeProgressUnchecked(RenderProgressStatus status, int completedUnits, int totalUnits) {
        try {
            this.writeProgress(status, completedUnits, totalUnits);
        } catch (IOException exception) {
            LOGGER.warn("Unable to update render progress", exception);
        }
    }

    private void checkTimeout(String waitingFor) {
        this.waitTicks++;
        if (this.waitTicks > LOAD_TIMEOUT_TICKS) {
            throw new IllegalStateException("Timed out waiting for " + waitingFor);
        }
    }

    private void validateReplayIntegrity() throws IOException {
        if (Files.size(this.job.replay()) != this.job.replayBytes()) {
            throw new IOException("Replay size changed after render job preparation");
        }
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new IOException("SHA-256 is unavailable", exception);
        }
        try (DigestInputStream input = new DigestInputStream(Files.newInputStream(this.job.replay()), digest)) {
            input.transferTo(java.io.OutputStream.nullOutputStream());
        }
        String actual = java.util.HexFormat.of().formatHex(digest.digest());
        if (!actual.equals(this.job.replaySha256())) {
            throw new IOException("Replay SHA-256 changed after render job preparation");
        }
    }

    private void fail(Minecraft minecraft, Throwable throwable) {
        this.phase = Phase.FAILED;
        this.restoreClientPresentation(minecraft);
        LOGGER.error("Automated render failed", throwable);
        try {
            if (this.job != null) {
                this.writeStatus(RenderResultStatus.RENDER_RESULT_STATUS_FAILED, throwable, 0);
                this.writeProgress(RenderProgressStatus.RENDER_PROGRESS_STATUS_FAILED, 0, 0);
            }
        } catch (IOException statusFailure) {
            LOGGER.error("Unable to write render failure status", statusFailure);
        } finally {
            ReplayPacketCompatibility.endAutomatedRender();
        }
        if (this.job == null || this.job.stopWhenDone()) {
            minecraft.stop();
        }
    }

    private static void atomicWriteProto(Path destination, com.google.protobuf.Message value) throws IOException {
        Files.createDirectories(destination.getParent());
        Path partial = destination.resolveSibling(destination.getFileName() + ".inprogress");
        Files.writeString(partial, JSON.print(value) + System.lineSeparator(), StandardCharsets.UTF_8,
            StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE);
        forceFile(partial);
        atomicMove(partial, destination);
    }

    private static UnsupportedPackets unsupportedPacketEnvelope() {
        ReplayPacketCompatibility.Snapshot snapshot = ReplayPacketCompatibility.snapshot();
        UnsupportedPackets.Builder envelope = UnsupportedPackets.newBuilder()
            .setPolicy(snapshot.policy())
            .setTotalCount(snapshot.totalCount());
        snapshot.packetTypes().forEach((packetType, count) -> {
            envelope.addTypes(UnsupportedPacketCount.newBuilder()
                .setPacketType(packetType)
                .setCount(count));
        });
        return envelope.build();
    }

    private static TickRange ticks(long first, long last) {
        return TickRange.newBuilder().setFirstTick(first).setLastTick(last).build();
    }

    private static Timestamp timestamp(Instant value) {
        return Timestamp.newBuilder().setSeconds(value.getEpochSecond()).setNanos(value.getNano()).build();
    }

    private static RenderArtifact artifact(Path path, String mediaType) throws IOException {
        return RenderArtifact.newBuilder()
            .setPath(path.toString())
            .setSha256(sha256(path))
            .setSizeBytes(Files.size(path))
            .setMediaType(mediaType)
            .build();
    }

    private static String sha256(Path path) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new IOException("SHA-256 is unavailable", exception);
        }
        try (DigestInputStream input = new DigestInputStream(Files.newInputStream(path), digest)) {
            input.transferTo(java.io.OutputStream.nullOutputStream());
        }
        return java.util.HexFormat.of().formatHex(digest.digest());
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
        FIND_FIRST_ANCHOR,
        FIND_LAST_ANCHOR,
        NO_COVERAGE,
        WAIT_TARGET,
        EXPORTING,
        COMPLETE,
        FAILED
    }
}
