package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.ContainerSnapshot
import dev.recorderminecraft.artifacts.v1.ContainerSnapshot.Reason
import dev.recorderminecraft.artifacts.v1.InventorySlot
import org.junit.jupiter.api.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertSame
import kotlin.test.assertTrue

class ContainerTrackerTest {
    private class Entity(val name: String)

    @Test
    fun `repeated changes at one position collapse into one pending snapshot`() {
        val tracker = ContainerTracker<String, Entity>()
        repeat(5) { tracker.markChanged("overworld:1") }
        tracker.markChanged("overworld:2")
        tracker.markChanged("overworld:1")

        val drained = tracker.drainPending()
        assertEquals(listOf("overworld:1", "overworld:2"), drained.map { it.first })
        assertTrue(drained.all { it.second.reason == Reason.REASON_CHANGED })
        assertTrue(tracker.drainPending().isEmpty())
    }

    @Test
    fun `loaded takes precedence over a pending change`() {
        val tracker = ContainerTracker<String, Entity>()
        val chest = Entity("chest")
        tracker.markChanged("p")
        tracker.markLoaded("p", chest)
        tracker.markChanged("p")

        val (_, pending) = tracker.drainPending().single()
        assertEquals(Reason.REASON_LOADED, pending.reason)
        assertSame(chest, pending.blockEntity)
    }

    @Test
    fun `unchanged contents are not written again but a reload is`() {
        val tracker = ContainerTracker<String, Entity>()
        val diamonds = ContainerTracker.fingerprint(snapshot(Reason.REASON_CHANGED, "minecraft:diamond"))
        val empty = ContainerTracker.fingerprint(snapshot(Reason.REASON_CHANGED, null))

        assertTrue(tracker.admit("p", Reason.REASON_SESSION_START, empty))
        assertFalse(tracker.admit("p", Reason.REASON_CHANGED, empty))
        assertTrue(tracker.admit("p", Reason.REASON_CHANGED, diamonds))
        assertFalse(tracker.admit("p", Reason.REASON_CHANGED, diamonds))
        // A new validity interval always starts with a snapshot, even with identical contents.
        assertTrue(tracker.admit("p", Reason.REASON_LOADED, diamonds))
        assertEquals(1, tracker.writtenCount)
    }

    @Test
    fun `fingerprint ignores the reason but not the contents`() {
        assertContentEquals(
            ContainerTracker.fingerprint(snapshot(Reason.REASON_SESSION_START, "minecraft:diamond")),
            ContainerTracker.fingerprint(snapshot(Reason.REASON_CHANGED, "minecraft:diamond"))
        )
        assertFalse(
            ContainerTracker.fingerprint(snapshot(Reason.REASON_CHANGED, "minecraft:diamond"))
                .contentEquals(ContainerTracker.fingerprint(snapshot(Reason.REASON_CHANGED, "minecraft:emerald")))
        )
    }

    @Test
    fun `removal is owed only for positions that were written`() {
        val tracker = ContainerTracker<String, Entity>()
        assertFalse(tracker.forget("never-written"))
        tracker.admit("p", Reason.REASON_LOADED, byteArrayOf(1))
        assertTrue(tracker.forget("p"))
        assertFalse(tracker.forget("p"))
    }

    @Test
    fun `a replacement load survives removal of the block entity it replaced`() {
        val tracker = ContainerTracker<String, Entity>()
        val old = Entity("old")
        val replacement = Entity("replacement")
        // Fabric fires LOAD for the replacement before UNLOAD for the replaced instance.
        tracker.markLoaded("p", replacement)

        assertNull(tracker.takePendingFor("p", old))
        assertSame(replacement, tracker.takePendingFor("p", replacement)?.blockEntity)
    }

    @Test
    fun `a pending change is flushed for the block entity that leaves`() {
        val tracker = ContainerTracker<String, Entity>()
        tracker.markChanged("p")
        assertEquals(Reason.REASON_CHANGED, tracker.takePendingFor("p", Entity("chest"))?.reason)
        assertTrue(tracker.drainPending().isEmpty())
    }

    private fun snapshot(reason: Reason, item: String?): ContainerSnapshot {
        val value = ContainerSnapshot.newBuilder()
            .setDimension("minecraft:overworld")
            .setBlockEntityType("minecraft:chest")
            .setReason(reason)
            .setContentsState(ContainerSnapshot.ContentsState.CONTENTS_STATE_KNOWN)
            .setContainerSize(27)
        item?.let { value.addSlots(InventorySlot.newBuilder().setSlot(0).setItemId(it).setCount(1)) }
        return value.build()
    }
}
