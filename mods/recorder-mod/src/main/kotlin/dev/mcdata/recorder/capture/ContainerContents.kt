package dev.mcdata.recorder.capture

import com.mojang.serialization.DynamicOps
import dev.recorderminecraft.artifacts.v1.ContainerSnapshot
import dev.recorderminecraft.artifacts.v1.InventorySlot
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.nbt.Tag
import net.minecraft.world.Container
import net.minecraft.world.RandomizableContainer
import net.minecraft.world.item.ItemStack

object ContainerContents {
    /**
     * Fills the contents fields of [snapshot] from [container] without mutating the container.
     *
     * NOTICE: `RandomizableContainerBlockEntity.getItem` and `DecoratedPotBlockEntity.getTheItem`
     * call `unpackLootTable(null)`, which rolls the loot table when the block entity is in a server
     * level. Reading slots of a container with a pending loot table would therefore generate the
     * contents we are trying to observe and consume its seed. Such containers are reported as
     * LOOT_UNGENERATED and their slots are never read.
     *
     * @param componentOps serialization context for `components_snbt`; null omits component data,
     * matching `include_inventory_components=false` for player inventories.
     */
    fun fill(container: Container, componentOps: DynamicOps<Tag>?, snapshot: ContainerSnapshot.Builder) {
        snapshot.containerSize = container.containerSize
        val pendingLootTable = (container as? RandomizableContainer)?.lootTable
        if (pendingLootTable != null) {
            snapshot.contentsState = ContainerSnapshot.ContentsState.CONTENTS_STATE_LOOT_UNGENERATED
            snapshot.lootTable = pendingLootTable.location().toString()
            return
        }
        snapshot.contentsState = ContainerSnapshot.ContentsState.CONTENTS_STATE_KNOWN
        for (slot in 0 until container.containerSize) {
            val stack = container.getItem(slot)
            if (stack.isEmpty) continue
            snapshot.addSlots(slot(slot, stack, componentOps))
        }
    }

    // REVIEW: Mirrors PlayerSnapshot's inventory slot encoding. Kept separate in this change to
    // avoid colliding with concurrent PlayerSnapshot edits; fold into one helper when merged.
    private fun slot(index: Int, stack: ItemStack, componentOps: DynamicOps<Tag>?): InventorySlot.Builder {
        val value = InventorySlot.newBuilder()
            .setSlot(index)
            .setItemId(BuiltInRegistries.ITEM.getKey(stack.item).toString())
            .setCount(stack.count)
            .setDamage(stack.damageValue)
            .setMaxDamage(stack.maxDamage)
        if (componentOps != null) {
            value.componentsDebug = stack.components.toString()
            ItemStack.CODEC.encodeStart(componentOps, stack).result().ifPresent { value.componentsSnbt = it.toString() }
        }
        return value
    }
}
