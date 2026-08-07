package dev.mcdata.recorder

import dev.mcdata.recorder.network.ReplayTimelinePayload
import net.fabricmc.api.ClientModInitializer
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking

object RecorderClientMod : ClientModInitializer {
    override fun onInitializeClient() {
        ClientPlayNetworking.registerGlobalReceiver(ReplayTimelinePayload.TYPE) { _, _ ->
            // ServerReplay records this server-authored marker before the local client consumes it.
        }
    }
}
