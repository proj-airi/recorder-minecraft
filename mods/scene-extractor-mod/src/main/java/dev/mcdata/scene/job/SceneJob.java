package dev.mcdata.scene.job;

import java.nio.file.Path;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.UUID;

import dev.mcdata.scene.core.SceneEvent;

/** Immutable, fully validated extraction request. */
public record SceneJob(
    Path requestPath,
    String jobId,
    String sessionId,
    UUID playerUuid,
    UUID connectionId,
    long globalStartTick,
    long globalEndTick,
    Path output,
    Path result,
    List<SourceReplay> sourceReplays,
    SubjectPoseInput subjectPoses,
    boolean stopWhenDone
) {
    public static final String SCOPE = "client_visible";
    public static final String METADATA_POLICY = "full_packet_metadata";
    public static final String FLASHBACK_CAPTURE_CONTRACT = "client_visible_scene_v1";
    public static final String HOTBAR_SNAPSHOT_CONTRACT = "item_stack_copy_v1";

    public SceneJob {
        sourceReplays = List.copyOf(sourceReplays);
    }

    public record SourceReplay(
        UUID segmentId,
        int segmentOrdinal,
        Path path,
        String sha256,
        long sizeBytes
    ) { }

    public record SubjectPoseInput(
        String format,
        Path path,
        String sha256,
        long sizeBytes,
        long recordCount,
        long firstTick,
        long lastTick,
        SourceEvents sourceEvents,
        SubjectPoseFileIdentity fileIdentity,
        SubjectPoseTimeline timeline
    ) {
        public SubjectPose require(long tick) {
            return timeline.require(tick);
        }
    }

    public record SourceEvents(
        String eventsSha256,
        long eventsSizeBytes,
        long recordCount
    ) { }

    public record SubjectPoseFileIdentity(String fileKey, long lastModifiedMillis) { }

    public record SubjectPose(
        long serverTick,
        String sessionId,
        UUID playerUuid,
        UUID connectionId,
        int entityId,
        String dimension,
        SceneEvent.Vec3 position,
        SceneEvent.Vec3 velocity,
        float yaw,
        float pitch,
        float headYaw,
        boolean onGround
    ) { }

    /** Compact immutable storage for a contiguous pose timeline. */
    public static final class SubjectPoseTimeline {
        private final long firstTick;
        private final String sessionId;
        private final UUID playerUuid;
        private final UUID connectionId;
        private final int[] entityIds;
        private final String[] dimensions;
        private final double[] positionX;
        private final double[] positionY;
        private final double[] positionZ;
        private final double[] velocityX;
        private final double[] velocityY;
        private final double[] velocityZ;
        private final float[] yaw;
        private final float[] pitch;
        private final float[] headYaw;
        private final boolean[] onGround;

        public SubjectPoseTimeline(
            long firstTick,
            String sessionId,
            UUID playerUuid,
            UUID connectionId,
            int[] entityIds,
            String[] dimensions,
            double[] positionX,
            double[] positionY,
            double[] positionZ,
            double[] velocityX,
            double[] velocityY,
            double[] velocityZ,
            float[] yaw,
            float[] pitch,
            float[] headYaw,
            boolean[] onGround
        ) {
            int count = entityIds.length;
            if (
                dimensions.length != count
                    || positionX.length != count
                    || positionY.length != count
                    || positionZ.length != count
                    || velocityX.length != count
                    || velocityY.length != count
                    || velocityZ.length != count
                    || yaw.length != count
                    || pitch.length != count
                    || headYaw.length != count
                    || onGround.length != count
            ) {
                throw new IllegalArgumentException("subject pose timeline arrays have different lengths");
            }
            this.firstTick = firstTick;
            this.sessionId = sessionId;
            this.playerUuid = playerUuid;
            this.connectionId = connectionId;
            this.entityIds = entityIds.clone();
            this.dimensions = dimensions.clone();
            this.positionX = positionX.clone();
            this.positionY = positionY.clone();
            this.positionZ = positionZ.clone();
            this.velocityX = velocityX.clone();
            this.velocityY = velocityY.clone();
            this.velocityZ = velocityZ.clone();
            this.yaw = yaw.clone();
            this.pitch = pitch.clone();
            this.headYaw = headYaw.clone();
            this.onGround = onGround.clone();
        }

        public int size() {
            return entityIds.length;
        }

        public SubjectPose require(long tick) {
            long offset = tick - firstTick;
            if (offset < 0 || offset >= entityIds.length) {
                throw new NoSuchElementException("subject pose is missing global tick " + tick);
            }
            int index = (int) offset;
            return new SubjectPose(
                tick,
                sessionId,
                playerUuid,
                connectionId,
                entityIds[index],
                dimensions[index],
                new SceneEvent.Vec3(positionX[index], positionY[index], positionZ[index]),
                new SceneEvent.Vec3(velocityX[index], velocityY[index], velocityZ[index]),
                yaw[index],
                pitch[index],
                headYaw[index],
                onGround[index]
            );
        }
    }
}
