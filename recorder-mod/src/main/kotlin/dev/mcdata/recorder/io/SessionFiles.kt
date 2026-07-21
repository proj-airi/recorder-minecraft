package dev.mcdata.recorder.io

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import dev.mcdata.recorder.capture.ReplayScenePacketContract
import dev.mcdata.recorder.config.RecorderConfig
import net.fabricmc.loader.api.FabricLoader
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.UUID

data class SessionFiles(val sessionId: String, val directory: Path) {
    companion object {
        private val idTime = DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss.SSS'Z'")
            .withZone(ZoneOffset.UTC)

        fun create(config: RecorderConfig): SessionFiles {
            val root = config.capturePath()
            Files.createDirectories(root)
            val sessionId = "${idTime.format(Instant.now())}-${UUID.randomUUID().toString().take(8)}"
            val directory = root.resolve(sessionId)
            Files.createDirectory(directory)
            Files.createDirectory(directory.resolve("epochs"))

            val loader = FabricLoader.getInstance()
            val loadedMods = JsonObject().also { mods ->
                loader.allMods.sortedBy { it.metadata.id }.forEach { container ->
                    mods.addProperty(container.metadata.id, container.metadata.version.friendlyString)
                }
            }

            val manifest = JsonObject().apply {
                addProperty("schema_version", 1)
                addProperty("source_format", "mc-recorder-jsonl-v1")
                addProperty("session_id", sessionId)
                addProperty(
                    "flashback_capture_contract",
                    ReplayScenePacketContract.FLASHBACK_CAPTURE_CONTRACT
                )
                addProperty("created_at", Instant.now().toString())
                addProperty("minecraft_version", "1.21.8")
                loader.getModContainer("server-replay").ifPresent { container ->
                    addProperty("server_replay_version", container.metadata.version.friendlyString)
                }
                loader.getModContainer("mc-recorder").ifPresent { container ->
                    addProperty("recorder_mod_version", container.metadata.version.friendlyString)
                }
                add("loaded_mods", loadedMods)
                addProperty("epoch_ticks", config.epochTicks)
                addProperty("epoch_numbering", "monotonic_rotation_boundaries")
                addProperty("manual_epoch_rotation", true)
                addProperty("control_protocol", "mc-recorder-control-v1")
                addProperty("capture_scope", "all_connected_players")
                addProperty("record_all_players", config.recordAllPlayers)
                addProperty("timeline", "server_tick_with_global_sequence")
                addProperty("apply_phase", "main_thread_before_packet_handler_body")
                addProperty("raw_serverbound_bytes", false)
                addProperty(
                    "raw_serverbound_bytes_reason",
                    "decoded packets cannot be canonically re-encoded without connection protocol and registry context"
                )
                add("record_types", JsonArray().also { types ->
                    listOf(
                        "session_start", "session_end", "player_join", "player_leave",
                        "tick_start", "packet_arrival", "packet_apply", "control_state", "player_state",
                        "replay_timeline", "tick_end"
                    ).forEach(types::add)
                })
                add("privacy", JsonObject().apply {
                    addProperty("chat_content", "redacted")
                    addProperty("command_content", "redacted")
                    addProperty("custom_payload_content", "redacted")
                })
            }
            atomicWrite(directory.resolve("manifest.json"), manifest)
            return SessionFiles(sessionId, directory)
        }

        private fun atomicWrite(destination: Path, json: JsonObject) {
            val partial = destination.resolveSibling(destination.fileName.toString() + ".inprogress")
            Files.writeString(
                partial,
                com.google.gson.GsonBuilder().setPrettyPrinting().create().toJson(json) + "\n"
            )
            try {
                Files.move(partial, destination, StandardCopyOption.ATOMIC_MOVE)
            } catch (_: AtomicMoveNotSupportedException) {
                Files.move(partial, destination, StandardCopyOption.REPLACE_EXISTING)
            }
        }
    }
}
