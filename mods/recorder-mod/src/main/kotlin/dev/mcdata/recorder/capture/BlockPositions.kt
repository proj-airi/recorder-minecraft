package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.BlockPosition
import net.minecraft.core.BlockPos

/**
 * Container position shared by the world stream and `container_view` records. Both streams must
 * encode it identically because dimension plus position is the join key between them.
 */
internal fun blockPosition(pos: BlockPos): BlockPosition =
    BlockPosition.newBuilder().setX(pos.x).setY(pos.y).setZ(pos.z).build()
