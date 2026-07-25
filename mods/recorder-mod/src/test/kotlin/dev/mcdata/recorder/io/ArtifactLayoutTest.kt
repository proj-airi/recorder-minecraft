package dev.mcdata.recorder.io

import org.junit.jupiter.api.Test
import java.nio.file.Path
import java.util.UUID
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class ArtifactLayoutTest {
    @Test
    fun `constructs the only artifacts v1 play layout`() {
        val paths = ArtifactLayout.play(
            Path.of("/artifacts"),
            PlayIdentity(
                serverName = "local-test",
                serverInstanceId = UUID.fromString("11111111-1111-4111-8111-111111111111"),
                playerName = "RecorderPlayer",
                playerUuid = UUID.fromString("22222222-2222-4222-8222-222222222222"),
                startedAt = "20260725T102030.125Z",
                connectionId = UUID.fromString("33333333-3333-4333-8333-333333333333")
            )
        )

        assertEquals(
            Path.of(
                "/artifacts/v1/local-test--11111111-1111-4111-8111-111111111111/players/" +
                    "RecorderPlayer--22222222-2222-4222-8222-222222222222/plays/" +
                    "20260725T102030.125Z--33333333-3333-4333-8333-333333333333"
            ),
            paths.root
        )
        assertEquals(paths.root.resolve("renders/fpv_frames"), paths.fpvFrames)
    }

    @Test
    fun `rejects unsafe identity components`() {
        assertFailsWith<IllegalArgumentException> {
            ArtifactLayout.play(
                Path.of("/artifacts"),
                PlayIdentity(
                    serverName = "../escape",
                    serverInstanceId = UUID.randomUUID(),
                    playerName = "player",
                    playerUuid = UUID.randomUUID(),
                    startedAt = "20260725T102030Z",
                    connectionId = UUID.randomUUID()
                )
            )
        }
    }
}
