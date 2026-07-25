package dev.mcdata.renderer;

import com.moulberry.flashback.visuals.ReplayVisuals;
import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class ReplayPresentationTest {
    @Test
    void enablesEveryRecordedClientGuiOverlay() {
        ReplayVisuals visuals = new ReplayVisuals();

        ReplayPresentation.configureClientGui(visuals, false);

        assertTrue(visuals.showHotbar);
        assertTrue(visuals.showChat);
        assertTrue(visuals.showBossBar);
        assertTrue(visuals.showTitleText);
        assertTrue(visuals.showScoreboard);
        assertTrue(visuals.showActionBar);
        assertTrue(ReplayPresentation.useSpectatedPlayerCamera(false));
        assertFalse(ReplayPresentation.hideTrackedPlayerDuringExport(false));
    }

    @Test
    void supportsHudlessTrackedCameraRendering() {
        ReplayVisuals visuals = new ReplayVisuals();

        ReplayPresentation.configureClientGui(visuals, true);

        assertTrue(visuals.showHotbar);
        assertFalse(visuals.showChat);
        assertFalse(visuals.showBossBar);
        assertFalse(visuals.showTitleText);
        assertFalse(visuals.showScoreboard);
        assertFalse(visuals.showActionBar);
        assertFalse(ReplayPresentation.useSpectatedPlayerCamera(true));
        assertTrue(ReplayPresentation.hideTrackedPlayerDuringExport(true));
    }

    @Test
    void usesFlashbacksReplayServerSpectateCommands() {
        UUID playerId = UUID.fromString("1c4883d9-66f8-4760-8177-20ddb9a2ac21");

        assertEquals("spectate " + playerId, ReplayPresentation.startSpectatingCommand(playerId));
        assertEquals("spectate", ReplayPresentation.stopSpectatingCommand());
    }

    @Test
    void rebindsRequestedPlayerAcrossDeathCameraLossAndRespawnReplacement() {
        assertEquals(
            ReplayPresentation.CameraContinuity.KEEP,
            ReplayPresentation.decideCameraContinuity(true, true, true, true, false, true)
        );
        assertEquals(
            ReplayPresentation.CameraContinuity.REBIND_PRESENT,
            ReplayPresentation.decideCameraContinuity(false, false, true, true, false, true)
        );
        assertEquals(
            ReplayPresentation.CameraContinuity.KEEP,
            ReplayPresentation.decideCameraContinuity(false, true, false, true, true, true)
        );
        assertEquals(
            ReplayPresentation.CameraContinuity.REBIND_DEATH_CAMERA,
            ReplayPresentation.decideCameraContinuity(false, false, false, true, true, true)
        );
        assertEquals(
            ReplayPresentation.CameraContinuity.REBIND_PRESENT,
            ReplayPresentation.decideCameraContinuity(false, true, true, true, false, true)
        );
        assertEquals(
            ReplayPresentation.CameraContinuity.WAIT,
            ReplayPresentation.decideCameraContinuity(false, false, false, true, false, false)
        );
        assertEquals(
            ReplayPresentation.CameraContinuity.REJECT,
            ReplayPresentation.decideCameraContinuity(false, false, false, true, false, true)
        );
        assertEquals(
            ReplayPresentation.CameraContinuity.REJECT,
            ReplayPresentation.decideCameraContinuity(false, false, false, false, true, true)
        );

        ReplayPresentation.ServerSpectateRecovery deadReplacement =
            ReplayPresentation.planServerSpectateRecovery(false, true, true);
        assertTrue(deadReplacement.pending());
        assertFalse(deadReplacement.requestNow());
        assertEquals(
            new ReplayPresentation.ServerSpectateRecovery(false, true),
            ReplayPresentation.planServerSpectateRecovery(
                deadReplacement.pending(), false, false
            )
        );
    }
}
