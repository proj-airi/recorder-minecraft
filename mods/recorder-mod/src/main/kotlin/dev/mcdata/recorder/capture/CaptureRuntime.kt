package dev.mcdata.recorder.capture

import com.mojang.authlib.GameProfile
import dev.mcdata.recorder.config.RecorderConfig
import net.casual.arcade.replay.recorder.ReplayRecorder
import net.minecraft.network.protocol.Packet
import net.minecraft.core.BlockPos
import net.minecraft.server.MinecraftServer
import net.minecraft.server.level.ServerLevel
import net.minecraft.server.level.ServerPlayer
import net.minecraft.world.level.block.entity.BlockEntity
import org.slf4j.Logger
import java.nio.file.Path
import java.util.UUID
import java.util.concurrent.CompletableFuture

object CaptureRuntime {
    @Volatile
    private var coordinator: CaptureCoordinator? = null
    @Volatile
    private var failed = false
    @Volatile
    private var world: WorldCapture? = null
    private lateinit var logger: Logger

    fun initialize(logger: Logger) {
        this.logger = logger
    }

    @Synchronized
    fun start(server: MinecraftServer, config: RecorderConfig) {
        check(coordinator == null) { "a capture process is already active" }
        val sessionId = UUID.randomUUID().toString()
        val current = CaptureCoordinator(config, sessionId, logger) { world?.reference }
        coordinator = current
        failed = false
        logger.info("Started recorder capture process {}", sessionId)
        world = openWorld(server, config, sessionId, current.eventTick())

        server.playerList.players.forEach { coordinator?.playerJoin(it) }
    }

    @JvmStatic
    @Synchronized
    fun replayPathFor(profile: GameProfile): Path? = coordinator?.replayPathFor(profile)

    fun startTick() = safely { it.startTick() }

    fun endTick(server: MinecraftServer) {
        // The world flush runs first, while the coordinator is still inside the tick, so its
        // records carry the same server_tick as this tick's player_state records.
        withWorld { world, clock -> world.endTick(clock.eventTick()) }
        safely { it.endTick(server) }
    }

    fun playerJoin(player: ServerPlayer) = safely { it.playerJoin(player) }
    fun playerLeave(player: ServerPlayer) = safely { it.playerLeave(player) }
    fun packetArrival(player: ServerPlayer, packet: Packet<*>) = safely { it.packetArrival(player, packet) }
    fun replayRecorderStarted(recorder: ReplayRecorder) = safely { it.replayRecorderStarted(recorder) }
    fun replayRecorderStopping(recorder: ReplayRecorder, future: CompletableFuture<Long>) =
        safely { it.replayRecorderStopping(recorder, future) }
    fun replayRecorderSaved(recorder: ReplayRecorder, output: Path) = safely { it.replayRecorderSaved(recorder, output) }
    fun replayRecorderClosed(recorder: ReplayRecorder) = safely { it.replayRecorderClosed(recorder) }
    fun finishStoppingReplays() = safely { it.finishStoppingReplays() }

    @JvmStatic
    fun packetApply(player: ServerPlayer, packet: Packet<*>) = safely { it.packetApply(player, packet) }

    // Hot path: every server-side block entity change reaches this hook. withWorld returns on a
    // single volatile read when no world stream is open.
    @JvmStatic
    fun blockEntityChanged(level: ServerLevel, pos: BlockPos) =
        withWorld { world, _ -> world.blockEntityChanged(level, pos) }

    @JvmStatic
    fun chunkLoadedChanged(level: ServerLevel, chunkPos: Long, loaded: Boolean) =
        withWorld { world, _ -> world.chunkLoadedChanged(level, chunkPos, loaded) }

    fun blockEntityLoaded(level: ServerLevel, blockEntity: BlockEntity) =
        withWorld { world, _ -> world.blockEntityLoaded(level, blockEntity) }

    fun blockEntityUnloaded(level: ServerLevel, blockEntity: BlockEntity) =
        withWorld { world, clock -> world.blockEntityUnloaded(level, blockEntity, clock.eventTick()) }

    /**
     * Closes the world stream before worlds unload. Fabric fires BLOCK_ENTITY_UNLOAD for every
     * loaded block entity during world shutdown, which would otherwise be recorded as destruction.
     */
    @Synchronized
    fun closeWorld(terminalReason: String) {
        withWorld { world, clock ->
            this.world = null
            world.close(clock.eventTick(), terminalReason)
        }
    }

    @Synchronized
    fun stop() {
        closeWorld("server_shutdown")
        val current = coordinator ?: return
        runCatching { current.finishStoppingReplays() }.onFailure {
            logger.error("Failed to finish ServerReplay archives during server shutdown", it)
        }
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
            // The world stream borrows the Play capture tick clock, which stops advancing here.
            world?.let { stream ->
                world = null
                stream.fail(IllegalStateException("play capture aborted; server tick clock stopped", throwable),
                    current.eventTick(), logger)
            }
        }
    }

    private fun openWorld(server: MinecraftServer, config: RecorderConfig, sessionId: String, tick: Long): WorldCapture? {
        val stream = try {
            WorldCapture.open(config, sessionId, tick, logger)
        } catch (throwable: Throwable) {
            logger.error("World stream disabled for capture process {}; Plays keep the unopened-container gap", sessionId, throwable)
            return null
        }
        return try {
            stream.sessionStart(server, tick)
            stream
        } catch (throwable: Throwable) {
            logger.error("World stream failed during session-start enumeration", throwable)
            stream.fail(throwable, tick, logger)
            null
        }
    }

    /**
     * Runs world stream work isolated from Play capture.
     *
     * A world stream failure disables only the world stream: its metadata records the failure and
     * the coverage end tick, Plays started afterwards fall back to the unopened-container gap, and
     * Play capture continues. The reverse does not hold; see [safely].
     */
    private inline fun withWorld(block: (WorldCapture, CaptureCoordinator) -> Unit) {
        val current = world ?: return
        val clock = coordinator ?: return
        if (failed) return
        try {
            block(current, clock)
        } catch (throwable: Throwable) {
            if (world === current) world = null
            logger.error("World stream disabled after a write/capture error; Play capture continues", throwable)
            current.fail(throwable, clock.eventTick(), logger)
        }
    }
}
