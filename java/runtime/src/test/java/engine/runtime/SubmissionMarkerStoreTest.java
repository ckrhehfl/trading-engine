package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link SubmissionMarkerStore} is the durable (survives process restart),
 * {@code clientOrderId}-keyed marker store behind {@link
 * PersistentSubmissionOrderExecutor}'s {@code SUBMISSION_UNKNOWN} handling
 * -- see that class's Javadoc and {@code .planning/paper-trading-h-vst-
 * integration.md} for the full design. A simple JSON file under {@code
 * var/live/} (matching the existing {@code var/live/signals/}/{@code
 * var/live/reports/} convention), not a general persistence framework --
 * this is the first piece of cross-restart persistence in the project.
 */
class SubmissionMarkerStoreTest {

    @Test
    void aFreshStoreWithNoFileOnDiskStartsEmpty(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));

        assertTrue(store.all().isEmpty());
    }

    @Test
    void recordPersistsAMarkerVisibleToAFreshInstanceAgainstTheSameFile(@TempDir Path tempDir) {
        Path file = tempDir.resolve("markers.json");
        UUID id = UUID.randomUUID();

        SubmissionMarkerStore first = new SubmissionMarkerStore(file);
        first.record(id, "BTC-USDT");

        SubmissionMarkerStore second = new SubmissionMarkerStore(file); // simulates a process restart
        List<SubmissionMarker> markers = second.all();
        assertEquals(1, markers.size());
        assertEquals(id, markers.get(0).clientOrderId());
        assertEquals("BTC-USDT", markers.get(0).symbol());
    }

    @Test
    void clearRemovesAMarkerDurablyAcrossASimulatedRestart(@TempDir Path tempDir) {
        Path file = tempDir.resolve("markers.json");
        UUID id = UUID.randomUUID();

        SubmissionMarkerStore first = new SubmissionMarkerStore(file);
        first.record(id, "BTC-USDT");
        first.clear(id);

        SubmissionMarkerStore second = new SubmissionMarkerStore(file);
        assertTrue(second.all().isEmpty());
    }

    @Test
    void recordCreatesParentDirectoriesIfNeeded(@TempDir Path tempDir) {
        Path file = tempDir.resolve("nested").resolve("dir").resolve("markers.json");
        SubmissionMarkerStore store = new SubmissionMarkerStore(file);

        store.record(UUID.randomUUID(), "BTC-USDT");

        assertTrue(Files.exists(file));
    }

    @Test
    void multipleMarkersForDifferentClientOrderIdsAllPersist(@TempDir Path tempDir) {
        Path file = tempDir.resolve("markers.json");
        UUID a = UUID.randomUUID();
        UUID b = UUID.randomUUID();

        SubmissionMarkerStore store = new SubmissionMarkerStore(file);
        store.record(a, "BTC-USDT");
        store.record(b, "ETH-USDT");

        SubmissionMarkerStore reloaded = new SubmissionMarkerStore(file);
        assertEquals(2, reloaded.all().size());
    }

    @Test
    void clearingAnUnknownClientOrderIdIsANoOp(@TempDir Path tempDir) {
        Path file = tempDir.resolve("markers.json");
        SubmissionMarkerStore store = new SubmissionMarkerStore(file);

        store.clear(UUID.randomUUID()); // never recorded

        assertTrue(store.all().isEmpty());
    }

    @Test
    void aMissingFileIsTreatedAsEmptyNotAnError(@TempDir Path tempDir) {
        Path file = tempDir.resolve("does-not-exist.json");

        SubmissionMarkerStore store = new SubmissionMarkerStore(file);

        assertTrue(store.all().isEmpty());
    }

    @Test
    void aCorruptFileIsTreatedAsEmptyRatherThanThrowing(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("markers.json");
        Files.writeString(file, "this is not valid JSON {{{");

        SubmissionMarkerStore store = new SubmissionMarkerStore(file);

        assertTrue(store.all().isEmpty());
    }

    @Test
    void recordingTheSameClientOrderIdTwiceOverwritesRatherThanDuplicates(@TempDir Path tempDir) {
        Path file = tempDir.resolve("markers.json");
        UUID id = UUID.randomUUID();
        SubmissionMarkerStore store = new SubmissionMarkerStore(file);

        store.record(id, "BTC-USDT");
        store.record(id, "BTC-USDT");

        assertEquals(1, store.all().size());
    }
}
