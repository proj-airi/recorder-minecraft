package dev.mcdata.recorder.capture

import net.minecraft.network.protocol.Packet
import net.minecraft.network.protocol.game.ClientboundForgetLevelChunkPacket
import net.minecraft.network.protocol.game.ClientboundMoveEntityPacket
import net.minecraft.network.protocol.game.ClientboundMoveMinecartPacket
import net.minecraft.network.protocol.game.ClientboundPlayerPositionPacket
import net.minecraft.network.protocol.game.ClientboundSetChunkCacheCenterPacket
import net.minecraft.network.protocol.game.ClientboundSetEntityMotionPacket
import net.minecraft.network.protocol.game.ClientboundSetHeldSlotPacket
import net.minecraft.network.protocol.game.ClientboundTeleportEntityPacket
import net.minecraft.world.entity.PositionMoveRotation
import net.minecraft.world.level.ChunkPos
import net.minecraft.world.phys.Vec3
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ReplayScenePacketContractTest {
    @Test
    fun `flashback writer override retains exactly required scene packets`() {
        val retained = listOf<Packet<*>>(
            ClientboundForgetLevelChunkPacket(ChunkPos(1, 2)),
            ClientboundPlayerPositionPacket(3, change(), emptySet()),
            ClientboundMoveMinecartPacket(4, emptyList())
        )

        retained.forEach { packet ->
            assertFalse(
                ReplayScenePacketContract.shouldIgnoreAtFlashbackWriter(true, packet.javaClass),
                packet.javaClass.name
            )
            assertFalse(
                ReplayScenePacketContract.shouldIgnoreAtFlashbackWriter(false, packet.javaClass),
                packet.javaClass.name
            )
        }

        val unrelatedIgnored = ClientboundSetChunkCacheCenterPacket(5, 6)
        assertTrue(
            ReplayScenePacketContract.shouldIgnoreAtFlashbackWriter(
                true,
                unrelatedIgnored.javaClass
            )
        )
        val unrelatedRecorded = ClientboundSetHeldSlotPacket(7)
        assertFalse(
            ReplayScenePacketContract.shouldIgnoreAtFlashbackWriter(
                false,
                unrelatedRecorded.javaClass
            )
        )
        assertEquals("client_visible_scene_v1", ReplayScenePacketContract.FLASHBACK_CAPTURE_CONTRACT)
    }

    @Test
    fun `optimizer override retains every explicit entity movement form only`() {
        val retained = listOf<Packet<*>>(
            ClientboundMoveEntityPacket.Pos(1, 2, 3, 4, true),
            ClientboundMoveEntityPacket.Rot(1, 2, 3, true),
            ClientboundMoveEntityPacket.PosRot(1, 2, 3, 4, 5, 6, true),
            ClientboundTeleportEntityPacket(1, change(), emptySet(), true),
            ClientboundSetEntityMotionPacket(1, Vec3(0.1, 0.2, 0.3))
        )

        retained.forEach { packet ->
            assertFalse(
                ReplayScenePacketContract.shouldIgnoreAtOptimizer(true, packet),
                packet.javaClass.name
            )
        }

        val unrelated = ClientboundMoveMinecartPacket(2, emptyList())
        assertTrue(ReplayScenePacketContract.shouldIgnoreAtOptimizer(true, unrelated))
        assertFalse(ReplayScenePacketContract.shouldIgnoreAtOptimizer(false, unrelated))
    }

    private fun change() = PositionMoveRotation(
        Vec3(1.0, 2.0, 3.0),
        Vec3(0.1, 0.2, 0.3),
        45.0F,
        12.0F
    )
}
