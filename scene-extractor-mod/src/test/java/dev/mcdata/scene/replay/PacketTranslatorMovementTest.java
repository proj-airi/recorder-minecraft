package dev.mcdata.scene.replay;

import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneReducer;
import dev.mcdata.scene.core.SceneSnapshot;
import dev.mcdata.scene.job.SceneJob;
import net.minecraft.core.RegistryAccess;
import net.minecraft.network.protocol.game.ClientboundMoveMinecartPacket;
import net.minecraft.network.protocol.game.ClientboundMoveVehiclePacket;
import net.minecraft.network.protocol.game.ClientboundPlayerRotationPacket;
import net.minecraft.world.entity.vehicle.NewMinecartBehavior;
import net.minecraft.world.phys.Vec3;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class PacketTranslatorMovementTest {
    @Test
    void appliesTheFinalMinecartStepWithoutClobberingGroundOrHeadRotation() throws IOException {
        SceneReducer reducer = reducer();
        reducer.apply(entity(8, false, new SceneEvent.Vec3(1, 2, 3), new SceneEvent.Vec3(4, 5, 6)));
        reducer.apply(new SceneEvent.EntityMoved(
            8, new SceneEvent.Vec3(0, 0, 0), null, null, true
        ));
        PacketTranslator translator = new PacketTranslator(RegistryAccess.EMPTY, reducer);
        NewMinecartBehavior.MinecartStep first = new NewMinecartBehavior.MinecartStep(
            new Vec3(10, 20, 30), new Vec3(1, 2, 3), 20, 30, 1
        );
        NewMinecartBehavior.MinecartStep last = new NewMinecartBehavior.MinecartStep(
            new Vec3(40, 50, 60), new Vec3(4, 5, 6), 70, 80, 1
        );

        apply(translator.translate(new ClientboundMoveMinecartPacket(8, List.of(first, last))), reducer);

        SceneSnapshot.Entity minecart = entity(reducer, 8);
        assertEquals(new SceneEvent.Vec3(40, 50, 60), minecart.position());
        assertEquals(new SceneEvent.Vec3(4, 5, 6), minecart.velocity());
        assertEquals(70.0F, minecart.yaw());
        assertEquals(80.0F, minecart.pitch());
        assertEquals(13.0F, minecart.headYaw());
        assertTrue(minecart.onGround());
    }

    @Test
    void appliesVehicleCorrectionToTheSubjectsOutermostVehicleOnly() throws IOException {
        SceneReducer reducer = reducer();
        reducer.apply(entity(8, false, new SceneEvent.Vec3(8, 8, 8), new SceneEvent.Vec3(1, 1, 1)));
        reducer.apply(entity(9, false, new SceneEvent.Vec3(9, 9, 9), new SceneEvent.Vec3(2, 3, 4)));
        reducer.apply(new SceneEvent.EntityMoved(
            9, new SceneEvent.Vec3(0, 0, 0), null, null, true
        ));
        reducer.apply(new SceneEvent.EntityPassengersChanged(8, List.of(7)));
        reducer.apply(new SceneEvent.EntityPassengersChanged(9, List.of(8)));
        PacketTranslator translator = new PacketTranslator(RegistryAccess.EMPTY, reducer);

        apply(translator.translate(new ClientboundMoveVehiclePacket(
            new Vec3(100, 64, 200), 45, 15
        )), reducer);

        SceneSnapshot.Entity inner = entity(reducer, 8);
        SceneSnapshot.Entity root = entity(reducer, 9);
        assertEquals(new SceneEvent.Vec3(8, 8, 8), inner.position());
        assertEquals(new SceneEvent.Vec3(100, 64, 200), root.position());
        assertEquals(new SceneEvent.Vec3(2, 3, 4), root.velocity());
        assertEquals(45.0F, root.yaw());
        assertEquals(15.0F, root.pitch());
        assertEquals(13.0F, root.headYaw());
        assertTrue(root.onGround());
    }

    @Test
    void appliesPlayerRotationWithoutClobberingOtherSubjectState() throws IOException {
        SceneReducer reducer = reducer();
        reducer.apply(new SceneEvent.EntityMoved(
            7, new SceneEvent.Vec3(0, 0, 0), null, null, true
        ));
        PacketTranslator translator = new PacketTranslator(RegistryAccess.EMPTY, reducer);
        SceneSnapshot.Entity before = entity(reducer, 7);

        apply(translator.translate(new ClientboundPlayerRotationPacket(135, -20)), reducer);

        SceneSnapshot.Entity after = entity(reducer, 7);
        assertEquals(before.position(), after.position());
        assertEquals(before.velocity(), after.velocity());
        assertEquals(135.0F, after.yaw());
        assertEquals(-20.0F, after.pitch());
        assertEquals(before.headYaw(), after.headYaw());
        assertTrue(after.onGround());
    }

    @Test
    void failClosedClassifierCoversUnsupportedPersistentMovement() {
        assertTrue(FlashbackSceneExtractor.couldAffectScene("minecraft:projectile_power"));
        assertTrue(FlashbackSceneExtractor.couldAffectScene("minecraft:explode"));
        assertTrue(FlashbackSceneExtractor.couldAffectScene("minecraft:player_look_at"));
        assertTrue(FlashbackSceneExtractor.couldAffectScene("minecraft:move_vehicle"));
        assertTrue(FlashbackSceneExtractor.couldAffectScene("minecraft:move_minecart_along_track"));
        assertFalse(FlashbackSceneExtractor.couldAffectScene("minecraft:sound_entity"));
    }

    private static SceneReducer reducer() {
        SceneReducer reducer = new SceneReducer(job());
        reducer.beginSegment();
        reducer.apply(new SceneEvent.DimensionChanged("minecraft:overworld", 7, -64, 384));
        reducer.apply(entity(
            7, true, new SceneEvent.Vec3(1, 64, 2), new SceneEvent.Vec3(0.1, 0.2, 0.3)
        ));
        return reducer;
    }

    private static SceneEvent.EntitySpawned entity(
        int id,
        boolean subject,
        SceneEvent.Vec3 position,
        SceneEvent.Vec3 velocity
    ) {
        return new SceneEvent.EntitySpawned(
            id, new UUID(0, id), id == 7 ? "minecraft:player" : "minecraft:minecart",
            position, velocity, new SceneEvent.Rotation(11, 12, 13), 1, 1, 0, subject
        );
    }

    private static SceneSnapshot.Entity entity(SceneReducer reducer, int id) {
        return reducer.snapshot().entities().stream()
            .filter(entity -> entity.networkId() == id)
            .findFirst()
            .orElseThrow();
    }

    private static void apply(PacketTranslator.Translation translated, SceneReducer reducer) {
        translated.events().forEach(reducer::apply);
    }

    private static SceneJob job() {
        return new SceneJob(
            Path.of("job.json"), "job", "session", new UUID(0, 7), new UUID(0, 8),
            0, 10, Path.of("stream"), Path.of("result.json"), List.of(), subjectPoses(), true
        );
    }

    private static SceneJob.SubjectPoseInput subjectPoses() {
        int count = 11;
        String[] dimensions = new String[count];
        java.util.Arrays.fill(dimensions, "minecraft:overworld");
        int[] entityIds = new int[count];
        java.util.Arrays.fill(entityIds, 7);
        return new SceneJob.SubjectPoseInput(
            "mc-recorder-subject-poses-v1", Path.of("subject-poses.jsonl"), "a".repeat(64),
            11, count, 0, 10,
            List.of(new SceneJob.SourceEpoch(0, "b".repeat(64), 1, 1)),
            new SceneJob.SubjectPoseFileIdentity("test", 0),
            new SceneJob.SubjectPoseTimeline(
                0, "session", new UUID(0, 7), new UUID(0, 8), entityIds, dimensions,
                new double[count], new double[count], new double[count],
                new double[count], new double[count], new double[count],
                new float[count], new float[count], new float[count], new boolean[count]
            )
        );
    }
}
