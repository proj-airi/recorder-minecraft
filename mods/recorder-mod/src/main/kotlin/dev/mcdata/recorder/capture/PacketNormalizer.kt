package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.BlockHit
import dev.recorderminecraft.artifacts.v1.CameraOrPositionAction
import dev.recorderminecraft.artifacts.v1.EntityTarget
import dev.recorderminecraft.artifacts.v1.HashedItemStack
import dev.recorderminecraft.artifacts.v1.MovementInput
import dev.recorderminecraft.artifacts.v1.Packet
import dev.recorderminecraft.artifacts.v1.PacketIdentity
import dev.recorderminecraft.artifacts.v1.Vector3
import dev.mcdata.recorder.mixin.ServerboundInteractPacketAccessor
import dev.mcdata.recorder.model.InputFlags
import net.fabricmc.loader.api.FabricLoader
import net.minecraft.core.BlockPos
import net.minecraft.core.Direction
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.network.HashedStack
import net.minecraft.network.protocol.Packet as MinecraftPacket
import net.minecraft.network.protocol.game.ServerboundBlockEntityTagQueryPacket
import net.minecraft.network.protocol.game.ServerboundContainerButtonClickPacket
import net.minecraft.network.protocol.game.ServerboundContainerClickPacket
import net.minecraft.network.protocol.game.ServerboundContainerClosePacket
import net.minecraft.network.protocol.game.ServerboundContainerSlotStateChangedPacket
import net.minecraft.network.protocol.game.ServerboundInteractPacket
import net.minecraft.network.protocol.game.ServerboundJigsawGeneratePacket
import net.minecraft.network.protocol.game.ServerboundMovePlayerPacket
import net.minecraft.network.protocol.game.ServerboundPickItemFromBlockPacket
import net.minecraft.network.protocol.game.ServerboundPickItemFromEntityPacket
import net.minecraft.network.protocol.game.ServerboundPlaceRecipePacket
import net.minecraft.network.protocol.game.ServerboundPlayerActionPacket
import net.minecraft.network.protocol.game.ServerboundPlayerInputPacket
import net.minecraft.network.protocol.game.ServerboundSeenAdvancementsPacket
import net.minecraft.network.protocol.game.ServerboundSetCarriedItemPacket
import net.minecraft.network.protocol.game.ServerboundSetCreativeModeSlotPacket
import net.minecraft.network.protocol.game.ServerboundSetJigsawBlockPacket
import net.minecraft.network.protocol.game.ServerboundSetStructureBlockPacket
import net.minecraft.network.protocol.game.ServerboundSignUpdatePacket
import net.minecraft.network.protocol.game.ServerboundSwingPacket
import net.minecraft.network.protocol.game.ServerboundUseItemOnPacket
import net.minecraft.network.protocol.game.ServerboundUseItemPacket
import net.minecraft.server.level.ServerPlayer
import net.minecraft.world.InteractionHand
import net.minecraft.world.entity.player.Input
import net.minecraft.world.phys.BlockHitResult
import net.minecraft.world.phys.Vec3
import java.util.Locale

/**
 * Turns a decoded serverbound packet into the capture's semantic `Packet` record.
 *
 * Every field is read through a compile-time reference to the packet class. Loom remaps those
 * references with the jar, so they resolve in production, where Minecraft runs under intermediary
 * names such as `class_2813`. Never look fields up by method-name strings: those are not remapped,
 * match nothing outside the Mojang-named development runtime, and fail silently.
 * `ReflectionFreeTest` rejects by-name lookup anywhere in the mod.
 */
object PacketNormalizer {
    data class Result(val data: Packet.Builder, val input: InputFlags? = null, val selectedSlot: Int? = null)

    fun normalize(packet: MinecraftPacket<*>): Result {
        val packetType = packet.type().toString()
        val result = Packet.newBuilder()
            .setIdentity(
                PacketIdentity.newBuilder()
                    .setPacketClass(PACKET_CLASS_NAMES.get(packet.javaClass))
                    .setPacketType(packetType)
                    .setRawBytesAvailable(false)
            )
            .setActionKind(actionKind(packetType))

        if (isPrivatePayload(packetType)) {
            result.payloadRedacted = true
            return Result(result)
        }

        var input: InputFlags? = null
        var selectedSlot: Int? = null
        when (packet) {
            is ServerboundPlayerInputPacket -> {
                input = readInput(packet.input())
                result.movementInput = movementInput(input)
            }
            is ServerboundMovePlayerPacket -> addMoveFields(packet, result)
            is ServerboundInteractPacket -> addInteractFields(packet, result)
            is ServerboundContainerClickPacket -> addContainerClickFields(packet, result)
            is ServerboundContainerButtonClickPacket -> {
                result.containerId = packet.containerId()
                result.buttonNumber = packet.buttonId()
            }
            is ServerboundContainerClosePacket -> result.containerId = packet.containerId
            is ServerboundContainerSlotStateChangedPacket -> {
                result.containerId = packet.containerId()
                result.slotNumber = packet.slotId()
            }
            is ServerboundPlaceRecipePacket -> result.containerId = packet.containerId()
            is ServerboundSetCreativeModeSlotPacket -> result.slotNumber = packet.slotNum().toInt()
            is ServerboundSetCarriedItemPacket -> {
                selectedSlot = packet.slot
                result.slot = packet.slot
            }
            is ServerboundSwingPacket -> result.hand = hand(packet.hand)
            is ServerboundUseItemPacket -> {
                result.hand = hand(packet.hand)
                result.interactionSequence = packet.sequence
            }
            is ServerboundUseItemOnPacket -> {
                result.hand = hand(packet.hand)
                result.interactionSequence = packet.sequence
                result.blockHit = blockHit(packet.hitResult)
            }
            is ServerboundPlayerActionPacket -> {
                result.action = packet.action.name.lowercase(Locale.ROOT)
                result.blockPosition = vector(packet.pos)
                result.direction = direction(packet.direction)
                result.interactionSequence = packet.sequence
            }
            is ServerboundPickItemFromBlockPacket -> result.blockPosition = vector(packet.pos())
            is ServerboundPickItemFromEntityPacket -> result.entityId = packet.id()
            is ServerboundSeenAdvancementsPacket -> result.action = packet.action.name.lowercase(Locale.ROOT)
            // Block-editing screens: only the edited block is recorded, never the submitted text.
            is ServerboundSignUpdatePacket -> result.blockPosition = vector(packet.pos)
            is ServerboundBlockEntityTagQueryPacket -> result.blockPosition = vector(packet.pos)
            is ServerboundSetStructureBlockPacket -> result.blockPosition = vector(packet.pos)
            is ServerboundSetJigsawBlockPacket -> result.blockPosition = vector(packet.pos)
            is ServerboundJigsawGeneratePacket -> result.blockPosition = vector(packet.pos)
        }
        return Result(result, input, selectedSlot)
    }

    fun enrichAtApply(player: ServerPlayer, packet: MinecraftPacket<*>, data: Packet.Builder) {
        if (packet !is ServerboundInteractPacket) return
        val target = packet.getTarget(player.level()) ?: return
        data.target = EntityTarget.newBuilder()
            .setEntityId(target.id)
            .setUuid(target.uuid.toString())
            .setTypeId(BuiltInRegistries.ENTITY_TYPE.getKey(target.type).toString())
            .setPlayer(target is ServerPlayer)
            .build()
    }

    private fun movementInput(value: InputFlags): MovementInput = MovementInput.newBuilder()
        .setForward(value.forward).setBackward(value.backward).setLeft(value.left).setRight(value.right)
        .setJump(value.jump).setSneak(value.sneak).setSprint(value.sprint).build()

    private fun readInput(input: Input): InputFlags = InputFlags(
        input.forward(), input.backward(), input.left(), input.right(), input.jump(), input.shift(), input.sprint()
    )

    private fun addMoveFields(packet: ServerboundMovePlayerPacket, result: Packet.Builder) {
        val action = CameraOrPositionAction.newBuilder()
            .setActionKind(result.actionKind)
            .setPacket(result.identity)
            .setHasPosition(packet.hasPosition())
            .setHasRotation(packet.hasRotation())
            .setOnGround(packet.isOnGround)
            .setHorizontalCollision(packet.horizontalCollision())
        if (packet.hasPosition()) {
            action.setX(packet.getX(Double.NaN)).setY(packet.getY(Double.NaN)).setZ(packet.getZ(Double.NaN))
        }
        if (packet.hasRotation()) {
            action.setYaw(packet.getYRot(Float.NaN).toDouble()).setPitch(packet.getXRot(Float.NaN).toDouble())
        }
        result.cameraOrPosition = action.build()
    }

    private fun addInteractFields(packet: ServerboundInteractPacket, result: Packet.Builder) {
        result.entityId = (packet as ServerboundInteractPacketAccessor).mcRecorderEntityId()
        result.secondaryAction = packet.isUsingSecondaryAction
        packet.dispatch(object : ServerboundInteractPacket.Handler {
            override fun onInteraction(hand: InteractionHand) {
                result.interaction = "interact"
                result.hand = hand(hand)
            }
            override fun onInteraction(hand: InteractionHand, location: Vec3) {
                result.interaction = "interact_at"
                result.hand = hand(hand)
                result.blockPosition = vector(location)
            }
            override fun onAttack() { result.interaction = "attack" }
        })
    }

    private fun addContainerClickFields(packet: ServerboundContainerClickPacket, result: Packet.Builder) {
        result.containerId = packet.containerId()
        result.containerStateId = packet.stateId()
        result.slotNumber = packet.slotNum().toInt()
        result.buttonNumber = packet.buttonNum().toInt()
        result.clickType = packet.clickType().name.lowercase(Locale.ROOT)
        // The packet map is a hash map; sort so equal clicks always serialize identically.
        packet.changedSlots().int2ObjectEntrySet()
            .sortedBy { it.intKey }
            .forEach { result.addChangedSlotStacks(hashedStack(it.intKey, it.value)) }
        result.carriedStack = hashedStack(CARRIED_SLOT, packet.carriedItem())
    }

    /**
     * The client sends `HashedStack`, not `ItemStack`: component values are already reduced to
     * hashes, so the shared `InventorySlots` encoder cannot apply. Item id and count keep the same
     * meaning as `InventorySlot`, including `minecraft:air` with count 0 for an empty slot.
     */
    private fun hashedStack(slot: Int, stack: HashedStack): HashedItemStack {
        val value = HashedItemStack.newBuilder().setSlot(slot)
        if (stack !is HashedStack.ActualItem) {
            return value.setItemId(AIR_ITEM_ID).setCount(0).build()
        }
        value.setItemId(BuiltInRegistries.ITEM.getKey(stack.item().value()).toString()).setCount(stack.count())
        stack.components().addedComponents().entries
            .map { (type, hash) -> componentId(type) to hash }
            .sortedBy { it.first }
            .forEach { (id, hash) -> value.putComponentHashes(id, hash) }
        stack.components().removedComponents().map(::componentId).sorted().forEach(value::addRemovedComponents)
        return value.build()
    }

    private fun componentId(type: net.minecraft.core.component.DataComponentType<*>): String =
        BuiltInRegistries.DATA_COMPONENT_TYPE.getKey(type).toString()

    private fun blockHit(hit: BlockHitResult): BlockHit = BlockHit.newBuilder()
        .setBlockPosition(vector(hit.blockPos))
        .setDirection(direction(hit.direction))
        .setLocation(vector(hit.location))
        .setInside(hit.isInside)
        .build()

    private fun hand(hand: InteractionHand): String = hand.name.lowercase(Locale.ROOT)

    // Enum constant names survive obfuscation, so `name` is stable in every runtime namespace.
    private fun direction(direction: Direction): String = direction.name.lowercase(Locale.ROOT)

    private fun vector(pos: BlockPos): Vector3 =
        Vector3.newBuilder().setX(pos.x.toDouble()).setY(pos.y.toDouble()).setZ(pos.z.toDouble()).build()

    private fun vector(value: Vec3): Vector3 = Vector3.newBuilder().setX(value.x).setY(value.y).setZ(value.z).build()

    private fun actionKind(packetType: String): String {
        val stableName = packetType.substringAfter(':', packetType).lowercase(Locale.ROOT)
        return when {
            stableName == "player_input" -> "movement_controls"
            stableName.startsWith("move_player") -> "camera_or_position"
            stableName == "player_action" -> "player_action"
            stableName.contains("interact") -> "interact"
            stableName.startsWith("use_item") -> "use"
            stableName == "swing" -> "swing"
            stableName.startsWith("container_") || stableName == "set_carried_item" -> "inventory"
            stableName == "player_command" -> "stance"
            stableName.contains("chat") || stableName.contains("command") -> "text_redacted"
            stableName.contains("custom_payload") -> "custom_payload_redacted"
            stableName == "client_tick_end" -> "tick_boundary"
            stableName in setOf("chunk_batch_received", "accept_teleportation", "player_loaded") -> "protocol_ack"
            else -> "other"
        }
    }

    // `packet.type()` is the protocol id (`serverbound/minecraft:chat`), identical in every runtime
    // namespace. JVM class names are not, so they take no part in redaction.
    private fun isPrivatePayload(packetType: String): Boolean =
        packetType.contains("chat", true) || packetType.contains("command", true) ||
            packetType.contains("custom_payload", true)

    private const val CARRIED_SLOT = -1
    private const val AIR_ITEM_ID = "minecraft:air"

    /**
     * `packet_class` is diagnostic. The JVM name differs by runtime: Mojang names in this
     * project's development runtime, intermediary names such as `net.minecraft.class_2813` in
     * production, and Yarn names in a Yarn development client. Recording the intermediary name
     * everywhere keeps one capture contract. Without a Fabric launcher (plain unit tests) there
     * are no mappings, and the JVM name is the only name available.
     */
    private val PACKET_CLASS_NAMES = object : ClassValue<String>() {
        override fun computeValue(type: Class<*>): String = runCatching {
            FabricLoader.getInstance().mappingResolver.unmapClassName("intermediary", type.name)
        }.getOrDefault(type.name)
    }
}
