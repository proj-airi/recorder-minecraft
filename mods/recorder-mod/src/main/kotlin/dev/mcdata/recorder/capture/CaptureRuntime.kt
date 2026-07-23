package dev.mcdata.recorder.capture

import dev.mcdata.recorder.config.RecorderConfig
import dev.mcdata.recorder.control.RecorderControlPlane
import dev.mcdata.recorder.io.AsyncEpochWriter
import dev.mcdata.recorder.io.SessionFiles
import net.minecraft.network.protocol.Packet
import net.minecraft.server.MinecraftServer
import net.minecraft.server.level.ServerPlayer
import org.slf4j.Logger

object CaptureRuntime {
    @Volatile
    private var coordinator: CaptureCoordinator? = null
    @Volatile
    private var failed = false
    @Volatile
    private var replaySegments: ReplaySegmentTracker? = null
    private lateinit var logger: Logger

    fun initialize(logger: Logger) {
        this.logger = logger
    }

    @Synchronized
    fun start(server: MinecraftServer, config: RecorderConfig) {
        check(coordinator == null) { "a capture session is already active" }
        val session = SessionFiles.create(config)
        val writer = AsyncEpochWriter(session.sessionId, session.directory, config.writerQueueCapacity, logger)
        val controlPlane = runCatching {
            RecorderControlPlane(session.sessionId, session.directory, config.controlPath(), logger)
        }.onFailure {
            logger.error(
                "Recorder control plane is unavailable at {}; source capture will continue without dashboard control",
                config.controlPath(),
                it
            )
        }.getOrNull()
        val segmentTracker = ReplaySegmentTracker(
            session.sessionId,
            { segments -> controlPlane?.publishReplaySegments(segments) },
            logger
        )
        replaySegments = segmentTracker
        coordinator = CaptureCoordinator(config, session, writer, controlPlane, logger, segmentTracker)
        failed = false
        logger.info("Started dataset recording session {} in {}", session.sessionId, session.directory)

        // Dedicated server lifecycle normally starts before players can join. This also handles
        // integrated/test servers where players may already exist when the callback runs.
        server.playerList.players.forEach { coordinator?.playerJoin(it) }
    }

    fun startTick() = safely { it.startTick() }
    fun endTick(server: MinecraftServer) = safely { it.endTick(server) }
    fun playerJoin(player: ServerPlayer) = safely { it.playerJoin(player) }
    fun playerLeave(player: ServerPlayer) = safely { it.playerLeave(player) }
    fun packetArrival(player: ServerPlayer, packet: Packet<*>) = safely { it.packetArrival(player, packet) }

    fun replayRecorderStarted(recorder: net.casual.arcade.replay.recorder.ReplayRecorder) {
        runCatching { replaySegments?.recorderStarted(recorder) }.onFailure {
            logger.error("Could not register ServerReplay segment identity", it)
        }
    }

    fun replayRecorderSaved(recorder: net.casual.arcade.replay.recorder.ReplayRecorder, output: java.nio.file.Path) {
        runCatching { replaySegments?.recorderSaved(recorder, output) }.onFailure {
            logger.error("Could not publish saved ServerReplay segment", it)
        }
    }

    @JvmStatic
    fun packetApply(player: ServerPlayer, packet: Packet<*>) = safely { it.packetApply(player, packet) }

    @Synchronized
    fun stop() {
        val current = coordinator ?: return
        coordinator = null
        runCatching { current.close() }.onFailure {
            logger.error("Failed to seal dataset recording session", it)
        }
    }

    private inline fun safely(block: (CaptureCoordinator) -> Unit) {
        val current = coordinator ?: return
        if (failed) return
        try {
            block(current)
        } catch (throwable: Throwable) {
            failed = true
            logger.error(
                "Dataset capture disabled after a fatal write/capture error; no records will be silently dropped",
                throwable
            )
            runCatching { current.abort(throwable) }.onFailure { abortFailure ->
                logger.error("Failed to mark dataset capture incomplete", abortFailure)
            }
        }
    }
}
