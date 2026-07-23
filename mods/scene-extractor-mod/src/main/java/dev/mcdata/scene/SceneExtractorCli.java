package dev.mcdata.scene;

import dev.mcdata.scene.replay.ReplayArchiveValidator;
import net.minecraft.DetectedVersion;
import net.minecraft.SharedConstants;
import net.minecraft.core.LayeredRegistryAccess;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.server.Bootstrap;
import net.minecraft.server.RegistryLayer;
import net.minecraft.server.packs.BuiltInMetadata;
import net.minecraft.server.packs.PackLocationInfo;
import net.minecraft.server.packs.PackType;
import net.minecraft.server.packs.VanillaPackResources;
import net.minecraft.server.packs.VanillaPackResourcesBuilder;
import net.minecraft.server.packs.metadata.pack.PackMetadataSection;
import net.minecraft.server.packs.repository.KnownPack;
import net.minecraft.server.packs.repository.PackSource;
import net.minecraft.server.packs.resources.MultiPackResourceManager;
import net.minecraft.resources.RegistryDataLoader;

import java.io.IOException;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/** Headless command-line entrypoint for one scene extraction job. */
public final class SceneExtractorCli {
    private static final String ENVIRONMENT_JOB = "MC_RECORDER_SCENE_JOB";
    private static final String PROPERTY_JOB = "mc.recorder.sceneJob";

    private SceneExtractorCli() { }

    public static void main(String[] args) throws Exception {
        Path request = resolveRequestPath(args);
        SharedConstants.setVersion(DetectedVersion.BUILT_IN);
        Bootstrap.bootStrap();
        RegistryAccess registries = createHeadlessRegistries();
        new SceneExtractionRunner(registries, ReplayArchiveValidator.runtimeMods()).run(request);
    }

    private static RegistryAccess createHeadlessRegistries() throws IOException {
        LayeredRegistryAccess<RegistryLayer> registries = RegistryLayer.createRegistryAccess();
        PackLocationInfo vanilla = new PackLocationInfo(
            "vanilla",
            Component.literal("vanilla"),
            PackSource.BUILT_IN,
            Optional.of(KnownPack.vanilla(SharedConstants.getCurrentVersion().id()))
        );
        BuiltInMetadata metadata = BuiltInMetadata.of(
            PackMetadataSection.TYPE,
            new PackMetadataSection(Component.literal("vanilla"), SharedConstants.getCurrentVersion().packVersion(PackType.SERVER_DATA), Optional.empty())
        );
        try (
            VanillaPackResources resources = new VanillaPackResourcesBuilder()
                .pushJarResources()
                .exposeNamespace("minecraft")
                .setMetadata(metadata)
                .build(vanilla);
            MultiPackResourceManager resourceManager = new MultiPackResourceManager(PackType.SERVER_DATA, List.of(resources))
        ) {
            RegistryAccess.Frozen worldgen = RegistryDataLoader.load(
                resourceManager,
                registries.getAccessForLoading(RegistryLayer.WORLDGEN).listRegistries().toList(),
                RegistryDataLoader.SYNCHRONIZED_REGISTRIES.stream()
                    .filter(data -> !data.key().equals(Registries.ENCHANTMENT))
                    .filter(data -> !data.key().equals(Registries.TEST_ENVIRONMENT))
                    .filter(data -> !data.key().equals(Registries.TEST_INSTANCE))
                    .filter(data -> !data.key().equals(Registries.DIALOG))
                    .toList()
            );
            registries = registries.replaceFrom(RegistryLayer.WORLDGEN, worldgen);
            return registries.compositeAccess();
        }
    }

    static Path resolveRequestPath(String[] args) {
        String cli = null;
        for (int index = 0; index < args.length; index++) {
            String arg = args[index];
            if (arg.equals("--job")) {
                if (cli != null || index + 1 >= args.length) {
                    throw new IllegalArgumentException("--job requires exactly one value");
                }
                cli = args[++index];
            } else {
                throw new IllegalArgumentException("unsupported argument: " + arg);
            }
        }
        String environment = System.getenv(ENVIRONMENT_JOB);
        String property = System.getProperty(PROPERTY_JOB);
        Map<String, String> values = new HashMap<>();
        if (cli != null && !cli.isBlank()) {
            values.put("--job", cli);
        }
        if (environment != null && !environment.isBlank()) {
            values.put(ENVIRONMENT_JOB, environment);
        }
        if (property != null && !property.isBlank()) {
            values.put("-D" + PROPERTY_JOB, property);
        }
        if (values.size() != 1) {
            throw new IllegalArgumentException(
                "set exactly one scene job path via --job, " + ENVIRONMENT_JOB + ", or -D" + PROPERTY_JOB
            );
        }
        return Path.of(values.values().iterator().next()).toAbsolutePath().normalize();
    }
}
