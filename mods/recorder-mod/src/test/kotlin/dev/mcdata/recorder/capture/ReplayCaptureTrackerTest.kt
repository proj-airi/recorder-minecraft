package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import dev.recorderminecraft.artifacts.v1.ServerMetadata
import com.google.protobuf.util.JsonFormat
import dev.mcdata.recorder.config.RecorderConfig
import dev.mcdata.recorder.io.PlayFiles
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.time.Instant
import java.util.UUID
import java.util.concurrent.CompletableFuture
import kotlin.test.assertContentEquals
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
        val working = play.paths.replayWorking.resolve("2026-07-25--10-20-30")
        val metadataProvider = tracker.captureStarted(
            recorder,
            UUID.fromString(PLAYER),
            "flashback",
            working
        )
        val embedded = JsonObject().also(metadataProvider).getAsJsonObject("recorder-minecraft")

        assertEquals(SESSION, embedded.get("session_id").asString)
        assertEquals(PLAYER, embedded.get("player_uuid").asString)
        assertEquals(CONNECTION, embedded.get("connection_id").asString)
        assertEquals(4, embedded.get("schema_version").asInt)
        UUID.fromString(embedded.get("replay_id").asString)
        assertFalse(embedded.has("segment_ordinal"))
        assertEquals("capture/replay.zip", embedded.get("capture_path").asString)

        Files.writeString(play.paths.events, "{}\n")
        play.eventsClosed(Instant.parse("2026-07-25T10:21:00Z"), 20, "disconnect")
        var metadata = readMetadata(play.paths.metadata)
        assertFalse(metadata.connection.hasEndServerTick())

        Files.createDirectories(working)
        val serverReplayOutput = working.resolveSibling(working.fileName.toString() + ".zip")
        Files.write(serverReplayOutput, byteArrayOf(1, 2, 3, 4))
        tracker.captureSaved(recorder, serverReplayOutput)
        Files.delete(working)
        tracker.captureClosed(recorder)

        metadata = readMetadata(play.paths.metadata)
        assertEquals(20, metadata.connection.endServerTick)
        assertTrue(Files.isRegularFile(play.paths.replay))
        assertContentEquals(byteArrayOf(1, 2, 3, 4), Files.readAllBytes(play.paths.replay))
        assertFalse(Files.exists(play.paths.replayWorking))
    }

    @Test
    fun `rejects replay rotation for one play`() {
        val play = playFiles()
        val tracker = ReplayCaptureTracker(SESSION) { play }
        val working = play.paths.replayWorking.resolve("2026-07-25--10-20-30")
        tracker.captureStarted(Any(), UUID.fromString(PLAYER), "flashback", working)

        assertFailsWith<IllegalStateException> {
            tracker.captureStarted(Any(), UUID.fromString(PLAYER), "flashback", working)
        }
    }

    @Test
    fun `rejects a writer outside the connection replay directory`() {
        val play = playFiles()
        val tracker = ReplayCaptureTracker(SESSION) { play }

        assertFailsWith<IllegalArgumentException> {
            tracker.captureStarted(
                Any(),
                UUID.fromString(PLAYER),
                "flashback",
                temporary.resolve("other/2026-07-25--10-20-30")
            )
        }
    }

    @Test
    fun `server shutdown finalizes a stopped recorder before deferred close events`() {
        val play = playFiles()
        val tracker = ReplayCaptureTracker(SESSION) { play }
        val recorder = Any()
        val working = play.paths.replayWorking.resolve("2026-07-25--10-20-30")
        tracker.captureStarted(recorder, UUID.fromString(PLAYER), "flashback", working)

        Files.writeString(play.paths.events, "{}\n")
        play.eventsClosed(Instant.parse("2026-07-25T10:21:00Z"), 20, "disconnect")
        Files.createDirectories(working)
        val serverReplayOutput = working.resolveSibling(working.fileName.toString() + ".zip")
        Files.write(serverReplayOutput, byteArrayOf(1, 2, 3, 4))
        Files.delete(working)
        tracker.captureStopping(recorder, CompletableFuture.completedFuture(4))

        tracker.finishStoppingRecorders()

        val metadata = readMetadata(play.paths.metadata)
        assertEquals(20, metadata.connection.endServerTick)
        assertTrue(Files.isRegularFile(play.paths.replay))
        assertFalse(Files.exists(play.paths.replayWorking))

        tracker.captureSaved(recorder, serverReplayOutput)
        tracker.captureClosed(recorder)
    }

    @Test
    fun `recorder stage reserves only metadata and capture inputs`() {
        val play = playFiles()
        val metadata = readMetadata(play.paths.metadata)

        assertTrue(Files.isRegularFile(play.paths.metadata))
        assertTrue(Files.isDirectory(play.paths.capture))
        assertEquals("capture/events.jsonl", metadata.capture.events)
        assertEquals("capture/replay.zip", metadata.capture.replay)
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

    private fun readMetadata(path: Path): ServerMetadata = ServerMetadata.newBuilder()
        .also { JsonFormat.parser().merge(Files.readString(path), it) }
        .build()

    companion object {
        private const val SESSION = "session-20260721"
        private const val PLAYER = "11111111-1111-1111-1111-111111111111"
        private const val CONNECTION = "22222222-2222-2222-2222-222222222222"
    }
}
