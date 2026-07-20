package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import dev.mcdata.recorder.control.RecorderControlPlane
import net.casual.arcade.replay.recorder.ReplayRecorder
import net.casual.arcade.replay.recorder.player.ReplayPlayerRecorder
import org.slf4j.Logger
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.util.IdentityHashMap
import java.util.UUID

/**
 * Binds ServerReplay recorder instances to exact capture connections.
 *
 * ServerReplay creates its player recorder during login, before Fabric publishes the player-join
 * callback. A mutable segment binding is therefore allocated at recorder start and completed when
 * the capture connection begins. The recorder's metadata provider reads that binding only when
 * ServerReplay materializes the archive, while post-save lookup stays keyed by recorder identity.
 */
class ReplaySegmentTracker internal constructor(
    private val sessionId: String,
    private val publish: (List<RecorderControlPlane.ReplaySegmentSnapshot>) -> Unit,
    private val logger: Logger,
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    constructor(
        sessionId: String,
        controlPlane: RecorderControlPlane,
        logger: Logger
    ) : this(sessionId, controlPlane::publishReplaySegments, logger)

    private val segmentsByRecorder = IdentityHashMap<Any, MutableSegment>()
    private val segmentsById = linkedMapOf<String, MutableSegment>()
    private val activeConnections = mutableMapOf<UUID, ConnectionBinding>()
    private val nextOrdinalByPlayer = mutableMapOf<UUID, Long>()

    init {
        publishSafely()
    }

    fun recorderStarted(recorder: ReplayRecorder) {
        if (recorder !is ReplayPlayerRecorder) return
        val metadataProvider = segmentStarted(
            recorderIdentity = recorder,
            playerUuid = recorder.recordingPlayerUUID,
            playerName = recorder.profile.name,
            sourceLocation = recorder.location,
            replayFormat = recorder.format.name.lowercase()
        )
        recorder.addMetadataProvider(metadataProvider)
    }

    fun recorderSaved(recorder: ReplayRecorder, output: Path) {
        if (recorder !is ReplayPlayerRecorder) return
        segmentSaved(recorder, output)
    }

    @Synchronized
    fun connectionStarted(
        playerUuid: UUID,
        connectionId: String,
        joinServerTick: Long,
        joinSequence: Long
    ) {
        val binding = ConnectionBinding(connectionId, joinServerTick, joinSequence)
        activeConnections[playerUuid] = binding
        segmentsById.values
            .filter { it.playerUuid == playerUuid && it.connection == null && it.state == SEGMENT_RECORDING }
            .forEach { it.connection = binding }
        publishSafely()
    }

    @Synchronized
    fun connectionEnded(
        connectionId: String,
        endServerTick: Long,
        endSequence: Long,
        terminalReason: String
    ) {
        val active = activeConnections.entries.firstOrNull { it.value.connectionId == connectionId }
        if (active != null) activeConnections.remove(active.key)
        segmentsById.values
            .filter { it.connection?.connectionId == connectionId }
            .forEach {
                it.connectionEndServerTick = endServerTick
                it.connectionEndSequence = endSequence
                it.terminalReason = terminalReason
            }
        publishSafely()
    }

    @Synchronized
    internal fun segmentStarted(
        recorderIdentity: Any,
        playerUuid: UUID,
        playerName: String,
        sourceLocation: Path,
        replayFormat: String
    ): (JsonObject) -> Unit {
        check(!segmentsByRecorder.containsKey(recorderIdentity)) {
            "recorder instance was already registered"
        }
        val ordinal = nextOrdinalByPlayer.getOrDefault(playerUuid, 0L)
        nextOrdinalByPlayer[playerUuid] = ordinal + 1
        val segment = MutableSegment(
            segmentId = UUID.randomUUID().toString(),
            segmentOrdinal = ordinal,
            playerUuid = playerUuid,
            playerName = playerName,
            sourceLocation = sourceLocation.toAbsolutePath().normalize().toString(),
            replayFormat = replayFormat,
            startedAtUnixMs = nowMillis(),
            connection = activeConnections[playerUuid]
        )
        segmentsByRecorder[recorderIdentity] = segment
        segmentsById[segment.segmentId] = segment
        publishSafely()
        return { metadata -> addArchiveMetadata(segment.segmentId, metadata) }
    }

    @Synchronized
    internal fun segmentSaved(recorderIdentity: Any, output: Path): Boolean {
        val segment = segmentsByRecorder[recorderIdentity] ?: return false
        if (segment.state == SEGMENT_SAVED) return true
        val normalized = output.toAbsolutePath().normalize()
        segment.state = SEGMENT_SAVED
        segment.output = normalized.toString()
        segment.outputSizeBytes = runCatching {
            if (Files.isRegularFile(normalized, LinkOption.NOFOLLOW_LINKS)) Files.size(normalized) else null
        }.getOrNull()
        segment.savedAtUnixMs = nowMillis()
        publishSafely()
        return true
    }

    @Synchronized
    internal fun snapshots(): List<RecorderControlPlane.ReplaySegmentSnapshot> =
        segmentsById.values.map(MutableSegment::snapshot)

    @Synchronized
    private fun addArchiveMetadata(segmentId: String, metadata: JsonObject) {
        val segment = checkNotNull(segmentsById[segmentId]) { "unknown replay segment: $segmentId" }
        metadata.add("mc_recorder", JsonObject().apply {
            addProperty("schema_version", REPLAY_METADATA_SCHEMA_VERSION)
            addProperty("session_id", sessionId)
            addProperty("segment_id", segment.segmentId)
            addProperty("segment_ordinal", segment.segmentOrdinal)
            addProperty("player_uuid", segment.playerUuid.toString())
            segment.connection?.let { addProperty("connection_id", it.connectionId) }
        })
    }

    private fun publishSafely() {
        runCatching { publish(snapshots()) }.onFailure {
            logger.error("Could not publish replay segment ledger; structured capture remains active", it)
        }
    }

    private data class MutableSegment(
        val segmentId: String,
        val segmentOrdinal: Long,
        val playerUuid: UUID,
        val playerName: String,
        val sourceLocation: String,
        val replayFormat: String,
        val startedAtUnixMs: Long,
        var connection: ConnectionBinding?,
        var state: String = SEGMENT_RECORDING,
        var output: String? = null,
        var outputSizeBytes: Long? = null,
        var savedAtUnixMs: Long? = null,
        var connectionEndServerTick: Long? = null,
        var connectionEndSequence: Long? = null,
        var terminalReason: String? = null
    ) {
        fun snapshot() = RecorderControlPlane.ReplaySegmentSnapshot(
            segmentId = segmentId,
            segmentOrdinal = segmentOrdinal,
            playerUuid = playerUuid.toString(),
            playerName = playerName,
            connectionId = connection?.connectionId,
            connectionJoinServerTick = connection?.joinServerTick,
            connectionJoinSequence = connection?.joinSequence,
            connectionEndServerTick = connectionEndServerTick,
            connectionEndSequence = connectionEndSequence,
            terminalReason = terminalReason,
            replayFormat = replayFormat,
            sourceLocation = sourceLocation,
            state = state,
            startedAtUnixMs = startedAtUnixMs,
            savedAtUnixMs = savedAtUnixMs,
            output = output,
            outputSizeBytes = outputSizeBytes
        )
    }

    private data class ConnectionBinding(
        val connectionId: String,
        val joinServerTick: Long,
        val joinSequence: Long
    )

    companion object {
        private const val REPLAY_METADATA_SCHEMA_VERSION = 1
        private const val SEGMENT_RECORDING = "recording"
        private const val SEGMENT_SAVED = "saved"
    }
}
