package dev.mcdata.recorder.model

import kotlin.math.abs

class ControlStateTracker {
    private var input = InputFlags()
    private var previousYaw: Float? = null
    private var previousPitch: Float? = null
    private var selectedSlot: Int = 0

    fun updateInput(input: InputFlags) {
        this.input = input
    }

    fun updateSelectedSlot(slot: Int) {
        selectedSlot = slot
    }

    fun endTick(yaw: Float, pitch: Float, authoritativeSelectedSlot: Int): ControlTick {
        selectedSlot = authoritativeSelectedSlot
        val deltaYaw = previousYaw?.let { wrapDegrees(yaw - it) } ?: 0.0F
        val deltaPitch = previousPitch?.let { pitch - it } ?: 0.0F
        previousYaw = yaw
        previousPitch = pitch
        return ControlTick(input, yaw, pitch, cleanZero(deltaYaw), cleanZero(deltaPitch), selectedSlot)
    }

    data class ControlTick(
        val input: InputFlags,
        val yaw: Float,
        val pitch: Float,
        val deltaYaw: Float,
        val deltaPitch: Float,
        val selectedSlot: Int
    )

    companion object {
        fun wrapDegrees(value: Float): Float {
            var wrapped = value % 360.0F
            if (wrapped >= 180.0F) wrapped -= 360.0F
            if (wrapped < -180.0F) wrapped += 360.0F
            return wrapped
        }

        private fun cleanZero(value: Float): Float = if (abs(value) < 1.0e-7F) 0.0F else value
    }
}
