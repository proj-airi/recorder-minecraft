package dev.mcdata.recorder.model

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class TimelineTest {
    @Test
    fun `epoch zero contains session records and the configured number of ticks`() {
        assertEquals(0, Timeline.epochIndex(0, 6_000))
        assertEquals(0, Timeline.epochIndex(1, 6_000))
        assertEquals(0, Timeline.epochIndex(6_000, 6_000))
        assertEquals(1, Timeline.epochIndex(6_001, 6_000))
    }

    @Test
    fun `invalid values are rejected`() {
        assertFailsWith<IllegalArgumentException> { Timeline.epochIndex(-1, 20) }
        assertFailsWith<IllegalArgumentException> { Timeline.epochIndex(0, 0) }
    }
}
