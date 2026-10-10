package dev.mcdata.recorder.capture

import dev.mcdata.recorder.mixin.CompoundContainerAccessor
import dev.recorderminecraft.artifacts.v1.ContainerViewEvent
import dev.recorderminecraft.artifacts.v1.ContainerViewKind
import dev.recorderminecraft.artifacts.v1.ContainerViewOrigin
import dev.recorderminecraft.artifacts.v1.ContainerViewSource
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.network.protocol.Packet
import net.minecraft.network.protocol.game.ClientboundContainerClosePacket
import net.minecraft.network.protocol.game.ClientboundContainerSetContentPacket
import net.minecraft.network.protocol.game.ClientboundContainerSetSlotPacket
import net.minecraft.network.protocol.game.ClientboundOpenScreenPacket
import net.minecraft.network.protocol.game.ClientboundSetCursorItemPacket
import net.minecraft.network.protocol.game.ServerboundContainerClosePacket
import net.minecraft.world.CompoundContainer
import net.minecraft.world.Container
import net.minecraft.world.MenuProvider
import net.minecraft.world.entity.player.Inventory
import net.minecraft.world.inventory.AbstractContainerMenu
import net.minecraft.world.inventory.ChestMenu
import net.minecraft.world.inventory.MenuType
import net.minecraft.world.level.block.entity.BlockEntity

/**
 * Normalizes the container packets a player is actually sent into `container_view` records.
 *
 * Container id 0 is the player's own inventory menu. `player_state.inventory` already records it
 * every tick, so its packets are never turned into container views.
 */
object ContainerViews {
    private const val PLAYER_INVENTORY_CONTAINER_ID = 0

    /**
     * Cheap class filter for the clientbound send hook, which sees every packet the server sends.
     * It keeps the synchronized coordinator off the hot path for chunk and entity traffic.
     */
    @JvmStatic
    fun isObserved(packet: Packet<*>): Boolean =
        packet is ClientboundOpenScreenPacket ||
            packet is ClientboundContainerSetContentPacket ||
            packet is ClientboundContainerSetSlotPacket ||
            packet is ClientboundSetCursorItemPacket ||
            packet is ClientboundContainerClosePacket

    /**
     * @param openContainerId menu the player has open when the packet is sent. Cursor packets carry
     * no container id, so the carried stack is attributed to that menu.
     */
    fun clientbound(packet: Packet<*>, slots: InventorySlots, openContainerId: Int): ContainerViewEvent.Builder? {
        val view = when (packet) {
            is ClientboundOpenScreenPacket -> view(ContainerViewKind.CONTAINER_VIEW_KIND_OPENED, packet.containerId)
                .setMenuType(menuType(packet.type))
            is ClientboundContainerSetContentPacket -> view(ContainerViewKind.CONTAINER_VIEW_KIND_CONTENTS, packet.containerId)
                .setStateId(packet.stateId)
                .also { builder ->
                    packet.items.forEachIndexed { index, stack ->
                        if (!stack.isEmpty) builder.addSlots(slots.encode(index, stack))
                    }
                }
                .setCarriedItem(slots.encode(-1, packet.carriedItem))
            is ClientboundContainerSetSlotPacket -> view(ContainerViewKind.CONTAINER_VIEW_KIND_SLOT, packet.containerId)
                .setStateId(packet.stateId)
                .addSlots(slots.encode(packet.slot, packet.item))
            is ClientboundSetCursorItemPacket -> view(ContainerViewKind.CONTAINER_VIEW_KIND_CARRIED, openContainerId)
                .setCarriedItem(slots.encode(-1, packet.contents))
            is ClientboundContainerClosePacket -> view(ContainerViewKind.CONTAINER_VIEW_KIND_CLOSED, packet.containerId)
            else -> return null
        }
        return view.takeIf { it.containerId != PLAYER_INVENTORY_CONTAINER_ID }
    }

    /**
     * A client-initiated close never produces a clientbound close packet; the server only runs
     * `doCloseContainer`. The serverbound packet is therefore the only observable close.
     */
    fun closedByClient(packet: Packet<*>): ContainerViewEvent.Builder? {
        if (packet !is ServerboundContainerClosePacket) return null
        if (packet.containerId == PLAYER_INVENTORY_CONTAINER_ID) return null
        return ContainerViewEvent.newBuilder()
            .setKind(ContainerViewKind.CONTAINER_VIEW_KIND_CLOSED)
            .setOrigin(ContainerViewOrigin.CONTAINER_VIEW_ORIGIN_SERVERBOUND)
            .setContainerId(packet.containerId)
    }

    /**
     * Resolves the block entity behind a menu opened through `ServerPlayer.openMenu`.
     *
     * Single chests, barrels, shulker boxes, furnaces, and hoppers pass their block entity as the
     * provider. Double chests pass an anonymous provider, so their halves are recovered from the
     * created `ChestMenu`'s compound container. Ender chests, entity inventories, and stateless
     * menus such as crafting tables have no linked block and return null.
     */
    fun source(provider: MenuProvider, menu: AbstractContainerMenu): ContainerViewSource? {
        val blocks = if (provider is BlockEntity) listOf(provider) else containerBlocks((menu as? ChestMenu)?.container)
        val primary = blocks.firstOrNull() ?: return null
        val source = ContainerViewSource.newBuilder()
            .setBlockPos(blockPosition(primary.blockPos))
            .setBlockEntityType(BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(primary.type).toString())
        primary.level?.let { source.dimension = it.dimension().location().toString() }
        blocks.getOrNull(1)?.let { source.secondaryBlockPos = blockPosition(it.blockPos) }
        return source.build()
    }

    /** Vanilla menus place the opened container's slots before the player's inventory slots. */
    fun containerSlotCount(menu: AbstractContainerMenu): Int = menu.slots.count { it.container !is Inventory }

    fun menuType(type: MenuType<*>): String = BuiltInRegistries.MENU.getKey(type)?.toString().orEmpty()

    private fun view(kind: ContainerViewKind, containerId: Int): ContainerViewEvent.Builder =
        ContainerViewEvent.newBuilder()
            .setKind(kind)
            .setOrigin(ContainerViewOrigin.CONTAINER_VIEW_ORIGIN_CLIENTBOUND)
            .setContainerId(containerId)

    private fun containerBlocks(container: Container?): List<BlockEntity> = when (container) {
        is BlockEntity -> listOf(container)
        is CompoundContainer -> (container as CompoundContainerAccessor).let {
            listOfNotNull(it.mcRecorderFirst() as? BlockEntity, it.mcRecorderSecond() as? BlockEntity)
        }
        else -> emptyList()
    }
}
