package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.ContainerSnapshot
import java.security.MessageDigest

/**
 * Position-keyed bookkeeping for the world stream, free of Minecraft types so it can be tested.
 *
 * [K] identifies a container position (dimension plus block position). [B] is the live block entity
 * reference when one is known at mark time.
 *
 * Two independent maps are kept:
 * - `pending` collects positions to snapshot at the end of the tick. Many `setChanged` calls on the
 *   same position within one tick collapse into a single entry.
 * - `written` holds the fingerprint of the last snapshot written per position. It lets frequent
 *   no-op changes (hoppers that push and pull the same item) be dropped, and records which positions
 *   need a `ContainerRemoved` when they leave the world.
 */
class ContainerTracker<K : Any, B : Any> {
    data class Pending<B>(val reason: ContainerSnapshot.Reason, val blockEntity: B?)

    private val pending = LinkedHashMap<K, Pending<B>>()
    private val written = HashMap<K, ByteArray>()

    val writtenCount: Int get() = written.size

    fun markLoaded(key: K, blockEntity: B) {
        // LOADED wins over a pending CHANGED: the reader has not seen this block entity yet.
        pending[key] = Pending(ContainerSnapshot.Reason.REASON_LOADED, blockEntity)
    }

    fun markChanged(key: K) {
        pending.putIfAbsent(key, Pending(ContainerSnapshot.Reason.REASON_CHANGED, null))
    }

    /** Returns and clears every pending position in first-marked order. */
    fun drainPending(): List<Pair<K, Pending<B>>> {
        if (pending.isEmpty()) return emptyList()
        val drained = pending.entries.map { it.key to it.value }
        pending.clear()
        return drained
    }

    /**
     * Removes the pending entry for [key] when it belongs to [removed].
     *
     * Fabric fires LOAD for a replacement block entity before UNLOAD for the one it replaced, so a
     * pending LOADED entry for a different instance must survive the old instance's removal.
     */
    fun takePendingFor(key: K, removed: B): Pending<B>? {
        val entry = pending[key] ?: return null
        if (entry.blockEntity != null && entry.blockEntity !== removed) return null
        return pending.remove(key)
    }

    /**
     * Decides whether a snapshot must be written and, if so, remembers its fingerprint.
     *
     * Only CHANGED snapshots are deduplicated. SESSION_START and LOADED always start a new validity
     * interval for the position, so they are written even when contents match an older snapshot.
     */
    fun admit(key: K, reason: ContainerSnapshot.Reason, fingerprint: ByteArray): Boolean {
        val previous = written[key]
        if (reason == ContainerSnapshot.Reason.REASON_CHANGED && previous != null && previous.contentEquals(fingerprint)) {
            return false
        }
        written[key] = fingerprint
        return true
    }

    /** Forgets the last written snapshot. Returns whether a `ContainerRemoved` is owed to readers. */
    fun forget(key: K): Boolean = written.remove(key) != null

    companion object {
        /**
         * Content fingerprint of a snapshot, excluding its [ContainerSnapshot.Reason].
         *
         * The generated message has no map fields, so protobuf-java serializes equal content to
         * equal bytes within one process, which is all the per-session comparison requires.
         */
        fun fingerprint(snapshot: ContainerSnapshot): ByteArray {
            val content = snapshot.toBuilder().clearReason().build()
            return MessageDigest.getInstance("SHA-256").digest(content.toByteArray())
        }
    }
}
