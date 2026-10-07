package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.ClientInformationSource
import net.minecraft.network.protocol.common.ServerboundClientInformationPacket
import net.minecraft.network.protocol.game.ServerboundContainerClosePacket
import net.minecraft.server.level.ClientInformation
import net.minecraft.server.level.ParticleStatus
import net.minecraft.world.entity.HumanoidArm
import net.minecraft.world.entity.player.ChatVisiblity
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class ClientSettingsTest {
    private val information = ClientInformation(
        "en_us",
        6,
        ChatVisiblity.SYSTEM,
        false,
        0x7f,
        HumanoidArm.LEFT,
        true,
        false,
        ParticleStatus.MINIMAL
    )

    @Test
    fun `join snapshot copies every server-visible setting`() {
        val record = ClientSettings.record(information, ClientInformationSource.CLIENT_INFORMATION_SOURCE_JOIN_SNAPSHOT)

        assertEquals(ClientInformationSource.CLIENT_INFORMATION_SOURCE_JOIN_SNAPSHOT, record.source)
        assertEquals("en_us", record.language)
        assertEquals(6, record.viewDistance)
        assertEquals("system", record.chatVisibility)
        assertEquals(false, record.chatColors)
        assertEquals(0x7f, record.modelCustomisation)
        assertEquals("left", record.mainHand)
        assertEquals(true, record.textFiltering)
        assertEquals(false, record.allowsListing)
        assertEquals("minimal", record.particleStatus)
    }

    @Test
    fun `play phase settings packet is a packet-sourced record`() {
        val record = requireNotNull(ClientSettings.fromPacket(ServerboundClientInformationPacket(information)))

        assertEquals(ClientInformationSource.CLIENT_INFORMATION_SOURCE_PACKET, record.source)
        assertEquals(6, record.viewDistance)
        assertNull(ClientSettings.fromPacket(ServerboundContainerClosePacket(1)))
    }
}
