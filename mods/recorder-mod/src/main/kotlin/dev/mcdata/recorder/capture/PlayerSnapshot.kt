package dev.mcdata.recorder.capture

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import dev.mcdata.recorder.config.RecorderConfig
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.nbt.NbtOps
import net.minecraft.server.level.ServerPlayer
import net.minecraft.world.entity.Entity
import net.minecraft.world.item.ItemStack

object PlayerSnapshot {
    fun capture(player: ServerPlayer, config: RecorderConfig): JsonObject {
        val velocity = player.deltaMovement
        val inventory = player.inventory
        val food = player.foodData
        return JsonObject().apply {
            addProperty("player_uuid", player.uuid.toString())
            addProperty("player_name", player.gameProfile.name)
            addProperty("dimension", player.level().dimension().location().toString())
            add("position", vector(player.x, player.y, player.z))
            add("rotation", JsonObject().apply {
                addProperty("yaw", player.yRot)
                addProperty("pitch", player.xRot)
                addProperty("head_yaw", player.yHeadRot)
            })
            add("velocity", vector(velocity.x, velocity.y, velocity.z))
            addProperty("entity_id", player.id)
            addProperty("alive", player.isAlive)
            addProperty("on_ground", player.onGround())
            addProperty("pose", player.pose.name.lowercase())
            addProperty("sprinting", player.isSprinting)
            addProperty("sneaking", player.isShiftKeyDown)
            addProperty("swimming", player.isSwimming)
            addProperty("fall_flying", player.isFallFlying)
            addProperty("using_item", player.isUsingItem)
            addProperty("use_item_remaining_ticks", player.useItemRemainingTicks)
            addProperty("game_mode", player.gameMode().name.lowercase())
            addProperty("health", player.health)
            addProperty("max_health", player.maxHealth)
            addProperty("absorption", player.absorptionAmount)
            addProperty("armor", player.armorValue)
            addProperty("air", player.airSupply)
            addProperty("max_air", player.maxAirSupply)
            addProperty("food_level", food.foodLevel)
            addProperty("saturation", food.saturationLevel)
            addProperty("experience_level", player.experienceLevel)
            addProperty("experience_progress", player.experienceProgress)
            addProperty("total_experience", player.totalExperience)
            addProperty("selected_slot", inventory.selectedSlot)
            add("abilities", JsonObject().apply {
                addProperty("invulnerable", player.abilities.invulnerable)
                addProperty("flying", player.abilities.flying)
                addProperty("may_fly", player.abilities.mayfly)
                addProperty("instant_build", player.abilities.instabuild)
                addProperty("may_build", player.abilities.mayBuild)
            })
            add("effects", JsonArray().also { effects ->
                player.activeEffects.sortedBy { it.descriptionId }.forEach { effect ->
                    effects.add(JsonObject().apply {
                        addProperty("effect", BuiltInRegistries.MOB_EFFECT.getKey(effect.effect.value()).toString())
                        addProperty("duration", effect.duration)
                        addProperty("amplifier", effect.amplifier)
                        addProperty("ambient", effect.isAmbient)
                        addProperty("visible", effect.isVisible)
                        addProperty("show_icon", effect.showIcon())
                    })
                }
            })
            add("vehicle", entityReference(player.vehicle))
            add("passengers", JsonArray().also { passengers ->
                player.passengers.sortedBy { it.uuid.toString() }.forEach { passengers.add(entityReference(it)) }
            })
            add("inventory", JsonArray().also { slots ->
                for (slot in 0 until inventory.containerSize) {
                    val stack = inventory.getItem(slot)
                    if (stack.isEmpty) continue
                    slots.add(JsonObject().apply {
                        addProperty("slot", slot)
                        addProperty("item", BuiltInRegistries.ITEM.getKey(stack.item).toString())
                        addProperty("count", stack.count)
                        addProperty("damage", stack.damageValue)
                        addProperty("max_damage", stack.maxDamage)
                        if (config.includeInventoryComponents) {
                            addProperty("components_debug", stack.components.toString())
                            val ops = player.registryAccess().createSerializationContext(NbtOps.INSTANCE)
                            ItemStack.CODEC.encodeStart(ops, stack).result().ifPresent { tag ->
                                addProperty("stack_snbt", tag.toString())
                            }
                        }
                    })
                }
            })
        }
    }

    private fun vector(x: Number, y: Number, z: Number): JsonObject = JsonObject().apply {
        addProperty("x", x)
        addProperty("y", y)
        addProperty("z", z)
    }

    private fun entityReference(entity: Entity?): JsonObject? = entity?.let {
        JsonObject().apply {
            addProperty("entity_id", it.id)
            addProperty("uuid", it.uuid.toString())
            addProperty("type", BuiltInRegistries.ENTITY_TYPE.getKey(it.type).toString())
        }
    }
}
