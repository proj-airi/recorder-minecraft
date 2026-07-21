package dev.mcdata.scene.core;

import dev.mcdata.scene.job.SceneJob;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
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
