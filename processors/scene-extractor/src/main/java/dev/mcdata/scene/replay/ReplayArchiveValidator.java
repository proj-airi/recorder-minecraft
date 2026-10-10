package dev.mcdata.scene.replay;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import dev.mcdata.scene.io.Hashing;
import dev.mcdata.scene.job.SceneJob;
import dev.recorderminecraft.artifacts.v1.SceneSourceRuntime;
import dev.recorderminecraft.artifacts.v1.SceneToleratedMod;

import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * Verifies immutable bytes, embedded provenance, and that the extractor can decode the replay.
 *
 * <p>Only the Minecraft version, network protocol, and data version must equal the extractor's,
 * because the extractor decodes with the vanilla codecs and registries of that version (see
 * {@link ExtractorRuntime}). The ServerReplay {@code mods} list is provenance: it names every
 * top-level mod of the recording client or server, not what the replay bytes need. A mod that
 * only adds behavior, such as a path finder or a map, adds at most custom payloads, which the
 * extractor counts as ignored packets. A mod that adds blocks, items, entities, or entity data
 * serializers gets raw IDs after the vanilla ones, and the vanilla codecs reject unknown IDs, so
 * extraction fails at the first packet that uses one. A mod denylist would add no safety and
 * would need an update for every new mod.
 */
public final class ReplayArchiveValidator {
    private static final long MAX_METADATA_BYTES = 1_048_576;
    private final ExtractorRuntime runtime;

    public ReplayArchiveValidator(ExtractorRuntime runtime) {
        this.runtime = runtime;
    }

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
        JsonObject identity = requiredObject(arcadeMetadata, "recorder-minecraft");
        requireIdentity(identity, job, source);
        JsonObject flashbackMetadata = readMetadata(source.path(), "metadata.json");
        if (!flashbackMetadata.has("chunks") || !flashbackMetadata.has("total_ticks")) {
            throw new IOException("Flashback metadata lacks chunks or total_ticks: " + source.path());
        }
        SceneSourceRuntime sourceRuntime;
        try {
            sourceRuntime = compareRuntime(source.segmentId(), flashbackMetadata, arcadeMetadata, runtime);
        } catch (IOException exception) {
            throw new IOException(exception.getMessage() + ": " + source.path(), exception);
        }
        return new VerifiedSource(
            source, before.fileKey(), before.size(), before.lastModifiedTime().toMillis(), sourceRuntime
        );
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
        if (requiredInt(identity, "schema_version") != 4
            || !requiredString(identity, "session_id").equals(job.sessionId())
            || !requiredUuid(identity, "replay_id").equals(source.segmentId())
            || !requiredUuid(identity, "player_uuid").equals(job.playerUuid())
            || !requiredUuid(identity, "connection_id").equals(job.connectionId())
            || !requiredString(identity, "hotbar_snapshot_contract")
                .equals(SceneJob.HOTBAR_SNAPSHOT_CONTRACT)
            || !requiredString(identity, "flashback_capture_contract")
                .equals(SceneJob.FLASHBACK_CAPTURE_CONTRACT)) {
            throw new IOException("embedded recorder-minecraft identity does not match scene job for " + source.path());
        }
    }

    /**
     * Rejects a replay whose Minecraft identity differs from the extractor runtime and records every
     * other runtime difference as provenance.
     */
    static SceneSourceRuntime compareRuntime(
        UUID segmentId,
        JsonObject flashbackMetadata,
        JsonObject arcadeMetadata,
        ExtractorRuntime runtime
    ) throws IOException {
        // NOTICE: ServerReplay's mods list omits built-in mods, so it never names Minecraft. The
        // Flashback metadata written with the archive is the only record of the game version.
        String minecraftVersion = requiredString(flashbackMetadata, "version_string");
        int protocolVersion = requiredInt(flashbackMetadata, "protocol_version");
        int dataVersion = requiredInt(flashbackMetadata, "data_version");
        if (!minecraftVersion.equals(runtime.minecraftVersion())
            || protocolVersion != runtime.protocolVersion()
            || dataVersion != runtime.dataVersion()) {
            throw new IOException(
                "replay Minecraft " + minecraftVersion + " (protocol " + protocolVersion + ", data "
                    + dataVersion + ") cannot be decoded by extractor Minecraft " + runtime.minecraftVersion()
                    + " (protocol " + runtime.protocolVersion() + ", data " + runtime.dataVersion() + ")"
            );
        }

        SceneSourceRuntime.Builder result = SceneSourceRuntime.newBuilder()
            .setSegmentId(segmentId.toString())
            .setMinecraftVersion(minecraftVersion)
            .setProtocolVersion(protocolVersion)
            .setDataVersion(dataVersion);
        if (arcadeMetadata.has("server_replay_version")) {
            result.setServerReplayVersion(requiredString(arcadeMetadata, "server_replay_version"));
        }
        // A sorted map gives provenance a stable order, so equal inputs give equal results.
        Map<String, String> sourceMods = new TreeMap<>();
        for (Map.Entry<String, JsonElement> entry : requiredObject(arcadeMetadata, "mods").entrySet()) {
            if (!entry.getValue().isJsonPrimitive() || !entry.getValue().getAsJsonPrimitive().isString()) {
                throw new IOException("arcade replay mods must map IDs to version strings");
            }
            sourceMods.put(entry.getKey(), entry.getValue().getAsString());
        }
        for (Map.Entry<String, String> mod : sourceMods.entrySet()) {
            String extractorVersion = runtime.mods().get(mod.getKey());
            if (mod.getValue().equals(extractorVersion)) {
                continue;
            }
            SceneToleratedMod.Builder tolerated = SceneToleratedMod.newBuilder()
                .setModId(mod.getKey())
                .setSourceVersion(mod.getValue());
            if (extractorVersion == null) {
                tolerated.setDifference(SceneToleratedMod.Difference.DIFFERENCE_NOT_IN_EXTRACTOR);
            } else {
                tolerated.setDifference(SceneToleratedMod.Difference.DIFFERENCE_VERSION_MISMATCH)
                    .setExtractorVersion(extractorVersion);
            }
            result.addToleratedMods(tolerated);
        }
        return result.build();
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

    public record VerifiedSource(
        SceneJob.SourceReplay source,
        Object fileKey,
        long size,
        long modifiedMillis,
        SceneSourceRuntime runtime
    ) { }
}
