package dev.mcdata.recorder.io

import com.google.gson.GsonBuilder
import com.google.gson.JsonArray
import com.google.gson.JsonNull
import com.google.gson.JsonObject
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
    private val metadataValue: JsonObject
) {
    private var ended: CaptureEnd? = null
    private var replayClosed = false

    fun connectionId(): String =
        metadataValue.getAsJsonObject("connection").get("id").asString

    @Synchronized
    fun replaySaved(output: Path) {
        require(output.toAbsolutePath().normalize() == paths.replay) {
            "ServerReplay output does not match capture/replay.zip: $output"
        }
        require(Files.isRegularFile(paths.replay) && !Files.isSymbolicLink(paths.replay)) {
            "completed replay must be a non-symlinked regular file: ${paths.replay}"
        }
    }

    @Synchronized
    fun replayWriterClosed() {
        require(Files.isRegularFile(paths.replay) && !Files.isSymbolicLink(paths.replay)) {
            "Flashback replay did not finalize at ${paths.replay}"
        }
        require(!Files.exists(paths.replayWorking)) {
            "Flashback working directory still exists after replay close: ${paths.replayWorking}"
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
        val connection = metadataValue.getAsJsonObject("connection")
        connection.addProperty("capture_failure", reason.take(2_048))
        atomicWrite(paths.metadata, metadataValue)
    }

    private fun finishIfReady() {
        val completed = ended ?: return
        if (!replayClosed) return
        val connection = metadataValue.getAsJsonObject("connection")
        connection.addProperty("status", "complete")
        connection.addProperty("ended_at", completed.endedAt.toString())
        connection.addProperty("end_server_tick", completed.endServerTick)
        connection.addProperty("terminal_reason", completed.terminalReason)
        atomicWrite(paths.metadata, metadataValue)
    }

    companion object {
        private val gson = GsonBuilder().serializeNulls().setPrettyPrinting().create()
        private val pathTime = DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss.SSS'Z'")
            .withZone(ZoneOffset.UTC)

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
                serverName = config.serverName,
                serverInstanceId = config.serverInstanceUuid(),
                playerName = playerName,
                playerUuid = playerUuid,
                startedAt = pathTime.format(startedAt),
                connectionId = connectionId
            )
            val paths = ArtifactLayout.play(config.artifactsPath(), identity)
            Files.createDirectories(paths.root.parent)
            Files.createDirectory(paths.root)
            Files.createDirectory(paths.capture)
            val metadata = JsonObject().apply {
                addProperty("schema_version", 1)
                addProperty("layout_version", ArtifactLayout.VERSION)
                add("server", JsonObject().apply {
                    addProperty("name", config.serverName)
                    addProperty("instance_id", config.serverInstanceId)
                })
                addProperty("session_id", sessionId)
                add("player", JsonObject().apply {
                    addProperty("name", playerName)
                    addProperty("uuid", playerUuid.toString())
                })
                add("connection", JsonObject().apply {
                    addProperty("id", connectionId.toString())
                    addProperty("status", "recording")
                    addProperty("started_at", startedAt.toString())
                    addProperty("start_server_tick", startServerTick)
                    add("ended_at", JsonNull.INSTANCE)
                    add("end_server_tick", JsonNull.INSTANCE)
                })
                addProperty(
                    "flashback_capture_contract",
                    ReplayScenePacketContract.FLASHBACK_CAPTURE_CONTRACT
                )
                add("known_gaps", JsonArray().apply {
                    add("audio_not_extracted")
                    add("particles_not_extracted")
                    add("lighting_not_persisted_in_scene_v2")
                    add("unopened_container_contents_may_be_unknown")
                })
                add("capture", JsonObject().apply {
                    addProperty("events", "capture/events.jsonl")
                    addProperty("replay", "capture/replay.zip")
                    addProperty("replay_format", "flashback")
                })
            }
            atomicWrite(paths.metadata, metadata)
            return PlayFiles(paths, metadata)
        }

        private fun atomicWrite(destination: Path, json: JsonObject) {
            val partial = destination.resolveSibling(destination.fileName.toString() + ".inprogress")
            Files.writeString(partial, gson.toJson(json) + "\n")
            moveComplete(partial, destination, replace = true)
        }

        private fun moveComplete(source: Path, destination: Path, replace: Boolean = false) {
            val options = mutableListOf(StandardCopyOption.ATOMIC_MOVE)
            if (replace) options.add(StandardCopyOption.REPLACE_EXISTING)
            try {
                Files.move(source, destination, *options.toTypedArray())
            } catch (_: AtomicMoveNotSupportedException) {
                val fallback = if (replace) {
                    arrayOf(StandardCopyOption.REPLACE_EXISTING)
                } else {
                    emptyArray()
                }
                Files.move(source, destination, *fallback)
            }
        }
    }

    private data class CaptureEnd(
        val endedAt: Instant,
        val endServerTick: Long,
        val terminalReason: String
    )
}
