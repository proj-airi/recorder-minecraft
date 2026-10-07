package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.InventorySlot
import net.minecraft.core.HolderLookup
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.nbt.NbtOps
import net.minecraft.world.item.ItemStack

/**
 * One item-stack encoding for every capture record, so player inventory and container views can
 * be compared slot for slot.
 */
class InventorySlots(
    private val includeComponents: Boolean,
    private val registries: () -> HolderLookup.Provider
) {
    fun encode(slot: Int, stack: ItemStack): InventorySlot {
        // An empty stack still encodes as `minecraft:air` with count 0. Callers that list only
        // occupied slots skip empties themselves; single-slot updates need the explicit removal.
        val value = InventorySlot.newBuilder()
            .setSlot(slot)
            .setItemId(BuiltInRegistries.ITEM.getKey(stack.item).toString())
            .setCount(stack.count)
            .setDamage(stack.damageValue)
            .setMaxDamage(stack.maxDamage)
        if (includeComponents && !stack.isEmpty) {
            value.componentsDebug = stack.components.toString()
            val ops = registries().createSerializationContext(NbtOps.INSTANCE)
            ItemStack.CODEC.encodeStart(ops, stack).result().ifPresent { value.componentsSnbt = it.toString() }
        }
        return value.build()
    }
}
