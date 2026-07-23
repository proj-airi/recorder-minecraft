package dev.mcdata.recorder.model

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class EpochRotationPolicyTest {
    @Test
    fun `automatic rotation occurs after configured complete ticks`() {
        val policy = EpochRotationPolicy(3)

        assertNull(policy.rotationAtEndTick(1, manualRequested = false))
        assertNull(policy.rotationAtEndTick(2, manualRequested = false))
        val rotation = policy.rotationAtEndTick(3, manualRequested = false)!!

        assertEquals(0, rotation.epochIndex)
        assertEquals("automatic", rotation.reason)
        assertTrue(rotation.automatic)
        assertFalse(rotation.forced)
        policy.advanceAfter(rotation, 3)
        assertEquals(1, policy.currentEpochIndex)
        assertEquals(4, policy.currentEpochStartTick)
        assertNull(policy.rotationAtEndTick(5, manualRequested = false))
        assertEquals("automatic", policy.rotationAtEndTick(6, manualRequested = false)?.reason)
    }

    @Test
    fun `manual rotation restarts automatic interval and remains monotonic`() {
        val policy = EpochRotationPolicy(3)
        val manual = policy.rotationAtEndTick(1, manualRequested = true)!!
        assertEquals("manual", manual.reason)
        assertTrue(manual.forced)
        policy.advanceAfter(manual, 1)

        assertNull(policy.rotationAtEndTick(3, manualRequested = false))
        val automatic = policy.rotationAtEndTick(4, manualRequested = false)!!
        assertEquals(1, automatic.epochIndex)
        policy.advanceAfter(automatic, 4)
        assertEquals(2, policy.currentEpochIndex)
    }

    @Test
    fun `manual request coalesces with due automatic rotation`() {
        val policy = EpochRotationPolicy(2)
        val rotation = policy.rotationAtEndTick(2, manualRequested = true)!!

        assertEquals("manual_and_automatic", rotation.reason)
        assertTrue(rotation.automatic)
        assertTrue(rotation.manual)
        assertTrue(rotation.forced)
    }
}
