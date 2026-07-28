package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.BlockHit
import dev.recorderminecraft.artifacts.v1.CameraOrPositionAction
import dev.recorderminecraft.artifacts.v1.EntityTarget
import dev.recorderminecraft.artifacts.v1.MovementInput
import dev.recorderminecraft.artifacts.v1.Packet
import dev.recorderminecraft.artifacts.v1.PacketIdentity
import dev.recorderminecraft.artifacts.v1.Vector3
import dev.mcdata.recorder.mixin.ServerboundInteractPacketAccessor
import dev.mcdata.recorder.model.InputFlags
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.network.protocol.Packet as MinecraftPacket
import net.minecraft.network.protocol.game.ServerboundInteractPacket
import net.minecraft.network.protocol.game.ServerboundMovePlayerPacket
import net.minecraft.network.protocol.game.ServerboundPlayerInputPacket
import net.minecraft.server.level.ServerPlayer
import net.minecraft.world.InteractionHand
import net.minecraft.world.entity.player.Input
import net.minecraft.world.phys.Vec3
import java.util.Locale

object PacketNormalizer {
    data class Result(val data: Packet.Builder, val input: InputFlags? = null, val selectedSlot: Int? = null)

    fun normalize(packet: MinecraftPacket<*>): Result {
        val simpleName = packet.javaClass.simpleName
        val packetType = runCatching { packet.type().toString() }.getOrDefault(simpleName)
        val result = Packet.newBuilder()
            .setIdentity(
                PacketIdentity.newBuilder()
                    .setPacketClass(packet.javaClass.name)
                    .setPacketType(packetType)
                    .setRawBytesAvailable(false)
            )
            .setActionKind(actionKind(packetType, packet.javaClass))

        if (isPrivatePayload(packetType, simpleName)) {
            result.payloadRedacted = true
            return Result(result)
        }

        val input = if (packet is ServerboundPlayerInputPacket) {
            readInput(packet.input()).also { result.movementInput = movementInput(it) }
        } else null

        if (packet is ServerboundMovePlayerPacket) addMoveFields(packet, result)
        if (packet is ServerboundInteractPacket) addInteractFields(packet, result)

        val values = listOf(
            "getAction" to "action", "getDirection" to "direction", "getHand" to "hand",
            "getSequence" to "interaction_sequence", "getSlot" to "slot",
            "getContainerId" to "container_id", "getStateId" to "container_state_id",
            "getSlotNum" to "slot_number", "getButtonNum" to "button_number",
            "getClickType" to "click_type", "getData" to "action_data",
            "getId" to "entity_id", "isUsingSecondaryAction" to "secondary_action",
            "containerId" to "container_id", "stateId" to "container_state_id",
            "slotNum" to "slot_number", "buttonNum" to "button_number",
            "clickType" to "click_type", "changedSlots" to "changed_slots",
            "carriedItem" to "carried_item"
        ).mapNotNull { (method, key) -> invoke(packet, method)?.let { key to it } }
        values.forEach { (key, value) -> setValue(result, key, value) }
        invoke(packet, "getPos")?.let { result.blockPosition = vector(it) }
        invoke(packet, "getHitResult")?.let { hit ->
            val blockHit = BlockHit.newBuilder()
            invoke(hit, "getBlockPos")?.let { blockHit.blockPosition = vector(it) }
            invoke(hit, "getDirection")?.let { blockHit.direction = it.toString() }
            invoke(hit, "getLocation")?.let { blockHit.location = vector(it) }
            invoke(hit, "isInside")?.let { blockHit.inside = it as Boolean }
            result.blockHit = blockHit.build()
        }
        return Result(result, input, values.firstOrNull { it.first == "slot" }?.second?.let { (it as? Number)?.toInt() })
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
        packet.dispatch(object : ServerboundInteractPacket.Handler {
            override fun onInteraction(hand: InteractionHand) {
                result.interaction = "interact"
                result.hand = hand.name.lowercase(Locale.ROOT)
            }
            override fun onInteraction(hand: InteractionHand, location: Vec3) {
                result.interaction = "interact_at"
                result.hand = hand.name.lowercase(Locale.ROOT)
                result.blockPosition = vector(location)
            }
            override fun onAttack() { result.interaction = "attack" }
        })
    }

    private fun setValue(result: Packet.Builder, key: String, value: Any) {
        val text = if (value is Enum<*>) value.name.lowercase(Locale.ROOT) else value.toString()
        when (key) {
            "action" -> result.action = text
            "direction" -> result.direction = text
            "hand" -> result.hand = text.lowercase(Locale.ROOT)
            "interaction_sequence" -> result.interactionSequence = (value as Number).toInt()
            "slot" -> result.slot = (value as Number).toInt()
            "container_id" -> result.containerId = (value as Number).toInt()
            "container_state_id" -> result.containerStateId = (value as Number).toInt()
            "slot_number" -> result.slotNumber = (value as Number).toInt()
            "button_number" -> result.buttonNumber = (value as Number).toInt()
            "click_type" -> result.clickType = text
            "action_data" -> result.actionData = (value as Number).toInt()
            "entity_id" -> result.entityId = (value as Number).toInt()
            "secondary_action" -> result.secondaryAction = value as Boolean
            "changed_slots" -> result.changedSlots = text
            "carried_item" -> result.carriedItem = text
        }
    }

    private fun vector(value: Any): Vector3 {
        val x = invoke(value, "getX") as? Number ?: return Vector3.getDefaultInstance()
        val y = invoke(value, "getY") as? Number ?: return Vector3.getDefaultInstance()
        val z = invoke(value, "getZ") as? Number ?: return Vector3.getDefaultInstance()
        return Vector3.newBuilder().setX(x.toDouble()).setY(y.toDouble()).setZ(z.toDouble()).build()
    }

    private fun actionKind(packetType: String, type: Class<*>): String {
        val stableName = packetType.substringAfter(':', packetType).lowercase(Locale.ROOT)
        when {
            stableName == "player_input" -> return "movement_controls"
            stableName.startsWith("move_player") -> return "camera_or_position"
            stableName == "player_action" -> return "player_action"
            stableName.contains("interact") -> return "interact"
            stableName.startsWith("use_item") -> return "use"
            stableName == "swing" -> return "swing"
            stableName.startsWith("container_") || stableName == "set_carried_item" -> return "inventory"
            stableName == "player_command" -> return "stance"
            stableName.contains("chat") || stableName.contains("command") -> return "text_redacted"
            stableName.contains("custom_payload") -> return "custom_payload_redacted"
            stableName == "client_tick_end" -> return "tick_boundary"
            stableName in setOf("chunk_batch_received", "accept_teleportation", "player_loaded") -> return "protocol_ack"
        }
        val name = generateSequence(type as Class<*>?) { it.superclass }.joinToString(" ") { it.simpleName }
        return when {
            "PlayerInput" in name -> "movement_controls"
            "MovePlayer" in name -> "camera_or_position"
            "PlayerAction" in name -> "player_action"
            "Interact" in name -> "interact"
            "UseItem" in name -> "use"
            "Swing" in name -> "swing"
            "Container" in name || "CarriedItem" in name -> "inventory"
            "PlayerCommand" in name -> "stance"
            "Chat" in name || "Command" in name -> "text_redacted"
            "CustomPayload" in name -> "custom_payload_redacted"
            else -> "other"
        }
    }

    private fun isPrivatePayload(packetType: String, name: String): Boolean =
        packetType.contains("chat", true) || packetType.contains("command", true) ||
            packetType.contains("custom_payload", true) || name.contains("Chat", true) ||
            name == "ServerboundCommandPacket" || name.contains("ChatCommand", true) ||
            name.contains("CustomPayload", true)

    private fun invoke(target: Any, name: String): Any? = runCatching {
        target.javaClass.methods.firstOrNull { it.name == name && it.parameterCount == 0 }?.invoke(target)
    }.getOrNull()
}
