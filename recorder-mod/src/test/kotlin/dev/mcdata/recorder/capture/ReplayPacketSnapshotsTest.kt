package dev.mcdata.recorder.capture

import net.minecraft.network.protocol.game.ClientboundSetPlayerInventoryPacket
import net.minecraft.world.item.ItemStack
import net.minecraft.world.item.Items
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotSame
import kotlin.test.assertSame

class ReplayPacketSnapshotsTest {
    @Test
    fun `inventory packet contents are copied before asynchronous encoding`() {
        net.minecraft.SharedConstants.setVersion(net.minecraft.DetectedVersion.BUILT_IN)
        net.minecraft.server.Bootstrap.bootStrap()
        val live = ItemStack(Items.COBBLESTONE, 17)
        val original = ClientboundSetPlayerInventoryPacket(4, live)

        val frozen = ReplayPacketSnapshots.freezeMutableValues(original)
            as ClientboundSetPlayerInventoryPacket
        live.count = 9

        assertNotSame(original, frozen)
        assertNotSame(live, frozen.contents())
        assertEquals(4, frozen.slot())
        assertEquals(17, frozen.contents().count)
    }

    @Test
    fun `unrelated packets remain unchanged`() {
        val packet = net.minecraft.network.protocol.game.ClientboundSetHeldSlotPacket(3)

        assertSame(packet, ReplayPacketSnapshots.freezeMutableValues(packet))
    }
}
