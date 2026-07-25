package dev.mcdata.recorder.io

import com.google.gson.JsonObject
import org.slf4j.Logger
import java.io.BufferedOutputStream
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Single-owner asynchronous append stream for one player connection.
 *
 * The canonical events file exists from connection start and is never rotated. A clean close
 * drains the queue, flushes the buffer, and fsyncs the file before play metadata may be completed.
 */
class AsyncPlayWriter(
    private val events: Path,
    queueCapacity: Int,
    private val logger: Logger
) : AutoCloseable {
    private val queue = ArrayBlockingQueue<QueueItem>(queueCapacity)
    private val enqueueTransition = Any()
    private val closing = AtomicBoolean(false)
    private val failure = AtomicReference<Throwable?>()
    private val worker = Thread(::writeLoop, "mc-recorder-play-writer").apply {
        isDaemon = true
        start()
    }

    fun submit(record: JsonObject) {
        synchronized(enqueueTransition) {
            check(!closing.get()) { "play writer is closing" }
            offerWhileHealthy(QueueItem.Record(record), "record")
            failure.get()?.let { throw IllegalStateException("play writer failed", it) }
        }
    }

    override fun close() {
        val initiated = synchronized(enqueueTransition) {
            closing.compareAndSet(false, true)
        }
        if (!initiated) return
        try {
            offerWhileHealthy(QueueItem.Stop, "close marker")
            joinWorker()
        } catch (throwable: Throwable) {
            worker.interrupt()
            throw throwable
        }
    }

    fun abort() {
        val initiated = synchronized(enqueueTransition) {
            closing.compareAndSet(false, true)
        }
        if (!initiated || !worker.isAlive) return
        runCatching {
            offerWhileHealthy(QueueItem.Abort, "abort marker")
            joinWorker(throwOnFailure = false)
        }.onFailure {
            worker.interrupt()
            logger.error("Could not stop play writer cleanly while aborting {}", events, it)
        }
    }

    private fun offerWhileHealthy(item: QueueItem, description: String) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(QUEUE_TIMEOUT_SECONDS)
        while (true) {
            failure.get()?.let { throw IllegalStateException("play writer failed", it) }
            check(worker.isAlive) { "play writer stopped before accepting $description" }
            val remaining = deadline - System.nanoTime()
            check(remaining > 0) { "play writer queue timed out while accepting $description" }
            if (queue.offer(item, minOf(remaining, TimeUnit.MILLISECONDS.toNanos(250)), TimeUnit.NANOSECONDS)) {
                return
            }
            if (item is QueueItem.Record) check(!closing.get()) { "play writer is closing" }
        }
    }

    private fun joinWorker(throwOnFailure: Boolean = true) {
        worker.join(TimeUnit.SECONDS.toMillis(QUEUE_TIMEOUT_SECONDS))
        check(!worker.isAlive) { "play writer did not stop within $QUEUE_TIMEOUT_SECONDS seconds" }
        if (throwOnFailure) failure.get()?.let { throw IllegalStateException("play writer failed", it) }
    }

    private fun writeLoop() {
        try {
            Files.createDirectories(events.parent)
            FileOutputStream(events.toFile(), false).use { file ->
                BufferedOutputStream(file, BUFFER_BYTES).use { output ->
                    while (true) {
                        when (val item = queue.take()) {
                            is QueueItem.Record -> output.write(JsonLineEncoder.encode(item.value))
                            QueueItem.Stop -> {
                                output.flush()
                                file.fd.sync()
                                return
                            }
                            QueueItem.Abort -> {
                                output.flush()
                                file.fd.sync()
                                return
                            }
                        }
                    }
                }
            }
        } catch (throwable: Throwable) {
            failure.set(throwable)
            logger.error("Recorder play writer failed for {}", events, throwable)
        }
    }

    private sealed interface QueueItem {
        data class Record(val value: JsonObject) : QueueItem
        data object Stop : QueueItem
        data object Abort : QueueItem
    }

    companion object {
        private const val QUEUE_TIMEOUT_SECONDS = 30L
        private const val BUFFER_BYTES = 256 * 1024
    }
}
