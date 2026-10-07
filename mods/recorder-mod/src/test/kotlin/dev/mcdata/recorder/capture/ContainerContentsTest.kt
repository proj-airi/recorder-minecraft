package dev.mcdata.recorder.capture

import dev.recorderminecraft.artifacts.v1.ContainerSnapshot
import net.minecraft.core.BlockPos
import net.minecraft.core.registries.Registries
import net.minecraft.resources.ResourceKey
import net.minecraft.resources.ResourceLocation
import net.minecraft.world.SimpleContainer
import net.minecraft.world.item.ItemStack
import net.minecraft.world.item.Items
import net.minecraft.world.level.block.Blocks
import net.minecraft.world.level.block.entity.ChestBlockEntity
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class ContainerContentsTest {
    companion object {
        @JvmStatic
        @BeforeAll
        fun bootstrap() {
            net.minecraft.SharedConstants.setVersion(net.minecraft.DetectedVersion.BUILT_IN)
            net.minecraft.server.Bootstrap.bootStrap()
        }
    }

    @Test
    fun `known contents list only non-empty slots`() {
        val container = SimpleContainer(9)
        container.setItem(2, ItemStack(Items.DIAMOND, 3))
        container.setItem(7, ItemStack(Items.IRON_PICKAXE))

        val snapshot = ContainerSnapshot.newBuilder()
        ContainerContents.fill(container, null, snapshot)

        assertEquals(ContainerSnapshot.ContentsState.CONTENTS_STATE_KNOWN, snapshot.contentsState)
        assertEquals(9, snapshot.containerSize)
        assertEquals(listOf(2, 7), snapshot.slotsList.map { it.slot })
        assertEquals("minecraft:diamond", snapshot.slotsList[0].itemId)
        assertEquals(3, snapshot.slotsList[0].count)
        assertEquals(Items.IRON_PICKAXE.defaultInstance.maxDamage, snapshot.slotsList[1].maxDamage)
        assertTrue(snapshot.lootTable.isEmpty())
    }

    @Test
    fun `pending loot table is reported as ungenerated without being rolled`() {
        val chest = ChestBlockEntity(BlockPos(4, 64, -2), Blocks.CHEST.defaultBlockState())
        val lootTable = ResourceKey.create(
            Registries.LOOT_TABLE,
            ResourceLocation.withDefaultNamespace("chests/simple_dungeon")
        )
        chest.setLootTable(lootTable)

        val snapshot = ContainerSnapshot.newBuilder()
        ContainerContents.fill(chest, null, snapshot)

        assertEquals(ContainerSnapshot.ContentsState.CONTENTS_STATE_LOOT_UNGENERATED, snapshot.contentsState)
        assertEquals("minecraft:chests/simple_dungeon", snapshot.lootTable)
        assertEquals(27, snapshot.containerSize)
        assertTrue(snapshot.slotsList.isEmpty())
        // Observation must not consume the loot table.
        assertNotNull(chest.lootTable)
    }

    @Test
    fun `generated loot chest is known even when empty`() {
        val chest = ChestBlockEntity(BlockPos.ZERO, Blocks.CHEST.defaultBlockState())

        val snapshot = ContainerSnapshot.newBuilder()
        ContainerContents.fill(chest, null, snapshot)

        assertEquals(ContainerSnapshot.ContentsState.CONTENTS_STATE_KNOWN, snapshot.contentsState)
        assertTrue(snapshot.slotsList.isEmpty())
    }
}
