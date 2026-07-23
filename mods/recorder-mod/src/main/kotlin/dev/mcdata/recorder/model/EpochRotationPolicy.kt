package dev.mcdata.recorder.model

/**
 * Assigns monotonically increasing epoch numbers to explicit end-of-tick boundaries.
 * A manual boundary starts a fresh automatic interval, so no epoch spans more than
 * [epochTicks] complete server ticks.
 */
class EpochRotationPolicy(private val epochTicks: Long) {
    var currentEpochIndex: Long = 0
        private set
    var currentEpochStartTick: Long = 1
        private set

    init {
        require(epochTicks > 0) { "epochTicks must be positive" }
    }

    fun rotationAtEndTick(serverTick: Long, manualRequested: Boolean): Rotation? {
        require(serverTick >= 0) { "serverTick must not be negative" }
        val automatic = serverTick >= currentEpochStartTick + epochTicks - 1
        if (!automatic && !manualRequested) return null
        return Rotation(
            epochIndex = currentEpochIndex,
            reason = when {
                automatic && manualRequested -> "manual_and_automatic"
                manualRequested -> "manual"
                else -> "automatic"
            },
            forced = manualRequested,
            automatic = automatic,
            manual = manualRequested
        )
    }

    fun advanceAfter(rotation: Rotation, serverTick: Long) {
        require(rotation.epochIndex == currentEpochIndex) {
            "rotation targets epoch ${rotation.epochIndex}, current epoch is $currentEpochIndex"
        }
        require(serverTick >= 0) { "serverTick must not be negative" }
        currentEpochIndex++
        currentEpochStartTick = serverTick + 1
    }

    data class Rotation(
        val epochIndex: Long,
        val reason: String,
        val forced: Boolean,
        val automatic: Boolean,
        val manual: Boolean
    )
}
