package dev.mcdata.recorder.io

import com.google.gson.GsonBuilder
import com.google.gson.JsonArray
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
    @Synchronized
    fun addReplay(
        source: Path,
        segmentId: String,
        segmentOrdinal: Long,
        replayFormat: String
    ): Path {
        require(replayFormat == "flashback") { "artifacts/v1 accepts Flashback replay archives only" }
        require(segmentOrdinal >= 0) { "replay segment ordinal must be non-negative" }
        UUID.fromString(segmentId)
        require(Files.isRegularFile(source) && !Files.isSymbolicLink(source)) {
            "completed replay must be a non-symlinked regular file: $source"
        }
        val filename = "%06d--%s.zip".format(segmentOrdinal, segmentId)
        val destination = paths.replays.resolve(filename)
        require(!Files.exists(destination)) { "replay destination already exists: $destination" }
        val partial = destination.resolveSibling("$filename.inprogress")
        try {
            Files.copy(source, partial)
            moveComplete(partial, destination)
        } catch (failure: Throwable) {
            Files.deleteIfExists(partial)
            throw failure
        }
        metadataValue.getAsJsonArray("replays").add(JsonObject().apply {
            addProperty("segment_id", segmentId)
            addProperty("segment_ordinal", segmentOrdinal)
            addProperty("format", replayFormat)
            addProperty("path", "replays/$filename")
        })
        atomicWrite(paths.metadata, metadataValue)
        return destination
    }

    @Synchronized
    fun close(endedAt: Instant, endServerTick: Long, terminalReason: String) {
        finish("closed", endedAt, endServerTick, terminalReason)
    }

    @Synchronized
    fun fail(endedAt: Instant, endServerTick: Long, terminalReason: String) {
        finish("failed", endedAt, endServerTick, terminalReason)
    }

    private fun finish(status: String, endedAt: Instant, endServerTick: Long, terminalReason: String) {
        val connection = metadataValue.getAsJsonObject("connection")
        connection.addProperty("status", status)
        connection.addProperty("ended_at", endedAt.toString())
        connection.addProperty("end_server_tick", endServerTick)
        connection.addProperty("terminal_reason", terminalReason)
        atomicWrite(paths.metadata, metadataValue)
    }

    companion object {
        private val gson = GsonBuilder().setPrettyPrinting().create()
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
            Files.createDirectory(paths.replays)
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
                add("replays", JsonArray())
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
}
