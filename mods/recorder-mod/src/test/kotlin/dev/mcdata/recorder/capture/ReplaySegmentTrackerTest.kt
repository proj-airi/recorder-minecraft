package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import dev.mcdata.recorder.config.RecorderConfig
import dev.mcdata.recorder.io.PlayFiles
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.time.Instant
import java.util.UUID
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class ReplaySegmentTrackerTest {
    @TempDir
    lateinit var temporary: Path

    @Test
    fun `recorder created before join receives exact connection metadata`() {
        val tracker = ReplaySegmentTracker(SESSION)
        val player = UUID.fromString(PLAYER)
        val metadataProvider = tracker.segmentStarted(Any(), player, "flashback")

        tracker.connectionStarted(player, CONNECTION_ONE, playFiles(PLAYER, CONNECTION_ONE, "one"))
        val metadata = JsonObject()
        metadataProvider(metadata)

        val embedded = metadata.getAsJsonObject("mc_recorder")
        assertEquals(3, embedded.get("schema_version").asInt)
        assertEquals(SESSION, embedded.get("session_id").asString)
        assertEquals(PLAYER, embedded.get("player_uuid").asString)
        assertEquals(CONNECTION_ONE, embedded.get("connection_id").asString)
        assertEquals(0, embedded.get("segment_ordinal").asLong)
        assertEquals("item_stack_copy_v1", embedded.get("hotbar_snapshot_contract").asString)
        assertEquals(
            "client_visible_scene_v1",
            embedded.get("flashback_capture_contract").asString
        )
        assertTrue(UUID.fromString(embedded.get("segment_id").asString).toString().isNotBlank())
    }

    @Test
    fun `reconnect assigns new segments without rewriting old metadata`() {
        val tracker = ReplaySegmentTracker(SESSION)
        val player = UUID.fromString(PLAYER)
        val oldMetadata = tracker.segmentStarted(Any(), player, "flashback")
        tracker.connectionStarted(player, CONNECTION_ONE, playFiles(PLAYER, CONNECTION_ONE, "one"))
        tracker.connectionEnded(CONNECTION_ONE)
        val newMetadata = tracker.segmentStarted(Any(), player, "flashback")
        tracker.connectionStarted(player, CONNECTION_TWO, playFiles(PLAYER, CONNECTION_TWO, "two"))

        val old = JsonObject().also(oldMetadata)
        val newer = JsonObject().also(newMetadata)
        assertEquals(CONNECTION_ONE, old.getAsJsonObject("mc_recorder").get("connection_id").asString)
        assertEquals(CONNECTION_TWO, newer.getAsJsonObject("mc_recorder").get("connection_id").asString)
        assertEquals(1, newer.getAsJsonObject("mc_recorder").get("segment_ordinal").asLong)
    }

    @Test
    fun `non flashback player segments are rejected`() {
        val tracker = ReplaySegmentTracker(SESSION)
        assertFailsWith<IllegalArgumentException> {
            tracker.segmentStarted(Any(), UUID.fromString(PLAYER), "other")
        }
    }

    @Test
    fun `completed flashback replay is copied unchanged into its play`() {
        val tracker = ReplaySegmentTracker(SESSION)
        val recorder = Any()
        val player = UUID.fromString(PLAYER)
        val play = playFiles(PLAYER, CONNECTION_ONE, "saved")
        val metadataProvider = tracker.segmentStarted(recorder, player, "flashback")
        tracker.connectionStarted(player, CONNECTION_ONE, play)
        val embedded = JsonObject().also(metadataProvider).getAsJsonObject("mc_recorder")
        val source = temporary.resolve("source.zip")
        Files.write(source, byteArrayOf(1, 2, 3, 4))

        val destination = tracker.segmentSaved(recorder, source)

        assertEquals(source.toFile().readBytes().toList(), destination.toFile().readBytes().toList())
        assertTrue(Files.exists(source))
        assertEquals(
            "replays/000000--${embedded.get("segment_id").asString}.zip",
            JsonParser.parseString(Files.readString(play.paths.metadata))
                .asJsonObject.getAsJsonArray("replays")[0].asJsonObject.get("path").asString
        )
    }

    @Test
    fun `recorder stage creates only metadata and replay directory`() {
        val play = playFiles(PLAYER, CONNECTION_ONE, "initial")

        assertTrue(Files.isRegularFile(play.paths.metadata))
        assertTrue(Files.isDirectory(play.paths.replays))
        assertFalse(Files.exists(play.paths.actions))
        assertFalse(Files.exists(play.paths.scene))
        assertFalse(Files.exists(play.paths.renders))

        play.close(Instant.parse("2026-07-25T10:21:00Z"), 20, "disconnect")
        val metadata = JsonParser.parseString(Files.readString(play.paths.metadata)).asJsonObject
        assertEquals("closed", metadata.getAsJsonObject("connection").get("status").asString)
    }

    private fun playFiles(player: String, connection: String, suffix: String): PlayFiles =
        PlayFiles.create(
            config = RecorderConfig(
                artifactsRoot = temporary.resolve("artifacts-$suffix").toString(),
                intermediateRoot = temporary.resolve("intermediate-$suffix").toString(),
                serverName = "test-server",
                serverInstanceId = "00000000-0000-4000-8000-000000000001"
            ),
            sessionId = SESSION,
            playerName = "RecorderPlayer",
            playerUuid = UUID.fromString(player),
            connectionId = UUID.fromString(connection),
            startedAt = Instant.parse("2026-07-25T10:20:30Z"),
            startServerTick = 10
        )

    companion object {
        private const val SESSION = "session-20260721"
        private const val PLAYER = "11111111-1111-1111-1111-111111111111"
        private const val CONNECTION_ONE = "22222222-2222-2222-2222-222222222222"
        private const val CONNECTION_TWO = "33333333-3333-3333-3333-333333333333"
    }
}
