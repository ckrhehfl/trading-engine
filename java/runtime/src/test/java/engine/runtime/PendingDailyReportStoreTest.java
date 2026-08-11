package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link PendingDailyReportStore} is the durable (survives process
 * restart), ordered store behind {@link DailyReportGenerator}'s
 * pending-report retry queue -- GitHub issue #75. A simple JSON file, not
 * a general persistence framework -- mirrors {@link SubmissionMarkerStore
 * Test}'s own structure closely (same category of first-class,
 * standalone unit test for a durable local-file store), with one
 * deliberate divergence covered explicitly below: this store fails SAFE
 * (starts empty, logs loudly) rather than closed (throws) on a corrupt or
 * otherwise-unreadable file -- see {@link PendingDailyReportStore}'s own
 * class Javadoc for why that's the right choice here specifically.
 */
class PendingDailyReportStoreTest {

    private static DailyReport report(String isoDate, int ticksAttempted) {
        return new DailyReport(
                LocalDate.parse(isoDate),
                new BigDecimal("100000"),
                new BigDecimal("100000"),
                List.of(),
                List.of(),
                false,
                ticksAttempted,
                ticksAttempted,
                new BigDecimal("1.000000"));
    }

    @Test
    void aFreshStoreWithNoFileOnDiskStartsEmpty(@TempDir Path tempDir) {
        PendingDailyReportStore store = new PendingDailyReportStore(tempDir.resolve("pending.json"));

        assertTrue(store.all().isEmpty());
    }

    @Test
    void appendPersistsAReportVisibleToAFreshInstanceAgainstTheSameFile(@TempDir Path tempDir) {
        Path file = tempDir.resolve("pending.json");
        DailyReport report = report("2026-08-07", 3);

        PendingDailyReportStore first = new PendingDailyReportStore(file);
        first.append(report);

        PendingDailyReportStore second = new PendingDailyReportStore(file); // simulates a process restart
        List<DailyReport> reports = second.all();
        assertEquals(1, reports.size());
        assertEquals(LocalDate.of(2026, 8, 7), reports.get(0).date());
        assertEquals(3, reports.get(0).ticksAttempted());
    }

    @Test
    void removeOldestRemovesTheOldestEntryDurablyAcrossASimulatedRestart(@TempDir Path tempDir) {
        Path file = tempDir.resolve("pending.json");
        PendingDailyReportStore first = new PendingDailyReportStore(file);
        first.append(report("2026-08-07", 1));
        first.removeOldest();

        PendingDailyReportStore second = new PendingDailyReportStore(file);
        assertTrue(second.all().isEmpty());
    }

    @Test
    void multipleReportsAppendedPreserveOrderAcrossAReload(@TempDir Path tempDir) {
        Path file = tempDir.resolve("pending.json");
        PendingDailyReportStore store = new PendingDailyReportStore(file);
        store.append(report("2026-08-07", 1));
        store.append(report("2026-08-08", 2));
        store.append(report("2026-08-09", 3));

        PendingDailyReportStore reloaded = new PendingDailyReportStore(file);
        List<DailyReport> reports = reloaded.all();
        assertEquals(3, reports.size());
        assertEquals(LocalDate.of(2026, 8, 7), reports.get(0).date());
        assertEquals(LocalDate.of(2026, 8, 8), reports.get(1).date());
        assertEquals(LocalDate.of(2026, 8, 9), reports.get(2).date());
    }

    @Test
    void removeOldestOnAnEmptyStoreIsANoOp(@TempDir Path tempDir) {
        Path file = tempDir.resolve("pending.json");
        PendingDailyReportStore store = new PendingDailyReportStore(file);

        store.removeOldest(); // never appended anything

        assertTrue(store.all().isEmpty());
        assertFalse(Files.exists(file), "a no-op removal must not create a file that never needed to exist");
    }

    @Test
    void aMissingFileIsTreatedAsEmptyNotAnError(@TempDir Path tempDir) {
        Path file = tempDir.resolve("does-not-exist.json");

        PendingDailyReportStore store = new PendingDailyReportStore(file);

        assertTrue(store.all().isEmpty());
    }

    /**
     * The deliberate divergence from {@code SubmissionMarkerStore}'s own
     * fail-CLOSED behavior -- see {@link PendingDailyReportStore}'s class
     * Javadoc, "Fails SAFE (not closed)". A corrupt file here must NOT
     * throw and must NOT crash construction -- it starts empty instead,
     * logged loudly, matching this task's own explicit brief ("should fail
     * safe, not crash the whole app").
     */
    @Test
    void aCorruptFileFailsSafeByStartingEmptyRatherThanThrowing(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("pending.json");
        Files.writeString(file, "this is not valid JSON {{{");

        PendingDailyReportStore store = assertDoesNotThrow(() -> new PendingDailyReportStore(file));

        assertTrue(store.all().isEmpty(), "a corrupt file must be treated as empty, not crash construction");
    }

    /**
     * A second fail-safe case: the file's own parent path component isn't
     * even a directory (e.g. a prior obstruction), so the read itself fails
     * with something other than {@code NoSuchFileException}. Must be
     * handled identically to a corrupt file -- fail safe, not throw.
     */
    @Test
    void anUnreadablePathFailsSafeByStartingEmptyRatherThanThrowing(@TempDir Path tempDir) throws IOException {
        Path blockingFile = tempDir.resolve("blocked");
        Files.writeString(blockingFile, "not a directory");
        Path file = blockingFile.resolve("pending.json"); // blockingFile's parent segment isn't a directory

        PendingDailyReportStore store = assertDoesNotThrow(() -> new PendingDailyReportStore(file));

        assertTrue(store.all().isEmpty());
    }

    /**
     * A persist (write) failure must also fail safe rather than propagate
     * -- see class Javadoc's "must never crash the scheduler" reasoning.
     * The in-memory mutation must still be visible even though the durable
     * copy could not be written.
     */
    @Test
    void aPersistFailureDoesNotThrowAndTheInMemoryQueueStaysCorrect(@TempDir Path tempDir) throws IOException {
        Path blockingFile = tempDir.resolve("blocked");
        Files.writeString(blockingFile, "not a directory");
        Path file = blockingFile.resolve("pending.json"); // parent can never be created

        PendingDailyReportStore store = new PendingDailyReportStore(file);

        assertDoesNotThrow(() -> store.append(report("2026-08-07", 1)));

        assertEquals(1, store.all().size(), "the in-memory queue must reflect the append even though persistence failed");
    }

    @Test
    void writesAreAtomicNoTempFileLeftBehindAfterASuccessfulAppend(@TempDir Path tempDir) {
        Path file = tempDir.resolve("pending.json");
        PendingDailyReportStore store = new PendingDailyReportStore(file);

        store.append(report("2026-08-07", 1));

        assertTrue(Files.exists(file));
        assertFalse(Files.exists(tempDir.resolve("pending.json.tmp")), "the temp file must be renamed away, not left behind");
    }

    /**
     * Test-only seam (package-private constructor overload, mirroring
     * {@code SubmissionMarkerStore}'s/{@code DailyReportGenerator}'s own
     * identical {@code AtomicMover} testability pattern): forces the
     * atomic-move step to fail, proving the fallback still leaves the
     * store durably correct rather than losing the just-appended report.
     */
    @Test
    void aNonAtomicMoveFallbackStillPersistsTheReportWhenAtomicMoveFails(@TempDir Path tempDir) {
        Path file = tempDir.resolve("pending.json");
        PendingDailyReportStore.AtomicMover flakyMover = (source, target) -> {
            throw new AtomicMoveNotSupportedException(source.toString(), target.toString(), "test-forced failure");
        };
        PendingDailyReportStore store = new PendingDailyReportStore(file, flakyMover);

        store.append(report("2026-08-07", 1));

        PendingDailyReportStore reloaded = new PendingDailyReportStore(file);
        assertEquals(1, reloaded.all().size());
        assertEquals(LocalDate.of(2026, 8, 7), reloaded.all().get(0).date());
    }

    @Test
    void defaultAtomicMoverPersistsWithoutTheTestSeam(@TempDir Path tempDir) {
        // Exercises the real production default (1-arg constructor) rather
        // than a lambda that merely re-implements the same logic -- same
        // real, correctly-identified CodeRabbit review finding
        // SubmissionMarkerStoreTest's own identical test already documents.
        Path file = tempDir.resolve("pending.json");
        PendingDailyReportStore store = new PendingDailyReportStore(file);

        store.append(report("2026-08-07", 1));

        assertTrue(Files.exists(file));
        assertFalse(Files.exists(tempDir.resolve("pending.json.tmp")));
    }
}
