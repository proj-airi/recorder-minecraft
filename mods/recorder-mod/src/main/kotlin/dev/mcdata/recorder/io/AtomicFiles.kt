package dev.mcdata.recorder.io

import com.google.protobuf.MessageOrBuilder
import com.google.protobuf.Timestamp
import com.google.protobuf.util.JsonFormat
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.time.Instant

/** Metadata publication shared by Play and world session directories. */
internal object AtomicFiles {
    fun writeProtoJson(destination: Path, value: MessageOrBuilder) {
        val partial = destination.resolveSibling(destination.fileName.toString() + ".inprogress")
        Files.writeString(partial, JsonFormat.printer().print(value) + "\n")
        move(partial, destination, replace = true)
    }

    fun move(source: Path, destination: Path, replace: Boolean = false) {
        val options = mutableListOf(StandardCopyOption.ATOMIC_MOVE)
        if (replace) options.add(StandardCopyOption.REPLACE_EXISTING)
        try {
            Files.move(source, destination, *options.toTypedArray())
        } catch (_: AtomicMoveNotSupportedException) {
            val fallback = if (replace) arrayOf(StandardCopyOption.REPLACE_EXISTING) else emptyArray()
            Files.move(source, destination, *fallback)
        }
    }

    fun timestamp(value: Instant): Timestamp = Timestamp.newBuilder()
        .setSeconds(value.epochSecond)
        .setNanos(value.nano)
        .build()
}
