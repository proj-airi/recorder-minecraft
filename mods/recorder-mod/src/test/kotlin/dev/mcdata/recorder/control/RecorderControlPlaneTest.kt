package dev.mcdata.recorder.control

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.mcdata.recorder.io.AsyncEpochWriter
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

class RecorderControlPlaneTest {
    @TempDir
    lateinit var directory: Path

    @Test
    fun `ledger keeps reconnects distinct and marks clean shutdown terminals`() {
        val plane = plane("session-ledger")
        val player = UUID.randomUUID().toString()
        val first = UUID.randomUUID().toString()
        val second = UUID.randomUUID().toString()

        plane.connectionStarted(player, "Alex", first, 1, 2)
        plane.connectionEnded(first, 20, 80, "disconnect")
        plane.connectionStarted(player, "Alex", second, 21, 82)
        plane.connectionEnded(second, 30, 120, "server_shutdown")

        val ledger = readJson(directory.resolve("control/connections.json"))
        val rows = ledger.getAsJsonArray("connections")
        assertEquals(2, rows.size())
        assertEquals(first, rows[0].asJsonObject.get("connection_id").asString)
        assertEquals("disconnect", rows[0].asJsonObject.get("terminal_reason").asString)
        assertEquals(second, rows[1].asJsonObject.get("connection_id").asString)
        assertEquals("server_shutdown", rows[1].asJsonObject.get("terminal_reason").asString)
        assertTrue(rows.all { it.asJsonObject.get("state").asString == "disconnected" })
    }

    @Test
    fun `control plane publishes file-only state without command spools`() {
        plane("session-file-only")

        assertTrue(Files.isDirectory(directory.resolve("control/sessions")))
        assertTrue(Files.isDirectory(directory.resolve("control/render-ready")))
        assertFalse(Files.exists(directory.resolve("control/requests")))
        assertFalse(Files.exists(directory.resolve("control/responses")))
    }

    @Test
    fun `heartbeat exposes queue progress storage and only active connections`() {
        val session = "session-status"
        val sessionDirectory = directory.resolve(session)
        Files.createDirectories(sessionDirectory.resolve("epochs"))
        val plane = RecorderControlPlane(
            session,
            sessionDirectory,
            directory.resolve("control"),
            LoggerFactory.getLogger("test"),
            nowMillis = { 5_000 }
        )
        val activePlayer = UUID.randomUUID().toString()
        val activeConnection = UUID.randomUUID().toString()
        val endedPlayer = UUID.randomUUID().toString()
        val endedConnection = UUID.randomUUID().toString()
        plane.connectionStarted(activePlayer, "Active", activeConnection, 1, 2)
        plane.connectionStarted(endedPlayer, "Ended", endedConnection, 1, 3)
        plane.connectionEnded(endedConnection, 2, 4, "disconnect")
        val writer = AsyncEpochWriter(session, sessionDirectory, 1_024, LoggerFactory.getLogger("test"))
        writer.submit(record(0, 2, 5, "tick_end"))

        plane.publishStatus(
            RecorderControlPlane.StatusSnapshot("recording", 2, 5, 0, 0, 1, writer.metrics())
        )

        val status = readJson(directory.resolve("control/status.json"))
        assertEquals("recording", status.get("state").asString)
        assertEquals(5_000, status.get("updated_at_unix_ms").asLong)
        assertEquals(1, status.getAsJsonArray("active_connections").size())
        assertEquals(activeConnection, status.getAsJsonArray("active_connections")[0].asJsonObject
            .get("connection_id").asString)
        assertNotNull(status.getAsJsonObject("writer").get("queue_capacity"))
        assertNotNull(status.getAsJsonObject("storage").get("active_epoch_inprogress_bytes"))
        writer.close()
    }

    @Test
    fun `new session replaces current ledger while retaining session history`() {
        val first = plane("session-one")
        val oldConnection = UUID.randomUUID().toString()
        first.connectionStarted(UUID.randomUUID().toString(), "Old", oldConnection, 1, 2)
        first.connectionEnded(oldConnection, 2, 3, "disconnect")

        plane("session-two")

        val ledger = readJson(directory.resolve("control/connections.json"))
        assertEquals("session-two", ledger.get("session_id").asString)
        assertEquals(0, ledger.getAsJsonArray("connections").size())
        val historical = readJson(directory.resolve("control/sessions/session-one.connections.json"))
        assertEquals("session-one", historical.get("session_id").asString)
        assertEquals(oldConnection, historical.getAsJsonArray("connections")[0].asJsonObject
            .get("connection_id").asString)
        assertTrue(Files.isRegularFile(directory.resolve("control/sessions/session-two.connections.json")))
    }

    @Test
    fun `saved terminal replay segment emits a render ready spool file`() {
        val session = "session-render-ready"
        val sessionDirectory = directory.resolve(session)
        Files.createDirectories(sessionDirectory.resolve("epochs"))
        val plane = RecorderControlPlane(
            session,
            sessionDirectory,
            directory.resolve("control"),
            LoggerFactory.getLogger("test"),
            nowMillis = { 12_000 }
        )
        val player = UUID.randomUUID().toString()
        val connection = UUID.randomUUID().toString()
        plane.connectionStarted(player, "Alex", connection, 4, 8)
        plane.connectionEnded(connection, 40, 80, "disconnect")

        plane.publishReplaySegments(
            listOf(
                RecorderControlPlane.ReplaySegmentSnapshot(
                    segmentId = UUID.randomUUID().toString(),
                    segmentOrdinal = 0,
                    playerUuid = player,
                    playerName = "Alex",
                    connectionId = connection,
                    connectionJoinServerTick = 4,
                    connectionJoinSequence = 8,
                    connectionEndServerTick = 40,
                    connectionEndSequence = 80,
                    terminalReason = "disconnect",
                    replayFormat = "flashback",
                    hotbarSnapshotContract = "item_stack_copy_v1",
                    flashbackCaptureContract = "client_visible_scene_v1",
                    sourceLocation = "/replays/players/$player",
                    state = "saved",
                    startedAtUnixMs = 10_000,
                    savedAtUnixMs = 11_000,
                    output = "/replays/players/$player.zip",
                    outputSizeBytes = 123
                )
            )
        )

        val ready = readJson(directory.resolve("control/render-ready/$connection.json"))
        assertEquals(1, ready.get("schema_version").asInt)
        assertEquals("render_ready", ready.get("kind").asString)
        assertEquals(session, ready.get("session_id").asString)
        assertEquals(player, ready.get("player_uuid").asString)
        assertEquals(connection, ready.get("connection_id").asString)
        assertEquals(40, ready.get("connection_end_server_tick").asLong)
        assertEquals(80, ready.get("connection_end_sequence").asLong)
        assertEquals(12_000, ready.get("emitted_at_unix_ms").asLong)
        val segments = ready.getAsJsonArray("segments")
        assertEquals(1, segments.size())
        assertEquals(
            "client_visible_scene_v1",
            segments.single().asJsonObject.get("flashback_capture_contract").asString
        )
    }

    @Test
    fun `heartbeat repairs a connection ledger after a transient publication failure`() {
        val session = "session-ledger-retry"
        val sessionDirectory = directory.resolve(session)
        Files.createDirectories(sessionDirectory.resolve("epochs"))
        val plane = RecorderControlPlane(
            session,
            sessionDirectory,
            directory.resolve("control"),
            LoggerFactory.getLogger("test")
        )
        val player = UUID.randomUUID().toString()
        val connection = UUID.randomUUID().toString()
        plane.connectionStarted(player, "Alex", connection, 1, 2)

        val sessions = directory.resolve("control/sessions")
        val savedSessions = directory.resolve("control/sessions.saved")
        Files.move(sessions, savedSessions)
        Files.writeString(sessions, "temporarily unavailable\n")
        assertFailsWith<Exception> {
            plane.connectionEnded(connection, 8, 10, "disconnect")
        }
        Files.delete(sessions)
        Files.move(savedSessions, sessions)

        val writer = AsyncEpochWriter(session, sessionDirectory, 1_024, LoggerFactory.getLogger("test"))
        plane.publishStatus(
            RecorderControlPlane.StatusSnapshot("recording", 8, 10, 0, 0, 1, writer.metrics())
        )

        val current = readJson(directory.resolve("control/connections.json"))
            .getAsJsonArray("connections").single().asJsonObject
        val historical = readJson(directory.resolve("control/sessions/$session.connections.json"))
            .getAsJsonArray("connections").single().asJsonObject
        assertEquals(10, current.get("end_sequence").asInt)
        assertEquals("disconnect", current.get("terminal_reason").asString)
        assertEquals(10, historical.get("end_sequence").asInt)
        writer.close()
    }

    private fun plane(session: String): RecorderControlPlane {
        val sessionDirectory = directory.resolve(session)
        Files.createDirectories(sessionDirectory.resolve("epochs"))
        return RecorderControlPlane(
            session,
            sessionDirectory,
            directory.resolve("control"),
            LoggerFactory.getLogger("test")
        )
    }

    private fun readJson(path: Path): JsonObject =
        Files.newBufferedReader(path).use { JsonParser.parseReader(it).asJsonObject }

    private fun record(
        epoch: Long,
        tick: Long,
        sequence: Long,
        type: String
    ): AsyncEpochWriter.QueuedRecord {
        val json = JsonObject().apply {
            addProperty("record_type", type)
            addProperty("server_tick", tick)
            addProperty("sequence", sequence)
        }
        return AsyncEpochWriter.QueuedRecord(epoch, tick, sequence, type, json)
    }
}
