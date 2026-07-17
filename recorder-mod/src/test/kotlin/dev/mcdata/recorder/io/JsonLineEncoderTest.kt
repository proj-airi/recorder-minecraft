package dev.mcdata.recorder.io

import com.google.gson.JsonObject
import kotlin.test.Test
import kotlin.test.assertEquals

class JsonLineEncoderTest {
    @Test
    fun `encodes exactly one escaped newline terminated record`() {
        val record = JsonObject().apply { addProperty("message", "one\ntwo") }
        val encoded = JsonLineEncoder.encode(record).toString(Charsets.UTF_8)

        assertEquals("{\"message\":\"one\\ntwo\"}\n", encoded)
    }
}
