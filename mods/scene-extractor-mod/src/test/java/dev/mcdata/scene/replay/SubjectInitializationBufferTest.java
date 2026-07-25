package dev.mcdata.scene.replay;

import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneReducer;
import dev.mcdata.scene.core.SceneSnapshot;
import dev.mcdata.scene.job.SceneJob;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class SubjectInitializationBufferTest {
    private static final UUID PLAYER = UUID.fromString("11111111-1111-1111-1111-111111111111");
    private static final UUID CONNECTION = UUID.fromString("22222222-2222-2222-2222-222222222222");
    private static final UUID SEGMENT = UUID.fromString("33333333-3333-3333-3333-333333333333");

    @Test
    void replaysSubjectCorrectionsAfterFlashbackCreatesTheLocalPlayer() {
        SceneReducer reducer = new SceneReducer(job());
        reducer.beginSegment();
        SubjectInitializationBuffer buffer = new SubjectInitializationBuffer(reducer);
        buffer.beginSnapshot();
        buffer.identifySubject(7);
        buffer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));
        buffer.apply(new SceneEvent.EntityTeleported(
            7, new SceneEvent.Vec3(2, 3, 4), new SceneEvent.Vec3(0.1, 0.2, 0.3),
            30, 10, Set.of("X", "Z"), true
        ));
        SceneEvent.EncodedValue metadata = new SceneEvent.EncodedValue(
            "minecraft:entity_metadata", "packet", 3, "AA=="
        );
        buffer.apply(new SceneEvent.EntityMetadataChanged(7, Map.of(4, metadata)));

        buffer.spawn(subject(new SceneEvent.Vec3(10, 70, 20)));

        SceneSnapshot.Entity subject = reducer.snapshot().entities().getFirst();
        assertEquals(new SceneEvent.Vec3(12, 3, 24), subject.position());
        assertEquals(new SceneEvent.Vec3(0.1, 0.2, 0.3), subject.velocity());
        assertEquals(metadata, subject.metadata().get(4));
        assertEquals(1, subject.generation());
    }

    @Test
    void doesNotHideUpdatesForUnknownNonSubjectEntities() {
        SceneReducer reducer = new SceneReducer(job());
        reducer.beginSegment();
        SubjectInitializationBuffer buffer = new SubjectInitializationBuffer(reducer);
        buffer.beginSnapshot();
        buffer.identifySubject(7);
        buffer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));

        assertThrows(
            SceneReducer.SceneStateException.class,
            () -> buffer.apply(new SceneEvent.EntityVelocityChanged(
                8, new SceneEvent.Vec3(0, 0, 0)
            ))
        );
    }

    @Test
    void forcedSnapshotDropsUncommittedSubjectUpdates() {
        SceneReducer reducer = new SceneReducer(job());
        reducer.beginSegment();
        SubjectInitializationBuffer buffer = new SubjectInitializationBuffer(reducer);
        buffer.beginSnapshot();
        buffer.identifySubject(7);
        buffer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));
        buffer.apply(new SceneEvent.EntityVelocityChanged(7, new SceneEvent.Vec3(9, 9, 9)));

        reducer.beginSnapshot();
        buffer.beginSnapshot();
        buffer.identifySubject(7);
        buffer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));
        buffer.spawn(subject(new SceneEvent.Vec3(1, 2, 3)));

        assertEquals(
            new SceneEvent.Vec3(0, 0, 0),
            reducer.snapshot().entities().getFirst().velocity()
        );
    }

    private static SceneEvent.EntitySpawned subject(SceneEvent.Vec3 position) {
        return new SceneEvent.EntitySpawned(
            7, PLAYER, "minecraft:player", position, new SceneEvent.Vec3(0, 0, 0),
            new SceneEvent.Rotation(0, 0, 0), 0.6, 1.8, 0, true
        );
    }

    private static SceneJob job() {
        return new SceneJob(
            Path.of("/tmp/job/scene-job.json"), "job", "session", PLAYER, CONNECTION,
            100, 101, Path.of("/tmp/job/spool"), Path.of("/tmp/job/result.json"),
            List.of(source()), poses(), true
        );
    }

    private static SceneJob.SourceReplay source() {
        return new SceneJob.SourceReplay(
            SEGMENT, 0, Path.of("/tmp/replay.zip"), "a".repeat(64), 100
        );
    }

    private static SceneJob.SubjectPoseInput poses() {
        int count = 2;
        return new SceneJob.SubjectPoseInput(
            "mc-recorder-subject-poses-v1", Path.of("/tmp/subject-poses.jsonl"),
            "b".repeat(64), 2, 2, 100, 101,
            new SceneJob.SourceEvents("c".repeat(64), 1, 1),
            new SceneJob.SubjectPoseFileIdentity("test", 0),
            new SceneJob.SubjectPoseTimeline(
                100, "session", PLAYER, CONNECTION,
                new int[] {7, 7},
                new String[] {"minecraft:overworld", "minecraft:overworld"},
                new double[count], new double[count], new double[count],
                new double[count], new double[count], new double[count],
                new float[count], new float[count], new float[count], new boolean[count]
            )
        );
    }
}
