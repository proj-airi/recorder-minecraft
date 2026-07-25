package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import dev.mcdata.recorder.io.PlayFiles
import net.casual.arcade.replay.recorder.ReplayRecorder
import net.casual.arcade.replay.recorder.player.ReplayPlayerRecorder
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
    private val sessionId: String
) {
    private val segmentsByRecorder = IdentityHashMap<Any, MutableSegment>()
    private val segmentsById = linkedMapOf<String, MutableSegment>()
    private val activeConnections = mutableMapOf<UUID, ActiveConnection>()
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
        connectionId: String,
        playFiles: PlayFiles
    ) {
        activeConnections[playerUuid] = ActiveConnection(connectionId, playFiles)
        segmentsById.values
            .filter { it.playerUuid == playerUuid && it.connectionId == null }
            .forEach {
                it.connectionId = connectionId
                it.playFiles = playFiles
            }
    }

    @Synchronized
    fun connectionEnded(connectionId: String) {
        val active = activeConnections.entries.firstOrNull { it.value.connectionId == connectionId }
        if (active != null) activeConnections.remove(active.key)
    }

    fun recorderSaved(recorder: ReplayRecorder, output: Path) {
        segmentSaved(recorder, output)
    }

    @Synchronized
    internal fun segmentSaved(recorderIdentity: Any, output: Path): Path {
        val segment = checkNotNull(segmentsByRecorder.remove(recorderIdentity)) {
            "completed recorder was not registered"
        }
        segmentsById.remove(segment.segmentId)
        val playFiles = checkNotNull(segment.playFiles) {
            "completed replay segment has no play connection"
        }
        return playFiles.addReplay(
            source = output,
            segmentId = segment.segmentId,
            segmentOrdinal = segment.segmentOrdinal,
            replayFormat = segment.replayFormat
        )
    }

    @Synchronized
    internal fun segmentStarted(
        recorderIdentity: Any,
        playerUuid: UUID,
        replayFormat: String
    ): (JsonObject) -> Unit {
        require(replayFormat == FLASHBACK_FORMAT) {
            "artifacts/v1 requires Flashback player replay segments"
        }
        check(!segmentsByRecorder.containsKey(recorderIdentity)) {
            "recorder instance was already registered"
        }
        val ordinal = nextOrdinalByPlayer.getOrDefault(playerUuid, 0L)
        nextOrdinalByPlayer[playerUuid] = ordinal + 1
        val active = activeConnections[playerUuid]
        val segment = MutableSegment(
            segmentId = UUID.randomUUID().toString(),
            segmentOrdinal = ordinal,
            playerUuid = playerUuid,
            replayFormat = replayFormat,
            connectionId = active?.connectionId,
            playFiles = active?.playFiles
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
            addProperty(
                "flashback_capture_contract",
                ReplayScenePacketContract.FLASHBACK_CAPTURE_CONTRACT
            )
            segment.connectionId?.let { addProperty("connection_id", it) }
        })
    }

    private data class MutableSegment(
        val segmentId: String,
        val segmentOrdinal: Long,
        val playerUuid: UUID,
        val replayFormat: String,
        var connectionId: String?,
        var playFiles: PlayFiles?
    )

    private data class ActiveConnection(
        val connectionId: String,
        val playFiles: PlayFiles
    )

    companion object {
        private const val REPLAY_METADATA_SCHEMA_VERSION = 3
        private const val FLASHBACK_FORMAT = "flashback"
    }
}
