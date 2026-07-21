package dev.mcdata.scene.core;

import dev.mcdata.scene.job.SceneJob;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class SceneReducerTest {
    private static final UUID PLAYER = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID CONNECTION = UUID.fromString("22222222-2222-2222-2222-222222222222");
    private static final UUID SEGMENT = UUID.fromString("33333333-3333-3333-3333-333333333333");

    @Test
    void tracksSubjectAcrossMovementAndFrame() {
        SceneReducer reducer = new SceneReducer(job());
        reducer.beginSegment();
        reducer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));
        reducer.apply(subject(7, new SceneEvent.Vec3(10, 70, 20)));
        reducer.apply(new SceneEvent.EntityMoved(
            7, new SceneEvent.Vec3(0.5, 1, -0.25), 90.0F, 10.0F, true
        ));

        SceneFrame frame = reducer.frame(
            new TimelineMarker("session", CONNECTION, 101, 44), 13, source()
        ).orElseThrow();
        assertEquals(new SceneEvent.Vec3(10.5, 71, 19.75), frame.subjectPosition());
        assertEquals("minecraft:overworld", frame.dimension());
        assertTrue(frame.complete());
    }

    @Test
    void preservesSubjectButClearsOtherEntitiesAcrossRespawn() {
        SceneReducer reducer = new SceneReducer(job());
        reducer.beginSegment();
        reducer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));
        reducer.apply(subject(7, new SceneEvent.Vec3(1, 2, 3)));
        reducer.apply(entity(8));
        reducer.apply(new SceneEvent.DimensionChanged("minecraft:the_nether", 7, 0, 256));
        reducer.apply(new SceneEvent.EntityTeleported(
            7, new SceneEvent.Vec3(4, 5, 6), new SceneEvent.Vec3(0, 0, 0),
            0, 0, Set.of(), true
        ));
        assertThrows(
            SceneReducer.SceneStateException.class,
            () -> reducer.apply(new SceneEvent.EntityVelocityChanged(8, new SceneEvent.Vec3(0, 0, 0)))
        );
        assertEquals(
            new SceneEvent.Vec3(4, 5, 6),
            reducer.frame(new TimelineMarker("session", CONNECTION, 100, 1), 1, source())
                .orElseThrow().subjectPosition()
        );
    }

    @Test
    void distinguishesChunkZWhenValidatingBlockUpdates() {
        SceneReducer reducer = new SceneReducer(job());
        reducer.beginSegment();
        reducer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));
        reducer.apply(new SceneEvent.SectionLoaded(
            "minecraft:overworld", 2, 5, 0, emptySection()
        ));
        reducer.apply(new SceneEvent.BlockChanged(
            "minecraft:overworld", 32, 0, 80,
            new SceneEvent.BlockState("minecraft:stone", Map.of())
        ));
        assertThrows(
            SceneReducer.SceneStateException.class,
            () -> reducer.apply(new SceneEvent.BlockChanged(
                "minecraft:overworld", 32, 0, 32,
                new SceneEvent.BlockState("minecraft:stone", Map.of())
            ))
        );
    }

    @Test
    void materializesBlockAndEntityUpdatesIntoFrameSnapshot() {
        SceneReducer reducer = new SceneReducer(job());
        reducer.beginSegment();
        reducer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));
        reducer.apply(new SceneEvent.SectionLoaded(
            "minecraft:overworld", 2, 5, 0, emptySection()
        ));
        reducer.apply(subject(7, new SceneEvent.Vec3(32.5, 1, 80.5)));
        SceneEvent.EncodedValue value = new SceneEvent.EncodedValue(
            "minecraft:entity_metadata", "packet", 3, "AA=="
        );
        reducer.apply(new SceneEvent.EntityMetadataChanged(7, Map.of(4, value)));
        reducer.apply(new SceneEvent.BlockChanged(
            "minecraft:overworld", 33, 2, 83,
            new SceneEvent.BlockState("minecraft:stone", Map.of())
        ));

        SceneSnapshot snapshot = reducer.snapshot();
        SceneEvent.SectionSnapshot section = snapshot.sections().getFirst().snapshot();
        int offset = 2 * 256 + 3 * 16 + 1;
        assertEquals("minecraft:stone", section.palette().get(section.indices()[offset]).name());
        assertEquals(value, snapshot.entities().getFirst().metadata().get(4));
        assertEquals("minecraft:overworld", snapshot.entities().getFirst().dimension());
    }

    @Test
    void retainsBlockEntityAcrossCompatiblePropertyUpdate() {
        SceneReducer reducer = reducerWithSection(2, 5, sectionWith(
            1, 2, 3, blockEntityState("minecraft:chest", "minecraft:chest", "north")
        ));
        reducer.apply(blockEntity(33, 2, 83, "minecraft:chest"));

        reducer.apply(new SceneEvent.BlockChanged(
            "minecraft:overworld", 33, 2, 83,
            blockEntityState("minecraft:chest", "minecraft:chest", "south")
        ));

        assertEquals(1, reducer.snapshot().blockEntities().size());
        assertEquals("minecraft:chest", reducer.snapshot().blockEntities().getFirst().typeId());
    }

    @Test
    void removesBlockEntityWhenBlockTypeChanges() {
        SceneReducer reducer = reducerWithSection(2, 5, sectionWith(
            1, 2, 3, blockEntityState("minecraft:chest", "minecraft:chest", "north")
        ));
        reducer.apply(blockEntity(33, 2, 83, "minecraft:chest"));

        reducer.apply(new SceneEvent.BlockChanged(
            "minecraft:overworld", 33, 2, 83,
            new SceneEvent.BlockState("minecraft:stone", Map.of())
        ));

        assertTrue(reducer.snapshot().blockEntities().isEmpty());
    }

    @Test
    void removesBlockEntityWhenSameBlockStateIsIncompatibleWithItsType() {
        SceneReducer reducer = reducerWithSection(2, 5, sectionWith(
            1, 2, 3, blockEntityState("minecraft:chest", "minecraft:chest", "north")
        ));
        reducer.apply(blockEntity(33, 2, 83, "minecraft:furnace"));

        reducer.apply(new SceneEvent.BlockChanged(
            "minecraft:overworld", 33, 2, 83,
            blockEntityState("minecraft:chest", "minecraft:chest", "south")
        ));

        assertTrue(reducer.snapshot().blockEntities().isEmpty());
    }

    @Test
    void fullChunkReplacementClearsOnlyAbsentBlockEntitiesInThatChunk() {
        SceneReducer reducer = reducerWithSection(2, 5, sectionWith(
            1, 2, 3, blockEntityState("minecraft:chest", "minecraft:chest", "north")
        ));
        reducer.apply(new SceneEvent.BlockChanged(
            "minecraft:overworld", 34, 2, 83,
            blockEntityState("minecraft:furnace", "minecraft:furnace", "north")
        ));
        reducer.apply(new SceneEvent.SectionLoaded(
            "minecraft:overworld", 3, 5, 0, sectionWith(
                1, 2, 3, blockEntityState("minecraft:chest", "minecraft:chest", "north")
            )
        ));
        reducer.apply(blockEntity(33, 2, 83, "minecraft:chest"));
        reducer.apply(blockEntity(34, 2, 83, "minecraft:furnace"));
        reducer.apply(blockEntity(49, 2, 83, "minecraft:chest"));

        reducer.apply(new SceneEvent.ChunkReplaced("minecraft:overworld", 2, 5));
        reducer.apply(new SceneEvent.SectionLoaded(
            "minecraft:overworld", 2, 5, 0, sectionWith(
                2, 2, 3, blockEntityState("minecraft:barrel", "minecraft:barrel", "north")
            )
        ));
        reducer.apply(blockEntity(34, 2, 83, "minecraft:barrel"));

        List<SceneSnapshot.BlockEntity> blockEntities = reducer.snapshot().blockEntities();
        assertEquals(2, blockEntities.size());
        assertFalse(blockEntities.stream().anyMatch(blockEntity -> blockEntity.x() == 33));
        assertTrue(blockEntities.stream().anyMatch(blockEntity ->
            blockEntity.x() == 34 && blockEntity.typeId().equals("minecraft:barrel")
        ));
        assertTrue(blockEntities.stream().anyMatch(blockEntity -> blockEntity.x() == 49));
    }

    @Test
    void rejectsTimelineIdentityMismatch() {
        SceneReducer reducer = new SceneReducer(job());
        reducer.beginSegment();
        reducer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));
        reducer.apply(subject(7, new SceneEvent.Vec3(0, 0, 0)));
        assertThrows(
            SceneReducer.SceneStateException.class,
            () -> reducer.frame(
                new TimelineMarker("other", CONNECTION, 100, 1), 0, source()
            )
        );
    }

    private static SceneEvent.EntitySpawned subject(int id, SceneEvent.Vec3 position) {
        return new SceneEvent.EntitySpawned(
            id, PLAYER, "minecraft:player", position, new SceneEvent.Vec3(0, 0, 0),
            new SceneEvent.Rotation(0, 0, 0), 0.6, 1.8, 0, true
        );
    }

    private static SceneEvent.EntitySpawned entity(int id) {
        return new SceneEvent.EntitySpawned(
            id, UUID.randomUUID(), "minecraft:pig", new SceneEvent.Vec3(0, 0, 0),
            new SceneEvent.Vec3(0, 0, 0), new SceneEvent.Rotation(0, 0, 0),
            0.9, 0.9, 0, false
        );
    }

    private static SceneEvent.SectionSnapshot emptySection() {
        return new SceneEvent.SectionSnapshot(
            List.of(new SceneEvent.BlockState("minecraft:air", Map.of())), new int[4096]
        );
    }

    private static SceneReducer reducerWithSection(
        int chunkX,
        int chunkZ,
        SceneEvent.SectionSnapshot section
    ) {
        SceneReducer reducer = new SceneReducer(job());
        reducer.beginSegment();
        reducer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));
        reducer.apply(new SceneEvent.SectionLoaded(
            "minecraft:overworld", chunkX, chunkZ, 0, section
        ));
        return reducer;
    }

    private static SceneEvent.SectionSnapshot sectionWith(
        int x,
        int y,
        int z,
        SceneEvent.BlockState state
    ) {
        int[] indices = new int[4096];
        indices[y * 256 + z * 16 + x] = 1;
        return new SceneEvent.SectionSnapshot(
            List.of(new SceneEvent.BlockState("minecraft:air", Map.of()), state), indices
        );
    }

    private static SceneEvent.BlockEntityChanged blockEntity(
        int x,
        int y,
        int z,
        String type
    ) {
        return new SceneEvent.BlockEntityChanged(
            "minecraft:overworld", x, y, z, type,
            new SceneEvent.EncodedValue("minecraft:nbt", "nbt", -1, "CgAAAA==")
        );
    }

    private static SceneEvent.BlockState blockEntityState(
        String block,
        String blockEntityType,
        String facing
    ) {
        return new SceneEvent.BlockState(
            block, Map.of("facing", facing), Set.of(blockEntityType)
        );
    }

    private static SceneJob job() {
        return new SceneJob(
            Path.of("/tmp/job/scene-job.json"), "job", "session", PLAYER, CONNECTION,
            100, 101, Path.of("/tmp/job/spool"), Path.of("/tmp/job/result.json"),
            List.of(source()), true
        );
    }

    private static SceneJob.SourceReplay source() {
        return new SceneJob.SourceReplay(SEGMENT, 0, Path.of("/tmp/source.zip"), "0".repeat(64), 1);
    }
}
