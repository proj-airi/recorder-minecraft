package dev.mcdata.recorder.io

import org.junit.jupiter.api.Test
import kotlin.test.assertEquals

class PlayFilesTest {
    @Test
    fun `world stream replaces the unopened container gap`() {
        val shared = listOf("audio_not_extracted", "particles_not_extracted", "lighting_not_persisted_in_scene_v2")
        assertEquals(shared + "unopened_container_contents_may_be_unknown", PlayFiles.knownGaps(worldStreamActive = false))
        assertEquals(shared + "world_entities_not_recorded", PlayFiles.knownGaps(worldStreamActive = true))
    }
}
