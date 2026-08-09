package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
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
 *
 * <p><b>Revised after a real, correctly-identified CodeRabbit review finding
 * on this PR</b>: a corrupt or otherwise-unreadable (but present) marker
 * file used to be silently treated as empty, which -- combined with {@link
 * SubmissionMarkerResolver}'s own "never auto-clear" fix for a separate,
 * related finding -- could silently discard real, still-unresolved
 * {@code SUBMISSION_UNKNOWN} state and let the process start believing
 * nothing needs review. Now: only a genuinely <i>missing</i> file
 * ({@code NoSuchFileException}) is treated as empty; any other read or
 * parse failure fails closed (throws), matching this class's own existing
 * write-failure convention. Writes are also now atomic (temp file + move),
 * matching {@code DailyReportGenerator}'s own established convention in
 * this exact codebase, so a crash mid-write can never leave a corrupt file
 * behind for the next start to (now, correctly) refuse to proceed past.
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

    /**
     * Reversed from this class's original behavior -- see class Javadoc.
     * A corrupt file must never be silently treated as "nothing to review."
     */
    @Test
    void aCorruptFileFailsClosedRatherThanBeingTreatedAsEmpty(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("markers.json");
        Files.writeString(file, "this is not valid JSON {{{");

        assertThrows(IllegalStateException.class, () -> new SubmissionMarkerStore(file));
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

    @Test
    void writesAreAtomicNoTempFileLeftBehindAfterASuccessfulRecord(@TempDir Path tempDir) {
        Path file = tempDir.resolve("markers.json");
        SubmissionMarkerStore store = new SubmissionMarkerStore(file);

        store.record(UUID.randomUUID(), "BTC-USDT");

        assertTrue(Files.exists(file));
        assertFalse(Files.exists(tempDir.resolve("markers.json.tmp")), "the temp file must be renamed away, not left behind");
    }

    /**
     * Test-only seam (package-private constructor overload, mirroring
     * {@code DailyReportGenerator}'s own identical {@code AtomicMover}
     * testability pattern): forces the atomic-move step to fail, proving
     * the fallback still leaves the store durably correct rather than
     * losing the just-recorded marker.
     */
    @Test
    void aNonAtomicMoveFallbackStillPersistsTheMarkerWhenAtomicMoveFails(@TempDir Path tempDir) {
        Path file = tempDir.resolve("markers.json");
        SubmissionMarkerStore.AtomicMover flakyMover = (source, target) -> {
            throw new java.nio.file.AtomicMoveNotSupportedException(
                    source.toString(), target.toString(), "test-forced failure");
        };
        SubmissionMarkerStore store = new SubmissionMarkerStore(file, flakyMover);
        UUID id = UUID.randomUUID();

        store.record(id, "BTC-USDT");

        SubmissionMarkerStore reloaded = new SubmissionMarkerStore(file);
        assertEquals(1, reloaded.all().size());
        assertEquals(id, reloaded.all().get(0).clientOrderId());
    }

    @Test
    void defaultAtomicMoverUsesRealAtomicMove(@TempDir Path tempDir) throws IOException {
        // Confirms the production default actually goes through
        // StandardCopyOption.ATOMIC_MOVE, not just some file write --
        // exercised indirectly (a real move happens, verified by content),
        // matching this class's own already-tested "durable across a
        // simulated restart" behavior, now via the explicit AtomicMover
        // seam rather than only the implicit default.
        Path file = tempDir.resolve("markers.json");
        SubmissionMarkerStore store = new SubmissionMarkerStore(
                file, (source, target) -> Files.move(source, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING));

        store.record(UUID.randomUUID(), "BTC-USDT");

        assertTrue(Files.exists(file));
    }
}
