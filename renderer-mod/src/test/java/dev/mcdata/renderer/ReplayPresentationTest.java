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
    void leavesLegacyCleanCameraVisualsUnchanged() {
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
}
