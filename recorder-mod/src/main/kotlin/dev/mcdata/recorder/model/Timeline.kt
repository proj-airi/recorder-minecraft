package dev.mcdata.recorder.model

object Timeline {
    fun epochIndex(serverTick: Long, epochTicks: Long): Long {
        require(serverTick >= 0) { "serverTick must not be negative" }
        require(epochTicks > 0) { "epochTicks must be positive" }
        return if (serverTick == 0L) 0 else (serverTick - 1) / epochTicks
    }
}
