package dev.mcdata.scene.core;

import java.util.UUID;

public record TimelineMarker(String sessionId, UUID connectionId, long globalTick, long eventSequence) { }
