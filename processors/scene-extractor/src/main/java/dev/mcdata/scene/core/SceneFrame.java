package dev.mcdata.scene.core;

import java.util.UUID;

public record SceneFrame(
    long globalTick,
    int replayTick,
    long eventSequence,
    UUID segmentId,
    int segmentOrdinal,
    String dimension,
    int subjectEntityId,
    SceneEvent.Vec3 subjectPosition,
    int loadedSectionCount,
    int entityCount,
    boolean complete
) { }
