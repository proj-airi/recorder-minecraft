package dev.mcdata.renderer;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.SectionPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;

import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Base64;
import java.util.BitSet;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.GZIPOutputStream;

final class VoxelSnapshotWriter {
    private VoxelSnapshotWriter() {
    }

    static JsonObject write(
        ClientLevel level,
        Entity target,
        RenderJobSpec job,
        int replayTick,
        long serverTick
    ) throws IOException {
        int horizontalRadius = job.voxelHorizontalRadius();
        int verticalRadius = job.voxelVerticalRadius();
        int sizeX = horizontalRadius * 2 + 1;
        int sizeY = verticalRadius * 2 + 1;
        int sizeZ = horizontalRadius * 2 + 1;
        int total = Math.multiplyExact(Math.multiplyExact(sizeX, sizeY), sizeZ);

        BlockPos center = target.blockPosition();
        int originX = center.getX() - horizontalRadius;
        int originY = center.getY() - verticalRadius;
        int originZ = center.getZ() - horizontalRadius;

        LinkedHashMap<String, Integer> palette = new LinkedHashMap<>();
        palette.put("minecraft:air", 0);
        Map<BlockState, String> stateNames = new HashMap<>();
        int[] indices = new int[total];
        BitSet coverage = new BitSet(total);
        BlockPos.MutableBlockPos cursor = new BlockPos.MutableBlockPos();

        int linear = 0;
        for (int y = 0; y < sizeY; y++) {
            int worldY = originY + y;
            for (int z = 0; z < sizeZ; z++) {
                int worldZ = originZ + z;
                for (int x = 0; x < sizeX; x++, linear++) {
                    int worldX = originX + x;
                    cursor.set(worldX, worldY, worldZ);
                    if (level.isOutsideBuildHeight(worldY)
                        || !level.hasChunk(
                            SectionPos.blockToSectionCoord(worldX),
                            SectionPos.blockToSectionCoord(worldZ)
                        )) {
                        indices[linear] = 0;
                        continue;
                    }
                    BlockState state = level.getBlockState(cursor);
                    String stateName = stateNames.computeIfAbsent(state, VoxelSnapshotWriter::stateName);
                    indices[linear] = palette.computeIfAbsent(stateName, ignored -> palette.size());
                    coverage.set(linear);
                }
            }
        }

        String indexDtype = palette.size() <= 65_536 ? "uint16" : "uint32";
        int indexWidth = indexDtype.equals("uint16") ? Short.BYTES : Integer.BYTES;
        ByteBuffer indexBytes = ByteBuffer.allocate(Math.multiplyExact(total, indexWidth))
            .order(ByteOrder.LITTLE_ENDIAN);
        for (int index : indices) {
            if (indexWidth == Short.BYTES) {
                indexBytes.putShort((short) index);
            } else {
                indexBytes.putInt(index);
            }
        }

        JsonObject snapshot = new JsonObject();
        snapshot.addProperty("schema_version", 1);
        snapshot.addProperty("format", "mc-recorder-voxel-palette-v1");
        snapshot.addProperty("session_id", job.sessionId());
        snapshot.addProperty("connection_id", job.connectionId());
        snapshot.addProperty("player_uuid", job.playerId().toString());
        snapshot.addProperty("server_tick", serverTick);
        snapshot.addProperty("replay_tick", replayTick);
        snapshot.addProperty("dimension", level.dimension().location().toString());
        snapshot.add("center", vector(center.getX(), center.getY(), center.getZ()));
        snapshot.add("origin", vector(originX, originY, originZ));
        snapshot.add("shape", vector(sizeX, sizeY, sizeZ));
        snapshot.addProperty("linear_order", "x_fastest_then_z_then_y");
        snapshot.addProperty("index_dtype", indexDtype);
        snapshot.addProperty("index_byte_order", "little_endian");
        snapshot.addProperty(
            "indices_base64",
            Base64.getEncoder().encodeToString(indexBytes.array())
        );
        snapshot.addProperty(
            "coverage_bitset_base64",
            Base64.getEncoder().encodeToString(coverage.toByteArray())
        );
        snapshot.addProperty("coverage_bit_order", "lsb0");
        snapshot.addProperty("covered_cells", coverage.cardinality());
        snapshot.addProperty("total_cells", total);
        snapshot.addProperty("coverage_complete", coverage.cardinality() == total);
        JsonArray paletteJson = new JsonArray();
        palette.keySet().forEach(paletteJson::add);
        snapshot.add("palette", paletteJson);
        snapshot.addProperty("block_entities_included", false);
        snapshot.addProperty(
            "block_entities_note",
            "not materialized in v1; preserved in the immutable replay source when client-visible"
        );

        Path voxelDirectory = job.output().resolve("voxels");
        Files.createDirectories(voxelDirectory);
        String fileName = String.format("voxel_%012d.json.gz", serverTick);
        Path destination = voxelDirectory.resolve(fileName);
        if (Files.exists(destination)) {
            throw new IOException("Voxel snapshot already exists: " + destination);
        }
        writeGzipAtomically(destination, snapshot.toString());

        JsonObject index = new JsonObject();
        index.addProperty("schema_version", 1);
        index.addProperty("session_id", job.sessionId());
        index.addProperty("connection_id", job.connectionId());
        index.addProperty("player_uuid", job.playerId().toString());
        index.addProperty("server_tick", serverTick);
        index.addProperty("replay_tick", replayTick);
        index.addProperty("reference", "voxels/" + fileName);
        index.add("origin", vector(originX, originY, originZ));
        index.add("shape", vector(sizeX, sizeY, sizeZ));
        index.addProperty("covered_cells", coverage.cardinality());
        index.addProperty("total_cells", total);
        index.addProperty("coverage_complete", coverage.cardinality() == total);
        return index;
    }

    private static JsonObject vector(int x, int y, int z) {
        JsonObject value = new JsonObject();
        value.addProperty("x", x);
        value.addProperty("y", y);
        value.addProperty("z", z);
        return value;
    }

    private static String stateName(BlockState state) {
        String block = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
        if (state.getValues().isEmpty()) {
            return block;
        }
        List<Map.Entry<Property<?>, Comparable<?>>> properties = new ArrayList<>(state.getValues().entrySet());
        properties.sort(Map.Entry.comparingByKey((left, right) -> left.getName().compareTo(right.getName())));
        StringBuilder builder = new StringBuilder(block).append('[');
        for (int index = 0; index < properties.size(); index++) {
            if (index > 0) {
                builder.append(',');
            }
            Map.Entry<Property<?>, Comparable<?>> entry = properties.get(index);
            builder.append(entry.getKey().getName()).append('=').append(propertyValue(entry.getKey(), entry.getValue()));
        }
        return builder.append(']').toString();
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static String propertyValue(Property property, Comparable value) {
        return property.getName(value);
    }

    private static void writeGzipAtomically(Path destination, String value) throws IOException {
        Path partial = destination.resolveSibling(destination.getFileName() + ".inprogress");
        Files.deleteIfExists(partial);
        try (FileOutputStream file = new FileOutputStream(partial.toFile());
             GZIPOutputStream gzip = new GZIPOutputStream(file, 256 * 1024)) {
            gzip.write(value.getBytes(StandardCharsets.UTF_8));
            gzip.finish();
            gzip.flush();
            file.getFD().sync();
        }
        try {
            Files.move(partial, destination, StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException ignored) {
            Files.move(partial, destination);
        }
    }
}
