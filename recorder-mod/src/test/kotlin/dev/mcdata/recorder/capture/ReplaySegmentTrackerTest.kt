package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.mcdata.recorder.control.RecorderControlPlane
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import java.util.UUID
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ReplaySegmentTrackerTest {
    @TempDir
    lateinit var temporary: Path

    private val logger = LoggerFactory.getLogger(ReplaySegmentTrackerTest::class.java)

    @Test
    fun `recorder created before join receives exact connection metadata`() {
        val publications = mutableListOf<List<RecorderControlPlane.ReplaySegmentSnapshot>>()
        var now = 1_000L
        val tracker = ReplaySegmentTracker(SESSION, { publications += it }, logger) { now++ }
        val player = UUID.fromString(PLAYER)
        val recorder = Any()

        val metadataProvider = tracker.segmentStarted(
            recorder,
            player,
            "alex",
            temporary.resolve("active"),
            "flashback"
        )
        assertNull(tracker.snapshots().single().connectionId)

        tracker.connectionStarted(player, CONNECTION_ONE, 41, 102)
        val metadata = JsonObject()
        metadataProvider(metadata)

        val embedded = metadata.getAsJsonObject("mc_recorder")
        assertEquals(1, embedded.get("schema_version").asInt)
        assertEquals(SESSION, embedded.get("session_id").asString)
        assertEquals(PLAYER, embedded.get("player_uuid").asString)
        assertEquals(CONNECTION_ONE, embedded.get("connection_id").asString)
        assertEquals(0, embedded.get("segment_ordinal").asLong)
        assertTrue(UUID.fromString(embedded.get("segment_id").asString).toString().isNotBlank())
        assertEquals(CONNECTION_ONE, publications.last().single().connectionId)
    }

    @Test
    fun `late save stays attached to old connection across reconnect`() {
        val tracker = ReplaySegmentTracker(SESSION, {}, logger)
        val player = UUID.fromString(PLAYER)
        val oldRecorder = Any()
        val newRecorder = Any()

        tracker.segmentStarted(oldRecorder, player, "alex", temporary.resolve("old-active"), "flashback")
        tracker.connectionStarted(player, CONNECTION_ONE, 1, 2)
        tracker.connectionEnded(CONNECTION_ONE, 50, 500, "disconnect")

        tracker.segmentStarted(newRecorder, player, "alex", temporary.resolve("new-active"), "flashback")
        tracker.connectionStarted(player, CONNECTION_TWO, 60, 600)

        val oldOutput = temporary.resolve("old.zip")
        val newOutput = temporary.resolve("new.zip")
        Files.writeString(oldOutput, "old")
        Files.writeString(newOutput, "newer")
        assertTrue(tracker.segmentSaved(oldRecorder, oldOutput))
        assertTrue(tracker.segmentSaved(newRecorder, newOutput))

        val snapshots = tracker.snapshots()
        assertEquals(listOf(0L, 1L), snapshots.map { it.segmentOrdinal })
        assertEquals(CONNECTION_ONE, snapshots[0].connectionId)
        assertEquals(50, snapshots[0].connectionEndServerTick)
        assertEquals("disconnect", snapshots[0].terminalReason)
        assertEquals(CONNECTION_TWO, snapshots[1].connectionId)
        assertEquals("saved", snapshots[0].state)
        assertEquals(3, snapshots[0].outputSizeBytes)
        assertEquals(5, snapshots[1].outputSizeBytes)
        assertFalse(tracker.segmentSaved(Any(), temporary.resolve("unknown.zip")))
    }

    @Test
    fun `control plane publishes current and durable replay ledgers`() {
        val sessionDirectory = temporary.resolve("captures").resolve(SESSION)
        Files.createDirectories(sessionDirectory)
        val controlRoot = temporary.resolve("control")
        val control = RecorderControlPlane(SESSION, sessionDirectory, controlRoot, logger) { 9_000L }
        val tracker = ReplaySegmentTracker(SESSION, control, logger)
        val player = UUID.fromString(PLAYER)
        val recorder = Any()

        tracker.segmentStarted(recorder, player, "alex", temporary.resolve("active"), "flashback")
        tracker.connectionStarted(player, CONNECTION_ONE, 10, 11)
        val output = temporary.resolve("segment.zip")
        Files.writeString(output, "archive")
        tracker.segmentSaved(recorder, output)

        val current = JsonParser.parseString(
            Files.readString(controlRoot.resolve("replay-segments.json"))
        ).asJsonObject
        val historical = JsonParser.parseString(
            Files.readString(controlRoot.resolve("sessions/$SESSION.replay-segments.json"))
        ).asJsonObject
        assertEquals(current, historical)
        assertEquals(1, current.get("schema_version").asInt)
        assertEquals(SESSION, current.get("session_id").asString)
        val segment = current.getAsJsonArray("segments").single().asJsonObject
        assertEquals(CONNECTION_ONE, segment.get("connection_id").asString)
        assertEquals("saved", segment.get("state").asString)
        assertEquals(7, segment.get("output_size_bytes").asLong)
    }

    companion object {
        private const val SESSION = "session-20260721"
        private const val PLAYER = "11111111-1111-1111-1111-111111111111"
        private const val CONNECTION_ONE = "22222222-2222-2222-2222-222222222222"
        private const val CONNECTION_TWO = "33333333-3333-3333-3333-333333333333"
    }
}
