package dev.mcdata.recorder.io

import com.google.protobuf.Timestamp
import com.google.protobuf.util.JsonFormat
import dev.recorderminecraft.artifacts.v1.CaptureMetadata
import dev.recorderminecraft.artifacts.v1.Connection
import dev.recorderminecraft.artifacts.v1.PlayerIdentity
import dev.recorderminecraft.artifacts.v1.ServerIdentity
import dev.recorderminecraft.artifacts.v1.ServerMetadata
import dev.mcdata.recorder.capture.ReplayScenePacketContract
import dev.mcdata.recorder.config.RecorderConfig
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.UUID

class PlayFiles private constructor(
    val paths: PlayPaths,
    private val metadata: ServerMetadata.Builder
) {
    private var ended: CaptureEnd? = null
    private var savedReplay: Path? = null
    private var replayClosed = false

    fun connectionId(): String = metadata.connection.id

    @Synchronized
    fun replaySaved(output: Path) {
        require(savedReplay == null) { "ServerReplay replay was already saved" }
        val source = output.toAbsolutePath().normalize()
        require(source.parent == paths.replayWorking && source.fileName.toString().endsWith(".zip")) {
            "ServerReplay output must be one ZIP inside ${paths.replayWorking}: $source"
        }
        require(Files.isRegularFile(source) && !Files.isSymbolicLink(source)) {
            "completed replay must be a non-symlinked regular file: $source"
        }
        savedReplay = source
    }

    @Synchronized
    fun replayWriterClosed() {
        val source = checkNotNull(savedReplay) { "Flashback writer closed before saving its replay" }
        val working = source.resolveSibling(source.fileName.toString().removeSuffix(".zip"))
        require(!Files.exists(working)) { "Flashback working directory still exists after replay close: $working" }
        require(Files.isRegularFile(source) && !Files.isSymbolicLink(source)) {
            "completed replay changed before writer close: $source"
        }
        val remaining = Files.list(paths.replayWorking).use { entries ->
            entries.map { it.toAbsolutePath().normalize() }.toList()
        }
        require(remaining == listOf(source)) { "unexpected files remain in ServerReplay working directory: ${paths.replayWorking}" }
        moveComplete(source, paths.replay)
        Files.delete(paths.replayWorking)
        require(Files.isRegularFile(paths.replay) && !Files.isSymbolicLink(paths.replay)) {
            "Flashback replay did not finalize at ${paths.replay}"
        }
        replayClosed = true
        finishIfReady()
    }

    @Synchronized
    fun eventsClosed(endedAt: Instant, endServerTick: Long, terminalReason: String) {
        require(ended == null) { "capture events were already closed" }
        require(Files.isRegularFile(paths.events) && !Files.isSymbolicLink(paths.events)) {
            "capture events did not finalize at ${paths.events}"
        }
        ended = CaptureEnd(endedAt, endServerTick, terminalReason)
        finishIfReady()
    }

    @Synchronized
    fun fail(reason: String) {
        metadata.connection = metadata.connection.toBuilder().setCaptureFailure(reason.take(2_048)).build()
        atomicWrite(paths.metadata, metadata.build())
    }

    private fun finishIfReady() {
        val completed = ended ?: return
        if (!replayClosed) return
        metadata.connection = metadata.connection.toBuilder()
            .setEndedAt(timestamp(completed.endedAt))
            .setEndServerTick(completed.endServerTick)
            .setTerminalReason(completed.terminalReason)
            .build()
        atomicWrite(paths.metadata, metadata.build())
    }

    companion object {
        private val pathTime = DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss.SSS'Z'").withZone(ZoneOffset.UTC)

        fun create(
            config: RecorderConfig,
            sessionId: String,
            playerName: String,
            playerUuid: UUID,
            connectionId: UUID,
            startedAt: Instant,
            startServerTick: Long
        ): PlayFiles {
            val identity = PlayIdentity(
                config.serverName, config.serverInstanceUuid(), playerName, playerUuid,
                pathTime.format(startedAt), connectionId
            )
            val paths = ArtifactLayout.play(config.artifactsPath(), identity)
            Files.createDirectories(paths.root.parent)
            Files.createDirectory(paths.root)
            Files.createDirectory(paths.capture)
            val metadata = ServerMetadata.newBuilder()
                .setSchemaVersion(1)
                .setLayoutVersion(ArtifactLayout.VERSION)
                .setServer(ServerIdentity.newBuilder().setName(config.serverName).setInstanceId(config.serverInstanceId))
                .setSessionId(sessionId)
                .setPlayer(PlayerIdentity.newBuilder().setName(playerName).setUuid(playerUuid.toString()))
                .setConnection(
                    Connection.newBuilder()
                        .setId(connectionId.toString())
                        .setStartedAt(timestamp(startedAt))
                        .setStartServerTick(startServerTick)
                )
                .setFlashbackCaptureContract(ReplayScenePacketContract.FLASHBACK_CAPTURE_CONTRACT)
                .addAllKnownGaps(listOf(
                    "audio_not_extracted", "particles_not_extracted", "lighting_not_persisted_in_scene_v2",
                    "unopened_container_contents_may_be_unknown"
                ))
                .setCapture(
                    CaptureMetadata.newBuilder()
                        .setEvents("capture/events.jsonl")
                        .setReplay("capture/replay.zip")
                        .setReplayFormat("flashback")
                )
            atomicWrite(paths.metadata, metadata.build())
            return PlayFiles(paths, metadata)
        }

        private fun atomicWrite(destination: Path, value: ServerMetadata) {
            val partial = destination.resolveSibling(destination.fileName.toString() + ".inprogress")
            Files.writeString(partial, JsonFormat.printer().print(value) + "\n")
            moveComplete(partial, destination, replace = true)
        }

        private fun timestamp(value: Instant): Timestamp = Timestamp.newBuilder()
            .setSeconds(value.epochSecond)
            .setNanos(value.nano)
            .build()

        private fun moveComplete(source: Path, destination: Path, replace: Boolean = false) {
            val options = mutableListOf(StandardCopyOption.ATOMIC_MOVE)
            if (replace) options.add(StandardCopyOption.REPLACE_EXISTING)
            try {
                Files.move(source, destination, *options.toTypedArray())
            } catch (_: AtomicMoveNotSupportedException) {
                val fallback = if (replace) arrayOf(StandardCopyOption.REPLACE_EXISTING) else emptyArray()
                Files.move(source, destination, *fallback)
            }
        }
    }

    private data class CaptureEnd(val endedAt: Instant, val endServerTick: Long, val terminalReason: String)
}
