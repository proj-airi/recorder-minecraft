package dev.mcdata.scene.replay;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import kotlin.Unit;
import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneFrame;
import dev.mcdata.scene.core.SceneReducer;
import dev.mcdata.scene.io.SceneSpoolWriter;
import dev.mcdata.scene.job.SceneJob;
import net.casual.arcade.replay.io.FlashbackIO;
import net.casual.arcade.replay.io.reader.flashback.FlashbackChunkedReader;
import net.casual.arcade.replay.io.writer.flashback.EntityMovement;
import net.casual.arcade.replay.util.flashback.FlashbackAction;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.ConnectionProtocol;
import net.minecraft.network.ProtocolInfo;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.protocol.BundlePacket;
import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.configuration.ConfigurationProtocols;
import net.minecraft.network.protocol.game.ClientGamePacketListener;
import net.minecraft.network.protocol.game.ClientboundBundlePacket;
import net.minecraft.network.protocol.game.ClientboundLevelChunkWithLightPacket;
import net.minecraft.network.protocol.game.ClientboundLoginPacket;
import net.minecraft.network.protocol.game.GameProtocols;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.phys.Vec2;
import net.minecraft.world.phys.Vec3;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.FileSystem;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Sequential, non-client Flashback interpreter. */
public final class FlashbackSceneExtractor {
    private static final int MAX_CACHED_CHUNK_BYTES = 2 * 1024 * 1024;

    private final SceneJob job;
    private final RegistryAccess registries;
    private final SceneReducer reducer;
    private final PacketTranslator translator;
    private final SceneSpoolWriter spool;
    private final ProtocolInfo<ClientGamePacketListener> gameProtocol;
    private final Map<String, Long> ignoredPackets = new java.util.TreeMap<>();
    private final Set<Long> coveredTicks = new HashSet<>();
    private final Set<String> markerKeys = new HashSet<>();

    public FlashbackSceneExtractor(SceneJob job, RegistryAccess registries, SceneSpoolWriter spool) {
        this.job = job;
        this.registries = registries;
        this.reducer = new SceneReducer(job);
        this.translator = new PacketTranslator(registries, reducer);
        this.spool = spool;
        this.gameProtocol = GameProtocols.CLIENTBOUND_TEMPLATE.bind(
            RegistryFriendlyByteBuf.decorator(registries)
        );
    }

    public ExtractionStats extract(List<ReplayArchiveValidator.VerifiedSource> sources) throws IOException {
        for (ReplayArchiveValidator.VerifiedSource verified : sources) {
            extractSegment(verified.source());
            if (coveredTicks.contains(job.globalEndTick())) {
                break;
            }
        }
        for (long tick = job.globalStartTick(); tick <= job.globalEndTick(); tick++) {
            if (!coveredTicks.contains(tick)) {
                throw new IOException("scene replay coverage is missing global tick " + tick);
            }
        }
        return new ExtractionStats(Map.copyOf(ignoredPackets), coveredTicks.size());
    }

    private void extractSegment(SceneJob.SourceReplay source) throws IOException {
        reducer.beginSegment();
        spool.beginSegment(source);
        SegmentContext context = new SegmentContext(source);
        try (FileSystem system = FileSystems.newFileSystem(source.path())) {
            context.system = system;
            FlashbackChunkedReader reader = new FlashbackChunkedReader(system, registries);
            try {
                boolean initial = true;
                do {
                    if (initial || reader.shouldPlaySnapshot()) {
                        initial = false;
                        consumeSnapshot(reader, context);
                    }
                    while (!context.finished && consumeNext(reader, context)) {
                        // Actions are consumed by the callback.
                    }
                } while (!context.finished && reader.moveToNextChunk());
            } finally {
                reader.close();
            }
        } catch (ReplayDecodeFailure failure) {
            throw failure.asIOException(source.path());
        }
    }

    private void consumeSnapshot(FlashbackChunkedReader reader, SegmentContext context) {
        reader.consumeSnapshot((action, buffer) -> {
            processAction(action, buffer, context);
            return Unit.INSTANCE;
        });
    }

    private boolean consumeNext(FlashbackChunkedReader reader, SegmentContext context) {
        return reader.consumeNextAction((action, buffer) -> {
            processAction(action, buffer, context);
            return Unit.INSTANCE;
        });
    }

    private void processAction(
        FlashbackAction action,
        RegistryFriendlyByteBuf buffer,
        SegmentContext context
    ) {
        try {
            switch (action) {
                case NextTick -> context.replayTick++;
                case ConfigurationPacket -> processConfiguration(buffer);
                case GamePacket -> processPacket(gameProtocol.codec().decode(buffer), context);
                case CacheChunk -> processPacket(readCachedChunk(buffer.readVarInt(), context), context);
                case CreatePlayer -> processCreatePlayer(buffer, context);
                case MoveEntities -> processMoveEntities(buffer, context);
                case VoiceChat, EncodedVoiceChat -> ignoredPackets.merge(
                    "flashback:" + action.name().toLowerCase(java.util.Locale.ROOT), 1L, Long::sum
                );
            }
        } catch (IOException | RuntimeException exception) {
            if (exception instanceof ReplayDecodeFailure failure) {
                throw failure;
            }
            throw new ReplayDecodeFailure(
                "failed action " + action + " at replay tick " + context.replayTick,
                exception
            );
        }
    }

    private void processConfiguration(RegistryFriendlyByteBuf buffer) {
        Packet<?> packet = ConfigurationProtocols.CLIENTBOUND.codec().decode(buffer);
        ignoredPackets.merge("configuration:" + packet.type().id(), 1L, Long::sum);
    }

    private void processPacket(Packet<?> packet, SegmentContext context) throws IOException {
        if (packet instanceof BundlePacket<?> bundle) {
            for (Packet<?> nested : castPackets(bundle.subPackets())) {
                processPacket(nested, context);
            }
            return;
        }
        if (packet instanceof ClientboundLoginPacket login) {
            context.playerId = login.playerId();
        }

        PacketTranslator.Translation translated = translator.translate(packet);
        if (translated.unhandledType() != null) {
            if (couldAffectScene(translated.unhandledType())) {
                throw new IOException("unclassified scene-affecting packet " + translated.unhandledType());
            }
            ignoredPackets.merge(translated.unhandledType(), 1L, Long::sum);
            return;
        }
        for (SceneEvent event : translated.events()) {
            reducer.apply(event);
        }
        if (translated.timeline().isPresent()) {
            SceneFrame frame = reducer.frame(translated.timeline().orElseThrow(), context.outputTick(), context.source)
                .orElse(null);
            if (frame == null) {
                return;
            }
            if (!frame.complete()) {
                throw new IOException("timeline frame lacks the recorded subject at global tick " + frame.globalTick());
            }
            String markerKey = context.source.segmentId() + ":" + frame.globalTick();
            if (!markerKeys.add(markerKey)) {
                throw new IOException("duplicate timeline marker in one replay segment at global tick " + frame.globalTick());
            }
            if (!coveredTicks.add(frame.globalTick())) {
                return;
            }
            spool.writeFrame(frame, reducer.snapshot());
            if (frame.globalTick() == job.globalEndTick()) {
                context.finished = true;
            }
        }
    }

    @SuppressWarnings("unchecked")
    private static Iterable<Packet<?>> castPackets(Iterable<?> packets) {
        return (Iterable<Packet<?>>) packets;
    }

    private void processCreatePlayer(RegistryFriendlyByteBuf buffer, SegmentContext context) throws IOException {
        if (context.playerId < 0) {
            throw new IOException("Flashback create-local-player action preceded the login packet");
        }
        java.util.UUID uuid = buffer.readUUID();
        double x = buffer.readDouble();
        double y = buffer.readDouble();
        double z = buffer.readDouble();
        float pitch = buffer.readFloat();
        float yaw = buffer.readFloat();
        float headYaw = buffer.readFloat();
        Vec3 velocity = buffer.readVec3();
        ByteBufCodecs.GAME_PROFILE.decode(buffer);
        buffer.readVarInt();

        SceneEvent.EntitySpawned event = translator.createLocalPlayer(
            context.playerId, uuid, x, y, z, pitch, yaw, headYaw, velocity
        );
        reducer.apply(event);
    }

    private void processMoveEntities(RegistryFriendlyByteBuf buffer, SegmentContext context) throws IOException {
        int dimensions = buffer.readVarInt();
        if (dimensions < 0 || dimensions > 1024) {
            throw new IOException("Flashback move-entities dimension count is invalid: " + dimensions);
        }
        for (int dimensionIndex = 0; dimensionIndex < dimensions; dimensionIndex++) {
            ResourceKey<net.minecraft.world.level.Level> movementDimension = buffer.readResourceKey(Registries.DIMENSION);
            boolean currentDimension = movementDimension.location().toString().equals(reducer.dimension());
            int movements = buffer.readVarInt();
            if (movements < 0 || movements > 1_000_000) {
                throw new IOException("Flashback move-entities count is invalid: " + movements);
            }
            for (int movementIndex = 0; movementIndex < movements; movementIndex++) {
                EntityMovement movement = EntityMovement.Companion.read(buffer);
                if (!currentDimension || !reducer.hasEntity(movement.getId())) {
                    ignoredPackets.merge("flashback:movement_for_untracked_entity", 1L, Long::sum);
                    continue;
                }
                SceneEvent.EntityTeleported event = new SceneEvent.EntityTeleported(
                    movement.getId(),
                    vector(movement.getPosition()),
                    new SceneEvent.Vec3(0, 0, 0),
                    movement.getRotation().y,
                    movement.getRotation().x,
                    Set.of(),
                    movement.getOnGround()
                );
                reducer.apply(event);
                SceneEvent.EntityHeadRotated head = new SceneEvent.EntityHeadRotated(
                    movement.getId(), movement.getHeadRot()
                );
                reducer.apply(head);
            }
        }
    }

    private ClientboundLevelChunkWithLightPacket readCachedChunk(int index, SegmentContext context) throws IOException {
        ClientboundLevelChunkWithLightPacket cached = context.chunkCache.get(index);
        if (cached != null) {
            return cached;
        }
        int fileIndex = FlashbackIO.INSTANCE.getChunkCacheFileIndex(index);
        Path caches = context.system.getPath(FlashbackIO.CHUNK_CACHES).resolve(Integer.toString(fileIndex));
        if (!Files.isRegularFile(caches)) {
            throw new IOException("Flashback chunk cache file is missing: " + caches);
        }
        try (InputStream input = Files.newInputStream(caches)) {
            int cacheIndex = fileIndex * FlashbackIO.LEVEL_CHUNK_CACHE_SIZE;
            byte[] sizeBytes = new byte[4];
            while (true) {
                int first = input.read();
                if (first < 0) {
                    break;
                }
                sizeBytes[0] = (byte) first;
                if (input.readNBytes(sizeBytes, 1, 3) != 3) {
                    throw new IOException("truncated Flashback chunk cache size");
                }
                int size = ((sizeBytes[0] & 0xFF) << 24)
                    | ((sizeBytes[1] & 0xFF) << 16)
                    | ((sizeBytes[2] & 0xFF) << 8)
                    | (sizeBytes[3] & 0xFF);
                if (size <= 0 || size > MAX_CACHED_CHUNK_BYTES) {
                    throw new IOException("Flashback cached chunk size is invalid: " + size);
                }
                byte[] packetBytes = input.readNBytes(size);
                if (packetBytes.length != size) {
                    throw new IOException("truncated Flashback cached chunk packet");
                }
                ByteBuf packetBuffer = Unpooled.wrappedBuffer(packetBytes);
                try {
                    Packet<?> decoded = gameProtocol.codec().decode(packetBuffer);
                    if (!(decoded instanceof ClientboundLevelChunkWithLightPacket chunk)) {
                        throw new IOException("Flashback chunk cache contains non-chunk packet " + decoded.type().id());
                    }
                    if (packetBuffer.isReadable()) {
                        throw new IOException("Flashback cached chunk packet has trailing bytes");
                    }
                    context.chunkCache.put(cacheIndex++, chunk);
                } finally {
                    packetBuffer.release();
                }
            }
        }
        cached = context.chunkCache.get(index);
        if (cached == null) {
            throw new IOException("Flashback chunk cache does not contain requested index " + index);
        }
        return cached;
    }

    private static boolean couldAffectScene(String packetType) {
        String path = packetType.substring(packetType.indexOf(':') + 1);
        if (Set.of(
            "entity_event", "damage_event", "animate", "hurt_animation", "take_item_entity",
            "block_event", "block_destruction", "level_event", "chunks_biomes", "light_update",
            "set_chunk_cache_center", "set_chunk_cache_radius", "set_simulation_distance"
        ).contains(path)) {
            return false;
        }
        return path.contains("entity")
            || path.contains("block")
            || path.contains("chunk")
            || path.contains("mob_effect")
            || path.contains("equipment")
            || path.contains("passenger")
            || path.equals("login")
            || path.equals("respawn")
            || path.equals("player_position")
            || path.equals("player_info_update")
            || path.equals("player_info_remove");
    }

    private static SceneEvent.Vec3 vector(Vec3 vector) {
        return new SceneEvent.Vec3(vector.x, vector.y, vector.z);
    }

    public record ExtractionStats(Map<String, Long> ignoredPacketCounts, long coveredTickCount) { }

    private static final class SegmentContext {
        private final SceneJob.SourceReplay source;
        private final Map<Integer, ClientboundLevelChunkWithLightPacket> chunkCache = new HashMap<>();
        private FileSystem system;
        private int replayTick;
        private int playerId = -1;
        private boolean finished;

        private SegmentContext(SceneJob.SourceReplay source) {
            this.source = source;
        }

        private int outputTick() {
            // Flashback's ReplayServer exposes the action-stream cursor as a one-based replay tick.
            return replayTick + 1;
        }
    }

    private static final class ReplayDecodeFailure extends RuntimeException {
        private ReplayDecodeFailure(String message, Throwable cause) {
            super(message, cause);
        }

        private IOException asIOException(Path replay) {
            return new IOException("failed to decode Flashback replay " + replay + ": " + getMessage(), getCause());
        }
    }
}
