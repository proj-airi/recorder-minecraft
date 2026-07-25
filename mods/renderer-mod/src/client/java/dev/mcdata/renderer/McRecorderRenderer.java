package dev.mcdata.renderer;

import com.google.gson.JsonArray;
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
    private static final String JOB_PROPERTY = "mc.recorder.renderJob";
    private static final String JOB_ENVIRONMENT = "MC_RECORDER_RENDER_JOB";
    private static final int LOAD_TIMEOUT_TICKS = 20 * 120;
    private static final int MAX_ANCHOR_SCAN_TICKS = 200;
    private static final int ANCHOR_SETTLE_CLIENT_TICKS = 3;
    private static final long EXPORT_SETTLE_NANOS = 1_000_000_000L;

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
    private boolean clientPresentationRequested;
    private boolean clientPresentationActive;
    private boolean clientPresentationNeedsServerRebind;
    private AbstractClientPlayer clientPresentationTarget;
    private Entity previousCameraEntity;
    private CameraType previousCameraType;
    private boolean previousHideGui;
    private long exportSettleStartedNanos;

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
            this.writeProgress("prepared", 0, 0);
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
        this.writeProgressUnchecked("opening_replay", 0, 0);
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
        this.writeProgress("finding_coverage", 0, 0);
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
        this.writeStatus("no_coverage", null);
        this.writeProgress("no_coverage", 0, 0);
        ReplayPacketCompatibility.endAutomatedRender();
        this.phase = Phase.COMPLETE;
        LOGGER.info(
            "Replay segment {} has no coverage for requested global ticks {}..{}",
            this.job.segmentId(), this.job.globalStartTick(), this.job.globalEndTick()
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

        this.writeStatus("running", null);
        this.writeProgress("rendering", 0, this.resolvedEndTick - this.resolvedStartTick + 1);
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
            if (!ReplayPresentation.useSpectatedPlayerCamera(this.job.noGui())) {
                KeyframeTrack track = new KeyframeTrack(TrackEntityKeyframeType.INSTANCE);
                TrackEntityKeyframe firstPerson = new TrackEntityKeyframe(
                    this.job.playerId(), TrackingBodyPart.HEAD,
                    0.0f, 0.0f, new Vector3d(), new Vector3d(), 0.0f,
                    InterpolationType.HOLD
                );
                track.keyframesByTick.put(this.resolvedStartTick, firstPerson);
                track.keyframesByTick.put(this.resolvedEndTick, firstPerson.copy());
                scene.keyframeTracks.add(track);
            }
            if (ReplayPresentation.hideTrackedPlayerDuringExport(this.job.noGui())) {
                editorState.hideDuringExport.add(this.job.playerId());
            }
            editorState.markDirty();
        } finally {
            editorState.release(stamp);
        }
        return editorState;
    }

    private boolean activateClientPresentation(Minecraft minecraft, Entity target) {
        if (!ReplayPresentation.useSpectatedPlayerCamera(this.job.noGui())) {
            return true;
        }
        if (!(target instanceof AbstractClientPlayer replayPlayer) || replayPlayer == minecraft.player) {
            throw new IllegalStateException(
                "Recorded player cannot be used as the first-person replay camera: " + this.job.playerId()
            );
        }
        this.clientPresentationTarget = replayPlayer;
        if (!this.clientPresentationRequested) {
            if (minecraft.getConnection() == null) {
                throw new IllegalStateException("Flashback replay connection is unavailable");
            }
            this.previousCameraEntity = minecraft.getCameraEntity();
            this.previousCameraType = minecraft.options.getCameraType();
            this.previousHideGui = minecraft.options.hideGui;
            this.clientPresentationRequested = true;
            minecraft.getConnection().sendCommand(
                ReplayPresentation.startSpectatingCommand(this.job.playerId())
            );
            LOGGER.info(
                "Requested replay-server spectate for player {} so Flashback can synchronize first-person HUD state",
                this.job.playerId()
            );
        }
        minecraft.options.setCameraType(CameraType.FIRST_PERSON);
        minecraft.options.hideGui = false;
        if (minecraft.getCameraEntity() != replayPlayer || Flashback.getSpectatingPlayer() != replayPlayer) {
            return false;
        }
        if (!this.clientPresentationActive) {
            this.clientPresentationActive = true;
            LOGGER.info("Replay server activated player {} for first-person rendering", this.job.playerId());
        }
        return true;
    }

    private void maintainClientPresentation(Minecraft minecraft, boolean countsTowardRender) throws IOException {
        if (!ReplayPresentation.useSpectatedPlayerCamera(this.job.noGui())) {
            return;
        }
        if (minecraft.level == null || minecraft.player == null) {
            throw new IOException("Replay client world is unavailable for a counted render frame");
        }

        AbstractClientPlayer presentTarget = this.findPresentPresentationTarget(minecraft);
        Entity currentCamera = minecraft.getCameraEntity();
        AbstractClientPlayer currentRequestedPlayer = null;
        if (currentCamera instanceof AbstractClientPlayer player
            && player != minecraft.player
            && player.getUUID().equals(this.job.playerId())) {
            currentRequestedPlayer = player;
        }

        boolean replayReportsDeath = (currentRequestedPlayer != null && currentRequestedPlayer.isDeadOrDying())
            || (this.clientPresentationTarget != null && this.clientPresentationTarget.isDeadOrDying());
        boolean targetReplaced = presentTarget != null
            && this.clientPresentationTarget != null
            && presentTarget != this.clientPresentationTarget;
        ReplayPresentation.CameraContinuity continuity = ReplayPresentation.decideCameraContinuity(
            currentCamera == presentTarget,
            currentRequestedPlayer != null,
            presentTarget != null,
            this.clientPresentationTarget != null,
            replayReportsDeath,
            countsTowardRender
        );
        AbstractClientPlayer resolvedTarget;
        switch (continuity) {
            case KEEP -> resolvedTarget = presentTarget != null
                ? presentTarget : currentRequestedPlayer;
            case REBIND_PRESENT -> {
                resolvedTarget = presentTarget;
                minecraft.setCameraEntity(resolvedTarget);
                LOGGER.info(
                    "Rebound first-person render camera to replay player {} after a replay lifecycle transition",
                    this.job.playerId()
                );
            }
            case REBIND_DEATH_CAMERA -> {
                resolvedTarget = this.clientPresentationTarget;
                minecraft.setCameraEntity(resolvedTarget);
                LOGGER.info(
                    "Holding detached death camera for replay player {} until its respawn entity appears",
                    this.job.playerId()
                );
            }
            case WAIT -> {
                return;
            }
            case REJECT -> throw new IOException(
                "Requested replay player is unavailable for healthy counted frame "
                    + this.job.playerId()
            );
            default -> throw new IllegalStateException("Unhandled camera continuity decision");
        }

        boolean cameraRecovered = targetReplaced
            || continuity == ReplayPresentation.CameraContinuity.REBIND_PRESENT
            || continuity == ReplayPresentation.CameraContinuity.REBIND_DEATH_CAMERA
            || (continuity == ReplayPresentation.CameraContinuity.KEEP && presentTarget == null);
        ReplayPresentation.ServerSpectateRecovery serverRecovery =
            ReplayPresentation.planServerSpectateRecovery(
                this.clientPresentationNeedsServerRebind,
                cameraRecovered,
                replayReportsDeath
            );
        this.clientPresentationNeedsServerRebind = serverRecovery.pending();
        if (presentTarget != null && serverRecovery.requestNow()) {
            if (minecraft.getConnection() == null) {
                throw new IOException("Flashback replay connection disappeared during camera recovery");
            }
            minecraft.getConnection().sendCommand(
                ReplayPresentation.startSpectatingCommand(this.job.playerId())
            );
            LOGGER.info(
                "Reactivated replay-server spectating for respawned player {}",
                this.job.playerId()
            );
        }
        this.clientPresentationTarget = resolvedTarget;
        minecraft.options.setCameraType(CameraType.FIRST_PERSON);
        minecraft.options.hideGui = false;
        if (countsTowardRender
            && (minecraft.getCameraEntity() != resolvedTarget
                || Flashback.getSpectatingPlayer() != resolvedTarget)) {
            throw new IOException(
                "Unable to retain requested replay player as the counted render camera "
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
        if (!this.clientPresentationRequested && !this.clientPresentationActive) {
            return;
        }
        if (this.clientPresentationRequested && minecraft.getConnection() != null) {
            minecraft.getConnection().sendCommand(ReplayPresentation.stopSpectatingCommand());
        }
        this.clientPresentationRequested = false;
        this.clientPresentationActive = false;
        this.clientPresentationNeedsServerRebind = false;
        this.clientPresentationTarget = null;
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
                    "rendering",
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

        this.writeStatus("complete", null);
        this.writeProgress("complete", actualFrames, expectedFrames);
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
                JsonObject row = new JsonObject();
                row.addProperty("frame", number);
                row.addProperty("server_tick", serverTick);
                row.addProperty("replay_tick", replayTick);
                row.addProperty("partial_tick", 0.0);
                row.addProperty("session_id", this.job.sessionId());
                row.addProperty("connection_id", this.job.connectionId());
                row.addProperty("player_uuid", this.job.playerId().toString());
                if (this.job.segmentId() != null) {
                    row.addProperty("segment_id", this.job.segmentId());
                    row.addProperty("segment_ordinal", this.job.segmentOrdinal());
                }
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
        result.addProperty("replay_sha256", this.job.replaySha256());
        result.addProperty("replay_bytes", this.job.replayBytes());
        result.addProperty("output", this.job.output().toString());
        result.addProperty("session_id", this.job.sessionId());
        result.addProperty("connection_id", this.job.connectionId());
        result.addProperty("player_uuid", this.job.playerId().toString());
        result.addProperty("segment_id", this.job.segmentId());
        result.addProperty("segment_ordinal", this.job.segmentOrdinal());
        result.addProperty("range_policy", this.job.rangePolicy().serialized());
        result.addProperty("requested_global_start_tick", this.job.globalStartTick());
        result.addProperty("requested_global_end_tick", this.job.globalEndTick());
        result.addProperty(
            "global_start_tick",
            this.resolvedStartTick >= 0 ? this.resolvedGlobalStartTick : this.job.globalStartTick()
        );
        result.addProperty(
            "global_end_tick",
            this.resolvedEndTick >= 0 ? this.resolvedGlobalEndTick : this.job.globalEndTick()
        );
        result.addProperty("replay_start_tick", this.resolvedStartTick);
        result.addProperty("replay_end_tick", this.resolvedEndTick);
        result.addProperty("global_tick_offset", this.globalTickOffset);
        if (this.firstTimelineObservation != null) {
            result.addProperty("segment_coverage_start_tick", this.segmentCoverageStartTick);
            result.addProperty("segment_coverage_end_tick", this.segmentCoverageEndTick);
        }
        result.addProperty("fps", (int) Math.round(this.job.framesPerSecond()));
        result.addProperty("width", this.job.width());
        result.addProperty("height", this.job.height());
        result.addProperty("no_gui", this.job.noGui());
        result.add("unsupported_packets", unsupportedPacketEnvelope());
        if (failure != null) {
            result.addProperty("error", failure.getClass().getSimpleName() + ": " + failure.getMessage());
        }
        atomicWriteString(this.job.result(), result.toString() + System.lineSeparator());
    }

    private int countRenderedFrames() throws IOException {
        int count = 0;
        try (DirectoryStream<Path> stream = Files.newDirectoryStream(this.job.output(), "frame_*.png")) {
            for (Path ignored : stream) count++;
        }
        return count;
    }

    private void writeProgress(String status, int completedUnits, int totalUnits) throws IOException {
        JsonObject progress = new JsonObject();
        progress.addProperty("schema_version", 1);
        progress.addProperty("status", status);
        progress.addProperty("session_id", this.job.sessionId());
        progress.addProperty("connection_id", this.job.connectionId());
        progress.addProperty("player_uuid", this.job.playerId().toString());
        if (this.job.segmentId() != null) {
            progress.addProperty("segment_id", this.job.segmentId());
            progress.addProperty("segment_ordinal", this.job.segmentOrdinal());
        }
        progress.addProperty("completed_units", completedUnits);
        progress.addProperty("total_units", totalUnits);
        progress.addProperty("updated_at", Instant.now().toString());
        atomicWriteString(this.job.progress(), progress.toString() + System.lineSeparator());
    }

    private void writeProgressUnchecked(String status, int completedUnits, int totalUnits) {
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
                this.writeStatus("failed", throwable);
                this.writeProgress("failed", 0, 0);
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

    private static void atomicWriteString(Path destination, String value) throws IOException {
        Files.createDirectories(destination.getParent());
        Path partial = destination.resolveSibling(destination.getFileName() + ".inprogress");
        Files.writeString(partial, value, StandardCharsets.UTF_8,
            StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE);
        forceFile(partial);
        atomicMove(partial, destination);
    }

    private static JsonObject unsupportedPacketEnvelope() {
        ReplayPacketCompatibility.Snapshot snapshot = ReplayPacketCompatibility.snapshot();
        JsonObject envelope = new JsonObject();
        envelope.addProperty("policy", snapshot.policy());
        envelope.addProperty("total_count", snapshot.totalCount());
        JsonArray types = new JsonArray();
        snapshot.packetTypes().forEach((packetType, count) -> {
            JsonObject entry = new JsonObject();
            entry.addProperty("packet_type", packetType);
            entry.addProperty("count", count);
            types.add(entry);
        });
        envelope.add("types", types);
        return envelope;
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
