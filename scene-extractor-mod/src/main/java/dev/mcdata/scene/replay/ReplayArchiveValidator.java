package dev.mcdata.scene.replay;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dev.mcdata.scene.io.Hashing;
import dev.mcdata.scene.job.SceneJob;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.ModContainer;

import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/** Verifies immutable bytes, embedded provenance, and source-compatible mod versions. */
public final class ReplayArchiveValidator {
    private static final long MAX_METADATA_BYTES = 1_048_576;
    private static final Set<String> REPLACED_CAPTURE_INFRASTRUCTURE_IDS = Set.of(
        "mc-recorder", "server-replay"
    );
    private static final Set<String> INFRASTRUCTURE_IDS = Set.of(
        "minecraft", "java", "fabricloader", "fabric-api", "fabric-language-kotlin",
        "mc-recorder", "server-replay", "mc-recorder-scene-extractor", "mixinextras", "inject"
    );

    public VerifiedSource verify(SceneJob job, SceneJob.SourceReplay source) throws IOException {
        BasicFileAttributes before = attributes(source.path());
        if (before.size() != source.sizeBytes()) {
            throw new IOException("source replay size does not match scene job: " + source.path());
        }
        String digest = Hashing.sha256(source.path());
        BasicFileAttributes after = attributes(source.path());
        requireStable(before, after, source.path());
        if (!digest.equals(source.sha256())) {
            throw new IOException("source replay SHA-256 does not match scene job: " + source.path());
        }

        JsonObject arcadeMetadata = readMetadata(source.path(), "arcade_replay_meta.json");
        JsonObject identity = requiredObject(arcadeMetadata, "mc_recorder");
        requireIdentity(identity, job, source);
        verifyMods(requiredObject(arcadeMetadata, "mods"));
        JsonObject flashbackMetadata = readMetadata(source.path(), "metadata.json");
        if (!flashbackMetadata.has("chunks") || !flashbackMetadata.has("total_ticks")) {
            throw new IOException("Flashback metadata lacks chunks or total_ticks: " + source.path());
        }
        return new VerifiedSource(source, before.fileKey(), before.size(), before.lastModifiedTime().toMillis());
    }

    public void verifyUnchanged(VerifiedSource verified) throws IOException {
        SceneJob.SourceReplay source = verified.source();
        BasicFileAttributes current = attributes(source.path());
        if (current.size() != verified.size()
            || current.lastModifiedTime().toMillis() != verified.modifiedMillis()
            || !java.util.Objects.equals(current.fileKey(), verified.fileKey())) {
            throw new IOException("source replay changed during extraction: " + source.path());
        }
        String digest = Hashing.sha256(source.path());
        if (!digest.equals(source.sha256())) {
            throw new IOException("source replay bytes changed during extraction: " + source.path());
        }
    }

    private static BasicFileAttributes attributes(java.nio.file.Path path) throws IOException {
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) {
            throw new IOException("source replay is no longer a regular non-symlink file: " + path);
        }
        return Files.readAttributes(path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
    }

    private static void requireStable(BasicFileAttributes before, BasicFileAttributes after, java.nio.file.Path path)
        throws IOException {
        if (before.size() != after.size()
            || !before.lastModifiedTime().equals(after.lastModifiedTime())
            || !java.util.Objects.equals(before.fileKey(), after.fileKey())) {
            throw new IOException("source replay changed while it was hashed: " + path);
        }
    }

    private static JsonObject readMetadata(java.nio.file.Path archive, String name) throws IOException {
        try (ZipFile zip = new ZipFile(archive.toFile())) {
            ZipEntry selected = null;
            int matches = 0;
            var entries = zip.entries();
            while (entries.hasMoreElements()) {
                ZipEntry candidate = entries.nextElement();
                if (candidate.getName().equals(name)) {
                    matches++;
                    selected = candidate;
                }
            }
            if (matches != 1 || selected == null || selected.isDirectory()) {
                throw new IOException("replay must contain exactly one regular " + name);
            }
            if (selected.getSize() < 0 || selected.getSize() > MAX_METADATA_BYTES) {
                throw new IOException(name + " is missing a bounded uncompressed size");
            }
            try (InputStreamReader reader = new InputStreamReader(zip.getInputStream(selected), StandardCharsets.UTF_8)) {
                JsonElement parsed = JsonParser.parseReader(reader);
                if (!parsed.isJsonObject()) {
                    throw new IOException(name + " root must be an object");
                }
                return parsed.getAsJsonObject();
            } catch (RuntimeException exception) {
                throw new IOException(name + " is invalid JSON", exception);
            }
        }
    }

    private static void requireIdentity(
        JsonObject identity,
        SceneJob job,
        SceneJob.SourceReplay source
    ) throws IOException {
        if (requiredInt(identity, "schema_version") < 1
            || !requiredString(identity, "session_id").equals(job.sessionId())
            || !requiredUuid(identity, "segment_id").equals(source.segmentId())
            || requiredInt(identity, "segment_ordinal") != source.segmentOrdinal()
            || !requiredUuid(identity, "player_uuid").equals(job.playerUuid())
            || !requiredUuid(identity, "connection_id").equals(job.connectionId())) {
            throw new IOException("embedded mc_recorder identity does not match scene job for " + source.path());
        }
    }

    private static void verifyMods(JsonObject sourceMods) throws IOException {
        Map<String, String> expected = new HashMap<>();
        for (Map.Entry<String, JsonElement> entry : sourceMods.entrySet()) {
            if (!entry.getValue().isJsonPrimitive() || !entry.getValue().getAsJsonPrimitive().isString()) {
                throw new IOException("arcade replay mods must map IDs to version strings");
            }
            expected.put(entry.getKey(), entry.getValue().getAsString());
        }

        Map<String, String> loaded = new HashMap<>();
        for (ModContainer container : FabricLoader.getInstance().getAllMods()) {
            loaded.put(
                container.getMetadata().getId(),
                container.getMetadata().getVersion().getFriendlyString()
            );
        }
        verifyModCompatibility(expected, loaded);
    }

    static void verifyModCompatibility(Map<String, String> expected, Map<String, String> loaded)
        throws IOException {
        for (Map.Entry<String, String> required : expected.entrySet()) {
            if (REPLACED_CAPTURE_INFRASTRUCTURE_IDS.contains(required.getKey())) {
                continue;
            }
            String actual = loaded.get(required.getKey());
            if (!required.getValue().equals(actual)) {
                throw new IOException(
                    "extractor mod version mismatch for " + required.getKey()
                        + ": source=" + required.getValue() + ", loaded=" + actual
                );
            }
        }

        Set<String> unexpected = new HashSet<>();
        for (String loadedId : loaded.keySet()) {
            if (expected.containsKey(loadedId) || isInfrastructureDependency(loadedId)) {
                continue;
            }
            unexpected.add(loadedId);
        }
        if (!unexpected.isEmpty()) {
            throw new IOException("extractor has source-incompatible extra mods: " + unexpected);
        }
    }

    private static boolean isInfrastructureDependency(String id) {
        return INFRASTRUCTURE_IDS.contains(id)
            || id.startsWith("fabric-")
            || id.startsWith("arcade-")
            || id.startsWith("kotlin-");
    }

    private static JsonObject requiredObject(JsonObject object, String name) throws IOException {
        JsonElement value = object.get(name);
        if (value == null || !value.isJsonObject()) {
            throw new IOException(name + " must be an object");
        }
        return value.getAsJsonObject();
    }

    private static String requiredString(JsonObject object, String name) throws IOException {
        JsonElement value = object.get(name);
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) {
            throw new IOException(name + " must be a string");
        }
        return value.getAsString();
    }

    private static UUID requiredUuid(JsonObject object, String name) throws IOException {
        try {
            UUID uuid = UUID.fromString(requiredString(object, name));
            if (!uuid.toString().equals(object.get(name).getAsString())) {
                throw new IllegalArgumentException("not canonical");
            }
            return uuid;
        } catch (IllegalArgumentException exception) {
            throw new IOException(name + " must be a canonical UUID", exception);
        }
    }

    private static int requiredInt(JsonObject object, String name) throws IOException {
        JsonElement value = object.get(name);
        try {
            if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()) {
                throw new NumberFormatException();
            }
            return value.getAsBigDecimal().intValueExact();
        } catch (ArithmeticException | NumberFormatException exception) {
            throw new IOException(name + " must be a 32-bit integer", exception);
        }
    }

    public record VerifiedSource(SceneJob.SourceReplay source, Object fileKey, long size, long modifiedMillis) { }
}
