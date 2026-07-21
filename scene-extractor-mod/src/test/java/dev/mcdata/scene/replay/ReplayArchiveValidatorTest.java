package dev.mcdata.scene.replay;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class ReplayArchiveValidatorTest {
    @Test
    void permitsCaptureInfrastructureReplacedByExtractor() {
        assertDoesNotThrow(() -> ReplayArchiveValidator.verifyModCompatibility(
            Map.of(
                "fabricloader", "0.17.2",
                "mc-recorder", "0.1.0+1.21.8",
                "server-replay", "3.0.1+1.21.8"
            ),
            Map.of(
                "fabricloader", "0.17.2",
                "mc-recorder-scene-extractor", "0.1.0"
            )
        ));
    }

    @Test
    void stillRequiresExactContentModVersions() {
        assertThrows(IOException.class, () -> ReplayArchiveValidator.verifyModCompatibility(
            Map.of("example-content", "1.0.0"),
            Map.of("example-content", "2.0.0")
        ));
    }
}
