package dev.mcdata.recorder.config

import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals

class RecorderConfigTest {
    @Test
    fun `default artifacts root resolves below game directory`() {
        val gameDirectory = Path.of("/minecraft")

        assertEquals(
            gameDirectory.resolve("artifacts"),
            RecorderConfig().artifactsPath(gameDirectory)
        )
    }

    @Test
    fun `absolute artifacts root remains absolute`() {
        assertEquals(
            Path.of("/artifacts"),
            RecorderConfig(artifactsRoot = "/artifacts").artifactsPath(Path.of("/minecraft"))
        )
    }
}
