package dev.mcdata.recorder.io

import java.nio.file.Path
import java.text.Normalizer
import java.util.UUID

data class PlayIdentity(
    val serverName: String,
    val serverInstanceId: UUID,
    val playerName: String,
    val playerUuid: UUID,
    val startedAt: String,
    val connectionId: UUID
)

data class PlayPaths(val root: Path) {
    val metadata: Path = root.resolve("metadata.json")
    val capture: Path = root.resolve("capture")
    val events: Path = capture.resolve("events.jsonl")
    val replayWorking: Path = capture.resolve("replay")
    val replay: Path = capture.resolve("replay.zip")
    val actions: Path = root.resolve("actions.jsonl")
    val scene: Path = root.resolve("scene.sqlite3")
    val renders: Path = root.resolve("renders")
    val fpvFrames: Path = renders.resolve("fpv_frames")
}

object ArtifactLayout {
    const val VERSION = "v1"
    private val startPattern = Regex("^[0-9]{8}T[0-9]{6}(?:\\.[0-9]{1,9})?Z$")

    fun play(artifactsRoot: Path, identity: PlayIdentity): PlayPaths {
        val serverName = displayName(identity.serverName, "server name")
        val playerName = displayName(identity.playerName, "player name")
        require(startPattern.matches(identity.startedAt)) {
            "play start must use UTC filesystem form YYYYMMDDTHHMMSS[.fraction]Z"
        }
        return PlayPaths(
            artifactsRoot
                .resolve(VERSION)
                .resolve("$serverName--${identity.serverInstanceId}")
                .resolve("players")
                .resolve("$playerName--${identity.playerUuid}")
                .resolve("plays")
                .resolve("${identity.startedAt}--${identity.connectionId}")
                .toAbsolutePath()
                .normalize()
        )
    }

    private fun displayName(value: String, label: String): String {
        require(value.isNotBlank() && value.toByteArray(Charsets.UTF_8).size <= 180) {
            "$label must contain 1 to 180 UTF-8 bytes"
        }
        require(value.none { it == '/' || it == '\\' || it.code < 32 || it.code == 127 }) {
            "$label contains a character unsafe for artifact paths"
        }
        require(Normalizer.normalize(value, Normalizer.Form.NFC) == value) {
            "$label must use NFC Unicode normalization"
        }
        return value
    }
}
