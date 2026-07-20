package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.mcdata.recorder.config.RecorderConfig
import dev.mcdata.recorder.control.RecorderControlPlane
import dev.mcdata.recorder.io.AsyncEpochWriter
import dev.mcdata.recorder.io.SessionFiles
import org.junit.jupiter.api.io.TempDir
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import java.util.UUID
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class CaptureCoordinatorShutdownTest {
    @TempDir
    lateinit var directory: Path

    @Test
    fun `failed final epoch seal leaves shutdown connection unterminated`() {
        val sessionId = "session-shutdown-failure"
        val sessionDirectory = directory.resolve(sessionId)
        Files.createDirectories(sessionDirectory.resolve("epochs"))
        val config = RecorderConfig(
            captureRoot = directory.resolve("captures").toString(),
            controlRoot = directory.resolve("control").toString(),
            epochTicks = 20,
            writerQueueCapacity = 1_024
        )
        val session = SessionFiles(sessionId, sessionDirectory)
        val logger = LoggerFactory.getLogger("test")
        val writer = AsyncEpochWriter(sessionId, sessionDirectory, 1_024, logger)
        val plane = RecorderControlPlane(sessionId, sessionDirectory, config.controlPath(), logger)
        val coordinator = CaptureCoordinator(config, session, writer, plane, logger)
        val playerUuid = UUID.randomUUID()
        val connectionId = UUID.randomUUID().toString()
        plane.connectionStarted(playerUuid.toString(), "Alex", connectionId, 0, 2)
        addConnection(coordinator, connectionId, playerUuid)

        val deadline = System.nanoTime() + 2_000_000_000L
        while (writer.metrics().lastWrittenSequence == null && System.nanoTime() < deadline) {
            Thread.yield()
        }
        assertNotNull(writer.metrics().lastWrittenSequence)
        Files.createDirectory(sessionDirectory.resolve("epochs/epoch-000000/events.jsonl"))

        assertFailsWith<IllegalStateException> { coordinator.close() }

        val ledger = Files.newBufferedReader(config.controlPath().resolve("connections.json")).use {
            JsonParser.parseReader(it).asJsonObject
        }
        val connection = ledger.getAsJsonArray("connections").single().asJsonObject
        assertEquals("recording", connection.get("state").asString)
        assertFalse(connection.has("end_sequence"))
        assertFalse(connection.has("terminal_reason"))
        val status = Files.newBufferedReader(config.controlPath().resolve("status.json")).use {
            JsonParser.parseReader(it).asJsonObject
        }
        assertEquals("failed", status.get("state").asString)
    }

    @Test
    fun `capture abort fails and retires unresolved seal requests`() {
        val sessionId = "session-abort-request"
        val sessionDirectory = directory.resolve(sessionId)
        Files.createDirectories(sessionDirectory.resolve("epochs"))
        val config = RecorderConfig(
            captureRoot = directory.resolve("captures").toString(),
            controlRoot = directory.resolve("control").toString(),
            epochTicks = 20,
            writerQueueCapacity = 1_024
        )
        val session = SessionFiles(sessionId, sessionDirectory)
        val logger = LoggerFactory.getLogger("test")
        val writer = AsyncEpochWriter(sessionId, sessionDirectory, 1_024, logger)
        val plane = RecorderControlPlane(sessionId, sessionDirectory, config.controlPath(), logger)
        val coordinator = CaptureCoordinator(config, session, writer, plane, logger)
        val playerUuid = UUID.randomUUID().toString()
        val connectionId = UUID.randomUUID().toString()
        plane.connectionStarted(playerUuid, "Alex", connectionId, 0, 2)
        plane.connectionEnded(connectionId, 8, 10, "disconnect")
        val requestId = UUID.randomUUID().toString()
        val request = JsonObject().apply {
            addProperty("schema_version", 1)
            addProperty("request_id", requestId)
            addProperty("operation", "seal_connection")
            addProperty("expected_session_id", sessionId)
            addProperty("player_uuid", playerUuid)
            addProperty("connection_id", connectionId)
            addProperty("connection_end_sequence", 10)
        }
        val requestPath = config.controlPath().resolve("requests/$requestId.json")
        Files.writeString(requestPath, request.toString())

        coordinator.abort(IllegalStateException("synthetic writer failure"))

        val responsePath = config.controlPath().resolve("responses/$requestId.json")
        val response = Files.newBufferedReader(responsePath).use {
            JsonParser.parseReader(it).asJsonObject
        }
        assertEquals("failed", response.get("status").asString)
        assertEquals("writer_failed", response.getAsJsonObject("error").get("code").asString)
        assertTrue(response.getAsJsonObject("error").get("message").asString.contains("synthetic writer failure"))
        assertFalse(Files.exists(requestPath))
    }

    @Suppress("UNCHECKED_CAST")
    private fun addConnection(
        coordinator: CaptureCoordinator,
        connectionId: String,
        playerUuid: UUID
    ) {
        val captureType = CaptureCoordinator::class.java.declaredClasses
            .single { it.simpleName == "ConnectionCapture" }
        val constructor = captureType.getDeclaredConstructor(
            String::class.java,
            UUID::class.java,
            String::class.java,
            Int::class.javaPrimitiveType,
            Long::class.javaPrimitiveType,
            Long::class.javaPrimitiveType
        ).also { it.isAccessible = true }
        val capture = constructor.newInstance(connectionId, playerUuid, "Alex", 1, 0L, 2L)
        val connectionsField = CaptureCoordinator::class.java.getDeclaredField("connections")
            .also { it.isAccessible = true }
        val connections = connectionsField.get(coordinator) as MutableMap<UUID, Any>
        connections[playerUuid] = capture
    }
}
