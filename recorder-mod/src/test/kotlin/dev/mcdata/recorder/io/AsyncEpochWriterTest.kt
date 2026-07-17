package dev.mcdata.recorder.io

import com.google.gson.JsonObject
import org.junit.jupiter.api.io.TempDir
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class AsyncEpochWriterTest {
    @TempDir
    lateinit var directory: Path

    @Test
    fun `close seals epochs with a matching content hash`() {
        val session = directory.resolve("session-test")
        Files.createDirectories(session.resolve("epochs"))
        val writer = AsyncEpochWriter("session-test", session, 1_024, LoggerFactory.getLogger("test"))

        writer.submit(record(0, 1, 1, "tick_end"))
        writer.submit(record(1, 3, 2, "tick_end"))
        writer.close()

        for (epoch in 0..1) {
            val epochDirectory = session.resolve("epochs/epoch-%06d".format(epoch))
            val events = epochDirectory.resolve("events.jsonl")
            val manifest = JsonLineEncoder.gson.fromJson(
                Files.readString(epochDirectory.resolve("manifest.json")),
                JsonObject::class.java
            )
            assertTrue(Files.exists(events))
            assertFalse(Files.exists(epochDirectory.resolve("events.jsonl.inprogress")))
            assertEquals(1, manifest.get("record_count").asInt)
            assertEquals(sha256(Files.readAllBytes(events)), manifest.get("events_sha256").asString)
        }
        assertTrue(Files.exists(session.resolve("session_end.json")))
    }

    @Test
    fun `abort leaves the active epoch unsealed and marks the session incomplete`() {
        val session = directory.resolve("session-aborted")
        Files.createDirectories(session.resolve("epochs"))
        val writer = AsyncEpochWriter("session-aborted", session, 1_024, LoggerFactory.getLogger("test"))

        writer.submit(record(0, 1, 1, "tick_end"))
        writer.abort("synthetic failure")

        val epoch = session.resolve("epochs/epoch-000000")
        assertTrue(Files.exists(epoch.resolve("events.jsonl.inprogress")))
        assertFalse(Files.exists(epoch.resolve("events.jsonl")))
        assertFalse(Files.exists(epoch.resolve("manifest.json")))
        val end = JsonLineEncoder.gson.fromJson(
            Files.readString(session.resolve("session_end.json")),
            JsonObject::class.java
        )
        assertFalse(end.get("clean_shutdown").asBoolean)
        assertEquals("incomplete", end.get("status").asString)
        assertEquals("synthetic failure", end.get("failure_reason").asString)
    }

    @Test
    fun `failed close publishes an incomplete session marker with the last durable record`() {
        val session = directory.resolve("session-close-failed")
        Files.createDirectories(session.resolve("epochs/epoch-000001"))
        // Force opening epoch 1 to fail after epoch 0 has been written and sealed.
        Files.writeString(session.resolve("epochs/epoch-000001/events.jsonl"), "collision\n")
        val writer = AsyncEpochWriter("session-close-failed", session, 1_024, LoggerFactory.getLogger("test"))

        writer.submit(record(0, 1, 1, "tick_end"))
        runCatching { writer.submit(record(1, 2, 2, "tick_end")) }

        assertFailsWith<IllegalStateException> { writer.close() }

        val end = JsonLineEncoder.gson.fromJson(
            Files.readString(session.resolve("session_end.json")),
            JsonObject::class.java
        )
        assertFalse(end.get("clean_shutdown").asBoolean)
        assertEquals("incomplete", end.get("status").asString)
        assertEquals(0, end.get("last_epoch_index").asInt)
        assertEquals(1, end.get("last_server_tick").asInt)
        assertEquals(1, end.get("last_sequence").asInt)
        assertTrue(end.get("failure_reason").asString.contains("epoch output already exists"))
    }

    private fun record(epoch: Long, tick: Long, sequence: Long, type: String): AsyncEpochWriter.QueuedRecord {
        val json = JsonObject().apply {
            addProperty("record_type", type)
            addProperty("server_tick", tick)
            addProperty("sequence", sequence)
        }
        return AsyncEpochWriter.QueuedRecord(epoch, tick, sequence, type, json)
    }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
