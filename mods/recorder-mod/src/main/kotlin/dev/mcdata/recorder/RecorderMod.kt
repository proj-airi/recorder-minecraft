package dev.mcdata.recorder

import dev.mcdata.recorder.capture.CaptureRuntime
import dev.mcdata.recorder.config.RecorderConfig
import dev.mcdata.recorder.network.ReplayTimelinePayload
import net.casual.arcade.events.GlobalEventHandler
import net.casual.arcade.events.ListenerRegistry.Companion.register
import net.casual.arcade.events.server.player.PlayerServerboundPacketEvent
import net.casual.arcade.replay.events.ReplayRecorderStartEvent
import net.casual.arcade.replay.events.ReplayRecorderCloseEvent
import net.casual.arcade.replay.events.ReplayRecorderSaveEvent
import net.fabricmc.api.ModInitializer
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry
import org.slf4j.LoggerFactory

object RecorderMod : ModInitializer {
    private val logger = LoggerFactory.getLogger("mc-recorder")
    private lateinit var config: RecorderConfig

    override fun onInitialize() {
        config = RecorderConfig.load(logger)
        CaptureRuntime.initialize(logger)
        PayloadTypeRegistry.playS2C().register(ReplayTimelinePayload.TYPE, ReplayTimelinePayload.STREAM_CODEC)

        ServerLifecycleEvents.SERVER_STARTED.register { server -> CaptureRuntime.start(server, config) }
        ServerLifecycleEvents.SERVER_STOPPED.register { CaptureRuntime.stop() }
        ServerTickEvents.START_SERVER_TICK.register { CaptureRuntime.startTick() }
        ServerTickEvents.END_SERVER_TICK.register { server -> CaptureRuntime.endTick(server) }
        ServerPlayConnectionEvents.JOIN.register { handler, _, _ -> CaptureRuntime.playerJoin(handler.player) }
        ServerPlayConnectionEvents.DISCONNECT.register { handler, _ -> CaptureRuntime.playerLeave(handler.player) }

        GlobalEventHandler.Server.register<PlayerServerboundPacketEvent> { event ->
            CaptureRuntime.packetArrival(event.player, event.packet)
        }
        GlobalEventHandler.Server.register<ReplayRecorderStartEvent> { event ->
            CaptureRuntime.replayRecorderStarted(event.recorder)
        }
        GlobalEventHandler.Server.register<ReplayRecorderSaveEvent>(
            phase = ReplayRecorderSaveEvent.PHASE_POST
        ) { event ->
            CaptureRuntime.replayRecorderSaved(event.recorder, event.output)
        }
        GlobalEventHandler.Server.register<ReplayRecorderCloseEvent> { event ->
            CaptureRuntime.replayRecorderClosed(event.recorder)
        }
        logger.info("Minecraft recorder initialized for automatic all-player capture")
    }
}
