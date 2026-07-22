package dev.mcdata.recorder.capture

import net.minecraft.network.protocol.game.ClientboundMoveMinecartPacket
import net.minecraft.network.protocol.game.ClientboundPlayerPositionPacket
import net.minecraft.network.protocol.game.ClientboundSetPlayerInventoryPacket
import net.minecraft.network.protocol.game.ClientboundTeleportEntityPacket
import net.minecraft.world.entity.PositionMoveRotation
import net.minecraft.world.entity.Relative
import net.minecraft.world.entity.vehicle.NewMinecartBehavior
import net.minecraft.world.item.ItemStack
import net.minecraft.world.item.Items
import net.minecraft.world.phys.Vec3
import org.junit.jupiter.api.Test
import java.util.EnumSet
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

    @Test
    fun `minecart steps are frozen before asynchronous encoding`() {
        val steps = mutableListOf(
            NewMinecartBehavior.MinecartStep(
                Vec3(1.0, 2.0, 3.0),
                Vec3(0.1, 0.2, 0.3),
                45.0F,
                12.0F,
                1.0F
            )
        )
        val original = ClientboundMoveMinecartPacket(17, steps)

        val frozen = ReplayPacketSnapshots.freezeMutableValues(original)
            as ClientboundMoveMinecartPacket
        steps.clear()

        assertNotSame(original, frozen)
        assertEquals(17, frozen.entityId())
        assertEquals(1, frozen.lerpSteps().size)
    }

    @Test
    fun `relative movement sets are frozen before asynchronous encoding`() {
        val playerRelatives = EnumSet.of(Relative.X)
        val teleportRelatives = EnumSet.of(Relative.DELTA_X)
        val change = PositionMoveRotation(Vec3.ZERO, Vec3.ZERO, 0.0F, 0.0F)
        val player = ClientboundPlayerPositionPacket(4, change, playerRelatives)
        val teleport = ClientboundTeleportEntityPacket(5, change, teleportRelatives, true)

        val frozenPlayer = ReplayPacketSnapshots.freezeMutableValues(player)
            as ClientboundPlayerPositionPacket
        val frozenTeleport = ReplayPacketSnapshots.freezeMutableValues(teleport)
            as ClientboundTeleportEntityPacket
        playerRelatives.add(Relative.Y)
        teleportRelatives.add(Relative.DELTA_Y)

        assertNotSame(player, frozenPlayer)
        assertNotSame(teleport, frozenTeleport)
        assertEquals(setOf(Relative.X), frozenPlayer.relatives())
        assertEquals(setOf(Relative.DELTA_X), frozenTeleport.relatives())
    }
}
