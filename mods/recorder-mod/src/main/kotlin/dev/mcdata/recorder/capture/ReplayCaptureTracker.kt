package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import dev.mcdata.recorder.io.PlayFiles
import net.casual.arcade.replay.recorder.ReplayRecorder
import net.casual.arcade.replay.recorder.player.ReplayPlayerRecorder
import java.nio.file.Path
import java.util.IdentityHashMap
import java.util.UUID

/** Binds one ServerReplay player recorder to one connection-local capture. */
class ReplayCaptureTracker(
    private val sessionId: String,
    private val captureForPlayer: (UUID) -> PlayFiles?
) {
    private val capturesByRecorder = IdentityHashMap<Any, ReplayCapture>()

    @Synchronized
    fun recorderStarted(recorder: ReplayRecorder) {
        if (recorder !is ReplayPlayerRecorder) return
        val metadataProvider = captureStarted(
            recorderIdentity = recorder,
            playerUuid = recorder.recordingPlayerUUID,
            replayFormat = recorder.format.name.lowercase(),
            workingDirectory = recorder.location
        )
        recorder.addMetadataProvider(metadataProvider)
    }

    @Synchronized
    internal fun captureStarted(
        recorderIdentity: Any,
        playerUuid: UUID,
        replayFormat: String,
        workingDirectory: Path
    ): (JsonObject) -> Unit {
        require(replayFormat == FLASHBACK_FORMAT) {
            "artifacts/v1 requires Flashback player replays"
        }
        check(!capturesByRecorder.containsKey(recorderIdentity)) { "recorder instance was already registered" }
        val play = checkNotNull(captureForPlayer(playerUuid)) {
            "ServerReplay started before a play capture path was allocated"
        }
        check(capturesByRecorder.values.none { it.playFiles === play }) {
            "play capture already has a ServerReplay recorder; replay rotation is disabled"
        }
        val working = workingDirectory.toAbsolutePath().normalize()
        require(working.parent == play.paths.replayWorking) {
            "ServerReplay working directory must be one child of ${play.paths.replayWorking}: $working"
        }
        val capture = ReplayCapture(
            replayId = UUID.randomUUID().toString(),
            playerUuid = playerUuid,
            playFiles = play,
            workingDirectory = working
        )
        capturesByRecorder[recorderIdentity] = capture
        return { metadata -> addArchiveMetadata(capture, metadata) }
    }

    @Synchronized
    fun recorderSaved(recorder: ReplayRecorder, output: Path) {
        captureSaved(recorder, output)
    }

    @Synchronized
    internal fun captureSaved(recorderIdentity: Any, output: Path) {
        val capture = checkNotNull(capturesByRecorder[recorderIdentity]) {
            "completed recorder was not registered"
        }
        val expected = capture.workingDirectory.resolveSibling(
            capture.workingDirectory.fileName.toString() + ".zip"
        )
        require(output.toAbsolutePath().normalize() == expected) {
            "ServerReplay output does not match its working directory: $output"
        }
        capture.playFiles.replaySaved(output)
        capture.saved = true
    }

    @Synchronized
    fun recorderClosed(recorder: ReplayRecorder) {
        captureClosed(recorder)
    }

    @Synchronized
    internal fun captureClosed(recorderIdentity: Any) {
        val capture = checkNotNull(capturesByRecorder.remove(recorderIdentity)) {
            "closed recorder was not registered"
        }
        check(capture.saved) { "Flashback recorder closed without a saved replay" }
        capture.playFiles.replayWriterClosed()
    }

    private fun addArchiveMetadata(capture: ReplayCapture, metadata: JsonObject) {
        metadata.add("mc_recorder", JsonObject().apply {
            addProperty("schema_version", REPLAY_METADATA_SCHEMA_VERSION)
            addProperty("session_id", sessionId)
            addProperty("replay_id", capture.replayId)
            addProperty("player_uuid", capture.playerUuid.toString())
            addProperty("connection_id", capture.playFiles.connectionId())
            addProperty("capture_path", "capture/replay.zip")
            addProperty("hotbar_snapshot_contract", ReplayPacketSnapshots.HOTBAR_SNAPSHOT_CONTRACT)
            addProperty(
                "flashback_capture_contract",
                ReplayScenePacketContract.FLASHBACK_CAPTURE_CONTRACT
            )
        })
    }

    private data class ReplayCapture(
        val replayId: String,
        val playerUuid: UUID,
        val playFiles: PlayFiles,
        val workingDirectory: Path,
        var saved: Boolean = false
    )

    companion object {
        private const val REPLAY_METADATA_SCHEMA_VERSION = 4
        private const val FLASHBACK_FORMAT = "flashback"
    }
}
