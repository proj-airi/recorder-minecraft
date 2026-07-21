package dev.mcdata.renderer;

import com.moulberry.flashback.visuals.ReplayVisuals;

final class ReplayPresentation {
    private ReplayPresentation() {
    }

    static void configureClientGui(ReplayVisuals visuals, boolean noGui) {
        if (noGui) {
            return;
        }
        visuals.showHotbar = true;
        visuals.showChat = true;
        visuals.showBossBar = true;
        visuals.showTitleText = true;
        visuals.showScoreboard = true;
        visuals.showActionBar = true;
    }

    static boolean useSpectatedPlayerCamera(boolean noGui) {
        return !noGui;
    }

    static boolean hideTrackedPlayerDuringExport(boolean noGui) {
        return noGui;
    }
}
