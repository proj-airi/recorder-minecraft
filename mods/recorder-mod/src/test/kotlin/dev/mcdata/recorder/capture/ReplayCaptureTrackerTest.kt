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
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ReplayCaptureTrackerTest {
    @TempDir
    lateinit var temporary: Path

    @Test
    fun `one recorder writes directly to one play replay`() {
        val play = playFiles()
        val tracker = ReplayCaptureTracker(SESSION) { play }
        val recorder = Any()
        val metadataProvider = tracker.captureStarted(
            recorder,
            UUID.fromString(PLAYER),
            "flashback",
            play.paths.replayWorking
        )
        val embedded = JsonObject().also(metadataProvider).getAsJsonObject("mc_recorder")

        assertEquals(SESSION, embedded.get("session_id").asString)
        assertEquals(PLAYER, embedded.get("player_uuid").asString)
        assertEquals(CONNECTION, embedded.get("connection_id").asString)
        assertEquals(4, embedded.get("schema_version").asInt)
        UUID.fromString(embedded.get("replay_id").asString)
        assertFalse(embedded.has("segment_ordinal"))
        assertEquals("capture/replay.zip", embedded.get("capture_path").asString)

        Files.writeString(play.paths.events, "{}\n")
        play.eventsClosed(Instant.parse("2026-07-25T10:21:00Z"), 20, "disconnect")
        var metadata = JsonParser.parseString(Files.readString(play.paths.metadata)).asJsonObject
        assertTrue(metadata.getAsJsonObject("connection").get("end_server_tick").isJsonNull)

        Files.write(play.paths.replay, byteArrayOf(1, 2, 3, 4))
        tracker.captureSaved(recorder, play.paths.replay)
        tracker.captureClosed(recorder)

        metadata = JsonParser.parseString(Files.readString(play.paths.metadata)).asJsonObject
        assertEquals(20, metadata.getAsJsonObject("connection").get("end_server_tick").asLong)
        assertTrue(Files.isRegularFile(play.paths.replay))
        assertFalse(Files.exists(play.paths.replayWorking))
    }

    @Test
    fun `rejects replay rotation for one play`() {
        val play = playFiles()
        val tracker = ReplayCaptureTracker(SESSION) { play }
        tracker.captureStarted(Any(), UUID.fromString(PLAYER), "flashback", play.paths.replayWorking)

        assertFailsWith<IllegalStateException> {
            tracker.captureStarted(Any(), UUID.fromString(PLAYER), "flashback", play.paths.replayWorking)
        }
    }

    @Test
    fun `recorder stage reserves only metadata and capture inputs`() {
        val play = playFiles()
        val metadata = JsonParser.parseString(Files.readString(play.paths.metadata)).asJsonObject

        assertTrue(Files.isRegularFile(play.paths.metadata))
        assertTrue(Files.isDirectory(play.paths.capture))
        assertEquals("capture/events.jsonl", metadata.getAsJsonObject("capture").get("events").asString)
        assertEquals("capture/replay.zip", metadata.getAsJsonObject("capture").get("replay").asString)
        assertFalse(Files.exists(play.paths.actions))
        assertFalse(Files.exists(play.paths.scene))
        assertFalse(Files.exists(play.paths.renders))
    }

    private fun playFiles(): PlayFiles = PlayFiles.create(
        config = RecorderConfig(
            artifactsRoot = temporary.resolve("artifacts").toString(),
            serverName = "test-server",
            serverInstanceId = "00000000-0000-4000-8000-000000000001"
        ),
        sessionId = SESSION,
        playerName = "RecorderPlayer",
        playerUuid = UUID.fromString(PLAYER),
        connectionId = UUID.fromString(CONNECTION),
        startedAt = Instant.parse("2026-07-25T10:20:30Z"),
        startServerTick = 10
    )

    companion object {
        private const val SESSION = "session-20260721"
        private const val PLAYER = "11111111-1111-1111-1111-111111111111"
        private const val CONNECTION = "22222222-2222-2222-2222-222222222222"
    }
}
