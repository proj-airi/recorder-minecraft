package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.Packet
import dev.recorderminecraft.artifacts.v1.Vector3
import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap
import net.minecraft.DetectedVersion
import net.minecraft.SharedConstants
import net.minecraft.core.BlockPos
import net.minecraft.core.Direction
import net.minecraft.core.component.DataComponents
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.network.chat.LastSeenMessages
import net.minecraft.network.HashedPatchMap
import net.minecraft.network.HashedStack
import net.minecraft.network.protocol.Packet as MinecraftPacket
import net.minecraft.network.protocol.game.ServerboundChatPacket
import net.minecraft.network.protocol.game.ServerboundContainerButtonClickPacket
import net.minecraft.network.protocol.game.ServerboundContainerClickPacket
import net.minecraft.network.protocol.game.ServerboundContainerClosePacket
import net.minecraft.network.protocol.game.ServerboundContainerSlotStateChangedPacket
import net.minecraft.network.protocol.game.ServerboundMovePlayerPacket
import net.minecraft.network.protocol.game.ServerboundPickItemFromBlockPacket
import net.minecraft.network.protocol.game.ServerboundPickItemFromEntityPacket
import net.minecraft.network.protocol.game.ServerboundPlayerActionPacket
import net.minecraft.network.protocol.game.ServerboundPlayerInputPacket
import net.minecraft.network.protocol.game.ServerboundSetCarriedItemPacket
import net.minecraft.network.protocol.game.ServerboundSetCreativeModeSlotPacket
import net.minecraft.network.protocol.game.ServerboundSwingPacket
import net.minecraft.network.protocol.game.ServerboundUseItemOnPacket
import net.minecraft.network.protocol.game.ServerboundUseItemPacket
import net.minecraft.server.Bootstrap
import net.minecraft.world.InteractionHand
import net.minecraft.world.entity.player.Input
import net.minecraft.world.inventory.ClickType
import net.minecraft.world.item.ItemStack
import net.minecraft.world.item.Items
import net.minecraft.world.phys.BlockHitResult
import net.minecraft.world.phys.Vec3
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import java.time.Instant
import java.util.BitSet
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * Pins the populated field set of every serverbound packet the normalizer reads. Each assertion
 * compares the complete set, so a packet path that silently drops fields fails here.
 *
 * `ServerboundInteractPacket` is not covered: its entity id comes from a mixin accessor, and
 * mixins are not applied in unit tests. The live capture check covers it.
 */
class PacketNormalizerTest {
    @Test
    fun `container click records every click field and the hashed stacks`() {
        val changed = Int2ObjectOpenHashMap<HashedStack>().apply {
            put(27, HashedStack.EMPTY)
            put(
                0,
                HashedStack.ActualItem(
                    BuiltInRegistries.ITEM.wrapAsHolder(Items.DIAMOND_PICKAXE),
                    1,
                    HashedPatchMap(mapOf(DataComponents.DAMAGE to 12345), setOf(DataComponents.REPAIR_COST))
                )
            )
        }
        val carried = HashedStack.ActualItem(BuiltInRegistries.ITEM.wrapAsHolder(Items.DIAMOND), 3, HashedPatchMap(mapOf(), setOf()))
        val packet = normalize(
            ServerboundContainerClickPacket(4, 7, 27.toShort(), 1.toByte(), ClickType.QUICK_MOVE, changed, carried)
        )

        assertFields(
            packet,
            "container_id", "container_state_id", "slot_number", "button_number", "click_type",
            "changed_slot_stacks", "carried_stack"
        )
        assertEquals("inventory", packet.actionKind)
        assertEquals("serverbound/minecraft:container_click", packet.identity.packetType)
        assertEquals(4, packet.containerId)
        assertEquals(7, packet.containerStateId)
        assertEquals(27, packet.slotNumber)
        assertEquals(1, packet.buttonNumber)
        assertEquals("quick_move", packet.clickType)
        assertEquals(listOf(0, 27), packet.changedSlotStacksList.map { it.slot })
        val pickaxe = packet.changedSlotStacksList[0]
        assertEquals("minecraft:diamond_pickaxe", pickaxe.itemId)
        assertEquals(1, pickaxe.count)
        assertEquals(mapOf("minecraft:damage" to 12345), pickaxe.componentHashesMap)
        assertEquals(listOf("minecraft:repair_cost"), pickaxe.removedComponentsList)
        val emptied = packet.changedSlotStacksList[1]
        assertEquals("minecraft:air", emptied.itemId)
        assertEquals(0, emptied.count)
        assertEquals(-1, packet.carriedStack.slot)
        assertEquals("minecraft:diamond", packet.carriedStack.itemId)
        assertEquals(3, packet.carriedStack.count)
    }

    @Test
    fun `hashed stacks match the client's stack hashing`() {
        // The recorded item id and count must describe the same stack the client hashed.
        val stack = ItemStack(Items.OAK_LOG, 5)
        val packet = normalize(
            ServerboundContainerClickPacket(
                1, 0, 0.toShort(), 0.toByte(), ClickType.PICKUP, Int2ObjectOpenHashMap(),
                HashedStack.create(stack) { 0 }
            )
        )

        assertEquals("minecraft:oak_log", packet.carriedStack.itemId)
        assertEquals(5, packet.carriedStack.count)
        assertTrue(packet.carriedStack.componentHashesMap.isEmpty())
    }

    @Test
    fun `use item on records hand, sequence, and the complete block hit`() {
        val hit = BlockHitResult(Vec3(4.5, -59.25, 0.75), Direction.UP, BlockPos(4, -60, 0), true)
        val packet = normalize(ServerboundUseItemOnPacket(InteractionHand.OFF_HAND, hit, 42))

        assertFields(packet, "hand", "interaction_sequence", "block_hit")
        assertEquals("use", packet.actionKind)
        assertEquals("off_hand", packet.hand)
        assertEquals(42, packet.interactionSequence)
        assertEquals(vector(4.0, -60.0, 0.0), packet.blockHit.blockPosition)
        assertEquals("up", packet.blockHit.direction)
        assertEquals(vector(4.5, -59.25, 0.75), packet.blockHit.location)
        assertTrue(packet.blockHit.inside)
    }

    @Test
    fun `use item records hand and sequence`() {
        val packet = normalize(ServerboundUseItemPacket(InteractionHand.MAIN_HAND, 9, 90f, 10f))

        assertFields(packet, "hand", "interaction_sequence")
        assertEquals("main_hand", packet.hand)
        assertEquals(9, packet.interactionSequence)
    }

    @Test
    fun `player action records action, block, face, and sequence`() {
        val packet = normalize(
            ServerboundPlayerActionPacket(
                ServerboundPlayerActionPacket.Action.START_DESTROY_BLOCK, BlockPos(1, -61, 2), Direction.NORTH, 17
            )
        )

        assertFields(packet, "action", "block_position", "direction", "interaction_sequence")
        assertEquals("player_action", packet.actionKind)
        assertEquals("start_destroy_block", packet.action)
        assertEquals(vector(1.0, -61.0, 2.0), packet.blockPosition)
        assertEquals("north", packet.direction)
        assertEquals(17, packet.interactionSequence)
    }

    @Test
    fun `swing records the hand`() {
        val packet = normalize(ServerboundSwingPacket(InteractionHand.MAIN_HAND))

        assertFields(packet, "hand")
        assertEquals("swing", packet.actionKind)
        assertEquals("main_hand", packet.hand)
    }

    @Test
    fun `set carried item records the slot and reports the selected slot`() {
        val result = PacketNormalizer.normalize(ServerboundSetCarriedItemPacket(6))

        assertFields(result.data.build(), "slot")
        assertEquals(6, result.data.slot)
        assertEquals(6, result.selectedSlot)
    }

    @Test
    fun `container close, button click, and slot state record the container`() {
        val close = normalize(ServerboundContainerClosePacket(3))
        assertFields(close, "container_id")
        assertEquals(3, close.containerId)

        val button = normalize(ServerboundContainerButtonClickPacket(5, 2))
        assertFields(button, "container_id", "button_number")
        assertEquals(5, button.containerId)
        assertEquals(2, button.buttonNumber)

        val slotState = normalize(ServerboundContainerSlotStateChangedPacket(8, 6, true))
        assertFields(slotState, "container_id", "slot_number")
        assertEquals(6, slotState.containerId)
        assertEquals(8, slotState.slotNumber)
    }

    @Test
    fun `creative slot records the slot number`() {
        val packet = normalize(ServerboundSetCreativeModeSlotPacket(36, ItemStack(Items.STONE)))

        assertFields(packet, "slot_number")
        assertEquals(36, packet.slotNumber)
    }

    @Test
    fun `pick item records the picked block or entity`() {
        val block = normalize(ServerboundPickItemFromBlockPacket(BlockPos(3, 4, 5), false))
        assertFields(block, "block_position")
        assertEquals(vector(3.0, 4.0, 5.0), block.blockPosition)

        val entity = normalize(ServerboundPickItemFromEntityPacket(77, false))
        assertFields(entity, "entity_id")
        assertEquals(77, entity.entityId)
    }

    @Test
    fun `movement packets keep their typed payloads`() {
        val input = PacketNormalizer.normalize(
            ServerboundPlayerInputPacket(Input(true, false, false, true, false, true, false))
        )
        assertFields(input.data.build(), "movement_input")
        assertEquals(true, input.input?.forward)
        assertEquals(true, input.input?.sneak)

        val move = normalize(ServerboundMovePlayerPacket.PosRot(1.0, 2.0, 3.0, 45f, 10f, true, false))
        assertFields(move, "camera_or_position")
        assertEquals(45.0, move.cameraOrPosition.yaw)
    }

    @Test
    fun `chat stays redacted`() {
        val packet = normalize(
            ServerboundChatPacket("secret", Instant.EPOCH, 0L, null, LastSeenMessages.Update(0, BitSet(), 0))
        )

        assertFields(packet, "payload_redacted")
        assertEquals("text_redacted", packet.actionKind)
    }

    private fun normalize(packet: MinecraftPacket<*>): Packet = PacketNormalizer.normalize(packet).data.build()

    /** Asserts the exact set of populated payload fields; identity and action kind are always set. */
    private fun assertFields(packet: Packet, vararg expected: String) {
        val populated = packet.allFields.keys.map { it.name }.toSet() - setOf("identity", "action_kind")
        assertEquals(expected.toSet(), populated, "populated fields of ${packet.identity.packetType}")
    }

    private fun vector(x: Double, y: Double, z: Double): Vector3 = Vector3.newBuilder().setX(x).setY(y).setZ(z).build()

    companion object {
        @JvmStatic
        @BeforeAll
        fun bootstrap() {
            SharedConstants.setVersion(DetectedVersion.BUILT_IN)
            Bootstrap.bootStrap()
        }
    }
}
