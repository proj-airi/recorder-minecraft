package dev.mcdata.recorder.io

import com.google.protobuf.util.JsonFormat
import dev.mcdata.recorder.config.RecorderConfig
import dev.recorderminecraft.artifacts.v1.WorldSessionMetadata
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.time.Instant
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class WorldSessionFilesTest {
    @TempDir lateinit var temporary: Path

    private val sessionId = "44444444-4444-4444-8444-444444444444"
    private val config get() = RecorderConfig(
        artifactsRoot = temporary.toString(),
        serverName = "local-test",
        serverInstanceId = "11111111-1111-4111-8111-111111111111"
    )

    @Test
    fun `metadata gains its end tick only when the stream closes`() {
        val files = WorldSessionFiles.create(config, sessionId, Instant.parse("2026-07-25T10:20:30.125Z"), 0)
        assertEquals(
            temporary.resolve(
                "v1/local-test--11111111-1111-4111-8111-111111111111/world/sessions/" +
                    "20260725T102030.125Z--$sessionId"
            ),
            files.paths.root
        )
        val open = read(files.paths.metadata)
        assertFalse(open.hasEndServerTick())
        assertEquals("world", open.scope)
        assertEquals("engine-reported", open.provenance)
        assertEquals("world-events.jsonl", open.events)
        assertEquals(listOf("world_entities_not_recorded"), open.knownGapsList)
        assertEquals(
            "world/sessions/20260725T102030.125Z--$sessionId/world-events.jsonl",
            files.reference.events
        )

        files.closed(Instant.now(), 120, "server_shutdown")
        val closed = read(files.paths.metadata)
        assertEquals(120, closed.endServerTick)
        assertEquals("server_shutdown", closed.terminalReason)
        assertFalse(closed.hasStreamFailure())
        assertFalse(Files.exists(files.paths.root.resolve("metadata.json.inprogress")))
    }

    @Test
    fun `a contained failure records where coverage stops`() {
        val files = WorldSessionFiles.create(config, sessionId, Instant.now(), 5)
        files.failed(Instant.now(), 42, "IOException: disk full")
        files.closed(Instant.now(), 99, "server_shutdown")

        val failed = read(files.paths.metadata)
        assertEquals(42, failed.endServerTick)
        assertEquals("stream_failure", failed.terminalReason)
        assertTrue(failed.streamFailure.contains("disk full"))
    }

    private fun read(path: Path): WorldSessionMetadata =
        WorldSessionMetadata.newBuilder().also { JsonFormat.parser().merge(Files.readString(path), it) }.build()
}
