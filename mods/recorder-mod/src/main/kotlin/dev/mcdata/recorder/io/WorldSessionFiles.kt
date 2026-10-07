package dev.mcdata.recorder.io

import dev.mcdata.recorder.config.RecorderConfig
import dev.recorderminecraft.artifacts.v1.ServerIdentity
import dev.recorderminecraft.artifacts.v1.WorldSessionMetadata
import dev.recorderminecraft.artifacts.v1.WorldTruthReference
import java.nio.file.Files
import java.time.Instant
import java.util.UUID

/**
 * Owns `world/sessions/<start>--<session>/metadata.json` for one recorder session.
 *
 * Like Play metadata, the end tick is written last and its absence means the stream did not close.
 * A contained stream failure also writes an end tick, together with `stream_failure`, so readers
 * know exactly where world coverage stops while Plays continue.
 */
class WorldSessionFiles private constructor(
    val paths: WorldSessionPaths,
    private val metadata: WorldSessionMetadata.Builder
) {
    private var ended = false

    val reference: WorldTruthReference = WorldTruthReference.newBuilder()
        .setMetadata(paths.relativeMetadata)
        .setEvents(paths.relativeEvents)
        .build()

    @Synchronized
    fun closed(endedAt: Instant, endServerTick: Long, terminalReason: String) {
        end(endedAt, endServerTick, terminalReason, failure = null)
    }

    @Synchronized
    fun failed(endedAt: Instant, endServerTick: Long, failure: String) {
        end(endedAt, endServerTick, "stream_failure", failure)
    }

    private fun end(endedAt: Instant, endServerTick: Long, terminalReason: String, failure: String?) {
        if (ended) return
        ended = true
        metadata
            .setEndedAt(AtomicFiles.timestamp(endedAt))
            .setEndServerTick(endServerTick)
            .setTerminalReason(terminalReason)
        failure?.let { metadata.streamFailure = it.take(2_048) }
        AtomicFiles.writeProtoJson(paths.metadata, metadata.build())
    }

    companion object {
        const val SCOPE = "world"
        const val PROVENANCE = "engine-reported"

        fun create(config: RecorderConfig, sessionId: String, startedAt: Instant, startServerTick: Long): WorldSessionFiles {
            val identity = WorldSessionIdentity(
                config.serverName, config.serverInstanceUuid(), PlayFiles.pathTime.format(startedAt),
                UUID.fromString(sessionId)
            )
            val paths = ArtifactLayout.world(config.artifactsPath(), identity)
            Files.createDirectories(paths.root.parent)
            Files.createDirectory(paths.root)
            val metadata = WorldSessionMetadata.newBuilder()
                .setSchemaVersion(1)
                .setLayoutVersion(ArtifactLayout.VERSION)
                .setServer(ServerIdentity.newBuilder().setName(config.serverName).setInstanceId(config.serverInstanceId))
                .setSessionId(sessionId)
                .setScope(SCOPE)
                .setProvenance(PROVENANCE)
                .setStartedAt(AtomicFiles.timestamp(startedAt))
                .setStartServerTick(startServerTick)
                .addKnownGaps("world_entities_not_recorded")
                .setEvents(WorldSessionPaths.EVENTS)
            AtomicFiles.writeProtoJson(paths.metadata, metadata.build())
            return WorldSessionFiles(paths, metadata)
        }
    }
}
