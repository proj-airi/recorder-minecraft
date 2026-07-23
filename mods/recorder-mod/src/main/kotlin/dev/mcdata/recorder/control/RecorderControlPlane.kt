package dev.mcdata.recorder.control

import com.google.gson.GsonBuilder
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import dev.mcdata.recorder.io.AsyncEpochWriter
import org.slf4j.Logger
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.StandardOpenOption
import java.time.Instant
import java.util.UUID

/**
 * Runtime-only dashboard bridge. None of these files are accepted as capture source truth:
 * append-only source records, completed replay archives, and verified slice manifests remain
 * authoritative.
 */
class RecorderControlPlane(
    private val sessionId: String,
    private val sessionDirectory: Path,
    controlRoot: Path,
    private val logger: Logger,
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    private val root = controlRoot.toAbsolutePath().normalize()
    private val sessions = root.resolve("sessions")
    private val renderReady = root.resolve("render-ready")
    private val connections = linkedMapOf<String, ConnectionRecord>()
    private var connectionsDirty = true

    init {
        require(SESSION_ID_PATTERN.matches(sessionId)) { "session_id is not safe for the control spool" }
        Files.createDirectories(root)
        check(!Files.isSymbolicLink(root)) { "control_root must not be a symbolic link: $root" }
        Files.createDirectories(sessions)
        Files.createDirectories(renderReady)
        check(
            !Files.isSymbolicLink(sessions) &&
                !Files.isSymbolicLink(renderReady)
        ) {
            "control session and render-ready directories must not be symbolic links"
        }
        writeConnections()
    }

    @Synchronized
    fun connectionStarted(
        playerUuid: String,
        playerName: String,
        connectionId: String,
        joinServerTick: Long,
        joinSequence: Long
    ) {
        require(connectionId !in connections) { "duplicate connection_id: $connectionId" }
        connections[connectionId] = ConnectionRecord(
            playerUuid = playerUuid,
            playerName = playerName,
            connectionId = connectionId,
            joinServerTick = joinServerTick,
            joinSequence = joinSequence
        )
        writeConnections()
    }

    @Synchronized
    fun connectionEnded(
        connectionId: String,
        endServerTick: Long,
        endSequence: Long,
        terminalReason: String
    ) {
        require(terminalReason in TERMINAL_REASONS) { "unsupported terminal reason: $terminalReason" }
        val connection = checkNotNull(connections[connectionId]) { "unknown connection_id: $connectionId" }
        if (connection.endSequence != null) return
        connection.endServerTick = endServerTick
        connection.endSequence = endSequence
        connection.terminalReason = terminalReason
        writeConnections()
    }

    @Synchronized
    fun publishStatus(status: StatusSnapshot) {
        if (connectionsDirty) {
            runCatching { writeConnections() }.onFailure {
                logger.error("Could not retry recorder connection ledger publication", it)
            }
        }
        val now = nowMillis()
        val writer = status.writer
        val json = JsonObject().apply {
            addProperty("schema_version", CONTROL_SCHEMA_VERSION)
            addProperty("session_id", sessionId)
            addProperty("state", status.state)
            addProperty("updated_at", Instant.ofEpochMilli(now).toString())
            addProperty("updated_at_unix_ms", now)
            addProperty("server_tick", status.serverTick)
            addProperty("sequence", status.sequence)
            addProperty("apply_sequence", status.applySequence)
            add("epoch", JsonObject().apply {
                addProperty("index", status.epochIndex)
                addProperty("start_server_tick", status.epochStartServerTick)
                addProperty("ticks_recorded", maxOf(0, status.serverTick - status.epochStartServerTick + 1))
                writer.lastSealedEpoch?.let { addProperty("last_sealed_index", it.epochIndex) }
            })
            add("writer", JsonObject().apply {
                addProperty("queue_size", writer.queueSize)
                addProperty("queue_capacity", writer.queueCapacity)
                addProperty("closing", writer.closing)
                addProperty("failed", writer.failed)
                writer.failureReason?.let { addProperty("failure_reason", it) }
                writer.lastWrittenEpochIndex?.let { addProperty("last_written_epoch_index", it) }
                writer.lastWrittenServerTick?.let { addProperty("last_written_server_tick", it) }
                writer.lastWrittenSequence?.let { addProperty("last_written_sequence", it) }
                writer.lastWrittenAtUnixMs?.let { addProperty("last_written_at_unix_ms", it) }
            })
            add("storage", storageStatus(status.epochIndex, writer, now))
            add("active_connections", JsonArray().also { array ->
                connections.values.filter { it.active }.forEach { array.add(it.toJson()) }
            })
            status.failureReason?.let { addProperty("failure_reason", it.take(2_048)) }
        }
        atomicWrite(root.resolve("status.json"), json)
    }

    @Synchronized
    fun publishReplaySegments(segments: List<ReplaySegmentSnapshot>) {
        val now = nowMillis()
        val json = JsonObject().apply {
            addProperty("schema_version", CONTROL_SCHEMA_VERSION)
            addProperty("session_id", sessionId)
            addProperty("updated_at", Instant.ofEpochMilli(now).toString())
            addProperty("updated_at_unix_ms", now)
            add("segments", JsonArray().also { array ->
                segments.sortedBy { it.segmentOrdinal }.forEach { array.add(it.toJson()) }
            })
        }
        atomicWrite(sessions.resolve("$sessionId.replay-segments.json"), json)
        atomicWrite(root.resolve("replay-segments.json"), json)
        publishRenderReadySegments(segments)
    }

    private fun publishRenderReadySegments(segments: List<ReplaySegmentSnapshot>) {
        segments
            .filter {
                it.state == "saved" &&
                    it.connectionId != null &&
                    it.connectionEndServerTick != null &&
                    it.connectionEndSequence != null &&
                    it.output != null &&
                    it.outputSizeBytes != null
            }
            .groupBy { checkNotNull(it.connectionId) }
            .forEach { (connectionId, connectionSegments) ->
                if (!isUuid(connectionId)) return@forEach
                val first = connectionSegments.minBy { it.segmentOrdinal }
                val now = nowMillis()
                val json = JsonObject().apply {
                    addProperty("schema_version", CONTROL_SCHEMA_VERSION)
                    addProperty("kind", "render_ready")
                    addProperty("session_id", sessionId)
                    addProperty("player_uuid", first.playerUuid)
                    addProperty("connection_id", connectionId)
                    addProperty("connection_end_server_tick", first.connectionEndServerTick)
                    addProperty("connection_end_sequence", first.connectionEndSequence)
                    first.terminalReason?.let { addProperty("terminal_reason", it) }
                    addProperty("emitted_at", Instant.ofEpochMilli(now).toString())
                    addProperty("emitted_at_unix_ms", now)
                    add("segments", JsonArray().also { array ->
                        connectionSegments.sortedBy { it.segmentOrdinal }.forEach { segment ->
                            array.add(JsonObject().apply {
                                addProperty("segment_id", segment.segmentId)
                                addProperty("segment_ordinal", segment.segmentOrdinal)
                                addProperty("state", segment.state)
                                addProperty("output", segment.output)
                                addProperty("output_size_bytes", segment.outputSizeBytes)
                                addProperty("replay_format", segment.replayFormat)
                                addProperty("hotbar_snapshot_contract", segment.hotbarSnapshotContract)
                                segment.flashbackCaptureContract?.let { addProperty("flashback_capture_contract", it) }
                            })
                        }
                    })
                }
                atomicWrite(renderReady.resolve("$connectionId.json"), json)
            }
    }

    @Synchronized
    fun snapshotConnections(): List<ConnectionSnapshot> = connections.values.map {
        ConnectionSnapshot(
            playerUuid = it.playerUuid,
            playerName = it.playerName,
            connectionId = it.connectionId,
            joinServerTick = it.joinServerTick,
            joinSequence = it.joinSequence,
            endServerTick = it.endServerTick,
            endSequence = it.endSequence,
            terminalReason = it.terminalReason
        )
    }

    private fun writeConnections() {
        connectionsDirty = true
        val now = nowMillis()
        val json = JsonObject().apply {
            addProperty("schema_version", CONTROL_SCHEMA_VERSION)
            addProperty("session_id", sessionId)
            addProperty("updated_at", Instant.ofEpochMilli(now).toString())
            addProperty("updated_at_unix_ms", now)
            add("connections", JsonArray().also { array -> connections.values.forEach { array.add(it.toJson()) } })
        }
        // Publish history first: a failure updating the current-session pointer must not erase the
        // durable connection ledger that dashboards scan after later server restarts.
        atomicWrite(sessions.resolve("$sessionId.connections.json"), json)
        atomicWrite(root.resolve("connections.json"), json)
        connectionsDirty = false
    }

    private fun storageStatus(
        epochIndex: Long,
        writer: AsyncEpochWriter.WriterMetrics,
        heartbeatAt: Long
    ): JsonObject = JsonObject().apply {
        val partial = sessionDirectory.resolve("epochs/epoch-%06d/events.jsonl.inprogress".format(epochIndex))
        addProperty("heartbeat_at_unix_ms", heartbeatAt)
        addProperty("last_writer_progress_unix_ms", writer.lastWrittenAtUnixMs ?: 0)
        addProperty(
            "active_epoch_inprogress_bytes",
            runCatching { if (Files.isRegularFile(partial)) Files.size(partial) else 0L }.getOrDefault(0L)
        )
        runCatching { Files.getFileStore(sessionDirectory) }.getOrNull()?.let { store ->
            runCatching { store.usableSpace }.getOrNull()?.let { addProperty("usable_space_bytes", it) }
            runCatching { store.totalSpace }.getOrNull()?.let { addProperty("total_space_bytes", it) }
        }
    }

    private fun isUuid(value: String): Boolean = runCatching { UUID.fromString(value).toString() == value }.getOrDefault(false)

    private fun atomicWrite(destination: Path, json: JsonObject) {
        val partial = destination.resolveSibling(
            ".${destination.fileName}.${UUID.randomUUID()}.inprogress"
        )
        val bytes = (GSON.toJson(json) + "\n").toByteArray(Charsets.UTF_8)
        Files.write(partial, bytes, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)
        try {
            try {
                Files.move(partial, destination, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
            } catch (_: AtomicMoveNotSupportedException) {
                Files.move(partial, destination, StandardCopyOption.REPLACE_EXISTING)
            }
        } finally {
            Files.deleteIfExists(partial)
        }
    }

    data class StatusSnapshot(
        val state: String,
        val serverTick: Long,
        val sequence: Long,
        val applySequence: Long,
        val epochIndex: Long,
        val epochStartServerTick: Long,
        val writer: AsyncEpochWriter.WriterMetrics,
        val failureReason: String? = null
    )

    data class ConnectionSnapshot(
        val playerUuid: String,
        val playerName: String,
        val connectionId: String,
        val joinServerTick: Long,
        val joinSequence: Long,
        val endServerTick: Long?,
        val endSequence: Long?,
        val terminalReason: String?
    )

    data class ReplaySegmentSnapshot(
        val segmentId: String,
        val segmentOrdinal: Long,
        val playerUuid: String,
        val playerName: String,
        val connectionId: String?,
        val connectionJoinServerTick: Long?,
        val connectionJoinSequence: Long?,
        val connectionEndServerTick: Long?,
        val connectionEndSequence: Long?,
        val terminalReason: String?,
        val replayFormat: String,
        val hotbarSnapshotContract: String,
        val flashbackCaptureContract: String?,
        val sourceLocation: String,
        val state: String,
        val startedAtUnixMs: Long,
        val savedAtUnixMs: Long?,
        val output: String?,
        val outputSizeBytes: Long?
    ) {
        fun toJson(): JsonObject = JsonObject().apply {
            addProperty("segment_id", segmentId)
            addProperty("segment_ordinal", segmentOrdinal)
            addProperty("player_uuid", playerUuid)
            addProperty("player_name", playerName)
            connectionId?.let { addProperty("connection_id", it) }
            connectionJoinServerTick?.let { addProperty("connection_join_server_tick", it) }
            connectionJoinSequence?.let { addProperty("connection_join_sequence", it) }
            connectionEndServerTick?.let { addProperty("connection_end_server_tick", it) }
            connectionEndSequence?.let { addProperty("connection_end_sequence", it) }
            terminalReason?.let { addProperty("terminal_reason", it) }
            addProperty("replay_format", replayFormat)
            addProperty("hotbar_snapshot_contract", hotbarSnapshotContract)
            flashbackCaptureContract?.let { addProperty("flashback_capture_contract", it) }
            addProperty("source_location", sourceLocation)
            addProperty("state", state)
            addProperty("started_at_unix_ms", startedAtUnixMs)
            savedAtUnixMs?.let { addProperty("saved_at_unix_ms", it) }
            output?.let { addProperty("output", it) }
            outputSizeBytes?.let { addProperty("output_size_bytes", it) }
        }
    }

    private data class ConnectionRecord(
        val playerUuid: String,
        val playerName: String,
        val connectionId: String,
        val joinServerTick: Long,
        val joinSequence: Long,
        var endServerTick: Long? = null,
        var endSequence: Long? = null,
        var terminalReason: String? = null
    ) {
        val active: Boolean get() = endSequence == null

        fun toJson(): JsonObject = JsonObject().apply {
            addProperty("player_uuid", playerUuid)
            addProperty("player_name", playerName)
            addProperty("connection_id", connectionId)
            addProperty("join_server_tick", joinServerTick)
            addProperty("join_sequence", joinSequence)
            addProperty("state", if (active) "recording" else "disconnected")
            endServerTick?.let { addProperty("end_server_tick", it) }
            endSequence?.let { addProperty("end_sequence", it) }
            terminalReason?.let { addProperty("terminal_reason", it) }
        }
    }

    companion object {
        private const val CONTROL_SCHEMA_VERSION = 1
        private val TERMINAL_REASONS = setOf("disconnect", "server_shutdown")
        private val SESSION_ID_PATTERN = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        private val GSON = GsonBuilder().setPrettyPrinting().create()
    }
}
