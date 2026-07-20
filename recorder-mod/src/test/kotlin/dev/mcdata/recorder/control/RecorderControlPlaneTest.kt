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
    fun `only an exact disconnected connection request is accepted`() {
        val session = "session-request"
        val plane = plane(session)
        val player = UUID.randomUUID().toString()
        val connection = UUID.randomUUID().toString()
        plane.connectionStarted(player, "Steve", connection, 1, 2)

        val activeRequest = writeRequest(session, player, connection, 40)
        assertTrue(plane.pollSealRequests().isEmpty())
        assertFailure(activeRequest, "connection_active")

        plane.connectionEnded(connection, 10, 40, "disconnect")
        val exactRequest = writeRequest(session, player, connection, 40)
        val accepted = plane.pollSealRequests()
        assertEquals(listOf(exactRequest), accepted.map { it.requestId })

        val mismatchRequest = writeRequest(session, player, connection, 39)
        plane.pollSealRequests()
        assertFailure(mismatchRequest, "connection_end_mismatch")

        val staleRequest = writeRequest("another-session", player, connection, 40)
        plane.pollSealRequests()
        assertFailure(staleRequest, "stale_session")
    }

    @Test
    fun `completed responses are idempotent and refer to a published verified manifest`() {
        val session = "session-complete"
        val sessionDirectory = directory.resolve(session)
        Files.createDirectories(sessionDirectory.resolve("epochs"))
        val plane = RecorderControlPlane(
            session,
            sessionDirectory,
            directory.resolve("control"),
            LoggerFactory.getLogger("test"),
            nowMillis = { 1_000 }
        )
        val player = UUID.randomUUID().toString()
        val connection = UUID.randomUUID().toString()
        plane.connectionStarted(player, "Alex", connection, 1, 2)
        plane.connectionEnded(connection, 8, 10, "disconnect")
        val requestId = writeRequest(session, player, connection, 10)
        val request = plane.pollSealRequests().single()

        val writer = AsyncEpochWriter(session, sessionDirectory, 1_024, LoggerFactory.getLogger("test"))
        writer.submit(record(0, 8, 10, "tick_end"))
        val sealed = writer.sealEpoch("manual", forced = true)
        plane.completeSealRequest(request, sealed, reused = false, coalesced = false)
        val responsePath = responsePath(requestId)
        val original = Files.readString(responsePath)

        assertTrue(Files.isRegularFile(sealed.manifestPath))
        val response = readJson(responsePath)
        assertEquals("complete", response.get("status").asString)
        assertEquals(0, response.get("sealed_epoch_index").asInt)
        assertEquals(10, response.get("sealed_through_sequence").asInt)
        assertTrue(response.get("forced_seal").asBoolean)
        assertFalse(Files.exists(directory.resolve("control/requests/$requestId.json")))
        assertTrue(plane.pollSealRequests().isEmpty())
        plane.completeSealRequest(request, sealed, reused = true, coalesced = true)
        assertEquals(original, Files.readString(responsePath))
        writer.close()
    }

    @Test
    fun `completed request files do not starve later unresolved requests`() {
        val session = "session-spool-cap"
        val plane = plane(session)
        val player = UUID.randomUUID().toString()
        val connection = UUID.randomUUID().toString()
        plane.connectionStarted(player, "Alex", connection, 1, 2)
        plane.connectionEnded(connection, 8, 10, "disconnect")

        repeat(128) { index ->
            val requestId = "00000000-0000-0000-0000-${(index + 1).toString().padStart(12, '0')}"
            writeRequest(session, player, connection, 10, requestId)
            Files.writeString(responsePath(requestId), "{}\n")
        }
        val unresolved = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        writeRequest(session, player, connection, 10, unresolved)

        assertEquals(listOf(unresolved), plane.pollSealRequests().map { it.requestId })
    }

    @Test
    fun `one global seal satisfies concurrent disconnected player requests`() {
        val session = "session-coalesced"
        val sessionDirectory = directory.resolve(session)
        Files.createDirectories(sessionDirectory.resolve("epochs"))
        val plane = RecorderControlPlane(
            session,
            sessionDirectory,
            directory.resolve("control"),
            LoggerFactory.getLogger("test")
        )
        val firstPlayer = UUID.randomUUID().toString()
        val firstConnection = UUID.randomUUID().toString()
        val secondPlayer = UUID.randomUUID().toString()
        val secondConnection = UUID.randomUUID().toString()
        plane.connectionStarted(firstPlayer, "One", firstConnection, 1, 2)
        plane.connectionStarted(secondPlayer, "Two", secondConnection, 1, 3)
        plane.connectionEnded(firstConnection, 10, 20, "disconnect")
        plane.connectionEnded(secondConnection, 10, 22, "disconnect")
        val firstRequest = writeRequest(session, firstPlayer, firstConnection, 20)
        val secondRequest = writeRequest(session, secondPlayer, secondConnection, 22)
        val requests = plane.pollSealRequests()
        assertEquals(2, requests.size)

        val writer = AsyncEpochWriter(session, sessionDirectory, 1_024, LoggerFactory.getLogger("test"))
        writer.submit(record(0, 10, 20, "player_leave"))
        writer.submit(record(0, 10, 22, "tick_end"))
        val sealed = writer.sealEpoch("manual", forced = true)
        requests.forEach { plane.completeSealRequest(it, sealed, reused = false, coalesced = true) }

        assertEquals(0, readJson(responsePath(firstRequest)).get("sealed_epoch_index").asInt)
        assertEquals(0, readJson(responsePath(secondRequest)).get("sealed_epoch_index").asInt)
        assertTrue(readJson(responsePath(firstRequest)).get("coalesced").asBoolean)
        assertTrue(readJson(responsePath(secondRequest)).get("coalesced").asBoolean)
        writer.close()
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

    private fun writeRequest(
        session: String,
        player: String,
        connection: String,
        endSequence: Long,
        requestId: String = UUID.randomUUID().toString()
    ): String {
        val json = JsonObject().apply {
            addProperty("schema_version", 1)
            addProperty("request_id", requestId)
            addProperty("operation", "seal_connection")
            addProperty("expected_session_id", session)
            addProperty("player_uuid", player)
            addProperty("connection_id", connection)
            addProperty("connection_end_sequence", endSequence)
        }
        val requestDirectory = directory.resolve("control/requests")
        Files.createDirectories(requestDirectory)
        Files.writeString(requestDirectory.resolve("$requestId.json"), json.toString())
        return requestId
    }

    private fun assertFailure(requestId: String, expectedCode: String) {
        val response = readJson(responsePath(requestId))
        assertEquals("failed", response.get("status").asString)
        assertEquals(expectedCode, response.getAsJsonObject("error").get("code").asString)
        assertFalse(Files.exists(directory.resolve("control/requests/$requestId.json")))
    }

    private fun responsePath(requestId: String): Path = directory.resolve("control/responses/$requestId.json")

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
