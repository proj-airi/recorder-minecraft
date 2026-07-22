package dev.mcdata.renderer;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class ReplayPacketCompatibilityTest {
    @AfterEach
    void disableCompatibilityPolicy() {
        ReplayPacketCompatibility.endAutomatedRender();
    }

    @Test
    void remainsStrictOutsideAutomatedRendering() {
        assertNull(ReplayPacketCompatibility.suppressIfAutomated("minecraft:player_position"));
        assertEquals(0, ReplayPacketCompatibility.snapshot().totalCount());
    }

    @Test
    void recordsDeterministicPacketCountsDuringAutomatedRendering() {
        ReplayPacketCompatibility.beginAutomatedRender();

        ReplayPacketCompatibility.Suppression first =
            ReplayPacketCompatibility.suppressIfAutomated("minecraft:player_position");
        ReplayPacketCompatibility.Suppression second =
            ReplayPacketCompatibility.suppressIfAutomated("minecraft:move_minecart");
        ReplayPacketCompatibility.Suppression third =
            ReplayPacketCompatibility.suppressIfAutomated("minecraft:player_position");

        assertEquals(1, first.packetTypeCount());
        assertEquals(2, second.totalCount());
        assertEquals(2, third.packetTypeCount());
        ReplayPacketCompatibility.Snapshot snapshot = ReplayPacketCompatibility.snapshot();
        assertEquals(ReplayPacketCompatibility.POLICY, snapshot.policy());
        assertEquals(3, snapshot.totalCount());
        assertEquals(
            List.of("minecraft:move_minecart", "minecraft:player_position"),
            List.copyOf(snapshot.packetTypes().keySet())
        );
        assertEquals(1, snapshot.packetTypes().get("minecraft:move_minecart"));
        assertEquals(2, snapshot.packetTypes().get("minecraft:player_position"));
        assertThrows(
            UnsupportedOperationException.class,
            () -> snapshot.packetTypes().put("minecraft:login", 1L)
        );
    }

    @Test
    void aNewRenderClearsPriorCounts() {
        ReplayPacketCompatibility.beginAutomatedRender();
        ReplayPacketCompatibility.suppressIfAutomated("minecraft:player_position");
        ReplayPacketCompatibility.endAutomatedRender();

        ReplayPacketCompatibility.beginAutomatedRender();

        assertEquals(0, ReplayPacketCompatibility.snapshot().totalCount());
        assertEquals(0, ReplayPacketCompatibility.snapshot().packetTypes().size());
    }
}
