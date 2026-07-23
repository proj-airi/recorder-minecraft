package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import net.casual.arcade.replay.recorder.ReplayRecorder
import net.casual.arcade.replay.recorder.player.ReplayPlayerRecorder
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
    private val sessionId: String
) {
    private val segmentsByRecorder = IdentityHashMap<Any, MutableSegment>()
    private val segmentsById = linkedMapOf<String, MutableSegment>()
    private val activeConnections = mutableMapOf<UUID, String>()
    private val nextOrdinalByPlayer = mutableMapOf<UUID, Long>()

    fun recorderStarted(recorder: ReplayRecorder) {
        if (recorder !is ReplayPlayerRecorder) return
        val metadataProvider = segmentStarted(
            recorderIdentity = recorder,
            playerUuid = recorder.recordingPlayerUUID,
            replayFormat = recorder.format.name.lowercase()
        )
        recorder.addMetadataProvider(metadataProvider)
    }

    @Synchronized
    fun connectionStarted(
        playerUuid: UUID,
        connectionId: String
    ) {
        activeConnections[playerUuid] = connectionId
        segmentsById.values
            .filter { it.playerUuid == playerUuid && it.connectionId == null }
            .forEach { it.connectionId = connectionId }
    }

    @Synchronized
    fun connectionEnded(connectionId: String) {
        val active = activeConnections.entries.firstOrNull { it.value == connectionId }
        if (active != null) activeConnections.remove(active.key)
    }

    @Synchronized
    internal fun segmentStarted(
        recorderIdentity: Any,
        playerUuid: UUID,
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
            replayFormat = replayFormat,
            connectionId = activeConnections[playerUuid]
        )
        segmentsByRecorder[recorderIdentity] = segment
        segmentsById[segment.segmentId] = segment
        return { metadata -> addArchiveMetadata(segment.segmentId, metadata) }
    }

    @Synchronized
    private fun addArchiveMetadata(segmentId: String, metadata: JsonObject) {
        val segment = checkNotNull(segmentsById[segmentId]) { "unknown replay segment: $segmentId" }
        metadata.add("mc_recorder", JsonObject().apply {
            addProperty("schema_version", REPLAY_METADATA_SCHEMA_VERSION)
            addProperty("session_id", sessionId)
            addProperty("segment_id", segment.segmentId)
            addProperty("segment_ordinal", segment.segmentOrdinal)
            addProperty("player_uuid", segment.playerUuid.toString())
            addProperty("hotbar_snapshot_contract", ReplayPacketSnapshots.HOTBAR_SNAPSHOT_CONTRACT)
            if (segment.replayFormat == FLASHBACK_FORMAT) {
                addProperty(
                    "flashback_capture_contract",
                    ReplayScenePacketContract.FLASHBACK_CAPTURE_CONTRACT
                )
            }
            segment.connectionId?.let { addProperty("connection_id", it) }
        })
    }

    private data class MutableSegment(
        val segmentId: String,
        val segmentOrdinal: Long,
        val playerUuid: UUID,
        val replayFormat: String,
        var connectionId: String?
    )

    companion object {
        private const val REPLAY_METADATA_SCHEMA_VERSION = 3
        private const val FLASHBACK_FORMAT = "flashback"
    }
}
