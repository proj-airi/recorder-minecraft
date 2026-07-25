package dev.mcdata.recorder.io

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import org.slf4j.LoggerFactory
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.assertEquals
import kotlin.test.assertFalse

class AsyncPlayWriterTest {
    @TempDir
    lateinit var temporary: Path

    @Test
    fun `writes one buffered events stream without epochs or manifests`() {
        val events = temporary.resolve("capture/events.jsonl")
        val writer = AsyncPlayWriter(events, 1_024, LoggerFactory.getLogger("test"))
        writer.submit(record("player_join", 10, 1))
        writer.submit(record("player_state", 11, 2))

        writer.close()

        val records = Files.readAllLines(events).map { JsonParser.parseString(it).asJsonObject }
        assertEquals(listOf("player_join", "player_state"), records.map { it.get("record_type").asString })
        assertEquals(listOf(1L, 2L), records.map { it.get("sequence").asLong })
        assertFalse(Files.exists(temporary.resolve("epochs")))
        assertFalse(Files.exists(temporary.resolve("manifest.json")))
    }

    private fun record(type: String, tick: Long, sequence: Long): JsonObject = JsonObject().apply {
        addProperty("schema_version", 1)
        addProperty("record_type", type)
        addProperty("server_tick", tick)
        addProperty("sequence", sequence)
    }
}
