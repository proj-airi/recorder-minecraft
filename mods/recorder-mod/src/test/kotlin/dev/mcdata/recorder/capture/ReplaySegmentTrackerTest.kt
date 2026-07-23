package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import org.junit.jupiter.api.Test
import java.util.UUID
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ReplaySegmentTrackerTest {
    @Test
    fun `recorder created before join receives exact connection metadata`() {
        val tracker = ReplaySegmentTracker(SESSION)
        val player = UUID.fromString(PLAYER)
        val metadataProvider = tracker.segmentStarted(Any(), player, "flashback")

        tracker.connectionStarted(player, CONNECTION_ONE)
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
        tracker.connectionStarted(player, CONNECTION_ONE)
        tracker.connectionEnded(CONNECTION_ONE)
        val newMetadata = tracker.segmentStarted(Any(), player, "flashback")
        tracker.connectionStarted(player, CONNECTION_TWO)

        val old = JsonObject().also(oldMetadata)
        val newer = JsonObject().also(newMetadata)
        assertEquals(CONNECTION_ONE, old.getAsJsonObject("mc_recorder").get("connection_id").asString)
        assertEquals(CONNECTION_TWO, newer.getAsJsonObject("mc_recorder").get("connection_id").asString)
        assertEquals(1, newer.getAsJsonObject("mc_recorder").get("segment_ordinal").asLong)
    }

    @Test
    fun `non flashback and unbound segments only publish known identity`() {
        val tracker = ReplaySegmentTracker(SESSION)
        val metadata = JsonObject()
        tracker.segmentStarted(Any(), UUID.fromString(PLAYER), "other")(metadata)

        val embedded = metadata.getAsJsonObject("mc_recorder")
        assertFalse(embedded.has("connection_id"))
        assertFalse(embedded.has("flashback_capture_contract"))
        assertNull(embedded.get("connection_id"))
    }

    companion object {
        private const val SESSION = "session-20260721"
        private const val PLAYER = "11111111-1111-1111-1111-111111111111"
        private const val CONNECTION_ONE = "22222222-2222-2222-2222-222222222222"
        private const val CONNECTION_TWO = "33333333-3333-3333-3333-333333333333"
    }
}
