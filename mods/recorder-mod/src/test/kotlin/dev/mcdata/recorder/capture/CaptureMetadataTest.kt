package dev.mcdata.recorder.capture

import com.google.gson.JsonParser
import dev.mcdata.recorder.config.RecorderConfig
import dev.mcdata.recorder.io.AsyncEpochWriter
import dev.mcdata.recorder.io.SessionFiles
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.assertEquals

class CaptureMetadataTest {
    @TempDir
    lateinit var temporary: Path

    @Test
    fun `session source declares flashback capture contract`() {
        val sessionDirectory = temporary.resolve(SESSION)
        Files.createDirectories(sessionDirectory.resolve("epochs"))
        val session = SessionFiles(SESSION, sessionDirectory)
        val config = RecorderConfig(
            captureRoot = temporary.resolve("captures").toString(),
            epochTicks = 20,
            writerQueueCapacity = 1_024
        )
        val logger = LoggerFactory.getLogger(CaptureMetadataTest::class.java)
        val writer = AsyncEpochWriter(SESSION, sessionDirectory, 1_024, logger)
        val coordinator = CaptureCoordinator(config, session, writer, logger)

        coordinator.close()

        val sessionStart = Files.newBufferedReader(
            sessionDirectory.resolve("epochs/epoch-000000/events.jsonl")
        ).useLines { lines ->
            lines.map(JsonParser::parseString)
                .map { it.asJsonObject }
                .first { it.get("record_type").asString == "session_start" }
        }
        assertEquals(
            "client_visible_scene_v1",
            sessionStart.get("flashback_capture_contract").asString
        )
    }

    companion object {
        private const val SESSION = "session-capture-contract"
    }
}
