package dev.mcdata.recorder.capture

import com.mojang.authlib.GameProfile
import dev.mcdata.recorder.config.RecorderConfig
import net.casual.arcade.replay.recorder.ReplayRecorder
import net.minecraft.network.protocol.Packet
import net.minecraft.server.MinecraftServer
import net.minecraft.server.level.ServerPlayer
import org.slf4j.Logger
import java.nio.file.Path
import java.util.UUID

object CaptureRuntime {
    @Volatile
    private var coordinator: CaptureCoordinator? = null
    @Volatile
    private var failed = false
    private lateinit var logger: Logger

    fun initialize(logger: Logger) {
        this.logger = logger
    }

    @Synchronized
    fun start(server: MinecraftServer, config: RecorderConfig) {
        check(coordinator == null) { "a capture process is already active" }
        val sessionId = UUID.randomUUID().toString()
        coordinator = CaptureCoordinator(config, sessionId, logger)
        failed = false
        logger.info("Started recorder capture process {}", sessionId)

        server.playerList.players.forEach { coordinator?.playerJoin(it) }
    }

    @JvmStatic
    @Synchronized
    fun replayPathFor(profile: GameProfile): Path? = coordinator?.replayPathFor(profile)

    fun startTick() = safely { it.startTick() }
    fun endTick(server: MinecraftServer) = safely { it.endTick(server) }
    fun playerJoin(player: ServerPlayer) = safely { it.playerJoin(player) }
    fun playerLeave(player: ServerPlayer) = safely { it.playerLeave(player) }
    fun packetArrival(player: ServerPlayer, packet: Packet<*>) = safely { it.packetArrival(player, packet) }
    fun replayRecorderStarted(recorder: ReplayRecorder) = safely { it.replayRecorderStarted(recorder) }
    fun replayRecorderSaved(recorder: ReplayRecorder, output: Path) = safely { it.replayRecorderSaved(recorder, output) }
    fun replayRecorderClosed(recorder: ReplayRecorder) = safely { it.replayRecorderClosed(recorder) }

    @JvmStatic
    fun packetApply(player: ServerPlayer, packet: Packet<*>) = safely { it.packetApply(player, packet) }

    @Synchronized
    fun stop() {
        val current = coordinator ?: return
        runCatching { current.close() }.onFailure {
            logger.error("Failed to close recorder captures", it)
        }
        coordinator = null
    }

    private inline fun safely(block: (CaptureCoordinator) -> Unit) {
        val current = coordinator ?: return
        if (failed) return
        try {
            block(current)
        } catch (throwable: Throwable) {
            failed = true
            logger.error(
                "Recording disabled after a fatal write/capture error; incomplete plays keep a null end tick",
                throwable
            )
            runCatching { current.abort(throwable) }.onFailure { abortFailure ->
                logger.error("Failed to mark open play captures incomplete", abortFailure)
            }
        }
    }
}
