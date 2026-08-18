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
import java.nio.file.LinkOption;
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
        // defaultAllocatedCapital held fixed at 2000 (>= both persisted
        // values below) throughout this test -- deliberately, so the new
        // stored-exceeds-configured-default fail-closed check (real Minor
        // finding, real CodeRabbit review of this PR) never trips here;
        // this test's own subject is the "no caching" property, not that
        // check, and 1000 -> 2000 already proves a real state change is
        // observed either direction.
        BigDecimal defaultAllocatedCapital = new BigDecimal("2000");
        AccountLedger first = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        AccountLedgerStore.persist(file, first);
        AccountLedger loadedFirst = AccountLedgerStore.load(file, "KIS", "acct-1", defaultAllocatedCapital);
        assertEquals(new BigDecimal("1000"), loadedFirst.allocatedVirtualCapital());

        AccountLedger second = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("2000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        AccountLedgerStore.persist(file, second);
        AccountLedger loadedSecond = AccountLedgerStore.load(file, "KIS", "acct-1", defaultAllocatedCapital);

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

    /**
     * Real Major finding, real CodeRabbit review of this PR: the JSON
     * literal {@code null} is valid JSON, so {@code
     * MAPPER.readValue(raw, AccountLedger.class)} returns a plain Java
     * {@code null} without throwing -- left unchecked, the very next
     * access ({@code ledger.venue()}) would have thrown a raw {@code
     * NullPointerException} instead of this method's own intended
     * {@code IllegalStateException} fail-closed contract.
     */
    @Test
    void aFileContainingTheJsonLiteralNullFailsClosed(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Files.writeString(file, "null");

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000")));
    }

    /**
     * Real Major finding, real CodeRabbit review of this PR:
     * {@code DeserializationFeature.FAIL_ON_TRAILING_TOKENS} is disabled
     * by default in Jackson, meaning {@code readValue} would otherwise
     * silently ignore anything after the first complete JSON value --
     * undermining this method's own fail-closed contract for a corrupted
     * file that happens to hold a valid ledger object followed by
     * trailing garbage.
     */
    @Test
    void aFileWithAValidLedgerFollowedByTrailingJsonNullFailsClosed(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        ObjectMapper mapper = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        Files.writeString(file, mapper.writeValueAsString(ledger) + "null");

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("1000")));
    }

    @Test
    void aFileWithTwoConcatenatedValidLedgerObjectsFailsClosed(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        ObjectMapper mapper = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        String single = mapper.writeValueAsString(ledger);
        Files.writeString(file, single + single);

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("1000")));
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
     * Real Minor finding, real CodeRabbit review of this PR: {@code
     * defaultAllocatedCapital} previously had no positivity check at all --
     * a zero or negative value is meaningless for a risk budget and would
     * otherwise flow straight into a freshly-bootstrapped ledger.
     */
    @Test
    void loadRejectsAZeroOrNegativeDefaultAllocatedCapital(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");

        assertThrows(
                IllegalArgumentException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", BigDecimal.ZERO));
        assertThrows(
                IllegalArgumentException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("-1")));
    }

    /**
     * Real Minor finding, real CodeRabbit review of this PR, grounded
     * directly in CLAUDE.md's own "never weaken risk limits... without
     * explicit human approval" rule: previously, {@code
     * defaultAllocatedCapital} was only ever consulted when bootstrapping a
     * brand new ledger -- for an existing one, the stored {@code
     * allocatedVirtualCapital} was returned as-is with no comparison
     * against the currently-configured default at all. That would silently
     * defeat an operator's own attempt to reduce a risk budget: lowering
     * {@code defaultAllocatedCapital} in configuration would have no effect
     * for as long as a larger, previously-persisted value remained on disk.
     * This proves the fail-closed fix directly, not merely by absence of a
     * counter-example.
     */
    @Test
    void loadFailsClosedWhenAnExistingLedgersAllocatedCapitalExceedsTheConfiguredDefault(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledgerWithALargeStoredAllocation = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("500000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        AccountLedgerStore.persist(file, ledgerWithALargeStoredAllocation);

        // An operator reducing the configured default below what's already
        // on disk must not have that reduction silently ignored.
        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000")));
    }

    /**
     * The boundary case immediately adjacent to the fail-closed check
     * above: a stored allocation exactly equal to (not merely less than)
     * the configured default must still load successfully -- the check is
     * "greater than", not "greater than or equal to".
     */
    @Test
    void loadSucceedsWhenAnExistingLedgersAllocatedCapitalExactlyEqualsTheConfiguredDefault(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("500000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        AccountLedgerStore.persist(file, ledger);

        AccountLedger reloaded = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("500000"));

        assertEquals(new BigDecimal("500000"), reloaded.allocatedVirtualCapital());
    }

    /**
     * {@link LedgerReservation}'s own compact constructor rejects a
     * non-positive {@code notional} -- exercised directly here; the
     * file-based path is pinned separately by {@link
     * #loadFailsClosedWhenTheLedgerFileHoldsANegativeNotional}.
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

    /**
     * The same invariant proven through a real ledger file on disk (a
     * corrupted or hand-edited file is exactly the scenario this record-
     * level validation exists to catch) -- proves {@link
     * AccountLedgerStore#load} fails closed rather than needing any
     * special-case notional-validation logic of its own. A negative
     * {@code notional} would increase the derived available capital, so
     * this is a real risk-limit-bypass direction, not merely a data-
     * integrity nicety -- see this test's sibling directly above for the
     * full reasoning.
     */
    @Test
    void loadFailsClosedWhenTheLedgerFileHoldsANegativeNotional(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Instant now = Instant.now();
        Files.writeString(
                file,
                "{\"venue\":\"KIS\",\"accountId\":\"acct-1\",\"allocatedVirtualCapital\":100000,"
                        + "\"lastReconciledDailyPnlPercent\":0,\"lastReconciledWeeklyPnlPercent\":0,"
                        + "\"lastReconciledMonthlyPnlPercent\":0,\"reservations\":["
                        + "{\"clientOrderId\":\"" + UUID.randomUUID() + "\",\"symbol\":\"A11609\","
                        + "\"processId\":1,\"hostname\":\"host-a\",\"notional\":-500,"
                        + "\"reservedAt\":\"" + now + "\"}]}");

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000")));
    }

    /**
     * Real Minor finding, real CodeRabbit review of this PR: {@link
     * AccountLedger}'s own compact constructor now rejects a
     * half-populated {@code reconciliationAlarmTrippedAt}/{@code
     * reconciliationAlarmReason} pair -- see that record's own Javadoc
     * for the full reasoning (a pure structural invariant, not an alarm
     * policy choice; either half-populated direction is dangerous, and
     * {@code reconciliationAlarmReason} alone is the more dangerous one,
     * since it would be read as "no alarm tripped" per this record's own
     * {@code null}-means-no-alarm contract -- a real kill-switch-adjacent
     * weakening). Proven here through a real ledger file on disk, the
     * same file-based pattern already established for the duplicate-
     * {@code clientOrderId} and negative-{@code notional} cases above:
     * proves {@link AccountLedgerStore#load} fails closed on a corrupted
     * or hand-edited file holding either half-populated direction.
     */
    @Test
    void loadFailsClosedWhenTheLedgerFileHoldsAHalfPopulatedReconciliationAlarmPair(@TempDir Path tempDir)
            throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Instant now = Instant.now();

        Files.writeString(
                file,
                "{\"venue\":\"KIS\",\"accountId\":\"acct-1\",\"allocatedVirtualCapital\":100000,"
                        + "\"lastReconciledDailyPnlPercent\":0,\"lastReconciledWeeklyPnlPercent\":0,"
                        + "\"lastReconciledMonthlyPnlPercent\":0,"
                        + "\"reconciliationAlarmTrippedAt\":\"" + now + "\","
                        + "\"reservations\":[]}");
        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000")),
                "trippedAt alone (no reason) must fail closed");

        Files.writeString(
                file,
                "{\"venue\":\"KIS\",\"accountId\":\"acct-1\",\"allocatedVirtualCapital\":100000,"
                        + "\"lastReconciledDailyPnlPercent\":0,\"lastReconciledWeeklyPnlPercent\":0,"
                        + "\"lastReconciledMonthlyPnlPercent\":0,"
                        + "\"reconciliationAlarmReason\":\"ledger exposure diverged from real account\","
                        + "\"reservations\":[]}");
        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000")),
                "reason alone (no trippedAt) must fail closed -- the more dangerous direction, since it would"
                        + " otherwise be read as no alarm tripped at all");
    }

    /**
     * Real Trivial finding, real CodeRabbit review of this PR: {@link
     * AccountLedger}'s own compact constructor now rejects two
     * reservations sharing the same {@code clientOrderId} -- a duplicate
     * would double-count that reservation's notional against available
     * capital, or (on release) leave one of the two still recorded,
     * understating real committed exposure.
     */
    @Test
    void twoReservationsWithTheSameClientOrderIdAreRejectedByAccountLedgerItself() {
        UUID sharedId = UUID.randomUUID();
        LedgerReservation first =
                new LedgerReservation(sharedId, "A11609", 1L, "host-a", new BigDecimal("100"), Instant.now());
        LedgerReservation second =
                new LedgerReservation(sharedId, "A50609", 2L, "host-b", new BigDecimal("200"), Instant.now());

        assertThrows(
                IllegalArgumentException.class,
                () -> new AccountLedger(
                        "KIS", "acct-1", new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO,
                        BigDecimal.ZERO, null, null, null, List.of(first, second)));
    }

    /**
     * Same invariant, exercised through a real ledger file on disk (a
     * corrupted or hand-edited file is exactly the scenario this record-
     * level validation exists to catch) -- proves {@code
     * AccountLedgerStore.load} fails closed rather than needing any
     * special-case duplicate-detection logic of its own, the same
     * reasoning already proven for the notional-positivity check above.
     */
    @Test
    void loadFailsClosedWhenTheLedgerFileHoldsTwoReservationsWithTheSameClientOrderId(@TempDir Path tempDir)
            throws IOException {
        Path file = tempDir.resolve("ledger.json");
        String sharedId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        Files.writeString(
                file,
                "{\"venue\":\"KIS\",\"accountId\":\"acct-1\",\"allocatedVirtualCapital\":100000,"
                        + "\"lastReconciledDailyPnlPercent\":0,\"lastReconciledWeeklyPnlPercent\":0,"
                        + "\"lastReconciledMonthlyPnlPercent\":0,\"reservations\":["
                        + "{\"clientOrderId\":\"" + sharedId + "\",\"symbol\":\"A11609\",\"processId\":1,"
                        + "\"hostname\":\"host-a\",\"notional\":100,\"reservedAt\":\"" + now + "\"},"
                        + "{\"clientOrderId\":\"" + sharedId + "\",\"symbol\":\"A50609\",\"processId\":2,"
                        + "\"hostname\":\"host-b\",\"notional\":200,\"reservedAt\":\"" + now + "\"}"
                        + "]}");

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000")));
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
     * Real Trivial finding, a further real CodeRabbit review round on
     * this PR: round 3's own fix (switching the temp-file open options
     * from a naive {@code CREATE_NEW} to {@code CREATE,
     * TRUNCATE_EXISTING, WRITE}, matching {@code Files.writeString}'s own
     * documented defaults) was never exercised by a dedicated test proving
     * its own stated reason for existing -- that a leftover {@code .tmp}
     * file from an earlier interrupted {@code persist()} must still be
     * overwritable on retry, not rejected with {@code
     * FileAlreadyExistsException}. Proven directly here: a stale, garbage
     * {@code .tmp} file is written by hand first, then {@code persist} is
     * called normally and must still succeed, consuming/replacing it.
     */
    @Test
    void persistOverwritesALeftoverTmpFileFromAnEarlierInterruptedAttempt(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Path tmp = tempDir.resolve("ledger.json.tmp");
        Files.writeString(tmp, "stale garbage from an earlier interrupted persist() call");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        AccountLedgerStore.persist(file, ledger);

        assertTrue(Files.exists(file), "persist must succeed despite the leftover .tmp file, not throw");
        assertFalse(Files.exists(tmp), "the stale .tmp file must be consumed/replaced, not left behind");
        AccountLedger reloaded = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("1000"));
        assertEquals(ledger, reloaded);
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

    /**
     * Real Minor finding, real CodeRabbit review of this PR: {@code
     * persist}'s non-atomic fallback is deliberately narrow -- only {@link
     * AtomicMoveNotSupportedException}/{@link FileAlreadyExistsException}
     * (the same class of failure {@code SubmissionMarkerStore}/{@code
     * DailyReportGenerator} already fall back for) trigger the {@code
     * REPLACE_EXISTING} fallback; every other {@link IOException} from
     * {@code mover.move} must propagate as a real failure instead. Nothing
     * previously pinned this boundary with a test -- a future change
     * accidentally widening the fallback's {@code catch} clause to plain
     * {@link IOException} would have made this class perform a non-atomic
     * replace after an arbitrary I/O failure (risking another process's
     * committed reservation being lost) while every existing test still
     * passed.
     */
    @Test
    void anUnrelatedIoFailureDuringTheMovePropagatesInsteadOfFallingBack(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedgerStore.AtomicMover brokenMover = (source, target) -> {
            throw new IOException("test-forced unrelated I/O failure");
        };
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(IllegalStateException.class, () -> AccountLedgerStore.persist(file, ledger, brokenMover));
        assertFalse(Files.exists(file), "the ledger must not be replaced by a non-atomic fallback here");
        // Strengthened per a further real CodeRabbit review round on this
        // PR: the original version of this test stopped at confirming
        // ledgerPath was never created, which does not prove the tmp file
        // this same persist() call created was cleaned up too -- see
        // persistCleansUpItsOwnTmpFileOnFailureSoASubsequentLoadStillBootstrapsFresh
        // below for why a leftover tmp here is a real availability bug on
        // its own, not merely untidy.
        assertFalse(
                Files.exists(tempDir.resolve("ledger.json.tmp")),
                "persist must clean up its own tmp file on failure, not leave it behind");
    }

    /**
     * A tmp file {@code persist} created but then failed to move (e.g.
     * {@code mover.move} throwing something other than {@link
     * AtomicMoveNotSupportedException}/{@link FileAlreadyExistsException})
     * must not be left behind: for a ledger that had never successfully
     * persisted before, a leftover tmp would otherwise make every future
     * {@link AccountLedgerStore#load} call fail closed <b>permanently</b>
     * via its own missing-ledger-plus-leftover-{@code .tmp} check ({@link
     * #loadFailsClosedWhenTheLedgerIsMissingButALeftoverTmpFileExists}) --
     * indistinguishable from a genuinely interrupted {@code persist()},
     * needlessly losing availability for a ledger that had simply never
     * succeeded even once. Proven directly here, not just by the absence
     * of a leftover tmp file: a real failed {@code persist} is
     * immediately followed by a real {@code load} call, which must
     * succeed as an ordinary fresh bootstrap rather than fail closed.
     */
    @Test
    void persistCleansUpItsOwnTmpFileOnFailureSoASubsequentLoadStillBootstrapsFresh(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedgerStore.AtomicMover brokenMover = (source, target) -> {
            throw new IOException("test-forced unrelated I/O failure");
        };
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(IllegalStateException.class, () -> AccountLedgerStore.persist(file, ledger, brokenMover));

        AccountLedger reloaded = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000"));

        assertEquals(
                new BigDecimal("100000"),
                reloaded.allocatedVirtualCapital(),
                "a ledger that never successfully persisted must still bootstrap fresh after a failed persist(),"
                        + " not fail closed forever because of a leftover tmp file");
        assertTrue(reloaded.reservations().isEmpty());
    }

    /**
     * The outer cleanup in {@code persist}'s catch block must never delete
     * {@code tmp} unconditionally on any {@link IOException} -- in
     * particular not one thrown by the non-atomic {@code
     * REPLACE_EXISTING} fallback itself. That fallback is not atomic and
     * can fail after it has already altered (or removed) the real,
     * existing {@code ledgerPath} it was replacing -- in that specific
     * sequence, {@code tmp} is the only remaining copy of valid data, and
     * deleting it turns a safe fail-closed outcome into <b>silent loss of
     * every other process's real, committed reservations</b> the next
     * time {@code load} runs. {@code persist} sets {@code tmp} to {@code
     * null} inside a dedicated catch around the fallback {@code
     * Files.move} call specifically, before rethrowing -- opting that one
     * failure class out of the outer cleanup, while leaving the
     * originally-intended cleanup case ({@code mover.move} itself
     * failing, before any fallback and before {@code ledgerPath} is ever
     * touched) unaffected.
     *
     * <p>Proven here at the level that is actually deterministic and
     * portable to reproduce via public {@code java.nio.file} APIs: a real
     * {@code ledgerPath} that is a non-empty directory reliably makes the
     * fallback {@code Files.move(..., REPLACE_EXISTING)} throw a real
     * {@link java.nio.file.DirectoryNotEmptyException} (confirmed via a
     * standalone probe before writing this test, not assumed) --
     * <b>without</b> altering either the source ({@code tmp} survives
     * intact) or the target first, so this specific setup does not
     * reproduce the harder, timing-dependent "target already removed
     * mid-move" sub-case verbatim. It does exercise the exact same code
     * path (the fallback {@code Files.move} call and this test's own
     * fix's {@code catch} block around it) and prove the same real
     * consequence that actually matters: {@code tmp} is preserved, not
     * deleted, when that call fails -- and a subsequent {@code load} call
     * still fails closed (via its own generic read-failure branch here,
     * since {@code ledgerPath} being a directory is itself unreadable as
     * a file -- a different one of {@code load}'s several fail-closed
     * branches than the missing-ledger-plus-leftover-{@code .tmp} one,
     * but the same overall safety property) rather than silently
     * bootstrapping an empty ledger.
     */
    @Test
    void persistPreservesItsTmpFileWhenTheNonAtomicFallbackMoveItselfFails(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        // A non-empty directory at ledgerPath's own path forces a real,
        // deterministic DirectoryNotEmptyException from the fallback
        // Files.move(..., REPLACE_EXISTING) call -- confirmed via a
        // standalone probe (not assumed) that this leaves both the
        // source and the target directory's own content untouched.
        Files.createDirectory(file);
        Files.writeString(file.resolve("occupies-the-directory.txt"), "not a real ledger, just occupies the path");
        AccountLedgerStore.AtomicMover atomicMoveNotSupportedMover = (source, target) -> {
            throw new AtomicMoveNotSupportedException(source.toString(), target.toString(), "test-forced fallback");
        };
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.persist(file, ledger, atomicMoveNotSupportedMover));

        assertTrue(
                Files.exists(tempDir.resolve("ledger.json.tmp")),
                "tmp must survive a fallback-move failure -- it may be the only remaining copy of valid data");
        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("100000")),
                "load must still fail closed after a fallback-move failure, never silently bootstrap empty");
    }

    /**
     * Real Major finding, a further real CodeRabbit review round on this
     * PR -- catching a deeper variant of the same tmp-cleanup-scope gap
     * round 19's own fix (directly above) closed only partially: that fix
     * protects the fallback-move-failure case, but a genuine, pre-existing
     * {@code .tmp} left by an earlier, different {@code persist()} attempt
     * (crashed, or itself failed with an {@code IOException}) was still
     * unconditionally eligible for this call's own cleanup if <i>this</i>
     * call's {@link java.nio.channels.FileChannel#open}/write/force step
     * then failed -- exactly the evidence {@link AccountLedgerStore#load}'s
     * own missing-ledger-plus-leftover-{@code .tmp} fail-closed check
     * depends on, deleted by a completely unrelated, later persist()
     * attempt's own failure. Fixed by checking whether the tmp path
     * already existed <i>before</i> this call ever touches it, and only
     * enabling cleanup when this call is genuinely the one creating it.
     *
     * <p>Proven here at the level that is actually deterministic and
     * portable to reproduce via public {@code java.nio.file} APIs,
     * disclosed honestly rather than overclaimed: an <b>empty directory</b>
     * standing in for a real pre-existing {@code .tmp} regular file
     * reliably makes {@code FileChannel.open(..., CREATE,
     * TRUNCATE_EXISTING, WRITE)} throw a real {@link
     * java.nio.file.FileSystemException}
     * ("Is a directory") -- confirmed via a standalone probe before
     * writing this test, not assumed -- while leaving the directory
     * itself both existing and, critically, still <i>deletable</i>
     * (unlike a non-empty directory, which {@link Files#deleteIfExists}
     * would refuse regardless of this fix, masking the real property
     * being tested here). That combination is exactly what makes this a
     * real test of the new ownership-tracking logic specifically, not
     * merely a re-confirmation that some deletion attempt happens to fail:
     * the old code would have called {@code Files.deleteIfExists} on this
     * exact path and it would have <i>succeeded</i>, silently destroying
     * the "leftover" -- this test proves the new code never attempts the
     * delete at all.
     */
    @Test
    void persistNeverCleansUpATmpPathThatAlreadyExistedBeforeThisCall(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Path candidateTmp = tempDir.resolve("ledger.json.tmp");
        Files.createDirectory(candidateTmp);
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(IllegalStateException.class, () -> AccountLedgerStore.persist(file, ledger));

        assertTrue(
                Files.exists(candidateTmp),
                "a tmp path that already existed before this persist() call must never be cleaned up by it,"
                        + " regardless of which step of this call then fails");
    }

    @Test
    void persistTreatsAnUndeterminableTmpPathAsPreexistingRatherThanDeletingIt(@TempDir Path tempDir)
            throws IOException {
        // Round-25/26's own tmpPreexisted check used Files.exists(candidateTmp),
        // which swallows an I/O/permission error into a plain false --
        // indistinguishable from "genuinely absent." That misreads an
        // undetermined existence as "this call is creating a new file,"
        // making it eligible for the failure-cleanup path below even
        // though it may really be another process's own leftover. A
        // self-referential symlink is a real, reproducible way to force
        // that exact undetermined state: Files.readAttributes(...) throws
        // a genuine FileSystemException ("too many levels of symbolic
        // links"), not a NoSuchFileException -- the entry is not absent,
        // its status just can't be positively resolved. FileChannel.open
        // on the same path fails identically, so persist() reaches its
        // outer cleanup catch with this as the active failure. Both
        // behaviors were confirmed empirically against this project's own
        // JDK/filesystem in a standalone probe before writing this test,
        // matching this file's own established discipline for OS-specific
        // behavior.
        Path file = tempDir.resolve("ledger.json");
        Path candidateTmp = tempDir.resolve("ledger.json.tmp");
        Files.createSymbolicLink(candidateTmp, candidateTmp);
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(IllegalStateException.class, () -> AccountLedgerStore.persist(file, ledger));

        assertTrue(
                Files.exists(candidateTmp, LinkOption.NOFOLLOW_LINKS),
                "a tmp path whose existence this persist() call could not positively determine must never be"
                        + " cleaned up by it -- treating a determination failure as \"pre-existing\" is the"
                        + " fail-safe direction, since deleting a file this call may not actually own is strictly"
                        + " worse than leaving it");
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
