package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.ClientInformationEvent
import dev.recorderminecraft.artifacts.v1.ClientInformationSource
import net.minecraft.network.protocol.Packet
import net.minecraft.network.protocol.common.ServerboundClientInformationPacket
import net.minecraft.server.level.ClientInformation
import java.util.Locale

/** Normalizes client-reported settings into `client_information` records. */
object ClientSettings {
    fun record(information: ClientInformation, source: ClientInformationSource): ClientInformationEvent =
        ClientInformationEvent.newBuilder()
            .setSource(source)
            .setLanguage(information.language())
            .setViewDistance(information.viewDistance())
            .setChatVisibility(information.chatVisibility().name.lowercase(Locale.ROOT))
            .setChatColors(information.chatColors())
            .setModelCustomisation(information.modelCustomisation())
            .setMainHand(information.mainHand().name.lowercase(Locale.ROOT))
            .setTextFiltering(information.textFilteringEnabled())
            .setAllowsListing(information.allowsListing())
            .setParticleStatus(information.particleStatus().name.lowercase(Locale.ROOT))
            .build()

    /** Settings carried by a play-phase packet; other packets yield null. */
    fun fromPacket(packet: Packet<*>): ClientInformationEvent? =
        (packet as? ServerboundClientInformationPacket)?.let {
            record(it.information(), ClientInformationSource.CLIENT_INFORMATION_SOURCE_PACKET)
        }
}
