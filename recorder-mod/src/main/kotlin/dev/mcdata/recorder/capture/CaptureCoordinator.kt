package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import dev.mcdata.recorder.config.RecorderConfig
import dev.mcdata.recorder.control.RecorderControlPlane
import dev.mcdata.recorder.io.AsyncEpochWriter
import dev.mcdata.recorder.io.SessionFiles
import dev.mcdata.recorder.model.ControlStateTracker
import dev.mcdata.recorder.model.EpochRotationPolicy
import dev.mcdata.recorder.network.ReplayTimelinePayload
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking
import net.minecraft.network.protocol.Packet
import net.minecraft.server.MinecraftServer
import net.minecraft.server.level.ServerPlayer
import org.slf4j.Logger
import java.util.IdentityHashMap
import java.util.UUID

class CaptureCoordinator(
    private val config: RecorderConfig,
    private val session: SessionFiles,
    private val writer: AsyncEpochWriter,
    private val controlPlane: RecorderControlPlane?,
    private val logger: Logger
) : AutoCloseable {
    private var serverTick = 0L
    private var sequence = 0L
    private var applySequence = 0L
    private var active = true
    private var lastHeartbeatAtUnixMs = 0L
    private var tickPhase = TickPhase.BEFORE_FIRST_TICK
    private val epochRotation = EpochRotationPolicy(config.epochTicks)
    private val arrivals = IdentityHashMap<Packet<*>, ArrivalStamp>()
    private val controls = mutableMapOf<UUID, ControlStateTracker>()
    private val connections = mutableMapOf<UUID, ConnectionCapture>()

    init {
        synchronized(this) {
            emit("session_start") {
                addProperty("epoch_ticks", config.epochTicks)
                addProperty("capture_root", config.capturePath().toString())
            }
            publishStatus(force = true)
        }
    }

    @Synchronized
    fun startTick() {
        if (!active) return
        serverTick++
        tickPhase = TickPhase.IN_TICK
        arrivals.entries.removeIf { (_, stamp) -> serverTick - stamp.arrivalTick > ARRIVAL_RETENTION_TICKS }
        emit("tick_start") { addProperty("apply_sequence_at_barrier", applySequence) }
    }

    @Synchronized
    fun packetArrival(player: ServerPlayer, packet: Packet<*>) {
        if (!active) return
        val eventTick = associatedEventTick()
        val normalized = PacketNormalizer.normalize(packet)
        val arrivalSequence = sequence + 1
        arrivals[packet] = ArrivalStamp(arrivalSequence, eventTick, normalized)
        emit("packet_arrival", eventTick) {
            addPlayer(player)
            addProperty("arrival_sequence", arrivalSequence)
            addProperty("tick_phase", tickPhase.serialized)
            addProperty("network_thread", Thread.currentThread().name)
            // The same normalized object is enriched with authoritative target data at apply time.
            // Queue an immutable arrival snapshot so writer timing cannot change this record.
            add("packet", normalized.data.deepCopy())
        }
    }

    @Synchronized
    fun packetApply(player: ServerPlayer, packet: Packet<*>) {
        if (!active) return
        val eventTick = associatedEventTick()
        applySequence++
        val arrival = arrivals.remove(packet)
        val normalized = arrival?.normalized ?: PacketNormalizer.normalize(packet)
        PacketNormalizer.enrichAtApply(player, packet, normalized.data)
        normalized.input?.let { controls.getOrPut(player.uuid, ::ControlStateTracker).updateInput(it) }
        normalized.data.get("slot")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.let {
            controls.getOrPut(player.uuid, ::ControlStateTracker).updateSelectedSlot(it.asInt)
        }

        emit("packet_apply", eventTick) {
            addPlayer(player)
            addProperty("apply_sequence", applySequence)
            addProperty("phase", "main_thread_before_handler_body")
            addProperty("tick_phase", tickPhase.serialized)
            addProperty("main_thread", Thread.currentThread().name)
            if (arrival != null) {
                addProperty("arrival_sequence", arrival.arrivalSequence)
                addProperty("arrival_server_tick", arrival.arrivalTick)
            } else {
                addProperty("arrival_missing", true)
            }
            add("packet", normalized.data)
        }
    }

    @Synchronized
    fun playerJoin(player: ServerPlayer) {
        if (!active) return
        if (connections.containsKey(player.uuid)) return
        val connection = ConnectionCapture(
            id = UUID.randomUUID().toString(),
            playerUuid = player.uuid,
            playerName = player.gameProfile.name,
            entityId = player.id,
            startServerTick = associatedEventTick(),
            startSequence = sequence + 1
        )
        connections[player.uuid] = connection
        controls.getOrPut(player.uuid, ::ControlStateTracker)
        emit("player_join", associatedEventTick()) {
            addPlayer(player)
            addProperty("replay_timeline_protocol", "mc_recorder:timeline/v1")
        }
        controlSafely("publish player join") {
            it.connectionStarted(
                playerUuid = connection.playerUuid.toString(),
                playerName = connection.playerName,
                connectionId = connection.id,
                joinServerTick = connection.startServerTick,
                joinSequence = connection.startSequence
            )
        }
    }

    @Synchronized
    fun playerLeave(player: ServerPlayer) {
        if (!active) return
        val connection = connections[player.uuid] ?: return
        val leaveTick = associatedEventTick()
        emit("player_leave", leaveTick) {
            addPlayer(player)
            addProperty("terminal_reason", "disconnect")
        }
        controlSafely("publish player disconnect") {
            it.connectionEnded(connection.id, leaveTick, sequence, "disconnect")
        }
        controls.remove(player.uuid)
        connections.remove(player.uuid)
    }

    @Synchronized
    fun endTick(server: MinecraftServer) {
        if (!active) return
        val players = server.playerList.players.sortedBy { it.uuid.toString() }
        val stateBarrier = applySequence
        for (player in players) {
            val state = PlayerSnapshot.capture(player, config)
            emit("player_state") {
                merge(state)
                addPlayer(player)
                addProperty("state_barrier_apply_sequence", stateBarrier)
                add("replay_coverage", JsonObject().apply {
                    addProperty("kind", "client_visible_best_effort")
                    addProperty("center_chunk_x", player.chunkPosition().x)
                    addProperty("center_chunk_z", player.chunkPosition().z)
                    addProperty("view_distance_chunks", server.playerList.viewDistance)
                    addProperty("complete", false)
                })
            }

            val tracker = controls.getOrPut(player.uuid, ::ControlStateTracker)
            val control = tracker.endTick(player.yRot, player.xRot, player.inventory.selectedSlot)
            emit("control_state") {
                addPlayer(player)
                addProperty("forward", control.input.forward)
                addProperty("backward", control.input.backward)
                addProperty("left", control.input.left)
                addProperty("right", control.input.right)
                addProperty("jump", control.input.jump)
                addProperty("sneak", control.input.sneak)
                addProperty("sprint", control.input.sprint)
                addProperty("camera_yaw", control.yaw)
                addProperty("camera_pitch", control.pitch)
                addProperty("camera_delta_yaw", control.deltaYaw)
                addProperty("camera_delta_pitch", control.deltaPitch)
                addProperty("selected_slot", control.selectedSlot)
            }

            val markerSequence = sequence + 1
            emit("replay_timeline") {
                addPlayer(player)
                addProperty("marker_event_sequence", markerSequence)
                addProperty("protocol", "mc_recorder:timeline/v1")
            }
            val connection = connections.getValue(player.uuid)
            ServerPlayNetworking.send(
                player,
                ReplayTimelinePayload(session.sessionId, connection.id, serverTick, markerSequence)
            )
        }
        emit("tick_end") {
            addProperty("player_count", players.size)
            addProperty("apply_sequence_at_barrier", applySequence)
        }
        tickPhase = TickPhase.BETWEEN_TICKS
        processEndOfTickControl()
        publishStatus()
    }

    override fun close() {
        var pendingRequests = emptyList<RecorderControlPlane.SealRequest>()
        var shutdownConnections = emptyList<ShutdownConnection>()
        try {
            synchronized(this) {
                if (!active) return
                val terminalTick = associatedEventTick()
                shutdownConnections = connections.values.sortedBy { it.playerUuid.toString() }.map { connection ->
                    emit("player_leave", terminalTick) {
                        addConnection(connection)
                        addProperty("terminal_reason", "server_shutdown")
                    }
                    ShutdownConnection(connection.id, terminalTick, sequence)
                }
                connections.clear()
                controls.clear()
                emit("session_end", associatedEventTick()) {
                    addProperty("clean_shutdown", true)
                    addProperty("apply_sequence_at_end", applySequence)
                }
                pendingRequests = pollSealRequests()
                active = false
                publishStatus(force = true, state = "stopping")
            }
            writer.close()
            shutdownConnections.forEach { connection ->
                controlSafely("publish shutdown disconnect") {
                    it.connectionEnded(
                        connection.connectionId,
                        connection.endServerTick,
                        connection.endSequence,
                        "server_shutdown"
                    )
                }
            }
            writer.metrics().lastSealedEpoch?.let { sealed ->
                completeRequests(pendingRequests, sealed, reused = false, coalesced = pendingRequests.size > 1)
            }
            publishStatus(force = true, state = "stopped")
            logger.info("Sealed dataset recording session {} at tick {}", session.sessionId, serverTick)
        } catch (throwable: Throwable) {
            // If publishing the final record failed before the coordinator became inactive,
            // abandon the active epoch and publish an incomplete session marker.
            abort(throwable)
            throw throwable
        }
    }

    fun abort(failure: Throwable) {
        val wasActive = synchronized(this) {
            val previous = active
            active = false
            previous
        }
        val reason = "${failure::class.java.simpleName}: ${failure.message ?: "capture failure"}"
        val pendingRequests = pollSealRequests()
        controlSafely("publish writer failure responses") {
            it.failSealRequests(pendingRequests, "writer_failed", reason)
        }
        writer.abort(reason)
        publishStatus(force = true, state = "failed", failureReason = reason)
        if (wasActive) {
            logger.error("Marked dataset recording session {} incomplete at tick {}", session.sessionId, serverTick)
        }
    }

    private fun emit(recordType: String, recordTick: Long = serverTick, payload: JsonObject.() -> Unit) {
        sequence++
        val epochIndex = epochRotation.currentEpochIndex
        val record = JsonObject().apply {
            addProperty("schema_version", 1)
            addProperty("record_type", recordType)
            addProperty("session_id", session.sessionId)
            addProperty("epoch_index", epochIndex)
            addProperty("server_tick", recordTick)
            addProperty("sequence", sequence)
            addProperty("recorded_at_ns", System.nanoTime())
            addProperty("recorded_at_unix_ms", System.currentTimeMillis())
            payload()
        }
        writer.submit(AsyncEpochWriter.QueuedRecord(epochIndex, recordTick, sequence, recordType, record))
    }

    private fun associatedEventTick(): Long =
        if (tickPhase == TickPhase.BETWEEN_TICKS) serverTick + 1 else serverTick

    private fun JsonObject.addPlayer(player: ServerPlayer) {
        addProperty("player_uuid", player.uuid.toString())
        addProperty("player_name", player.gameProfile.name)
        addProperty("entity_id", player.id)
        connections[player.uuid]?.let { connection ->
            addProperty("connection_id", connection.id)
            addProperty("connection_start_server_tick", connection.startServerTick)
        }
    }

    private fun JsonObject.addConnection(connection: ConnectionCapture) {
        addProperty("player_uuid", connection.playerUuid.toString())
        addProperty("player_name", connection.playerName)
        addProperty("entity_id", connection.entityId)
        addProperty("connection_id", connection.id)
        addProperty("connection_start_server_tick", connection.startServerTick)
    }

    private fun processEndOfTickControl() {
        val pending = pollSealRequests()
        val latestSeal = writer.metrics().lastSealedEpoch
        val (reused, requiringSeal) = pending.partition { request ->
            latestSeal != null && latestSeal.lastSequence >= request.connectionEndSequence
        }
        if (latestSeal != null) {
            completeRequests(reused, latestSeal, reused = true, coalesced = reused.size > 1)
        }

        val rotation = epochRotation.rotationAtEndTick(serverTick, requiringSeal.isNotEmpty()) ?: return
        val sealed = try {
            writer.sealEpoch(rotation.reason, rotation.forced)
        } catch (throwable: Throwable) {
            controlSafely("publish seal failure") {
                it.failSealRequests(
                    requiringSeal,
                    "seal_failed",
                    "${throwable::class.java.simpleName}: ${throwable.message ?: "epoch seal failed"}"
                )
            }
            throw throwable
        }
        check(sealed.epochIndex == rotation.epochIndex) {
            "writer sealed epoch ${sealed.epochIndex}, expected ${rotation.epochIndex}"
        }
        epochRotation.advanceAfter(rotation, serverTick)
        completeRequests(
            requiringSeal,
            sealed,
            reused = false,
            coalesced = requiringSeal.size > 1 || rotation.automatic
        )
    }

    private fun pollSealRequests(): List<RecorderControlPlane.SealRequest> =
        controlSafely("poll seal requests", emptyList()) { it.pollSealRequests() }

    private fun completeRequests(
        requests: Collection<RecorderControlPlane.SealRequest>,
        sealed: AsyncEpochWriter.SealedEpoch,
        reused: Boolean,
        coalesced: Boolean
    ) {
        requests.forEach { request ->
            controlSafely("publish seal response") {
                it.completeSealRequest(request, sealed, reused, coalesced)
            }
        }
    }

    private fun publishStatus(
        force: Boolean = false,
        state: String = "recording",
        failureReason: String? = null
    ) {
        val now = System.currentTimeMillis()
        if (!force && now - lastHeartbeatAtUnixMs < HEARTBEAT_INTERVAL_MILLIS) return
        controlSafely("publish recorder status") {
            it.publishStatus(
                RecorderControlPlane.StatusSnapshot(
                    state = state,
                    serverTick = serverTick,
                    sequence = sequence,
                    applySequence = applySequence,
                    epochIndex = epochRotation.currentEpochIndex,
                    epochStartServerTick = epochRotation.currentEpochStartTick,
                    writer = writer.metrics(),
                    failureReason = failureReason
                )
            )
            lastHeartbeatAtUnixMs = now
        }
    }

    private inline fun controlSafely(description: String, block: (RecorderControlPlane) -> Unit) {
        val control = controlPlane ?: return
        runCatching { block(control) }.onFailure {
            logger.error("Could not {} in recorder control plane; source capture remains active", description, it)
        }
    }

    private inline fun <T> controlSafely(
        description: String,
        fallback: T,
        block: (RecorderControlPlane) -> T
    ): T {
        val control = controlPlane ?: return fallback
        return runCatching { block(control) }.getOrElse {
            logger.error("Could not {} in recorder control plane; source capture remains active", description, it)
            fallback
        }
    }

    private fun JsonObject.merge(other: JsonObject) {
        other.entrySet().forEach { (key, value) -> add(key, value) }
    }

    private data class ArrivalStamp(
        val arrivalSequence: Long,
        val arrivalTick: Long,
        val normalized: PacketNormalizer.Result
    )

    private data class ConnectionCapture(
        val id: String,
        val playerUuid: UUID,
        val playerName: String,
        val entityId: Int,
        val startServerTick: Long,
        val startSequence: Long
    )

    private data class ShutdownConnection(
        val connectionId: String,
        val endServerTick: Long,
        val endSequence: Long
    )

    private enum class TickPhase(val serialized: String) {
        BEFORE_FIRST_TICK("before_first_tick"),
        IN_TICK("in_tick"),
        BETWEEN_TICKS("between_ticks_before_start")
    }

    companion object {
        private const val ARRIVAL_RETENTION_TICKS = 200L
        private const val HEARTBEAT_INTERVAL_MILLIS = 1_000L
    }
}
