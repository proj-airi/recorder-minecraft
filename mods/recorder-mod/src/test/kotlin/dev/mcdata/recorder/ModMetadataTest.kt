package dev.mcdata.recorder

import com.google.gson.JsonParser
import kotlin.test.Test
import kotlin.test.assertEquals

class ModMetadataTest {
    @Test
    fun `published Fabric mod ID matches replay compatibility identity`() {
        val metadata = requireNotNull(javaClass.classLoader.getResourceAsStream("fabric.mod.json")) {
            "fabric.mod.json must be published as a recorder mod resource"
        }.bufferedReader().use(JsonParser::parseReader).asJsonObject

        assertEquals("recorder-minecraft", metadata.get("id").asString)
    }
}
