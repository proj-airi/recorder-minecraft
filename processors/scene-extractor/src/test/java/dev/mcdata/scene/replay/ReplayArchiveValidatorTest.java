package dev.mcdata.scene.replay;

import com.google.gson.JsonObject;
import dev.mcdata.scene.io.Hashing;
import dev.mcdata.scene.job.SceneJob;
import dev.recorderminecraft.artifacts.v1.SceneSourceRuntime;
import dev.recorderminecraft.artifacts.v1.SceneToleratedMod;
import dev.recorderminecraft.artifacts.v1.SceneToleratedMod.Difference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class ReplayArchiveValidatorTest {
    private static final UUID SEGMENT = UUID.fromString("c6dd59e9-4638-4b31-80b9-a23363d7a179");
    private static final ExtractorRuntime RUNTIME = new ExtractorRuntime(
        "1.21.8", 772, 4440,
        Map.of(
            "fabricloader", "0.19.3",
            "fabric-api", "0.136.1+1.21.8",
            "fabric-language-kotlin", "1.13.13+kotlin.2.4.10"
        )
    );

    @Test
    void recordsAnUnrelatedExtraModAsProvenance() throws IOException {
        SceneSourceRuntime runtime = ReplayArchiveValidator.compareRuntime(
            SEGMENT, flashback("1.21.8", 772, 4440),
            arcade(Map.of("fabric-api", "0.136.1+1.21.8", "example-minimap", "2.0.0")),
            RUNTIME
        );

        assertEquals(SEGMENT.toString(), runtime.getSegmentId());
        assertEquals("1.21.8", runtime.getMinecraftVersion());
        assertEquals(772, runtime.getProtocolVersion());
        assertEquals(4440, runtime.getDataVersion());
        assertEquals(
            List.of(tolerated("example-minimap", "2.0.0", "", Difference.DIFFERENCE_NOT_IN_EXTRACTOR)),
            runtime.getToleratedModsList()
        );
    }

    @Test
    void recordsAFabricVersionMismatchAsProvenance() throws IOException {
        SceneSourceRuntime runtime = ReplayArchiveValidator.compareRuntime(
            SEGMENT, flashback("1.21.8", 772, 4440), arcade(Map.of("fabricloader", "0.19.5")), RUNTIME
        );

        assertEquals(
            List.of(tolerated("fabricloader", "0.19.5", "0.19.3", Difference.DIFFERENCE_VERSION_MISMATCH)),
            runtime.getToleratedModsList()
        );
    }

    @Test
    void rejectsAnotherMinecraftVersion() {
        IOException failure = assertThrows(IOException.class, () -> ReplayArchiveValidator.compareRuntime(
            SEGMENT, flashback("1.21.7", 772, 4438), arcade(Map.of()), RUNTIME
        ));
        assertTrue(failure.getMessage().contains("replay Minecraft 1.21.7"), failure.getMessage());
    }

    @Test
    void rejectsAnotherProtocolOrDataVersionOfTheSameName() {
        assertThrows(IOException.class, () -> ReplayArchiveValidator.compareRuntime(
            SEGMENT, flashback("1.21.8", 771, 4440), arcade(Map.of()), RUNTIME
        ));
        assertThrows(IOException.class, () -> ReplayArchiveValidator.compareRuntime(
            SEGMENT, flashback("1.21.8", 772, 4439), arcade(Map.of()), RUNTIME
        ));
    }

    @Test
    void rejectsFlashbackMetadataWithoutAMinecraftIdentity() {
        JsonObject metadata = flashback("1.21.8", 772, 4440);
        metadata.remove("protocol_version");
        assertThrows(IOException.class, () -> ReplayArchiveValidator.compareRuntime(
            SEGMENT, metadata, arcade(Map.of()), RUNTIME
        ));
    }

    @Test
    void acceptsTheAiricraftHostedPlaytestReplay(@TempDir Path directory) throws IOException {
        // Fixture: arcade_replay_meta.json and Flashback metadata.json of a real Airicraft hosted
        // playtest Play, with the local path and replay chunk data removed.
        Path replay = directory.resolve("replay.zip");
        try (ZipOutputStream zip = new ZipOutputStream(Files.newOutputStream(replay))) {
            for (String name : List.of("arcade_replay_meta.json", "metadata.json")) {
                zip.putNextEntry(new ZipEntry(name));
                try (InputStream fixture = fixture(name)) {
                    fixture.transferTo(zip);
                }
                zip.closeEntry();
            }
        }
        SceneJob.SourceReplay source = new SceneJob.SourceReplay(
            SEGMENT, 0, replay, Hashing.sha256(replay), Files.size(replay)
        );
        SceneJob job = new SceneJob(
            directory.resolve("scene-job.json"), "job", "05528100-1a00-46cf-97d3-6f76501d9e7e",
            UUID.fromString("2575798e-2b63-3ebe-a39e-5e3a3eba2b3f"),
            UUID.fromString("afe03965-030f-469f-8c5b-0dcd6c185c92"),
            11, 9901, directory.resolve("stream"), directory.resolve("result.json"), List.of(source), null, true
        );

        // The pinned versions are those of this build, so the test follows extractor upgrades.
        ExtractorRuntime extractor = new ExtractorRuntime("1.21.8", 772, 4440, ExtractorRuntime.pinnedMods());
        SceneSourceRuntime runtime = new ReplayArchiveValidator(extractor).verify(job, source).runtime();

        assertEquals("3.0.1+1.21.8", runtime.getServerReplayVersion());
        // Twelve of the thirteen recorded mods differ; only Fabric API equals the extractor pin.
        assertEquals(12, runtime.getToleratedModsCount());
        assertEquals(
            tolerated(
                "fabricloader", "0.19.5", extractor.mods().get("fabricloader"),
                Difference.DIFFERENCE_VERSION_MISMATCH
            ),
            runtime.getToleratedModsList().stream()
                .filter(mod -> mod.getModId().equals("fabricloader")).findFirst().orElseThrow()
        );
        assertTrue(runtime.getToleratedModsList().stream().anyMatch(mod ->
            mod.getModId().equals("baritone") && mod.getDifference() == Difference.DIFFERENCE_NOT_IN_EXTRACTOR
        ));
        assertTrue(runtime.getToleratedModsList().stream().noneMatch(mod -> mod.getModId().equals("fabric-api")));
    }

    @Test
    void extractorRuntimePinsMatchTheCaptureServer() {
        Map<String, String> mods = ExtractorRuntime.pinnedMods();
        assertEquals("0.136.1+1.21.8", mods.get("fabric-api"));
        assertEquals("1.13.13+kotlin.2.4.10", mods.get("fabric-language-kotlin"));
        assertEquals("0.6.2-beta.49+1.21.8", mods.get("arcade-replay"));
    }

    private static JsonObject flashback(String version, int protocol, int data) {
        JsonObject metadata = new JsonObject();
        metadata.addProperty("version_string", version);
        metadata.addProperty("protocol_version", protocol);
        metadata.addProperty("data_version", data);
        return metadata;
    }

    private static JsonObject arcade(Map<String, String> mods) {
        JsonObject metadata = new JsonObject();
        JsonObject values = new JsonObject();
        mods.forEach(values::addProperty);
        metadata.add("mods", values);
        return metadata;
    }

    private static SceneToleratedMod tolerated(String id, String source, String extractor, Difference difference) {
        return SceneToleratedMod.newBuilder()
            .setModId(id).setSourceVersion(source).setExtractorVersion(extractor).setDifference(difference).build();
    }

    private static InputStream fixture(String name) throws IOException {
        InputStream stream = ReplayArchiveValidatorTest.class.getResourceAsStream("/replay/airicraft/" + name);
        if (stream == null) {
            throw new IOException("missing test fixture " + name);
        }
        return stream;
    }
}
