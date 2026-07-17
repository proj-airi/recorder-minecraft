package dev.mcdata.recorder.capture

import com.google.gson.JsonObject
import dev.mcdata.recorder.mixin.ServerboundInteractPacketAccessor
import dev.mcdata.recorder.model.InputFlags
import net.minecraft.core.registries.BuiltInRegistries
import net.minecraft.network.protocol.Packet
import net.minecraft.network.protocol.game.ServerboundInteractPacket
import net.minecraft.network.protocol.game.ServerboundMovePlayerPacket
import net.minecraft.network.protocol.game.ServerboundPlayerInputPacket
import net.minecraft.server.level.ServerPlayer
import net.minecraft.world.InteractionHand
import net.minecraft.world.entity.player.Input
import net.minecraft.world.phys.Vec3
import java.util.Locale

object PacketNormalizer {
    data class Result(val data: JsonObject, val input: InputFlags? = null)

    fun normalize(packet: Packet<*>): Result {
        val simpleName = packet.javaClass.simpleName
        val packetType = runCatching { packet.type().toString() }.getOrDefault(simpleName)
        val data = JsonObject().apply {
            addProperty("packet_class", packet.javaClass.name)
            addProperty("packet_type", packetType)
            addProperty("action_kind", actionKind(packetType, packet.javaClass))
            addProperty("raw_bytes_available", false)
        }

        if (isPrivatePayload(packetType, simpleName)) {
            data.addProperty("payload_redacted", true)
            return Result(data)
        }

        val input = if (packet is ServerboundPlayerInputPacket) {
            readInput(packet.input()).also { flags ->
                data.add("input", JsonObject().apply {
                    addProperty("forward", flags.forward)
                    addProperty("backward", flags.backward)
                    addProperty("left", flags.left)
                    addProperty("right", flags.right)
                    addProperty("jump", flags.jump)
                    addProperty("sneak", flags.sneak)
                    addProperty("sprint", flags.sprint)
                })
            }
        } else null

        if (packet is ServerboundMovePlayerPacket) {
            addMoveFields(packet, data)
        }
        if (packet is ServerboundInteractPacket) {
            addInteractFields(packet, data)
        }

        listOf(
            "getAction" to "action",
            "getDirection" to "direction",
            "getHand" to "hand",
            "getSequence" to "interaction_sequence",
            "getSlot" to "slot",
            "getContainerId" to "container_id",
            "getStateId" to "container_state_id",
            "getSlotNum" to "slot_number",
            "getButtonNum" to "button_number",
            "getClickType" to "click_type",
            "getData" to "action_data",
            "getId" to "entity_id",
            "getYRot" to "yaw",
            "getXRot" to "pitch",
            "isUsingSecondaryAction" to "secondary_action",
            "containerId" to "container_id",
            "stateId" to "container_state_id",
            "slotNum" to "slot_number",
            "buttonNum" to "button_number",
            "clickType" to "click_type",
            "changedSlots" to "changed_slots",
            "carriedItem" to "carried_item"
        ).forEach { (method, key) -> addZeroArg(packet, method, key, data) }

        invoke(packet, "getPos")?.let { data.add("block_pos", structuredValue(it)) }
        invoke(packet, "getHitResult")?.let { hit ->
            data.add("block_hit", JsonObject().apply {
                invoke(hit, "getBlockPos")?.let { add("block_pos", structuredValue(it)) }
                invoke(hit, "getDirection")?.let { addProperty("direction", it.toString()) }
                invoke(hit, "getLocation")?.let { add("location", structuredValue(it)) }
                invoke(hit, "isInside")?.let { addProperty("inside", it as Boolean) }
            })
        }
        return Result(data, input)
    }

    fun enrichAtApply(player: ServerPlayer, packet: Packet<*>, data: JsonObject) {
        if (packet !is ServerboundInteractPacket) return
        val target = packet.getTarget(player.level()) ?: return
        data.add("target", JsonObject().apply {
            addProperty("entity_id", target.id)
            addProperty("uuid", target.uuid.toString())
            addProperty("type", BuiltInRegistries.ENTITY_TYPE.getKey(target.type).toString())
            addProperty("is_player", target is ServerPlayer)
        })
    }

    private fun readInput(input: Input): InputFlags = InputFlags(
        forward = input.forward(),
        backward = input.backward(),
        left = input.left(),
        right = input.right(),
        jump = input.jump(),
        sneak = input.shift(),
        sprint = input.sprint()
    )

    private fun addMoveFields(packet: ServerboundMovePlayerPacket, data: JsonObject) {
        val hasPosition = packet.hasPosition()
        val hasRotation = packet.hasRotation()
        data.addProperty("has_position", hasPosition)
        data.addProperty("has_rotation", hasRotation)
        data.addProperty("on_ground", packet.isOnGround)
        data.addProperty("horizontal_collision", packet.horizontalCollision())
        if (hasPosition) {
            data.addProperty("x", packet.getX(Double.NaN))
            data.addProperty("y", packet.getY(Double.NaN))
            data.addProperty("z", packet.getZ(Double.NaN))
        }
        if (hasRotation) {
            data.addProperty("yaw", packet.getYRot(Float.NaN))
            data.addProperty("pitch", packet.getXRot(Float.NaN))
        }
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
        packetType.contains("chat", ignoreCase = true) ||
            packetType.contains("command", ignoreCase = true) ||
            packetType.contains("custom_payload", ignoreCase = true) ||
            name.contains("Chat", ignoreCase = true) ||
            name == "ServerboundCommandPacket" ||
            name.contains("ChatCommand", ignoreCase = true) ||
            name.contains("CustomPayload", ignoreCase = true)

    private fun addInteractFields(packet: ServerboundInteractPacket, data: JsonObject) {
        data.addProperty("entity_id", (packet as ServerboundInteractPacketAccessor).mcRecorderEntityId())
        packet.dispatch(object : ServerboundInteractPacket.Handler {
            override fun onInteraction(hand: InteractionHand) {
                data.addProperty("interaction", "interact")
                data.addProperty("hand", hand.name.lowercase(Locale.ROOT))
            }

            override fun onInteraction(hand: InteractionHand, location: Vec3) {
                data.addProperty("interaction", "interact_at")
                data.addProperty("hand", hand.name.lowercase(Locale.ROOT))
                data.add("location", structuredValue(location))
            }

            override fun onAttack() {
                data.addProperty("interaction", "attack")
            }
        })
    }

    private fun addZeroArg(target: Any, method: String, key: String, data: JsonObject) {
        invoke(target, method)?.let { value ->
            when (value) {
                is Boolean -> data.addProperty(key, value)
                is Number -> data.addProperty(key, value)
                is Enum<*> -> data.addProperty(key, value.name.lowercase(Locale.ROOT))
                else -> data.addProperty(key, value.toString())
            }
        }
    }

    private fun structuredValue(value: Any): JsonObject {
        val result = JsonObject()
        val x = invoke(value, "getX")
        val y = invoke(value, "getY")
        val z = invoke(value, "getZ")
        if (x is Number && y is Number && z is Number) {
            result.addProperty("x", x)
            result.addProperty("y", y)
            result.addProperty("z", z)
        } else {
            result.addProperty("value", value.toString())
        }
        return result
    }

    private fun invoke(target: Any, name: String): Any? = runCatching {
        target.javaClass.methods.firstOrNull { it.name == name && it.parameterCount == 0 }?.invoke(target)
    }.getOrNull()

}
