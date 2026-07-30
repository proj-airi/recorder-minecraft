package dev.mcdata.scene.replay;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import kotlin.Unit;
import kotlin.jvm.functions.Function2;
import net.casual.arcade.replay.util.flashback.FlashbackAction;
import net.minecraft.core.RegistryAccess;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.resources.ResourceLocation;

import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileSystem;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/** Minimal Flashback chunk reader that avoids arcade metadata codecs that require Fabric mixins. */
final class PlainFlashbackChunkedReader implements AutoCloseable {
    private static final int FLASHBACK_CHUNK_MAGIC = -679417724;

    private final FileSystem system;
    private final RegistryAccess registries;
    private final TreeMap<Integer, PlayableChunk> chunks = new TreeMap<>();
    private final RegistryFriendlyByteBuf buffer;
    private final Map<Integer, FlashbackAction> actions = new HashMap<>();
    private final Set<Integer> ignored = new HashSet<>();
    private Map.Entry<Integer, PlayableChunk> current;
    private int snapshotIndex;
    private int playbackIndex;

    PlainFlashbackChunkedReader(FileSystem system, RegistryAccess registries) throws IOException {
        this.system = system;
        this.registries = registries;
        this.buffer = new RegistryFriendlyByteBuf(Unpooled.buffer(), registries);
        readChunks();
        this.current = chunks.floorEntry(0);
        if (current == null) {
            throw new IOException("Flashback metadata declares no playable chunks");
        }
        readHeader();
    }

    boolean shouldPlaySnapshot() {
        return current.getValue().forcePlaySnapshot();
    }

    void consumeSnapshot(Function2<? super FlashbackAction, ? super RegistryFriendlyByteBuf, Unit> consumer) {
        buffer.readerIndex(snapshotIndex);
        while (buffer.readerIndex() < playbackIndex) {
            consumeAction(consumer);
        }
    }

    boolean consumeNextAction(Function2<? super FlashbackAction, ? super RegistryFriendlyByteBuf, Unit> consumer) {
        if (buffer.readerIndex() >= buffer.writerIndex()) {
            return false;
        }
        if (buffer.readerIndex() < playbackIndex) {
            buffer.readerIndex(playbackIndex);
        }
        consumeAction(consumer);
        return true;
    }

    boolean moveToNextChunk() throws IOException {
        int nextTick = current.getKey() + current.getValue().duration();
        Map.Entry<Integer, PlayableChunk> next = chunks.floorEntry(nextTick);
        if (next == null || next.equals(current)) {
            return false;
        }
        current = next;
        readHeader();
        return true;
    }

    @Override
    public void close() {
        buffer.release();
        chunks.clear();
    }

    private void consumeAction(Function2<? super FlashbackAction, ? super RegistryFriendlyByteBuf, Unit> consumer) {
        int actionId = buffer.readVarInt();
        FlashbackAction action = actions.get(actionId);
        int bytes = buffer.readInt();
        ByteBuf slice = buffer.readSlice(bytes);
        if (action == null) {
            if (!ignored.contains(actionId)) {
                throw new IllegalStateException("unknown Flashback action id " + actionId);
            }
            return;
        }
        consumer.invoke(action, new RegistryFriendlyByteBuf(slice, registries));
        if (slice.readerIndex() < slice.writerIndex()) {
            throw new IllegalStateException("action " + action.getId() + " left unread bytes");
        }
    }

    private void readHeader() throws IOException {
        actions.clear();
        ignored.clear();
        buffer.readerIndex(0);
        buffer.writerIndex(0);
        buffer.writeBytes(Files.readAllBytes(current.getValue().path()));
        int magic = buffer.readInt();
        if (magic != FLASHBACK_CHUNK_MAGIC) {
            throw new IOException("Flashback chunk has invalid magic: " + current.getValue().path());
        }
        int actionCount = buffer.readVarInt();
        for (int index = 0; index < actionCount; index++) {
            ResourceLocation id = buffer.readResourceLocation();
            FlashbackAction action = FlashbackAction.Companion.from(id);
            if (action == null) {
                if (id.getPath().endsWith("optional")) {
                    ignored.add(index);
                    continue;
                }
                throw new IOException("unknown required Flashback action: " + id);
            }
            actions.put(index, action);
        }
        int snapshotBytes = buffer.readInt();
        snapshotIndex = buffer.readerIndex();
        buffer.skipBytes(snapshotBytes);
        playbackIndex = buffer.readerIndex();
    }

    private void readChunks() throws IOException {
        Path metadata = system.getPath("metadata.json");
        if (Files.notExists(metadata)) {
            metadata = system.getPath("metadata.json.old");
        }
        if (Files.notExists(metadata)) {
            throw new IOException("Flashback file has no metadata");
        }
        JsonObject root;
        try (InputStreamReader reader = new InputStreamReader(Files.newInputStream(metadata), StandardCharsets.UTF_8)) {
            JsonElement parsed = JsonParser.parseReader(reader);
            if (!parsed.isJsonObject()) {
                throw new IOException("Flashback metadata root must be an object");
            }
            root = parsed.getAsJsonObject();
        } catch (RuntimeException exception) {
            throw new IOException("Flashback metadata is invalid JSON", exception);
        }
        JsonElement chunksValue = root.get("chunks");
        if (chunksValue == null || !chunksValue.isJsonObject()) {
            throw new IOException("Flashback metadata chunks must be an object");
        }
        int startTick = 0;
        for (Map.Entry<String, JsonElement> entry : chunksValue.getAsJsonObject().entrySet()) {
            if (!entry.getValue().isJsonObject()) {
                throw new IOException("Flashback chunk metadata must be an object: " + entry.getKey());
            }
            JsonObject chunk = entry.getValue().getAsJsonObject();
            int duration = requiredInt(chunk, "duration");
            boolean forcePlaySnapshot = requiredBoolean(chunk, "forcePlaySnapshot");
            chunks.put(startTick, new PlayableChunk(system.getPath(entry.getKey()), duration, forcePlaySnapshot));
            startTick += duration;
        }
    }

    private static int requiredInt(JsonObject object, String key) throws IOException {
        JsonElement value = object.get(key);
        try {
            if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()) {
                throw new NumberFormatException();
            }
            return value.getAsBigDecimal().intValueExact();
        } catch (ArithmeticException | NumberFormatException exception) {
            throw new IOException("Flashback chunk " + key + " must be a 32-bit integer", exception);
        }
    }

    private static boolean requiredBoolean(JsonObject object, String key) throws IOException {
        JsonElement value = object.get(key);
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isBoolean()) {
            throw new IOException("Flashback chunk " + key + " must be a boolean");
        }
        return value.getAsBoolean();
    }

    private record PlayableChunk(Path path, int duration, boolean forcePlaySnapshot) { }
}
