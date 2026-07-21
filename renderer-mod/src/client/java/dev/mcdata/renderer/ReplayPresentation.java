package dev.mcdata.renderer;

import com.moulberry.flashback.visuals.ReplayVisuals;

import java.util.UUID;

final class ReplayPresentation {
    static final String FULL_CLIENT_PRESENTATION_CONTRACT =
        "flashback_server_spectate_structured_hud_v1";

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

    static String startSpectatingCommand(UUID playerId) {
        return "spectate " + playerId;
    }

    static String stopSpectatingCommand() {
        return "spectate";
    }

    static String resultPresentationContract(boolean noGui, String requestedContract) {
        if (noGui || requestedContract == null) {
            return null;
        }
        if (!FULL_CLIENT_PRESENTATION_CONTRACT.equals(requestedContract)) {
            throw new IllegalArgumentException(
                "Unsupported requested presentation contract: " + requestedContract
            );
        }
        return requestedContract;
    }
}
