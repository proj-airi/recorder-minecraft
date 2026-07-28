package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.Abilities
import dev.recorderminecraft.artifacts.v1.EntityReference
import dev.recorderminecraft.artifacts.v1.InventorySlot
import dev.recorderminecraft.artifacts.v1.Passenger
import dev.recorderminecraft.artifacts.v1.PlayerStateEvent
import dev.recorderminecraft.artifacts.v1.Rotation
import dev.recorderminecraft.artifacts.v1.StatusEffect
import dev.recorderminecraft.artifacts.v1.Vector3
import dev.mcdata.recorder.config.RecorderConfig
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.nbt.NbtOps
import net.minecraft.server.level.ServerPlayer
import net.minecraft.world.entity.Entity
import net.minecraft.world.item.ItemStack

object PlayerSnapshot {
    fun capture(player: ServerPlayer, config: RecorderConfig): PlayerStateEvent.Builder {
        val velocity = player.deltaMovement
        val inventory = player.inventory
        val food = player.foodData
        val result = PlayerStateEvent.newBuilder()
            .setDimension(player.level().dimension().location().toString())
            .setPosition(vector(player.x, player.y, player.z))
            .setRotation(
                Rotation.newBuilder()
                    .setYaw(player.yRot.toDouble())
                    .setPitch(player.xRot.toDouble())
                    .setHeadYaw(player.yHeadRot.toDouble())
            )
            .setVelocity(vector(velocity.x, velocity.y, velocity.z))
            .setAlive(player.isAlive)
            .setOnGround(player.onGround())
            .setPose(player.pose.name.lowercase())
            .setSprinting(player.isSprinting)
            .setSneaking(player.isShiftKeyDown)
            .setSwimming(player.isSwimming)
            .setFallFlying(player.isFallFlying)
            .setUsingItem(player.isUsingItem)
            .setUseItemRemainingTicks(player.useItemRemainingTicks)
            .setGameMode(player.gameMode().name.lowercase())
            .setHealth(player.health.toDouble())
            .setMaxHealth(player.maxHealth.toDouble())
            .setAbsorption(player.absorptionAmount.toDouble())
            .setArmor(player.armorValue)
            .setAir(player.airSupply)
            .setMaxAir(player.maxAirSupply)
            .setFoodLevel(food.foodLevel)
            .setSaturation(food.saturationLevel.toDouble())
            .setExperienceLevel(player.experienceLevel)
            .setExperienceProgress(player.experienceProgress.toDouble())
            .setTotalExperience(player.totalExperience)
            .setSelectedSlot(inventory.selectedSlot)
            .setAbilities(
                Abilities.newBuilder()
                    .setInvulnerable(player.abilities.invulnerable)
                    .setFlying(player.abilities.flying)
                    .setMayFly(player.abilities.mayfly)
                    .setInstantBuild(player.abilities.instabuild)
                    .setMayBuild(player.abilities.mayBuild)
            )

        player.activeEffects.sortedBy { it.descriptionId }.forEach { effect ->
            result.addEffects(
                StatusEffect.newBuilder()
                    .setEffectId(BuiltInRegistries.MOB_EFFECT.getKey(effect.effect.value()).toString())
                    .setDurationTicks(effect.duration)
                    .setAmplifier(effect.amplifier)
                    .setAmbient(effect.isAmbient)
                    .setVisible(effect.isVisible)
                    .setShowIcon(effect.showIcon())
            )
        }
        entityReference(player.vehicle)?.let(result::setVehicle)
        player.passengers.sortedBy { it.uuid.toString() }.forEach {
            result.addPassengers(
                Passenger.newBuilder()
                    .setUuid(it.uuid.toString())
                    .setTypeId(BuiltInRegistries.ENTITY_TYPE.getKey(it.type).toString())
            )
        }
        for (slot in 0 until inventory.containerSize) {
            val stack = inventory.getItem(slot)
            if (stack.isEmpty) continue
            val value = InventorySlot.newBuilder()
                .setSlot(slot)
                .setItemId(BuiltInRegistries.ITEM.getKey(stack.item).toString())
                .setCount(stack.count)
                .setDamage(stack.damageValue)
                .setMaxDamage(stack.maxDamage)
            if (config.includeInventoryComponents) {
                value.componentsDebug = stack.components.toString()
                val ops = player.registryAccess().createSerializationContext(NbtOps.INSTANCE)
                ItemStack.CODEC.encodeStart(ops, stack).result().ifPresent { value.componentsSnbt = it.toString() }
            }
            result.addInventory(value)
        }
        return result
    }

    private fun vector(x: Number, y: Number, z: Number): Vector3 = Vector3.newBuilder()
        .setX(x.toDouble())
        .setY(y.toDouble())
        .setZ(z.toDouble())
        .build()

    private fun entityReference(entity: Entity?): EntityReference? = entity?.let {
        EntityReference.newBuilder()
            .setEntityId(it.id)
            .setUuid(it.uuid.toString())
            .setTypeId(BuiltInRegistries.ENTITY_TYPE.getKey(it.type).toString())
            .build()
    }
}
