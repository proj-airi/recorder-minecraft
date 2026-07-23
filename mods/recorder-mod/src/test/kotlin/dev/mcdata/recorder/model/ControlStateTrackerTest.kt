package dev.mcdata.recorder.model

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class ControlStateTrackerTest {
    @Test
    fun `movement state persists and camera deltas wrap`() {
        val tracker = ControlStateTracker()
        tracker.updateInput(InputFlags(forward = true, sprint = true))

        val first = tracker.endTick(179.0F, 10.0F, 2)
        val second = tracker.endTick(-179.0F, 7.5F, 2)

        assertTrue(first.input.forward)
        assertTrue(second.input.sprint)
        assertEquals(0.0F, first.deltaYaw)
        assertEquals(2.0F, second.deltaYaw)
        assertEquals(-2.5F, second.deltaPitch)
        assertEquals(2, second.selectedSlot)
    }

    @Test
    fun `wrap degrees has a half open range`() {
        assertEquals(-180.0F, ControlStateTracker.wrapDegrees(180.0F))
        assertEquals(-179.0F, ControlStateTracker.wrapDegrees(181.0F))
        assertEquals(179.0F, ControlStateTracker.wrapDegrees(-181.0F))
    }
}
