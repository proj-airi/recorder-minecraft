package dev.mcdata.renderer;

import com.moulberry.flashback.visuals.ReplayVisuals;
import org.junit.jupiter.api.Test;

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
}
