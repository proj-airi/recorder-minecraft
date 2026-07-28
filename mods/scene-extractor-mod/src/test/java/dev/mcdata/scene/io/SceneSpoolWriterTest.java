package dev.mcdata.scene.io;

import dev.recorderminecraft.artifacts.v1.SceneChangeRecord;
import dev.recorderminecraft.artifacts.v1.SceneFrameRecord;
import com.google.protobuf.util.JsonFormat;
import dev.mcdata.scene.core.SceneEvent;
import dev.mcdata.scene.core.SceneFrame;
import dev.mcdata.scene.core.SceneSnapshot;
import dev.mcdata.scene.job.SceneJob;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class SceneSpoolWriterTest {
    @TempDir
    Path temporary;

    @Test
    void structurallySharesIdenticalSectionBlobsAndPublishesAtomically() throws IOException {
        Path output = temporary.resolve("spool");
        SceneJob.SourceReplay source = new SceneJob.SourceReplay(
            UUID.fromString("33333333-3333-3333-3333-333333333333"), 0,
            temporary.resolve("source.zip"), "0".repeat(64), 1
        );
        SceneEvent.SectionSnapshot section = new SceneEvent.SectionSnapshot(
            List.of(new SceneEvent.BlockState("minecraft:air", Map.of())), new int[4096]
        );

        SceneSpoolWriter.OutputStats stats;
        try (SceneSpoolWriter writer = new SceneSpoolWriter(output)) {
            writer.beginSegment(source);
            writer.writeFrame(new SceneFrame(
                1, 1, 4, source.segmentId(), 0, "minecraft:overworld", 7,
                new SceneEvent.Vec3(0, 64, 0), 2, 1, true
            ), new SceneSnapshot(
                List.of(
                    new SceneSnapshot.Section("minecraft:overworld", 0, 0, 0, section),
                    new SceneSnapshot.Section("minecraft:overworld", 1, 0, 0, section)
                ),
                List.of(),
                List.of()
            ));
            stats = writer.commit();
        }

        assertEquals(1, stats.blobCount());
        assertEquals(1, stats.frameCount());
        assertEquals(3, stats.changeCount());
        assertTrue(Files.isRegularFile(output.resolve("frames.jsonl")));
        assertTrue(Files.isRegularFile(output.resolve("changes.jsonl")));
        assertEquals(1, parseFrame(Files.readAllLines(output.resolve("frames.jsonl")).getFirst()).getServerTick());
        List<String> initialChanges = Files.readAllLines(output.resolve("changes.jsonl"));
        assertEquals(SceneChangeRecord.ChangeCase.SEGMENT_BEGIN, parseChange(initialChanges.get(0)).getChangeCase());
        assertEquals(SceneChangeRecord.ChangeCase.SECTION_SET, parseChange(initialChanges.get(1)).getChangeCase());
        try (var blobs = Files.list(output.resolve("blobs"))) {
            assertEquals(1, blobs.count());
        }
    }

    @Test
    void emitsProvenanceForAnEqualOverlapWithoutWritingADuplicateFrame() throws IOException {
        Path output = temporary.resolve("overlap-spool");
        SceneJob.SourceReplay first = new SceneJob.SourceReplay(
            UUID.fromString("33333333-3333-3333-3333-333333333333"), 0,
            temporary.resolve("source-a.zip"), "a".repeat(64), 1
        );
        SceneJob.SourceReplay second = new SceneJob.SourceReplay(
            UUID.fromString("44444444-4444-4444-4444-444444444444"), 1,
            temporary.resolve("source-b.zip"), "b".repeat(64), 2
        );
        SceneSnapshot snapshot = new SceneSnapshot(List.of(), List.of(), List.of());
        SceneFrame firstFrame = frame(first, 10, 50);
        SceneFrame secondFrame = frame(second, 10, 1);

        SceneSpoolWriter.OutputStats stats;
        try (SceneSpoolWriter writer = new SceneSpoolWriter(output)) {
            writer.beginSegment(first);
            SceneSpoolWriter.PreparedSnapshot firstSnapshot = writer.prepareSnapshot(firstFrame, snapshot);
            writer.writeFrame(firstFrame, firstSnapshot);

            writer.beginSegment(second);
            SceneSpoolWriter.PreparedSnapshot secondSnapshot = writer.prepareSnapshot(secondFrame, snapshot);
            assertEquals(firstSnapshot.sha256(), secondSnapshot.sha256());
            writer.writeSegmentBegin(secondFrame);
            stats = writer.commit();
        }

        assertEquals(1, stats.frameCount());
        assertEquals(2, stats.changeCount());
        List<String> frames = Files.readAllLines(output.resolve("frames.jsonl"));
        assertEquals(1, frames.size());
        assertEquals(first.segmentId().toString(), parseFrame(frames.getFirst()).getSegmentId());
        List<String> changes = Files.readAllLines(output.resolve("changes.jsonl"));
        assertEquals(2, changes.size());
        assertEquals(first.segmentId().toString(), parseChange(changes.get(0)).getSegmentId());
        assertEquals(second.segmentId().toString(), parseChange(changes.get(1)).getSegmentId());
    }

    @Test
    void discardsPrivateBlobStagingWhenTheSpoolIsNotCommitted() throws IOException {
        Path output = temporary.resolve("uncommitted-spool");
        SceneJob.SourceReplay source = new SceneJob.SourceReplay(
            UUID.fromString("33333333-3333-3333-3333-333333333333"), 0,
            temporary.resolve("source.zip"), "0".repeat(64), 1
        );
        SceneEvent.SectionSnapshot section = new SceneEvent.SectionSnapshot(
            List.of(new SceneEvent.BlockState("minecraft:air", Map.of())), new int[4096]
        );

        try (SceneSpoolWriter writer = new SceneSpoolWriter(output)) {
            writer.beginSegment(source);
            writer.writeFrame(frame(source, 1, 1), new SceneSnapshot(
                List.of(new SceneSnapshot.Section("minecraft:overworld", 0, 0, 0, section)),
                List.of(),
                List.of()
            ));
            assertFalse(Files.exists(output));
        }

        assertFalse(Files.exists(output));
        try (var entries = Files.list(temporary)) {
            assertFalse(entries.anyMatch(path -> path.getFileName().toString().startsWith(".uncommitted-spool.tmp-")));
        }
    }

    private static SceneFrame frame(SceneJob.SourceReplay source, long tick, int replayTick) {
        return new SceneFrame(
            tick, replayTick, 4, source.segmentId(), source.segmentOrdinal(),
            "minecraft:overworld", 7, new SceneEvent.Vec3(0, 64, 0), 0, 0, true
        );
    }

    private static SceneFrameRecord parseFrame(String json) throws IOException {
        SceneFrameRecord.Builder value = SceneFrameRecord.newBuilder();
        JsonFormat.parser().merge(json, value);
        return value.build();
    }

    private static SceneChangeRecord parseChange(String json) throws IOException {
        SceneChangeRecord.Builder value = SceneChangeRecord.newBuilder();
        JsonFormat.parser().merge(json, value);
        return value.build();
    }
}
