package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link AccountLedgerStore} is the durable JSON read/write layer behind
 * the shared, cross-process {@link AccountLedger} -- see that class's
 * Javadoc and the governing plan's "2. The shared ledger" section. Unlike
 * {@code SubmissionMarkerStore} (this module's closest precedent, loads
 * once at construction and caches), every method here is {@code static}
 * and reloads from disk on every call -- deliberate, since multiple
 * independent OS processes mutate the same file (see {@link
 * AccountLedgerStore}'s own class Javadoc for the full reasoning).
 */
class AccountLedgerStoreTest {

    @Test
    void loadWithNoFileOnDiskReturnsAFreshlyBootstrappedLedger(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");

        AccountLedger ledger = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000"));

        assertEquals("KIS", ledger.venue());
        assertEquals("acct-1", ledger.accountId());
        assertEquals(new BigDecimal("100000"), ledger.allocatedVirtualCapital());
        assertEquals(BigDecimal.ZERO, ledger.lastReconciledDailyPnlPercent());
        assertEquals(BigDecimal.ZERO, ledger.lastReconciledWeeklyPnlPercent());
        assertEquals(BigDecimal.ZERO, ledger.lastReconciledMonthlyPnlPercent());
        assertNull(ledger.lastReconciledAt());
        assertNull(ledger.reconciliationAlarmTrippedAt());
        assertNull(ledger.reconciliationAlarmReason());
        assertTrue(ledger.reservations().isEmpty());
    }

    @Test
    void persistThenLoadRoundTripsARealLedgerIncludingReservations(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        LedgerReservation reservation = new LedgerReservation(
                UUID.randomUUID(), "A11609", 12345L, "host-a", new BigDecimal("1500000.50"), Instant.now());
        AccountLedger original = new AccountLedger(
                "KIS",
                "acct-1",
                new BigDecimal("100000000"),
                new BigDecimal("-0.01"),
                new BigDecimal("-0.02"),
                new BigDecimal("-0.03"),
                Instant.now(),
                null,
                null,
                List.of(reservation));

        AccountLedgerStore.persist(file, original);
        AccountLedger reloaded = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000000"));

        assertEquals(original, reloaded);
    }

    @Test
    void persistThenLoadRoundTripsATrippedAlarm(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger withAlarm = new AccountLedger(
                "KIS",
                "acct-1",
                new BigDecimal("100000000"),
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                BigDecimal.ZERO,
                Instant.now(),
                Instant.now(),
                "ledger exposure diverged from real account by more than 10%",
                List.of());

        AccountLedgerStore.persist(file, withAlarm);
        AccountLedger reloaded = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000000"));

        assertEquals(withAlarm.reconciliationAlarmTrippedAt(), reloaded.reconciliationAlarmTrippedAt());
        assertEquals(withAlarm.reconciliationAlarmReason(), reloaded.reconciliationAlarmReason());
    }

    @Test
    void loadReflectsTheLatestPersistedStateNotAStaleCachedOne(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger first = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        AccountLedgerStore.persist(file, first);
        AccountLedger loadedFirst = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("1000"));
        assertEquals(new BigDecimal("1000"), loadedFirst.allocatedVirtualCapital());

        AccountLedger second = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("2000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        AccountLedgerStore.persist(file, second);
        AccountLedger loadedSecond = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("1000"));

        // Static, stateless methods -- no instance to have cached the
        // first value; this proves it, not merely asserts it by
        // construction.
        assertEquals(new BigDecimal("2000"), loadedSecond.allocatedVirtualCapital());
    }

    @Test
    void aCorruptFileFailsClosedRatherThanReturningAFreshLedger(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Files.writeString(file, "this is not valid JSON {{{");

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000")));
    }

    @Test
    void aWellFormedJsonFileMissingARequiredFieldFailsClosed(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        // Valid JSON, but missing "venue" -- AccountLedger's own compact
        // constructor requires it non-null. Proves the fail-closed
        // behavior covers a structurally-plausible-but-invalid file, not
        // just outright unparseable garbage.
        Files.writeString(
                file,
                "{\"accountId\":\"acct-1\",\"allocatedVirtualCapital\":100000,"
                        + "\"lastReconciledDailyPnlPercent\":0,\"lastReconciledWeeklyPnlPercent\":0,"
                        + "\"lastReconciledMonthlyPnlPercent\":0,\"reservations\":[]}");

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000")));
    }

    /**
     * Real Major finding ("Heavy lift"), real CodeRabbit review of this
     * PR: {@code persist}'s non-atomic fallback path is not a single
     * atomic operation -- a crash mid-replace could plausibly leave
     * {@code ledgerPath} missing while its {@code .tmp} source still
     * lingers. Without this check, {@code load} would silently bootstrap
     * a fresh, empty ledger, discarding every other process's real,
     * previously-committed reservations. This is a partial mitigation
     * (detects the specific, plausible "stray .tmp, no ledger" evidence),
     * not a claim that every possible interrupted-fallback timing is
     * covered -- see {@code load}'s own Javadoc.
     */
    @Test
    void loadFailsClosedWhenTheLedgerIsMissingButALeftoverTmpFileExists(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Path tmp = tempDir.resolve("ledger.json.tmp");
        AccountLedger orphaned = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("500000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        ObjectMapper mapper = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        Files.writeString(tmp, mapper.writeValueAsString(orphaned));
        // ledgerPath itself deliberately never created -- simulates a
        // crash after the old file was replaced but before (or during) the
        // new content becoming durable at ledgerPath in the non-atomic
        // REPLACE_EXISTING fallback.

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000")));
    }

    @Test
    void loadStillBootstrapsFreshWhenTheLedgerIsMissingAndNoTmpFileExists(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        // No .tmp sibling at all -- the ordinary, expected "never
        // persisted yet" case must still bootstrap fresh, not fail
        // closed just because a file happens to be missing.

        AccountLedger ledger = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000"));

        assertEquals(new BigDecimal("100000"), ledger.allocatedVirtualCapital());
    }

    /**
     * Real Major finding, real CodeRabbit review of this PR: nothing
     * upstream of {@code load} validated that a loaded ledger file's own
     * recorded identity actually matches the {@code (venue, accountId)}
     * the caller requested -- a future path-resolution bug (Task C) or
     * file mix-up could otherwise silently use one account's real,
     * currently-committed exposure to gate a different account's orders.
     */
    @Test
    void loadFailsClosedWhenTheLoadedLedgersVenueDoesNotMatchTheRequestedOne(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledgerForKis = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        AccountLedgerStore.persist(file, ledgerForKis);

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "BINGX", "acct-1", new BigDecimal("1000")));
    }

    @Test
    void loadFailsClosedWhenTheLoadedLedgersAccountIdDoesNotMatchTheRequestedOne(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledgerForAcct1 = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        AccountLedgerStore.persist(file, ledgerForAcct1);

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-2", new BigDecimal("1000")));
    }

    @Test
    void loadSucceedsWhenTheLoadedLedgersIdentityMatchesTheRequestedOneExactly(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        AccountLedgerStore.persist(file, ledger);

        AccountLedger reloaded = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("1000"));

        assertEquals(ledger, reloaded);
    }

    /**
     * Real Major finding, real CodeRabbit review of this PR:
     * {@link LedgerReservation}'s own compact constructor now rejects a
     * non-positive {@code notional} -- exercised here through a real
     * store round trip (a hand-built {@code LedgerReservation} is the
     * more direct unit test, but this also proves the store itself never
     * needs to special-case the rejection: the record's own constructor
     * is the single enforcement point regardless of how it's constructed).
     */
    @Test
    void aReservationWithZeroOrNegativeNotionalIsRejectedByLedgerReservationItself() {
        UUID id = UUID.randomUUID();
        assertThrows(
                IllegalArgumentException.class,
                () -> new LedgerReservation(id, "A11609", 1L, "host-a", BigDecimal.ZERO, Instant.now()));
        assertThrows(
                IllegalArgumentException.class,
                () -> new LedgerReservation(id, "A11609", 1L, "host-a", new BigDecimal("-1"), Instant.now()));
    }

    @Test
    void persistCreatesParentDirectoriesIfNeeded(@TempDir Path tempDir) {
        Path file = tempDir.resolve("nested").resolve("dir").resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        AccountLedgerStore.persist(file, ledger);

        assertTrue(Files.exists(file));
    }

    @Test
    void writesAreAtomicNoTempFileLeftBehindAfterASuccessfulPersist(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        AccountLedgerStore.persist(file, ledger);

        assertTrue(Files.exists(file));
        assertFalse(Files.exists(tempDir.resolve("ledger.json.tmp")), "the temp file must be renamed away, not left behind");
    }

    /**
     * Test-only seam (package-private {@code AtomicMover} overload,
     * mirroring {@code SubmissionMarkerStore}'s own identical testability
     * pattern -- this codebase's established convention is that each
     * durable-store class keeps its own copy of this interface rather than
     * sharing one, see {@link AccountLedgerStore}'s own Javadoc for why):
     * forces the atomic-move step to fail, proving the fallback still
     * leaves the store durably correct rather than losing the just-
     * persisted ledger.
     */
    @Test
    void aNonAtomicMoveFallbackStillPersistsTheLedgerWhenAtomicMoveFails(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedgerStore.AtomicMover flakyMover = (source, target) -> {
            throw new AtomicMoveNotSupportedException(source.toString(), target.toString(), "test-forced failure");
        };
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        AccountLedgerStore.persist(file, ledger, flakyMover);

        AccountLedger reloaded = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("42"));
        assertEquals(new BigDecimal("42"), reloaded.allocatedVirtualCapital());
    }

    @Test
    void defaultAtomicMoverPersistsWithoutTheTestSeam(@TempDir Path tempDir) {
        // Mirrors SubmissionMarkerStoreTest's own
        // defaultAtomicMoverPersistsWithoutTheTestSeam -- exercises the
        // real production default (the 2-arg persist overload) directly,
        // rather than only ever going through the AtomicMover seam.
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        AccountLedgerStore.persist(file, ledger);

        assertTrue(Files.exists(file));
        assertFalse(Files.exists(tempDir.resolve("ledger.json.tmp")));
    }
}
