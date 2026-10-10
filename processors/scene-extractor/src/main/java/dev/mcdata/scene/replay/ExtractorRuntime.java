package dev.mcdata.scene.replay;

import net.minecraft.SharedConstants;
import net.minecraft.WorldVersion;

import java.util.HashMap;
import java.util.Map;

/**
 * The code that decodes a replay in this process.
 *
 * <p>The extractor is a plain JVM program: its launcher calls {@code SceneExtractorCli} directly,
 * not through Fabric Loader's Knot launcher. No mod entrypoint runs and no mixin is applied, so
 * packets are decoded by the vanilla codecs and registries of the bundled Minecraft version. The
 * Fabric jars on the classpath only provide classes. {@link #mods} records their pinned versions
 * as provenance.
 */
public record ExtractorRuntime(
    String minecraftVersion,
    int protocolVersion,
    int dataVersion,
    Map<String, String> mods
) {
    public ExtractorRuntime {
        mods = Map.copyOf(mods);
    }

    /** Requires {@code SharedConstants.setVersion} to have run. */
    public static ExtractorRuntime current() {
        WorldVersion version = SharedConstants.getCurrentVersion();
        return new ExtractorRuntime(
            version.name(), version.protocolVersion(), version.dataVersion().version(), pinnedMods()
        );
    }

    static Map<String, String> pinnedMods() {
        Map<String, String> mods = new HashMap<>();
        putProperty(mods, "fabricloader", "mcRecorder.fabricLoaderVersion");
        putProperty(mods, "fabric-api", "mcRecorder.fabricVersion");
        putProperty(mods, "fabric-language-kotlin", "mcRecorder.fabricKotlinVersion");
        putProperty(mods, "arcade-replay", "mcRecorder.arcadeVersion");
        putProperty(mods, "recorder-minecraft-scene-extractor", "mcRecorder.extractorVersion");
        return Map.copyOf(mods);
    }

    private static void putProperty(Map<String, String> values, String modId, String property) {
        String value = System.getProperty(property);
        if (value != null && !value.isBlank()) {
            values.put(modId, value);
        }
    }
}
