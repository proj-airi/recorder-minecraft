package dev.mcdata.recorder.io

import com.google.protobuf.util.JsonFormat
import dev.recorderminecraft.artifacts.v1.ContainerRemoved
import dev.recorderminecraft.artifacts.v1.WorldEvent
import dev.recorderminecraft.artifacts.v1.WorldEventIdentity
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class AsyncWorldWriterTest {
    @TempDir lateinit var temporary: Path

    @Test
    fun `close drains every queued record before returning`() {
        val events = temporary.resolve("world/sessions/s/world-events.jsonl")
        val writer = AsyncWorldWriter(events, 1_024, LoggerFactory.getLogger("test"), "world")
        (1L..500L).forEach { writer.submit(record(it / 20, it)) }
        writer.close()

        val records = read(events)
        assertEquals((1L..500L).toList(), records.map { it.identity.sequence })
        assertEquals(ContainerRemoved.Cause.CAUSE_DESTROYED, records.first().containerRemoved.cause)
    }

    @Test
    fun `records are rejected after close and close is idempotent`() {
        val events = temporary.resolve("world-events.jsonl")
        val writer = AsyncWorldWriter(events, 1_024, LoggerFactory.getLogger("test"), "world")
        writer.submit(record(0, 1))
        writer.close()
        writer.close()

        val failure = assertFailsWith<IllegalStateException> { writer.submit(record(1, 2)) }
        assertEquals("world writer is closing", failure.message)
        assertEquals(listOf(1L), read(events).map { it.identity.sequence })
    }

    @Test
    fun `abort keeps records that were already accepted`() {
        val events = temporary.resolve("world-events.jsonl")
        val writer = AsyncWorldWriter(events, 1_024, LoggerFactory.getLogger("test"), "world")
        writer.submit(record(3, 1))
        writer.submit(record(3, 2))
        writer.abort()

        assertEquals(listOf(1L, 2L), read(events).map { it.identity.sequence })
    }

    private fun read(events: Path): List<WorldEvent> = Files.readAllLines(events).map { line ->
        WorldEvent.newBuilder().also { JsonFormat.parser().merge(line, it) }.build()
    }

    private fun record(tick: Long, sequence: Long): WorldEvent = WorldEvent.newBuilder()
        .setIdentity(
            WorldEventIdentity.newBuilder().setSchemaVersion(1).setSessionId("s").setServerTick(tick).setSequence(sequence)
        )
        .setContainerRemoved(ContainerRemoved.newBuilder().setCause(ContainerRemoved.Cause.CAUSE_DESTROYED))
        .build()
}
