package dev.mcdata.recorder.capture

import com.mojang.authlib.GameProfile
import dev.recorderminecraft.artifacts.v1.CaptureEvent
import dev.recorderminecraft.artifacts.v1.ControlState
import dev.recorderminecraft.artifacts.v1.ControlStateEvent
import dev.recorderminecraft.artifacts.v1.EventIdentity
import dev.recorderminecraft.artifacts.v1.PacketApplyEvent
import dev.recorderminecraft.artifacts.v1.PacketArrivalEvent
import dev.recorderminecraft.artifacts.v1.ReplayCoverage
import dev.recorderminecraft.artifacts.v1.ReplayTimelineEvent
import dev.mcdata.recorder.config.RecorderConfig
import dev.mcdata.recorder.io.AsyncPlayWriter
import dev.mcdata.recorder.io.PlayFiles
import dev.mcdata.recorder.model.ControlStateTracker
import dev.mcdata.recorder.network.ReplayTimelinePayload
import net.casual.arcade.replay.recorder.ReplayRecorder
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking
import net.minecraft.network.protocol.Packet
import net.minecraft.server.MinecraftServer
import net.minecraft.server.level.ServerPlayer
import org.slf4j.Logger
import java.nio.file.Path
import java.time.Instant
import java.util.IdentityHashMap
import java.util.UUID

class CaptureCoordinator(
    private val config: RecorderConfig,
    private val sessionId: String,
    private val logger: Logger
) : AutoCloseable {
    private var serverTick = 0L
    private var applySequence = 0L
    private var active = true
    private var tickPhase = TickPhase.BEFORE_FIRST_TICK
    private val arrivals = IdentityHashMap<Packet<*>, ArrivalStamp>()
    private val controls = mutableMapOf<UUID, ControlStateTracker>()
    private val captures = mutableMapOf<UUID, ConnectionCapture>()
    private val replays = ReplayCaptureTracker(sessionId) { playerUuid -> captures[playerUuid]?.playFiles }

    @Synchronized
    fun replayPathFor(profile: GameProfile): Path? {
        if (!active) return null
        check(!captures.containsKey(profile.id)) { "player already has an allocated play capture" }
        val connectionId = UUID.randomUUID()
        val playFiles = PlayFiles.create(
            config = config,
            sessionId = sessionId,
            playerName = profile.name,
            playerUuid = profile.id,
            connectionId = connectionId,
            startedAt = Instant.now(),
            startServerTick = associatedEventTick()
        )
        val capture = ConnectionCapture(
            id = connectionId.toString(),
            playerUuid = profile.id,
            startServerTick = associatedEventTick(),
            playFiles = playFiles,
            writer = AsyncPlayWriter(playFiles.paths.events, config.writerQueueCapacity, logger)
        )
        captures[profile.id] = capture
        logger.info("Started play capture {} in {}", capture.id, playFiles.paths.root)
        return playFiles.paths.replayWorking
    }

    @Synchronized
    fun startTick() {
        if (!active) return
        serverTick++
        tickPhase = TickPhase.IN_TICK
        arrivals.entries.removeIf { (_, stamp) -> serverTick - stamp.arrivalTick > ARRIVAL_RETENTION_TICKS }
    }

    @Synchronized
    fun packetArrival(player: ServerPlayer, packet: Packet<*>) {
        val capture = activeCapture(player) ?: return
        val eventTick = associatedEventTick()
        val normalized = PacketNormalizer.normalize(packet)
        val arrivalSequence = capture.peekNextSequence()
        arrivals[packet] = ArrivalStamp(arrivalSequence, eventTick, normalized)
        capture.emit(eventTick, player) { event ->
            event.packetArrival = PacketArrivalEvent.newBuilder()
                .setArrivalSequence(arrivalSequence)
                .setTickPhase(tickPhase.serialized)
                .setNetworkThread(Thread.currentThread().name)
                .setPacket(normalized.data.build())
                .build()
        }
    }

    @Synchronized
    fun packetApply(player: ServerPlayer, packet: Packet<*>) {
        val capture = activeCapture(player) ?: return
        val eventTick = associatedEventTick()
        applySequence++
        val arrival = arrivals.remove(packet)
        val normalized = arrival?.normalized ?: PacketNormalizer.normalize(packet)
        PacketNormalizer.enrichAtApply(player, packet, normalized.data)
        normalized.input?.let { controls.getOrPut(player.uuid, ::ControlStateTracker).updateInput(it) }
        normalized.selectedSlot?.let { controls.getOrPut(player.uuid, ::ControlStateTracker).updateSelectedSlot(it) }

        capture.emit(eventTick, player) { event ->
            val applied = PacketApplyEvent.newBuilder()
                .setApplySequence(applySequence)
                .setPhase("main_thread_before_handler_body")
                .setTickPhase(tickPhase.serialized)
                .setMainThread(Thread.currentThread().name)
                .setPacket(normalized.data.build())
            if (arrival != null) {
                applied.arrivalSequence = arrival.arrivalSequence
                applied.arrivalServerTick = arrival.arrivalTick
            } else {
                applied.arrivalMissing = true
            }
            event.packetApply = applied.build()
        }
    }

    @Synchronized
    fun replayRecorderStarted(recorder: ReplayRecorder) = replays.recorderStarted(recorder)

    @Synchronized
    fun replayRecorderSaved(recorder: ReplayRecorder, output: Path) = replays.recorderSaved(recorder, output)

    @Synchronized
    fun replayRecorderClosed(recorder: ReplayRecorder) = replays.recorderClosed(recorder)

    @Synchronized
    fun playerJoin(player: ServerPlayer) {
        if (!active) return
        val capture = captures[player.uuid] ?: run {
            replayPathFor(player.gameProfile)
            captures.getValue(player.uuid)
        }
        if (capture.joined) return
        capture.joined = true
        capture.entityId = player.id
        controls.getOrPut(player.uuid, ::ControlStateTracker)
    }

    @Synchronized
    fun playerLeave(player: ServerPlayer) {
        if (!active) return
        val capture = activeCapture(player) ?: return
        val leaveTick = associatedEventTick()
        closeEvents(capture, Instant.now(), leaveTick, "disconnect")
        controls.remove(player.uuid)
        captures.remove(player.uuid)
    }

    @Synchronized
    fun endTick(server: MinecraftServer) {
        if (!active) return
        val stateBarrier = applySequence
        for (player in server.playerList.players.sortedBy { it.uuid.toString() }) {
            val capture = activeCapture(player) ?: continue
            val state = PlayerSnapshot.capture(player, config)
            capture.emit(serverTick, player) { event ->
                state.stateBarrierApplySequence = stateBarrier
                state.replayCoverage = ReplayCoverage.newBuilder()
                    .setKind("client_visible_best_effort")
                    .setCenterChunkX(player.chunkPosition().x)
                    .setCenterChunkZ(player.chunkPosition().z)
                    .setViewDistanceChunks(server.playerList.viewDistance)
                    .setComplete(false)
                    .build()
                event.playerState = state.build()
            }

            val tracker = controls.getOrPut(player.uuid, ::ControlStateTracker)
            val control = tracker.endTick(player.yRot, player.xRot, player.inventory.selectedSlot)
            capture.emit(serverTick, player) { event ->
                event.controlState = ControlStateEvent.newBuilder().setState(
                    ControlState.newBuilder()
                        .setForward(control.input.forward).setBackward(control.input.backward)
                        .setLeft(control.input.left).setRight(control.input.right)
                        .setJump(control.input.jump).setSneak(control.input.sneak).setSprint(control.input.sprint)
                        .setCameraYaw(control.yaw.toDouble()).setCameraPitch(control.pitch.toDouble())
                        .setCameraDeltaYaw(control.deltaYaw.toDouble()).setCameraDeltaPitch(control.deltaPitch.toDouble())
                        .setSelectedSlot(control.selectedSlot)
                ).build()
            }

            val markerSequence = capture.emit(serverTick, player) { event ->
                event.replayTimeline = ReplayTimelineEvent.newBuilder()
                    .setProtocol("recorder-minecraft:timeline/v1")
                    .build()
            }
            ServerPlayNetworking.send(
                player,
                ReplayTimelinePayload(sessionId, capture.id, serverTick, markerSequence)
            )
        }
        tickPhase = TickPhase.BETWEEN_TICKS
    }

    override fun close() {
        synchronized(this) {
            if (!active) return
            val terminalTick = associatedEventTick()
            captures.values.sortedBy { it.playerUuid.toString() }.forEach { capture ->
                if (capture.joined) {
                    closeEvents(capture, Instant.now(), terminalTick, "server_shutdown")
                } else {
                    capture.writer.abort()
                    capture.playFiles.fail("server stopped before player join")
                }
            }
            captures.clear()
            controls.clear()
            active = false
            logger.info("Stopped recorder capture process {} at tick {}", sessionId, serverTick)
        }
    }

    fun abort(failure: Throwable) {
        val open = synchronized(this) {
            if (!active) return
            active = false
            captures.values.toList().also {
                captures.clear()
                controls.clear()
            }
        }
        val reason = "${failure::class.java.simpleName}: ${failure.message ?: "capture failure"}"
        open.forEach { capture ->
            capture.writer.abort()
            runCatching { capture.playFiles.fail(reason) }.onFailure { metadataFailure ->
                logger.error("Could not mark play capture failed for {}", capture.id, metadataFailure)
            }
        }
    }

    private fun closeEvents(
        capture: ConnectionCapture,
        endedAt: Instant,
        endServerTick: Long,
        terminalReason: String
    ) {
        capture.writer.close()
        capture.playFiles.eventsClosed(endedAt, endServerTick, terminalReason)
    }

    private fun activeCapture(player: ServerPlayer): ConnectionCapture? =
        captures[player.uuid]?.takeIf { active && it.joined }

    private fun associatedEventTick(): Long =
        if (tickPhase == TickPhase.BETWEEN_TICKS) serverTick + 1 else serverTick

    private inner class ConnectionCapture(
        val id: String,
        val playerUuid: UUID,
        val startServerTick: Long,
        val playFiles: PlayFiles,
        val writer: AsyncPlayWriter,
        var joined: Boolean = false,
        var entityId: Int = -1,
        private var sequence: Long = 0
    ) {
        fun peekNextSequence(): Long = sequence + 1

        fun emit(recordTick: Long, player: ServerPlayer, payload: (CaptureEvent.Builder) -> Unit): Long {
            sequence++
            val identity = EventIdentity.newBuilder()
                .setSchemaVersion(1)
                .setSessionId(sessionId)
                .setServerTick(recordTick)
                .setSequence(sequence)
                .setRecordedAtNs(System.nanoTime())
                .setRecordedAtUnixMs(System.currentTimeMillis())
                .setPlayerUuid(player.uuid.toString())
                .setPlayerName(player.gameProfile.name)
                .setEntityId(player.id.toLong())
                .setConnectionId(id)
                .setConnectionStartServerTick(startServerTick)
            val record = CaptureEvent.newBuilder().setIdentity(identity)
            payload(record)
            writer.submit(record.build())
            return sequence
        }
    }

    private data class ArrivalStamp(
        val arrivalSequence: Long,
        val arrivalTick: Long,
        val normalized: PacketNormalizer.Result
    )

    private enum class TickPhase(val serialized: String) {
        BEFORE_FIRST_TICK("before_first_tick"),
        IN_TICK("in_tick"),
        BETWEEN_TICKS("between_ticks_before_start")
    }

    companion object {
        private const val ARRIVAL_RETENTION_TICKS = 200L
    }
}
