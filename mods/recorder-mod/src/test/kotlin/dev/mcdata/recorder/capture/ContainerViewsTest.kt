package dev.mcdata.recorder.capture

import com.google.protobuf.util.JsonFormat
import dev.recorderminecraft.artifacts.v1.BlockPosition
import dev.recorderminecraft.artifacts.v1.ContainerViewKind
import dev.recorderminecraft.artifacts.v1.ContainerViewOrigin
import dev.recorderminecraft.artifacts.v1.ContainerViewSource
import net.minecraft.DetectedVersion
import net.minecraft.SharedConstants
import net.minecraft.core.BlockPos
import net.minecraft.network.chat.Component
import net.minecraft.network.protocol.game.ClientboundContainerClosePacket
import net.minecraft.network.protocol.game.ClientboundContainerSetContentPacket
import net.minecraft.network.protocol.game.ClientboundContainerSetSlotPacket
import net.minecraft.network.protocol.game.ClientboundOpenScreenPacket
import net.minecraft.network.protocol.game.ClientboundSetCursorItemPacket
import net.minecraft.network.protocol.game.ClientboundSetHeldSlotPacket
import net.minecraft.network.protocol.game.ServerboundContainerClosePacket
import net.minecraft.server.Bootstrap
import net.minecraft.world.SimpleMenuProvider
import net.minecraft.world.entity.EntityEquipment
import net.minecraft.world.entity.player.Inventory
import net.minecraft.world.entity.player.Player
import net.minecraft.world.inventory.ChestMenu
import net.minecraft.world.inventory.MenuType
import net.minecraft.world.item.ItemStack
import net.minecraft.world.item.Items
import net.minecraft.world.level.block.Blocks
import net.minecraft.world.level.block.entity.ChestBlockEntity
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ContainerViewsTest {
    // Component encoding needs live registries; these tests pin the shape shared with player_state.
    private val slots = InventorySlots(includeComponents = false) { error("registries are not needed") }

    @Test
    fun `open screen becomes an opened view with its menu type`() {
        val view = assertNotNullView(
            ContainerViews.clientbound(
                ClientboundOpenScreenPacket(5, MenuType.GENERIC_9x3, Component.literal("Chest")),
                slots,
                openContainerId = 0
            )
        )

        assertEquals(ContainerViewKind.CONTAINER_VIEW_KIND_OPENED, view.kind)
        assertEquals(ContainerViewOrigin.CONTAINER_VIEW_ORIGIN_CLIENTBOUND, view.origin)
        assertEquals(5, view.containerId)
        assertEquals("minecraft:generic_9x3", view.menuType)
        assertFalse(view.hasStateId())
    }

    @Test
    fun `full contents list only occupied slots and the carried stack`() {
        val packet = ClientboundContainerSetContentPacket(
            5,
            7,
            listOf(ItemStack(Items.DIAMOND, 3), ItemStack.EMPTY, ItemStack(Items.STONE, 64)),
            ItemStack.EMPTY
        )

        val view = assertNotNullView(ContainerViews.clientbound(packet, slots, openContainerId = 5))

        assertEquals(ContainerViewKind.CONTAINER_VIEW_KIND_CONTENTS, view.kind)
        assertEquals(7, view.stateId)
        assertEquals(listOf(0, 2), view.slotsList.map { it.slot })
        assertEquals("minecraft:diamond", view.slotsList[0].itemId)
        assertEquals(3, view.slotsList[0].count)
        assertEquals("minecraft:stone", view.slotsList[1].itemId)
        assertEquals("minecraft:air", view.carriedItem.itemId)
        assertEquals(0, view.carriedItem.count)
    }

    @Test
    fun `slot update encodes an emptied slot explicitly`() {
        val view = assertNotNullView(
            ContainerViews.clientbound(ClientboundContainerSetSlotPacket(5, 8, 3, ItemStack.EMPTY), slots, 5)
        )

        assertEquals(ContainerViewKind.CONTAINER_VIEW_KIND_SLOT, view.kind)
        assertEquals(8, view.stateId)
        assertEquals(1, view.slotsCount)
        assertEquals(3, view.slotsList[0].slot)
        assertEquals("minecraft:air", view.slotsList[0].itemId)
        assertEquals(0, view.slotsList[0].count)
    }

    @Test
    fun `cursor stack is attributed to the open menu`() {
        val packet = ClientboundSetCursorItemPacket(ItemStack(Items.DIAMOND, 2))

        val view = assertNotNullView(ContainerViews.clientbound(packet, slots, openContainerId = 9))

        assertEquals(ContainerViewKind.CONTAINER_VIEW_KIND_CARRIED, view.kind)
        assertEquals(9, view.containerId)
        assertEquals("minecraft:diamond", view.carriedItem.itemId)
        assertEquals(2, view.carriedItem.count)
        assertNull(ContainerViews.clientbound(packet, slots, openContainerId = 0))
    }

    @Test
    fun `close packets in both directions become closed views`() {
        val sent = assertNotNullView(ContainerViews.clientbound(ClientboundContainerClosePacket(5), slots, 5))
        assertEquals(ContainerViewKind.CONTAINER_VIEW_KIND_CLOSED, sent.kind)
        assertEquals(ContainerViewOrigin.CONTAINER_VIEW_ORIGIN_CLIENTBOUND, sent.origin)

        val received = assertNotNullView(ContainerViews.closedByClient(ServerboundContainerClosePacket(5)))
        assertEquals(ContainerViewKind.CONTAINER_VIEW_KIND_CLOSED, received.kind)
        assertEquals(ContainerViewOrigin.CONTAINER_VIEW_ORIGIN_SERVERBOUND, received.origin)
        assertEquals(5, received.containerId)
    }

    @Test
    fun `player inventory menu and unrelated packets are not container views`() {
        val inventoryContents = ClientboundContainerSetContentPacket(0, 1, listOf(ItemStack(Items.DIRT)), ItemStack.EMPTY)

        assertTrue(ContainerViews.isObserved(inventoryContents))
        assertNull(ContainerViews.clientbound(inventoryContents, slots, 0))
        assertNull(ContainerViews.clientbound(ClientboundContainerSetSlotPacket(0, 1, 36, ItemStack.EMPTY), slots, 0))
        assertNull(ContainerViews.closedByClient(ServerboundContainerClosePacket(0)))

        val unrelated = ClientboundSetHeldSlotPacket(3)
        assertFalse(ContainerViews.isObserved(unrelated))
        assertNull(ContainerViews.clientbound(unrelated, slots, 5))
    }

    @Test
    fun `block entity provider links the menu to its block`() {
        val chest = ChestBlockEntity(BlockPos(10, 64, -3), Blocks.CHEST.defaultBlockState())
        val menu = ChestMenu.threeRows(5, detachedInventory())

        val source = requireNotNull(ContainerViews.source(chest, menu))

        assertEquals(BlockPosition.newBuilder().setX(10).setY(64).setZ(-3).build(), source.blockPos)
        assertEquals("minecraft:chest", source.blockEntityType)
        assertFalse(source.hasSecondaryBlockPos())
        assertEquals(27, ContainerViews.containerSlotCount(menu))
    }

    @Test
    fun `source position uses the world stream integer encoding`() {
        val chest = ChestBlockEntity(BlockPos(2, -60, 5), Blocks.CHEST.defaultBlockState())
        val source = requireNotNull(ContainerViews.source(chest, ChestMenu.threeRows(5, detachedInventory())))

        val json = JsonFormat.printer().omittingInsignificantWhitespace().print(source)

        assertTrue("\"blockPos\":{\"x\":2,\"y\":-60,\"z\":5}" in json, json)
    }

    @Test
    fun `positions written as doubles before BlockPosition still parse`() {
        // Earlier captures encoded fields 2 and 4 as Vector3; ProtoJSON int32 accepts integral doubles.
        val legacy = """{"dimension":"minecraft:overworld","blockPos":{"x":2.0,"y":-60.0},""" +
            """"blockEntityType":"minecraft:chest","secondaryBlockPos":{"x":2.0,"y":-60.0,"z":1.0}}"""

        val source = ContainerViewSource.newBuilder().also { JsonFormat.parser().merge(legacy, it) }.build()

        assertEquals(BlockPosition.newBuilder().setX(2).setY(-60).build(), source.blockPos)
        assertEquals(BlockPosition.newBuilder().setX(2).setY(-60).setZ(1).build(), source.secondaryBlockPos)
    }

    @Test
    fun `menus without a backing block entity stay unlinked`() {
        val menu = ChestMenu.threeRows(5, detachedInventory())
        val provider = SimpleMenuProvider({ _, _, _ -> menu }, Component.literal("Ender Chest"))

        assertNull(ContainerViews.source(provider, menu))
    }

    // Menu slot layout only needs the inventory container, not a live player. The constructor is
    // called reflectively because Kotlin rejects a null argument for its non-null Player parameter.
    private fun detachedInventory(): Inventory = Inventory::class.java
        .getConstructor(Player::class.java, EntityEquipment::class.java)
        .newInstance(null, EntityEquipment())

    private fun assertNotNullView(view: dev.recorderminecraft.artifacts.v1.ContainerViewEvent.Builder?) =
        requireNotNull(view) { "expected a container view" }

    companion object {
        @JvmStatic
        @BeforeAll
        fun bootstrap() {
            SharedConstants.setVersion(DetectedVersion.BUILT_IN)
            Bootstrap.bootStrap()
        }
    }
}
