package dev.mcdata.recorder.control

import com.google.gson.GsonBuilder
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
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
 * Runtime-only dashboard bridge. None of these files are accepted as capture source truth: source
 * records and verified sealed epoch manifests remain authoritative.
 */
class RecorderControlPlane(
    private val sessionId: String,
    private val sessionDirectory: Path,
    controlRoot: Path,
    private val logger: Logger,
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    private val root = controlRoot.toAbsolutePath().normalize()
    private val requests = root.resolve("requests")
    private val responses = root.resolve("responses")
    private val sessions = root.resolve("sessions")
    private val connections = linkedMapOf<String, ConnectionRecord>()
    private var connectionsDirty = true

    init {
        require(SESSION_ID_PATTERN.matches(sessionId)) { "session_id is not safe for the control spool" }
        Files.createDirectories(root)
        check(!Files.isSymbolicLink(root)) { "control_root must not be a symbolic link: $root" }
        Files.createDirectories(requests)
        Files.createDirectories(responses)
        Files.createDirectories(sessions)
        check(
            !Files.isSymbolicLink(requests) &&
                !Files.isSymbolicLink(responses) &&
                !Files.isSymbolicLink(sessions)
        ) {
            "control request, response, and session directories must not be symbolic links"
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
    fun pollSealRequests(): List<SealRequest> {
        if (!Files.isDirectory(requests, LinkOption.NOFOLLOW_LINKS)) return emptyList()
        val accepted = mutableListOf<SealRequest>()
        Files.newDirectoryStream(requests, "*.json").use { stream ->
            stream.asSequence()
                .map { path ->
                    path to path.fileName.toString().removeSuffix(".json")
                }
                .filter { (_, fileRequestId) ->
                    if (!isUuid(fileRequestId)) {
                        false
                    } else if (responseExists(fileRequestId)) {
                        retireRequest(fileRequestId)
                        false
                    } else {
                        true
                    }
                }
                .sortedBy { (path, _) -> path.fileName.toString() }
                .take(MAX_REQUESTS_PER_POLL)
                .forEach { (path, fileRequestId) ->
                    val parsed = runCatching { parseRequest(path, fileRequestId) }
                    if (parsed.isFailure) {
                        writeFailure(
                            fileRequestId,
                            "invalid_request",
                            parsed.exceptionOrNull()?.message ?: "request is invalid"
                        )
                        return@forEach
                    }
                    val request = parsed.getOrThrow()
                    val validationFailure = validate(request)
                    if (validationFailure != null) {
                        writeFailure(request.requestId, validationFailure.first, validationFailure.second)
                    } else {
                        accepted += request
                    }
                }
        }
        return accepted
    }

    @Synchronized
    fun completeSealRequest(
        request: SealRequest,
        sealed: AsyncEpochWriter.SealedEpoch,
        reused: Boolean,
        coalesced: Boolean
    ) {
        if (responseExists(request.requestId)) {
            retireRequest(request.requestId)
            return
        }
        check(sealed.lastSequence >= request.connectionEndSequence) {
            "epoch ${sealed.epochIndex} does not cover connection end sequence ${request.connectionEndSequence}"
        }
        val manifestRelative = sessionDirectory.relativize(sealed.manifestPath).toString()
        val response = responseEnvelope(request.requestId, "complete").apply {
            addProperty("operation", SEAL_OPERATION)
            addProperty("session_id", sessionId)
            addProperty("player_uuid", request.playerUuid)
            addProperty("connection_id", request.connectionId)
            addProperty("connection_end_sequence", request.connectionEndSequence)
            addProperty("sealed_epoch_index", sealed.epochIndex)
            addProperty("sealed_through_sequence", sealed.lastSequence)
            addProperty("sealed_manifest", manifestRelative)
            addProperty("events_sha256", sealed.eventsSha256)
            addProperty("rotation_reason", sealed.rotationReason)
            addProperty("forced_seal", sealed.forced)
            addProperty("reused_existing_seal", reused)
            addProperty("coalesced", coalesced)
        }
        atomicWrite(responsePath(request.requestId), response)
        retireRequest(request.requestId)
    }

    @Synchronized
    fun failSealRequests(requests: Collection<SealRequest>, code: String, message: String) {
        requests.forEach { writeFailure(it.requestId, code, message) }
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

    private fun parseRequest(path: Path, fileRequestId: String): SealRequest {
        check(Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) { "request must be a regular file" }
        val size = Files.size(path)
        check(size in 1..MAX_REQUEST_BYTES) { "request size must be between 1 and $MAX_REQUEST_BYTES bytes" }
        val json = Files.newBufferedReader(path).use { reader -> JsonParser.parseReader(reader).asJsonObject }
        check(requiredInt(json, "schema_version") == CONTROL_SCHEMA_VERSION) {
            "unsupported schema_version"
        }
        check(requiredString(json, "operation") == SEAL_OPERATION) { "unsupported operation" }
        val requestId = requiredString(json, "request_id")
        check(requestId == fileRequestId && isUuid(requestId)) {
            "request_id must be a canonical UUID matching the filename"
        }
        return SealRequest(
            requestId = requestId,
            expectedSessionId = requiredString(json, "expected_session_id"),
            playerUuid = requiredString(json, "player_uuid").also { check(isUuid(it)) { "player_uuid must be a UUID" } },
            connectionId = requiredString(json, "connection_id").also { check(isUuid(it)) { "connection_id must be a UUID" } },
            connectionEndSequence = requiredLong(json, "connection_end_sequence").also {
                check(it >= 0) { "connection_end_sequence must not be negative" }
            }
        )
    }

    private fun validate(request: SealRequest): Pair<String, String>? {
        if (request.expectedSessionId != sessionId) {
            return "stale_session" to "expected session does not match the active capture session"
        }
        val connection = connections[request.connectionId]
            ?: return "unknown_connection" to "connection_id does not exist in the active session ledger"
        if (connection.playerUuid != request.playerUuid) {
            return "connection_player_mismatch" to "connection_id does not belong to player_uuid"
        }
        if (connection.active) {
            return "connection_active" to "a recording can be sealed only after the player disconnects"
        }
        if (connection.endSequence != request.connectionEndSequence) {
            return "connection_end_mismatch" to "connection_end_sequence does not match the recorder ledger"
        }
        return null
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

    private fun writeFailure(requestId: String, code: String, message: String) {
        if (responseExists(requestId)) {
            retireRequest(requestId)
            return
        }
        val response = responseEnvelope(requestId, "failed").apply {
            addProperty("operation", SEAL_OPERATION)
            addProperty("session_id", sessionId)
            add("error", JsonObject().apply {
                addProperty("code", code)
                addProperty("message", message.take(2_048))
            })
        }
        atomicWrite(responsePath(requestId), response)
        retireRequest(requestId)
        logger.warn("Rejected recorder control request {}: {}", requestId, code)
    }

    private fun retireRequest(requestId: String) {
        runCatching { Files.deleteIfExists(requests.resolve("$requestId.json")) }.onFailure {
            logger.warn("Could not retire completed recorder control request {}", requestId, it)
        }
    }

    private fun responseEnvelope(requestId: String, status: String): JsonObject {
        val now = nowMillis()
        return JsonObject().apply {
            addProperty("schema_version", CONTROL_SCHEMA_VERSION)
            addProperty("request_id", requestId)
            addProperty("status", status)
            addProperty("completed_at", Instant.ofEpochMilli(now).toString())
            addProperty("completed_at_unix_ms", now)
        }
    }

    private fun responseExists(requestId: String): Boolean =
        Files.isRegularFile(responsePath(requestId), LinkOption.NOFOLLOW_LINKS)

    private fun responsePath(requestId: String): Path = responses.resolve("$requestId.json")

    private fun requiredString(json: JsonObject, name: String): String {
        val value = json.get(name)
        check(value != null && value.isJsonPrimitive && value.asJsonPrimitive.isString) { "$name must be a string" }
        return value.asString.also { check(it.isNotBlank()) { "$name must not be blank" } }
    }

    private fun requiredLong(json: JsonObject, name: String): Long {
        val value = json.get(name)
        check(value != null && value.isJsonPrimitive && value.asJsonPrimitive.isNumber) { "$name must be an integer" }
        return runCatching { value.asBigDecimal.longValueExact() }
            .getOrElse { throw IllegalStateException("$name must be an integer") }
    }

    private fun requiredInt(json: JsonObject, name: String): Int = requiredLong(json, name).also {
        check(it in Int.MIN_VALUE..Int.MAX_VALUE) { "$name is out of range" }
    }.toInt()

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

    data class SealRequest(
        val requestId: String,
        val expectedSessionId: String,
        val playerUuid: String,
        val connectionId: String,
        val connectionEndSequence: Long
    )

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
        private const val SEAL_OPERATION = "seal_connection"
        private const val MAX_REQUEST_BYTES = 64L * 1024
        private const val MAX_REQUESTS_PER_POLL = 128
        private val TERMINAL_REASONS = setOf("disconnect", "server_shutdown")
        private val SESSION_ID_PATTERN = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        private val GSON = GsonBuilder().setPrettyPrinting().create()
    }
}
