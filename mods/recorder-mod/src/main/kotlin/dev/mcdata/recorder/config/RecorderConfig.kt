package dev.mcdata.recorder.config

import com.google.gson.GsonBuilder
import com.google.gson.JsonParser
import com.google.gson.annotations.SerializedName
import net.fabricmc.loader.api.FabricLoader
import org.slf4j.Logger
import java.nio.file.Files
import java.nio.file.Path
import java.text.Normalizer
import java.util.UUID
import kotlin.io.path.exists

data class RecorderConfig(
    @SerializedName("artifacts_root")
    val artifactsRoot: String = "/artifacts",
    @SerializedName("server_name")
    val serverName: String = "minecraft",
    @SerializedName("server_instance_id")
    val serverInstanceId: String = UUID.randomUUID().toString(),
    @SerializedName("record_all_players")
    val recordAllPlayers: Boolean = true,
    @SerializedName("writer_queue_capacity")
    val writerQueueCapacity: Int = 65_536,
    @SerializedName("include_inventory_components")
    val includeInventoryComponents: Boolean = true
) {
    init {
        validate()
    }

    private fun validate() {
        require(artifactsRoot.isNotBlank()) { "artifacts_root must not be blank" }
        require(serverName.isNotBlank() && serverName.toByteArray(Charsets.UTF_8).size <= 180) {
            "server_name must contain 1 to 180 UTF-8 bytes"
        }
        require(serverName.none { it == '/' || it == '\\' || it.code < 32 || it.code == 127 }) {
            "server_name contains a character unsafe for artifact paths"
        }
        require(Normalizer.normalize(serverName, Normalizer.Form.NFC) == serverName) {
            "server_name must use NFC Unicode normalization"
        }
        require(UUID.fromString(serverInstanceId).toString() == serverInstanceId) {
            "server_instance_id must be a canonical UUID"
        }
        require(recordAllPlayers) { "record_all_players=false is not supported in v1" }
        require(writerQueueCapacity >= 1_024) { "writer_queue_capacity must be at least 1024" }
    }

    fun artifactsPath(): Path = Path.of(artifactsRoot).toAbsolutePath().normalize()

    fun serverInstanceUuid(): UUID = UUID.fromString(serverInstanceId)

    companion object {
        private val gson = GsonBuilder().setPrettyPrinting().create()
        private val configKeys = setOf(
            "artifacts_root",
            "server_name",
            "server_instance_id",
            "record_all_players",
            "writer_queue_capacity",
            "include_inventory_components"
        )

        fun load(logger: Logger): RecorderConfig {
            val path = FabricLoader.getInstance().configDir.resolve("recorder-minecraft.json")
            if (!path.exists()) {
                val defaults = RecorderConfig()
                Files.createDirectories(path.parent)
                Files.writeString(path, gson.toJson(defaults) + "\n")
                logger.info("Created default recorder configuration at {}", path)
                return defaults
            }

            return try {
                Files.newBufferedReader(path).use { reader ->
                    val json = JsonParser.parseReader(reader).asJsonObject
                    val unknown = json.keySet() - configKeys
                    val missing = configKeys - json.keySet()
                    require(unknown.isEmpty()) {
                        "unsupported configuration keys: ${unknown.sorted().joinToString(", ")}"
                    }
                    require(missing.isEmpty()) {
                        "missing configuration keys: ${missing.sorted().joinToString(", ")}"
                    }
                    (gson.fromJson(json, RecorderConfig::class.java) ?: error("configuration is empty"))
                        .also { it.validate() }
                }
            } catch (exception: Exception) {
                throw IllegalStateException("Invalid recorder configuration at $path", exception)
            }
        }
    }
}
