package dev.mcdata.recorder.capture

import com.mojang.serialization.DynamicOps
import dev.mcdata.recorder.config.RecorderConfig
import dev.mcdata.recorder.io.AsyncWorldWriter
import dev.mcdata.recorder.io.WorldSessionFiles
import dev.mcdata.recorder.mixin.ChunkMapAccessor
import dev.recorderminecraft.artifacts.v1.BlockPosition
import dev.recorderminecraft.artifacts.v1.ContainerRemoved
import dev.recorderminecraft.artifacts.v1.ContainerSnapshot
import dev.recorderminecraft.artifacts.v1.WorldEvent
import dev.recorderminecraft.artifacts.v1.WorldEventIdentity
import dev.recorderminecraft.artifacts.v1.WorldTruthReference
import net.minecraft.core.BlockPos
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.nbt.NbtOps
import net.minecraft.nbt.Tag
import net.minecraft.server.MinecraftServer
import net.minecraft.server.level.ChunkHolder
import net.minecraft.server.level.ServerLevel
import net.minecraft.world.Container
import net.minecraft.world.level.ChunkPos
import net.minecraft.world.level.block.entity.BlockEntity
import org.slf4j.Logger
import java.time.Instant

/**
 * Session-level stream of authoritative container block entity contents.
 *
 * Every method runs on the server thread (Fabric block entity events, the `setChanged` and
 * `setLoaded` hooks, and tick callbacks); `@Synchronized` only guards against a hook reached from
 * an unexpected thread. Ticks are supplied by the caller from the Play capture clock so world and
 * Play records share `server_tick`.
 */
class WorldCapture private constructor(
    private val sessionId: String,
    private val files: WorldSessionFiles,
    private val writer: AsyncWorldWriter,
    private val includeComponents: Boolean
) {
    private val tracker = ContainerTracker<ContainerKey, BlockEntity>()
    private val unloadingChunks = HashSet<ChunkKey>()
    private var sequence = 0L
    private var closed = false

    val reference: WorldTruthReference get() = files.reference

    /**
     * Snapshots containers that were loaded before the stream existed.
     *
     * Spawn chunks load during server startup, before SERVER_STARTED creates the session, so their
     * BLOCK_ENTITY_LOAD events are never seen. Enumerating visible full chunks here covers them;
     * no pre-session buffering is needed because only the state at session start matters.
     */
    @Synchronized
    fun sessionStart(server: MinecraftServer, tick: Long) {
        for (level in server.allLevels) {
            val ops = componentOps(level)
            val containers = mutableListOf<BlockEntity>()
            val chunks = (level.chunkSource.chunkMap as ChunkMapAccessor).mcRecorderVisibleChunks()
            for (holder in chunks) {
                val chunk = holder.fullChunkFuture.getNow(ChunkHolder.UNLOADED_LEVEL_CHUNK).orElse(null) ?: continue
                chunk.blockEntities.values.filterTo(containers) { it is Container }
            }
            containers.sortBy { it.blockPos.asLong() }
            containers.forEach { snapshot(level, it, ContainerSnapshot.Reason.REASON_SESSION_START, tick, ops) }
        }
    }

    @Synchronized
    fun blockEntityLoaded(level: ServerLevel, blockEntity: BlockEntity) {
        if (closed || blockEntity !is Container) return
        // Contents are read at end of tick: a placed container is still empty at this point, and
        // a chunk-loaded one may receive further changes in the same tick.
        tracker.markLoaded(ContainerKey(level, blockEntity.blockPos.asLong()), blockEntity)
    }

    @Synchronized
    fun blockEntityChanged(level: ServerLevel, pos: BlockPos) {
        if (closed) return
        tracker.markChanged(ContainerKey(level, pos.asLong()))
    }

    @Synchronized
    fun chunkLoadedChanged(level: ServerLevel, chunkPos: Long, loaded: Boolean) {
        if (closed) return
        val key = ChunkKey(level, chunkPos)
        if (loaded) unloadingChunks.remove(key) else unloadingChunks.add(key)
    }

    @Synchronized
    fun blockEntityUnloaded(level: ServerLevel, blockEntity: BlockEntity, tick: Long) {
        if (closed) return
        val key = ContainerKey(level, blockEntity.blockPos.asLong())
        // Write the final contents before the removal so the last snapshot reflects the state the
        // container left the world with (for example, items moved in just before it was broken).
        tracker.takePendingFor(key, blockEntity)?.let { pending ->
            if (blockEntity is Container) snapshot(level, blockEntity, pending.reason, tick, componentOps(level))
        }
        if (!tracker.forget(key)) return
        val chunk = ChunkKey(level, ChunkPos.asLong(blockEntity.blockPos))
        val cause = if (chunk in unloadingChunks) {
            ContainerRemoved.Cause.CAUSE_CHUNK_UNLOADED
        } else {
            ContainerRemoved.Cause.CAUSE_DESTROYED
        }
        emit(tick) { event ->
            event.containerRemoved = ContainerRemoved.newBuilder()
                .setDimension(level.dimension().location().toString())
                .setBlockPos(position(blockEntity.blockPos))
                .setBlockEntityType(typeId(blockEntity))
                .setCause(cause)
                .build()
        }
    }

    /** Writes one snapshot per changed or loaded position. Called before the Play end-of-tick work. */
    @Synchronized
    fun endTick(tick: Long) {
        if (closed) return
        flushPending(tick)
        unloadingChunks.clear()
    }

    @Synchronized
    fun close(tick: Long, terminalReason: String) {
        if (closed) return
        // Changes applied between the last tick and shutdown (packet handling, commands) are still
        // pending; they belong to the upcoming tick like any other between-tick event.
        flushPending(tick)
        closed = true
        try {
            writer.close()
        } catch (throwable: Throwable) {
            files.failed(Instant.now(), tick, failureReason(throwable))
            throw throwable
        }
        files.closed(Instant.now(), tick, terminalReason)
    }

    /** Contains a failure to the world stream: keeps what is durable and marks coverage end. */
    fun fail(failure: Throwable, tick: Long, logger: Logger) {
        synchronized(this) {
            if (closed) return
            closed = true
        }
        writer.abort()
        runCatching { files.failed(Instant.now(), tick, failureReason(failure)) }.onFailure {
            logger.error("Could not mark world stream failed in {}", files.paths.metadata, it)
        }
    }

    private fun failureReason(failure: Throwable): String =
        "${failure::class.java.simpleName}: ${failure.message ?: "world stream failure"}"

    private fun flushPending(tick: Long) {
        for ((key, entry) in tracker.drainPending()) {
            val blockEntity = entry.blockEntity?.takeUnless { it.isRemoved } ?: loadedBlockEntity(key) ?: continue
            if (blockEntity !is Container) continue
            snapshot(key.level, blockEntity, entry.reason, tick, componentOps(key.level))
        }
    }

    /** Resolves a changed position without loading its chunk. */
    private fun loadedBlockEntity(key: ContainerKey): BlockEntity? {
        val pos = BlockPos.of(key.pos)
        val chunk = key.level.chunkSource.getChunkNow(pos.x shr 4, pos.z shr 4) ?: return null
        return chunk.getBlockEntity(pos)?.takeUnless { it.isRemoved }
    }

    private fun snapshot(
        level: ServerLevel,
        blockEntity: BlockEntity,
        reason: ContainerSnapshot.Reason,
        tick: Long,
        ops: DynamicOps<Tag>?
    ) {
        val snapshot = ContainerSnapshot.newBuilder()
            .setDimension(level.dimension().location().toString())
            .setBlockPos(position(blockEntity.blockPos))
            .setBlockEntityType(typeId(blockEntity))
        ContainerContents.fill(blockEntity as Container, ops, snapshot)
        val fingerprint = ContainerTracker.fingerprint(snapshot.build())
        if (!tracker.admit(ContainerKey(level, blockEntity.blockPos.asLong()), reason, fingerprint)) return
        snapshot.reason = reason
        emit(tick) { it.containerSnapshot = snapshot.build() }
    }

    private fun emit(tick: Long, payload: (WorldEvent.Builder) -> Unit) {
        sequence++
        val event = WorldEvent.newBuilder().setIdentity(
            WorldEventIdentity.newBuilder()
                .setSchemaVersion(1)
                .setSessionId(sessionId)
                .setServerTick(tick)
                .setSequence(sequence)
                .setRecordedAtNs(System.nanoTime())
                .setRecordedAtUnixMs(System.currentTimeMillis())
        )
        payload(event)
        writer.submit(event.build())
    }

    private fun componentOps(level: ServerLevel): DynamicOps<Tag>? =
        if (includeComponents) level.registryAccess().createSerializationContext(NbtOps.INSTANCE) else null

    private fun position(pos: BlockPos): BlockPosition =
        BlockPosition.newBuilder().setX(pos.x).setY(pos.y).setZ(pos.z).build()

    private fun typeId(blockEntity: BlockEntity): String =
        BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(blockEntity.type)?.toString() ?: "unknown"

    // ServerLevel uses identity equality, so a key never matches a level from an earlier
    // integrated-server world.
    private data class ContainerKey(val level: ServerLevel, val pos: Long)
    private data class ChunkKey(val level: ServerLevel, val pos: Long)

    companion object {
        fun open(
            config: RecorderConfig,
            sessionId: String,
            startServerTick: Long,
            logger: Logger
        ): WorldCapture {
            val files = WorldSessionFiles.create(config, sessionId, Instant.now(), startServerTick)
            val writer = AsyncWorldWriter(files.paths.events, config.writerQueueCapacity, logger, "world")
            logger.info("Started world stream {} in {}", sessionId, files.paths.root)
            return WorldCapture(sessionId, files, writer, config.includeInventoryComponents)
        }
    }
}
