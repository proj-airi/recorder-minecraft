package dev.mcdata.scene.replay;

import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneReducer;

import java.util.ArrayList;
import java.util.List;

/**
 * Preserves local-player packet order until Flashback publishes CreatePlayer.
 *
 * <p>ServerReplay's initialization snapshot records login and local-player
 * correction packets before its synthetic {@code create_local_player} action.
 * An ordinary client already has a local player object at that point; the
 * headless reducer does not. Only updates that target the login packet's exact
 * subject entity are deferred. Updates for any other unknown entity still fail
 * closed in {@link SceneReducer}.</p>
 */
final class SubjectInitializationBuffer {
    private static final int MAX_DEFERRED_EVENTS = 1024;

    private final SceneReducer reducer;
    private final List<SceneEvent> deferred = new ArrayList<>();
    private int subjectEntityId = -1;

    SubjectInitializationBuffer(SceneReducer reducer) {
        this.reducer = reducer;
    }

    void beginSnapshot() {
        deferred.clear();
        subjectEntityId = -1;
    }

    void identifySubject(int entityId) {
        if (entityId < 0) {
            throw new SceneReducer.SceneStateException("login declared a negative subject entity id");
        }
        if (subjectEntityId >= 0 && subjectEntityId != entityId) {
            throw new SceneReducer.SceneStateException("snapshot declared multiple subject entity ids");
        }
        subjectEntityId = entityId;
    }

    boolean isUnspawnedSubject(int entityId) {
        return entityId == subjectEntityId && !reducer.hasEntity(entityId);
    }

    void apply(SceneEvent event) {
        Integer target = targetEntityId(event);
        if (target != null && isUnspawnedSubject(target)) {
            if (deferred.size() >= MAX_DEFERRED_EVENTS) {
                throw new SceneReducer.SceneStateException(
                    "local-player initialization exceeded the deferred event bound"
                );
            }
            deferred.add(event);
            return;
        }
        reducer.apply(event);
    }

    void spawn(SceneEvent.EntitySpawned event) {
        if (!event.subject() || event.entityId() != subjectEntityId) {
            throw new SceneReducer.SceneStateException(
                "Flashback local-player action does not match the login subject"
            );
        }
        if (reducer.hasEntity(subjectEntityId)) {
            throw new SceneReducer.SceneStateException(
                "Flashback published the local-player action more than once in one snapshot"
            );
        }
        reducer.apply(event);
        for (SceneEvent pending : deferred) {
            reducer.apply(pending);
        }
        deferred.clear();
    }

    private static Integer targetEntityId(SceneEvent event) {
        return switch (event) {
            case SceneEvent.EntityMoved value -> value.entityId();
            case SceneEvent.EntityTeleported value -> value.entityId();
            case SceneEvent.EntityMinecartMoved value -> value.entityId();
            case SceneEvent.EntityVehicleMoved value -> value.entityId();
            case SceneEvent.EntityRotated value -> value.entityId();
            case SceneEvent.EntityVelocityChanged value -> value.entityId();
            case SceneEvent.EntityHeadRotated value -> value.entityId();
            case SceneEvent.EntityMetadataChanged value -> value.entityId();
            case SceneEvent.EntityEquipmentChanged value -> value.entityId();
            case SceneEvent.EntityPassengersChanged value -> value.vehicleId();
            case SceneEvent.EntityLeashChanged value -> value.sourceId();
            case SceneEvent.EntityAttributesChanged value -> value.entityId();
            case SceneEvent.EntityEffectChanged value -> value.entityId();
            case SceneEvent.EntityEffectRemoved value -> value.entityId();
            default -> null;
        };
    }
}
