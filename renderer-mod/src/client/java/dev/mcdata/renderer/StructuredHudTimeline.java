package dev.mcdata.renderer;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.brigadier.exceptions.CommandSyntaxException;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.nbt.NbtOps;
import net.minecraft.nbt.TagParser;
import net.minecraft.resources.RegistryOps;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.ai.attributes.AttributeInstance;
import net.minecraft.world.entity.ai.attributes.AttributeModifier;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.BitSet;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * An integrity-bound, tick-keyed projection of authoritative structured player state used to
 * repair HUD fields that a replay archive cannot faithfully reconstruct.
 */
final class StructuredHudTimeline {
    static final String SIDECAR_TYPE = "mc-recorder-structured-hud-v1";

    private static final long MAX_SIDECAR_BYTES = 512L * 1024L * 1024L;
    private static final int MAX_LINE_CHARS = 1_048_576;
    private static final int MAX_STACK_SNBT_CHARS = 262_144;
    // Inventory includes 36 carried slots plus feet, legs, chest, head,
    // offhand, body armour, and saddle equipment slots in Minecraft 1.21.8.
    private static final int MAX_INVENTORY_SLOT = 42;

    private final RenderJobSpec.StructuredHudSpec spec;
    private final UUID playerId;
    private final long startTick;
    private final List<HudState> states;
    private final Map<String, ItemStack> decodedStacks = new HashMap<>();
    private final BitSet appliedTicks;
    private volatile boolean prepared;

    enum CameraKind {
        REQUESTED_PLAYER,
        REPLAY_VIEWER,
        OTHER
    }

    record ProjectionPlan(
        boolean applyViewerPresentation,
        boolean applyCameraFullState,
        boolean reject
    ) {
    }

    private static final ProjectionPlan APPLY_REQUESTED_CAMERA =
        new ProjectionPlan(true, true, false);
    private static final ProjectionPlan SKIP_PROJECTION =
        new ProjectionPlan(false, false, false);
    private static final ProjectionPlan REJECT_PROJECTION =
        new ProjectionPlan(false, false, true);

    private StructuredHudTimeline(
        RenderJobSpec.StructuredHudSpec spec,
        UUID playerId,
        long startTick,
        List<HudState> states
    ) {
        this.spec = spec;
        this.playerId = playerId;
        this.startTick = startTick;
        this.states = List.copyOf(states);
        this.appliedTicks = new BitSet(states.size());
    }

    static StructuredHudTimeline load(RenderJobSpec job) throws IOException {
        RenderJobSpec.StructuredHudSpec spec = job.structuredHud();
        if (spec == null) {
            return null;
        }
        if (spec.sizeBytes() > MAX_SIDECAR_BYTES) {
            throw new IOException("Structured HUD sidecar exceeds the 512 MiB limit");
        }
        if (Files.isSymbolicLink(spec.path()) || !Files.isRegularFile(spec.path())) {
            throw new IOException("Structured HUD sidecar is missing or is a symlink: " + spec.path());
        }
        if (Files.size(spec.path()) != spec.sizeBytes()) {
            throw new IOException("Structured HUD sidecar size does not match its integrity envelope");
        }
        String digest = sha256(spec.path());
        if (!digest.equals(spec.sha256())) {
            throw new IOException("Structured HUD sidecar SHA-256 does not match its integrity envelope");
        }
        if (spec.records() > Integer.MAX_VALUE) {
            throw new IOException("Structured HUD sidecar has too many records");
        }

        List<HudState> states = new ArrayList<>((int) spec.records());
        long expectedTick = spec.startServerTick();
        try (BufferedReader reader = Files.newBufferedReader(spec.path(), StandardCharsets.UTF_8)) {
            String line;
            long lineNumber = 0;
            while ((line = reader.readLine()) != null) {
                lineNumber++;
                if (line.isEmpty() || line.length() > MAX_LINE_CHARS) {
                    throw new IOException("Invalid structured HUD line length at line " + lineNumber);
                }
                JsonElement parsed;
                try {
                    parsed = JsonParser.parseString(line);
                } catch (RuntimeException exception) {
                    throw new IOException("Invalid structured HUD JSON at line " + lineNumber, exception);
                }
                if (!parsed.isJsonObject()) {
                    throw new IOException("Structured HUD line is not an object at line " + lineNumber);
                }
                HudState state = parseState(parsed.getAsJsonObject(), job, lineNumber);
                if (state.serverTick() != expectedTick) {
                    throw new IOException(
                        "Structured HUD ticks must be contiguous; expected " + expectedTick
                            + " at line " + lineNumber + ", found " + state.serverTick()
                    );
                }
                states.add(state);
                expectedTick++;
                if (states.size() > spec.records()) {
                    throw new IOException("Structured HUD sidecar contains more records than declared");
                }
            }
        }
        if (states.size() != spec.records() || expectedTick - 1 != spec.endServerTick()) {
            throw new IOException("Structured HUD sidecar record count or tick envelope does not match");
        }
        if (Files.size(spec.path()) != spec.sizeBytes() || !sha256(spec.path()).equals(spec.sha256())) {
            throw new IOException("Structured HUD sidecar changed while it was being loaded");
        }
        return new StructuredHudTimeline(spec, job.playerId(), spec.startServerTick(), states);
    }

    void prepare(RegistryAccess registries) throws IOException {
        if (this.prepared) {
            return;
        }
        for (HudState state : this.states) {
            for (InventoryStack entry : state.inventory()) {
                ItemStack decoded = this.decodedStacks.get(entry.stackSnbt());
                if (decoded == null) {
                    decoded = decodeStack(registries, entry.stackSnbt());
                    this.decodedStacks.put(entry.stackSnbt(), decoded);
                }
                String decodedItem = BuiltInRegistries.ITEM.getKey(decoded.getItem()).toString();
                if (decoded.isEmpty()
                    || decoded.getCount() != entry.count()
                    || decoded.getDamageValue() != entry.damage()
                    || decoded.getMaxDamage() != entry.maxDamage()
                    || !decodedItem.equals(entry.item())) {
                    throw new IOException(
                        "Structured HUD stack diagnostics do not match stack_snbt at tick "
                            + state.serverTick() + " slot " + entry.slot()
                    );
                }
            }
        }
        this.prepared = true;
    }

    boolean covers(long serverTick) {
        return serverTick >= this.spec.startServerTick() && serverTick <= this.spec.endServerTick();
    }

    boolean isPrepared() {
        return this.prepared;
    }

    synchronized void apply(
        Minecraft minecraft, long serverTick, boolean countsTowardRender
    ) throws IOException {
        if (!this.covers(serverTick)) {
            return;
        }
        if (!this.prepared || minecraft.player == null) {
            throw new IOException("Structured HUD cannot be applied before the replay client is ready");
        }
        int index = Math.toIntExact(serverTick - this.startTick);
        HudState state = this.states.get(index);
        Entity camera = minecraft.getCameraEntity();
        CameraKind cameraKind;
        if (camera == minecraft.player) {
            cameraKind = CameraKind.REPLAY_VIEWER;
        } else if (camera instanceof Player && camera.getUUID().equals(this.playerId)) {
            cameraKind = CameraKind.REQUESTED_PLAYER;
        } else {
            cameraKind = CameraKind.OTHER;
        }

        ProjectionPlan plan = decideProjection(cameraKind, countsTowardRender);
        if (plan.reject()) {
            throw new IOException(
                "Structured HUD render camera does not match the requested player " + this.playerId
            );
        }
        if (!plan.applyCameraFullState()) {
            return;
        }
        // Minecraft renders the first-person hand and experience from the replay viewer,
        // but zero health on that LocalPlayer opens a DeathScreen and wedges Flashback export.
        if (plan.applyViewerPresentation()) {
            applyPresentation(minecraft.player, state);
        }
        applyCameraFullState((Player) camera, state);
        if (countsTowardRender) {
            this.appliedTicks.set(index);
        }
    }

    static ProjectionPlan decideProjection(CameraKind cameraKind, boolean countsTowardRender) {
        if (cameraKind == CameraKind.REQUESTED_PLAYER) {
            return APPLY_REQUESTED_CAMERA;
        }
        return countsTowardRender ? REJECT_PROJECTION : SKIP_PROJECTION;
    }

    private void applyPresentation(Player player, HudState state) {
        Inventory inventory = player.getInventory();
        inventory.clearContent();
        for (InventoryStack entry : state.inventory()) {
            inventory.setItem(entry.slot(), this.decodedStacks.get(entry.stackSnbt()).copy());
        }
        inventory.setSelectedSlot(state.selectedSlot());
        inventory.setChanged();

        player.experienceProgress = state.experienceProgress();
        player.totalExperience = state.totalExperience();
        player.experienceLevel = state.experienceLevel();
        if (player instanceof LocalPlayer localPlayer) {
            localPlayer.setExperienceValues(
                state.experienceProgress(), state.totalExperience(), state.experienceLevel()
            );
        }
    }

    private void applyCameraFullState(Player player, HudState state) throws IOException {
        applyPresentation(player, state);

        AttributeInstance maxHealth = player.getAttribute(Attributes.MAX_HEALTH);
        if (maxHealth == null) {
            throw new IOException("Replay player has no max-health attribute");
        }
        for (AttributeModifier modifier : List.copyOf(maxHealth.getModifiers())) {
            maxHealth.removeModifier(modifier);
        }
        maxHealth.setBaseValue(state.maxHealth());
        player.setHealth(state.health());
        player.setAbsorptionAmount(state.absorption());
        player.setAirSupply(state.air());
        if (player.getMaxAirSupply() != state.maxAir()) {
            throw new IOException(
                "Replay player max air does not match structured state at tick " + state.serverTick()
            );
        }
        player.getFoodData().setFoodLevel(state.foodLevel());
        player.getFoodData().setSaturation(state.saturation());
    }

    synchronized void verifyApplied(long firstTick, long lastTick) throws IOException {
        if (!this.covers(firstTick) || !this.covers(lastTick) || lastTick < firstTick) {
            throw new IOException("Rendered tick range is outside the structured HUD sidecar");
        }
        int first = Math.toIntExact(firstTick - this.startTick);
        int lastExclusive = Math.toIntExact(lastTick - this.startTick + 1);
        int missing = this.appliedTicks.nextClearBit(first);
        if (missing < lastExclusive) {
            throw new IOException(
                "Structured HUD state was not applied for rendered server tick " + (this.startTick + missing)
            );
        }
    }

    void validateIntegrity() throws IOException {
        if (Files.isSymbolicLink(this.spec.path()) || !Files.isRegularFile(this.spec.path())) {
            throw new IOException("Structured HUD sidecar disappeared or became a symlink");
        }
        if (Files.size(this.spec.path()) != this.spec.sizeBytes()
            || !sha256(this.spec.path()).equals(this.spec.sha256())) {
            throw new IOException("Structured HUD sidecar changed during rendering");
        }
    }

    JsonObject resultEnvelope() {
        JsonObject value = new JsonObject();
        value.addProperty("schema_version", this.spec.schemaVersion());
        value.addProperty("type", this.spec.type());
        value.addProperty("format", this.spec.format());
        value.addProperty("sha256", this.spec.sha256());
        value.addProperty("size_bytes", this.spec.sizeBytes());
        value.addProperty("records", this.spec.records());
        value.addProperty("start_server_tick", this.spec.startServerTick());
        value.addProperty("end_server_tick", this.spec.endServerTick());
        value.addProperty("dataset_id", this.spec.datasetId());
        value.addProperty("dataset_manifest_sha256", this.spec.datasetManifestSha256());
        value.addProperty("samples_sha256", this.spec.samplesSha256());
        value.addProperty("session_id", this.spec.sessionId());
        value.addProperty("player_uuid", this.spec.playerId().toString());
        value.addProperty("connection_id", this.spec.connectionId());
        return value;
    }

    private static HudState parseState(JsonObject row, RenderJobSpec job, long lineNumber)
        throws IOException {
        try {
            if (requiredInt(row, "schema_version") != 1
                || !SIDECAR_TYPE.equals(requiredString(row, "sidecar_type"))
                || !job.sessionId().equals(requiredString(row, "session_id"))
                || !job.connectionId().equals(requiredString(row, "connection_id"))
                || !job.playerId().equals(UUID.fromString(requiredString(row, "player_uuid")))) {
                throw new IOException("Structured HUD identity mismatch at line " + lineNumber);
            }
            long serverTick = requiredLong(row, "server_tick");
            JsonObject state = requiredObject(row, "state");
            float health = finiteFloat(state, "health", 0.0f, 2048.0f);
            float maxHealth = finiteFloat(state, "max_health", 0.01f, 2048.0f);
            float absorption = finiteFloat(state, "absorption", 0.0f, 2048.0f);
            int air = boundedInt(state, "air", -1_000_000, 1_000_000);
            int maxAir = boundedInt(state, "max_air", 1, 1_000_000);
            int food = boundedInt(state, "food_level", 0, 20);
            float saturation = finiteFloat(state, "saturation", 0.0f, 20.0f);
            float experienceProgress = finiteFloat(state, "experience_progress", 0.0f, 1.0f);
            int experienceLevel = boundedInt(state, "experience_level", 0, Integer.MAX_VALUE);
            int totalExperience = boundedInt(state, "total_experience", 0, Integer.MAX_VALUE);
            int selectedSlot = boundedInt(state, "selected_slot", 0, 8);
            JsonArray inventory = requiredArray(state, "inventory");
            if (inventory.size() > MAX_INVENTORY_SLOT + 1) {
                throw new IOException("Too many structured HUD inventory entries at line " + lineNumber);
            }
            Set<Integer> slots = new HashSet<>();
            List<InventoryStack> stacks = new ArrayList<>(inventory.size());
            for (JsonElement element : inventory) {
                if (!element.isJsonObject()) {
                    throw new IOException("Inventory entry is not an object at line " + lineNumber);
                }
                JsonObject entry = element.getAsJsonObject();
                int slot = boundedInt(entry, "slot", 0, MAX_INVENTORY_SLOT);
                if (!slots.add(slot)) {
                    throw new IOException("Duplicate inventory slot " + slot + " at line " + lineNumber);
                }
                String snbt = requiredString(entry, "stack_snbt");
                String item = requiredString(entry, "item");
                int count = boundedInt(entry, "count", 1, 999);
                int damage = boundedInt(entry, "damage", 0, Integer.MAX_VALUE);
                int maxDamage = boundedInt(entry, "max_damage", 0, Integer.MAX_VALUE);
                if (snbt.length() > MAX_STACK_SNBT_CHARS
                    || !item.matches("[a-z0-9_.-]+:[a-z0-9_./-]+")) {
                    throw new IOException("Invalid structured HUD stack at line " + lineNumber);
                }
                stacks.add(new InventoryStack(slot, snbt, item, count, damage, maxDamage));
            }
            stacks.sort(java.util.Comparator.comparingInt(InventoryStack::slot));
            return new HudState(
                serverTick, health, maxHealth, absorption, air, maxAir, food, saturation,
                experienceProgress, experienceLevel, totalExperience, selectedSlot, List.copyOf(stacks)
            );
        } catch (IOException exception) {
            throw exception;
        } catch (RuntimeException exception) {
            throw new IOException("Invalid structured HUD data at line " + lineNumber, exception);
        }
    }

    private static ItemStack decodeStack(RegistryAccess registries, String snbt) throws IOException {
        try {
            return ItemStack.CODEC.parse(
                RegistryOps.create(NbtOps.INSTANCE, registries),
                TagParser.parseCompoundFully(snbt)
            ).getOrThrow(message -> new IOException("Invalid structured HUD stack_snbt: " + message));
        } catch (CommandSyntaxException exception) {
            throw new IOException("Invalid structured HUD stack_snbt", exception);
        }
    }

    private static String sha256(java.nio.file.Path path) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException exception) {
            throw new IOException("SHA-256 is unavailable", exception);
        }
        try (InputStream input = Files.newInputStream(path)) {
            byte[] buffer = new byte[1024 * 1024];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                if (read > 0) {
                    digest.update(buffer, 0, read);
                }
            }
        }
        return java.util.HexFormat.of().formatHex(digest.digest());
    }

    private static JsonObject requiredObject(JsonObject object, String key) {
        if (!object.has(key) || !object.get(key).isJsonObject()) {
            throw new IllegalArgumentException("Missing object: " + key);
        }
        return object.getAsJsonObject(key);
    }

    private static JsonArray requiredArray(JsonObject object, String key) {
        if (!object.has(key) || !object.get(key).isJsonArray()) {
            throw new IllegalArgumentException("Missing array: " + key);
        }
        return object.getAsJsonArray(key);
    }

    private static String requiredString(JsonObject object, String key) {
        if (!object.has(key) || !object.get(key).isJsonPrimitive()) {
            throw new IllegalArgumentException("Missing string: " + key);
        }
        String value = object.get(key).getAsString();
        if (value.isBlank()) {
            throw new IllegalArgumentException("Blank string: " + key);
        }
        return value;
    }

    private static long requiredLong(JsonObject object, String key) {
        if (!object.has(key) || !object.get(key).isJsonPrimitive()) {
            throw new IllegalArgumentException("Missing integer: " + key);
        }
        return object.get(key).getAsLong();
    }

    private static int requiredInt(JsonObject object, String key) {
        return Math.toIntExact(requiredLong(object, key));
    }

    private static int boundedInt(JsonObject object, String key, int minimum, int maximum) {
        int value = requiredInt(object, key);
        if (value < minimum || value > maximum) {
            throw new IllegalArgumentException(key + " is out of range");
        }
        return value;
    }

    private static float finiteFloat(JsonObject object, String key, float minimum, float maximum) {
        if (!object.has(key) || !object.get(key).isJsonPrimitive()) {
            throw new IllegalArgumentException("Missing number: " + key);
        }
        float value = object.get(key).getAsFloat();
        if (!Float.isFinite(value) || value < minimum || value > maximum) {
            throw new IllegalArgumentException(key + " is out of range");
        }
        return value;
    }

    private record HudState(
        long serverTick,
        float health,
        float maxHealth,
        float absorption,
        int air,
        int maxAir,
        int foodLevel,
        float saturation,
        float experienceProgress,
        int experienceLevel,
        int totalExperience,
        int selectedSlot,
        List<InventoryStack> inventory
    ) {
    }

    private record InventoryStack(
        int slot,
        String stackSnbt,
        String item,
        int count,
        int damage,
        int maxDamage
    ) {
    }
}
