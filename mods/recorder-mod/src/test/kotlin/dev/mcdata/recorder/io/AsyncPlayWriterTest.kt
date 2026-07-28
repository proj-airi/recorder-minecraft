package dev.mcdata.recorder.io

import dev.recorderminecraft.artifacts.v1.CaptureEvent
import dev.recorderminecraft.artifacts.v1.EventIdentity
import dev.recorderminecraft.artifacts.v1.PlayerStateEvent
import com.google.protobuf.util.JsonFormat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.assertEquals
import kotlin.test.assertFalse

class AsyncPlayWriterTest {
    @TempDir lateinit var temporary: Path

    @Test
    fun `writes one buffered delimited protobuf stream`() {
        val events = temporary.resolve("capture/events.jsonl")
        val writer = AsyncPlayWriter(events, 1_024, LoggerFactory.getLogger("test"))
        writer.submit(record(10, 1))
        writer.submit(record(11, 2))
        writer.close()
        val records = Files.readAllLines(events).map { line ->
            CaptureEvent.newBuilder().also { JsonFormat.parser().merge(line, it) }.build()
        }
        assertEquals(listOf(1L, 2L), records.map { it.identity.sequence })
        assertFalse(Files.exists(temporary.resolve("epochs")))
        assertFalse(Files.exists(temporary.resolve("manifest.json")))
    }

    private fun record(tick: Long, sequence: Long): CaptureEvent = CaptureEvent.newBuilder()
        .setIdentity(EventIdentity.newBuilder().setSchemaVersion(1).setServerTick(tick).setSequence(sequence))
        .setPlayerState(PlayerStateEvent.getDefaultInstance())
        .build()
}
