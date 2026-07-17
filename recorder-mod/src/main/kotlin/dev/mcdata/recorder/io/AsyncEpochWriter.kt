package dev.mcdata.recorder.io

import com.google.gson.JsonObject
import org.slf4j.Logger
import java.io.BufferedOutputStream
import java.io.FileOutputStream
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.security.DigestOutputStream
import java.security.MessageDigest
import java.time.Instant
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

class AsyncEpochWriter(
    private val sessionId: String,
    private val sessionDirectory: Path,
    queueCapacity: Int,
    private val logger: Logger
) : AutoCloseable {
    private val queue = ArrayBlockingQueue<QueueItem>(queueCapacity)
    private val closing = AtomicBoolean(false)
    private val failure = AtomicReference<Throwable?>()
    private val worker = Thread(::writeLoop, "mc-recorder-writer").apply {
        isDaemon = true
        start()
    }

    fun submit(record: QueuedRecord) {
        check(!closing.get()) { "recorder writer is closing" }
        failure.get()?.let { throw IllegalStateException("recorder writer failed", it) }
        queue.put(QueueItem.Record(record))
        failure.get()?.let { throw IllegalStateException("recorder writer failed", it) }
    }

    override fun close() {
        if (!closing.compareAndSet(false, true)) return
        queue.put(QueueItem.Stop)
        joinWorker()
    }

    fun abort(reason: String) {
        if (!closing.compareAndSet(false, true)) return
        if (worker.isAlive) {
            queue.put(QueueItem.Abort(reason))
            joinWorker(throwOnWorkerFailure = false)
        }
        if (!Files.exists(sessionDirectory.resolve("session_end.json"))) {
            writeSessionEnd(null, clean = false, failureReason = reason)
        }
    }

    private fun joinWorker(throwOnWorkerFailure: Boolean = true) {
        worker.join(30_000)
        check(!worker.isAlive) { "recorder writer did not stop within 30 seconds" }
        if (throwOnWorkerFailure) {
            failure.get()?.let { throw IllegalStateException("recorder writer failed", it) }
        }
    }

    private fun writeLoop() {
        var segment: EpochSegment? = null
        var lastRecord: QueuedRecord? = null
        try {
            while (true) {
                when (val item = queue.take()) {
                    is QueueItem.Record -> {
                        val record = item.value
                        if (segment?.epochIndex != record.epochIndex) {
                            check(segment == null || record.epochIndex > segment.epochIndex) {
                                "records arrived out of epoch order: ${record.epochIndex} after ${segment?.epochIndex}"
                            }
                            segment?.seal()
                            segment = EpochSegment.open(sessionId, sessionDirectory, record.epochIndex)
                        }
                        segment.write(record)
                        lastRecord = record
                    }
                    QueueItem.Stop -> {
                        segment?.seal()
                        writeSessionEnd(lastRecord, clean = true)
                        return
                    }
                    is QueueItem.Abort -> {
                        segment?.abandon()
                        writeSessionEnd(lastRecord, clean = false, failureReason = item.reason)
                        return
                    }
                }
            }
        } catch (throwable: Throwable) {
            failure.set(throwable)
            logger.error("Dataset recorder writer failed; active epoch remains unsealed", throwable)
            runCatching { segment?.abandon() }
        }
    }

    private fun writeSessionEnd(lastRecord: QueuedRecord?, clean: Boolean, failureReason: String? = null) {
        val data = JsonObject().apply {
            addProperty("schema_version", 1)
            addProperty("session_id", sessionId)
            addProperty("closed_at", Instant.now().toString())
            addProperty("clean_shutdown", clean)
            addProperty("status", if (clean) "complete" else "incomplete")
            failureReason?.let { addProperty("failure_reason", it.take(2_048)) }
            addProperty("last_epoch_index", lastRecord?.epochIndex ?: 0)
            addProperty("last_server_tick", lastRecord?.serverTick ?: 0)
            addProperty("last_sequence", lastRecord?.sequence ?: 0)
        }
        atomicWrite(sessionDirectory.resolve("session_end.json"), prettyJson(data))
    }

    data class QueuedRecord(
        val epochIndex: Long,
        val serverTick: Long,
        val sequence: Long,
        val recordType: String,
        val json: JsonObject
    )

    private sealed interface QueueItem {
        data class Record(val value: QueuedRecord) : QueueItem
        data class Abort(val reason: String) : QueueItem
        data object Stop : QueueItem
    }

    private class EpochSegment private constructor(
        private val sessionId: String,
        val epochIndex: Long,
        private val epochDirectory: Path,
        private val partialPath: Path,
        private val fileOutput: FileOutputStream,
        private val digest: MessageDigest,
        private val output: BufferedOutputStream
    ) {
        private var firstTick = Long.MAX_VALUE
        private var lastTick = Long.MIN_VALUE
        private var firstSequence = Long.MAX_VALUE
        private var lastSequence = Long.MIN_VALUE
        private var recordCount = 0L
        private var byteCount = 0L
        private val typeCounts = linkedMapOf<String, Long>()
        private var closed = false

        fun write(record: QueuedRecord) {
            check(!closed)
            val bytes = JsonLineEncoder.encode(record.json)
            output.write(bytes)
            byteCount += bytes.size
            recordCount++
            firstTick = minOf(firstTick, record.serverTick)
            lastTick = maxOf(lastTick, record.serverTick)
            firstSequence = minOf(firstSequence, record.sequence)
            lastSequence = maxOf(lastSequence, record.sequence)
            typeCounts.compute(record.recordType) { _, count -> (count ?: 0L) + 1L }
        }

        fun seal() {
            if (closed) return
            closed = true
            output.flush()
            fileOutput.fd.sync()
            output.close()

            val finalPath = epochDirectory.resolve("events.jsonl")
            atomicMove(partialPath, finalPath)

            val manifest = JsonObject().apply {
                addProperty("schema_version", 1)
                addProperty("session_id", sessionId)
                addProperty("epoch_index", epochIndex)
                addProperty("sealed", true)
                addProperty("record_count", recordCount)
                addProperty("events_bytes", byteCount)
                addProperty("events_sha256", digest.digest().toHex())
                addProperty("first_server_tick", if (recordCount == 0L) 0 else firstTick)
                addProperty("last_server_tick", if (recordCount == 0L) 0 else lastTick)
                addProperty("first_sequence", if (recordCount == 0L) 0 else firstSequence)
                addProperty("last_sequence", if (recordCount == 0L) 0 else lastSequence)
                add("record_counts", JsonObject().also { counts ->
                    typeCounts.forEach { (type, count) -> counts.addProperty(type, count) }
                })
            }
            atomicWrite(epochDirectory.resolve("manifest.json"), prettyJson(manifest))
        }

        fun abandon() {
            if (closed) return
            closed = true
            runCatching { output.flush() }
            runCatching { fileOutput.fd.sync() }
            runCatching { output.close() }
        }

        companion object {
            fun open(sessionId: String, sessionDirectory: Path, epochIndex: Long): EpochSegment {
                val epochDirectory = sessionDirectory.resolve("epochs").resolve("epoch-%06d".format(epochIndex))
                Files.createDirectories(epochDirectory)
                val partialPath = epochDirectory.resolve("events.jsonl.inprogress")
                check(!Files.exists(partialPath) && !Files.exists(epochDirectory.resolve("events.jsonl"))) {
                    "epoch output already exists: $epochDirectory"
                }
                val digest = MessageDigest.getInstance("SHA-256")
                val file = FileOutputStream(partialPath.toFile())
                val output = BufferedOutputStream(DigestOutputStream(file, digest), 256 * 1024)
                return EpochSegment(sessionId, epochIndex, epochDirectory, partialPath, file, digest, output)
            }
        }
    }

    companion object {
        private fun prettyJson(data: JsonObject): ByteArray =
            (com.google.gson.GsonBuilder().setPrettyPrinting().create().toJson(data) + "\n")
                .toByteArray(Charsets.UTF_8)

        private fun atomicWrite(destination: Path, bytes: ByteArray) {
            val partial = destination.resolveSibling(destination.fileName.toString() + ".inprogress")
            Files.write(partial, bytes)
            atomicMove(partial, destination)
        }

        private fun atomicMove(source: Path, destination: Path) {
            try {
                Files.move(source, destination, StandardCopyOption.ATOMIC_MOVE)
            } catch (_: AtomicMoveNotSupportedException) {
                Files.move(source, destination, StandardCopyOption.REPLACE_EXISTING)
            }
        }

        private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
    }
}
