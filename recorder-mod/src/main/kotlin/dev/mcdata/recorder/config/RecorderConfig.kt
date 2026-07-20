package dev.mcdata.recorder.config

import com.google.gson.GsonBuilder
import com.google.gson.JsonParser
import com.google.gson.annotations.SerializedName
import net.fabricmc.loader.api.FabricLoader
import org.slf4j.Logger
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.exists

data class RecorderConfig(
    @SerializedName("capture_root")
    val captureRoot: String = "/captures",
    @SerializedName("control_root")
    val controlRoot: String = "/control",
    @SerializedName("epoch_ticks")
    val epochTicks: Long = 6_000,
    @SerializedName("record_all_players")
    val recordAllPlayers: Boolean = true,
    @SerializedName("writer_queue_capacity")
    val writerQueueCapacity: Int = 65_536,
    @SerializedName("include_inventory_components")
    val includeInventoryComponents: Boolean = true
) {
    init {
        require(captureRoot.isNotBlank()) { "capture_root must not be blank" }
        require(controlRoot.isNotBlank()) { "control_root must not be blank" }
        require(epochTicks > 0) { "epoch_ticks must be positive" }
        require(recordAllPlayers) { "record_all_players=false is not supported in v1" }
        require(writerQueueCapacity >= 1_024) { "writer_queue_capacity must be at least 1024" }
    }

    fun capturePath(): Path = Path.of(captureRoot).toAbsolutePath().normalize()

    fun controlPath(): Path = Path.of(controlRoot).toAbsolutePath().normalize()

    companion object {
        private val gson = GsonBuilder().setPrettyPrinting().create()

        fun load(logger: Logger): RecorderConfig {
            val path = FabricLoader.getInstance().configDir.resolve("mc-recorder.json")
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
                    // Gson does not invoke Kotlin default arguments for fields absent from an
                    // existing config, so explicitly migrate additive settings in memory.
                    if (!json.has("control_root")) json.addProperty("control_root", "/control")
                    gson.fromJson(json, RecorderConfig::class.java) ?: error("configuration is empty")
                }
            } catch (exception: Exception) {
                throw IllegalStateException("Invalid recorder configuration at $path", exception)
            }
        }
    }
}
