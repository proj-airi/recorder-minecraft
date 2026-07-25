package dev.mcdata.renderer;

import com.moulberry.flashback.visuals.ReplayVisuals;

import java.util.UUID;

final class ReplayPresentation {
    private ReplayPresentation() {
    }

    enum CameraContinuity {
        KEEP,
        REBIND_PRESENT,
        REBIND_DEATH_CAMERA,
        WAIT,
        REJECT
    }

    record ServerSpectateRecovery(boolean pending, boolean requestNow) {
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

    static CameraContinuity decideCameraContinuity(
        boolean currentCameraMatchesPresent,
        boolean currentCameraIsRequestedPlayer,
        boolean requestedPlayerPresent,
        boolean heldDeathCameraAvailable,
        boolean replayReportsDeath,
        boolean countsTowardRender
    ) {
        if (requestedPlayerPresent) {
            return currentCameraMatchesPresent ? CameraContinuity.KEEP : CameraContinuity.REBIND_PRESENT;
        }
        if (replayReportsDeath) {
            if (currentCameraIsRequestedPlayer) {
                return CameraContinuity.KEEP;
            }
            if (heldDeathCameraAvailable) {
                return CameraContinuity.REBIND_DEATH_CAMERA;
            }
        }
        return countsTowardRender ? CameraContinuity.REJECT : CameraContinuity.WAIT;
    }

    static ServerSpectateRecovery planServerSpectateRecovery(
        boolean pending, boolean cameraRecovered, boolean replayReportsDeath
    ) {
        boolean nextPending = pending || cameraRecovered;
        boolean requestNow = nextPending && !replayReportsDeath;
        return new ServerSpectateRecovery(requestNow ? false : nextPending, requestNow);
    }

}
