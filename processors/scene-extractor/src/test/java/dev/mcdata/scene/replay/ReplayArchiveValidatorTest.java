package dev.mcdata.scene.replay;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class ReplayArchiveValidatorTest {
    @Test
    void permitsCaptureInfrastructureReplacedByExtractor() {
        assertDoesNotThrow(() -> ReplayArchiveValidator.verifyModCompatibility(
            Map.of(
                "fabricloader", "0.17.2",
                "recorder-minecraft", "0.1.0+1.21.8",
                "server-replay", "3.0.1+1.21.8"
            ),
            Map.of(
                "fabricloader", "0.17.2",
                "recorder-minecraft-scene-extractor", "0.1.0"
            )
        ));
    }

    @Test
    void permitsLegacyRecorderModIdInExistingReplays() {
        assertDoesNotThrow(() -> ReplayArchiveValidator.verifyModCompatibility(
            Map.of(
                "mc-recorder", "0.1.0+1.21.8",
                "server-replay", "3.0.1+1.21.8"
            ),
            Map.of("recorder-minecraft-scene-extractor", "0.1.0")
        ));
    }

    @Test
    void stillRequiresExactContentModVersions() {
        assertThrows(IOException.class, () -> ReplayArchiveValidator.verifyModCompatibility(
            Map.of("example-content", "1.0.0"),
            Map.of("example-content", "2.0.0")
        ));
    }

    @Test
    void extractorRuntimePinsMatchTheCaptureServer() {
        assertEquals("0.136.1+1.21.8", System.getProperty("mcRecorder.fabricVersion"));
        assertEquals(
            "1.13.13+kotlin.2.4.10",
            System.getProperty("mcRecorder.fabricKotlinVersion")
        );
        assertEquals("0.6.2-beta.49+1.21.8", System.getProperty("mcRecorder.arcadeVersion"));
    }
}
