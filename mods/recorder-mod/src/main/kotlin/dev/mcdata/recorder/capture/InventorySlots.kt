package dev.mcdata.recorder.capture

import com.mojang.serialization.DynamicOps
import dev.recorderminecraft.artifacts.v1.InventorySlot
import net.minecraft.core.HolderLookup
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.nbt.NbtOps
import net.minecraft.nbt.Tag
import net.minecraft.world.item.ItemStack

/**
 * One item-stack encoding for every capture record, so player inventory and container views can
 * be compared slot for slot.
 */
class InventorySlots(
    private val includeComponents: Boolean,
    private val registries: () -> HolderLookup.Provider
) {
    fun encode(slot: Int, stack: ItemStack): InventorySlot =
        builder(slot, stack, if (includeComponents) registries().createSerializationContext(NbtOps.INSTANCE) else null).build()

    companion object {
        /**
         * Shared by player inventory, container views, and world container snapshots.
         *
         * @param componentOps serialization context for `components_snbt`; null omits component data.
         */
        fun builder(slot: Int, stack: ItemStack, componentOps: DynamicOps<Tag>?): InventorySlot.Builder {
            // An empty stack still encodes as `minecraft:air` with count 0. Callers that list only
            // occupied slots skip empties themselves; single-slot updates need the explicit removal.
            val value = InventorySlot.newBuilder()
                .setSlot(slot)
                .setItemId(BuiltInRegistries.ITEM.getKey(stack.item).toString())
                .setCount(stack.count)
                .setDamage(stack.damageValue)
                .setMaxDamage(stack.maxDamage)
            if (componentOps != null && !stack.isEmpty) {
                value.componentsDebug = stack.components.toString()
                ItemStack.CODEC.encodeStart(componentOps, stack).result().ifPresent { value.componentsSnbt = it.toString() }
            }
            return value
        }
    }
}
