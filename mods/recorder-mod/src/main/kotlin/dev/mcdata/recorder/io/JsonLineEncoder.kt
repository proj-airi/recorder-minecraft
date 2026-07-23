package dev.mcdata.recorder.io

import com.google.gson.Gson
import com.google.gson.GsonBuilder
import com.google.gson.JsonObject
import java.nio.charset.StandardCharsets

object JsonLineEncoder {
    val gson: Gson = GsonBuilder().disableHtmlEscaping().create()

    fun encode(record: JsonObject): ByteArray =
        (gson.toJson(record) + "\n").toByteArray(StandardCharsets.UTF_8)
}
