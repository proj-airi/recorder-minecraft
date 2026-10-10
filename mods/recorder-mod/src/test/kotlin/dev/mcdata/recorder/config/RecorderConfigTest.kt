package dev.mcdata.recorder.config

import org.slf4j.Logger
import java.lang.reflect.Proxy
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.createTempDirectory
import kotlin.io.path.exists
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class RecorderConfigTest {
    @Test
    fun `default artifacts root resolves below game directory`() {
        val gameDirectory = Path.of("/minecraft")

        assertEquals(
            gameDirectory.resolve("artifacts"),
            RecorderConfig().artifactsPath(gameDirectory)
        )
    }

    @Test
    fun `absolute artifacts root remains absolute`() {
        assertEquals(
            Path.of("/artifacts"),
            RecorderConfig(artifactsRoot = "/artifacts").artifactsPath(Path.of("/minecraft"))
        )
    }

    @Test
    fun `missing configuration creates defaults without warning`() {
        val configDirectory = createTempDirectory("recorder-config")
        val warnings = mutableListOf<String>()

        val config = RecorderConfig.load(configDirectory, recordingLogger(warnings))

        assertEquals("artifacts", config.artifactsRoot)
        assertTrue(configDirectory.resolve("recorder-minecraft.json").exists())
        assertEquals(emptyList(), warnings)
    }

    @Test
    fun `legacy configuration file is reported when defaults are created`() {
        val configDirectory = createTempDirectory("recorder-config")
        Files.writeString(configDirectory.resolve("mc-recorder.json"), "{\"artifacts_root\": \"/artifacts\"}\n")
        val warnings = mutableListOf<String>()

        val config = RecorderConfig.load(configDirectory, recordingLogger(warnings))

        assertEquals("artifacts", config.artifactsRoot)
        assertEquals(1, warnings.size)
        assertTrue(warnings.single().startsWith("Ignoring legacy recorder configuration"))
    }

    private fun recordingLogger(warnings: MutableList<String>): Logger {
        return Proxy.newProxyInstance(Logger::class.java.classLoader, arrayOf(Logger::class.java)) { _, method, args ->
            when {
                method.name == "warn" -> {
                    warnings += args?.firstOrNull() as String
                    null
                }
                method.returnType == java.lang.Boolean.TYPE -> false
                method.name == "getName" -> "test"
                else -> null
            }
        } as Logger
    }
}
