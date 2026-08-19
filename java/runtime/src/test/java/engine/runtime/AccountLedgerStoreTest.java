package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledOnOs;
import org.junit.jupiter.api.condition.OS;
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

        AccountLedger ledger = loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000"));

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

        persistWithLock(file, original);
        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000000"));

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

        persistWithLock(file, withAlarm);
        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000000"));

        assertEquals(withAlarm.reconciliationAlarmTrippedAt(), reloaded.reconciliationAlarmTrippedAt());
        assertEquals(withAlarm.reconciliationAlarmReason(), reloaded.reconciliationAlarmReason());
    }

    @Test
    void loadReflectsTheLatestPersistedStateNotAStaleCachedOne(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        // defaultAllocatedCapital held fixed at 2000 (>= both persisted
        // values below) throughout this test -- deliberately, so the
        // stored-exceeds-configured-default fail-closed check never trips
        // here; this test's own subject is the "no caching" property, not
        // that check. 1000 -> 500 (a decrease, not an increase --
        // persist()'s own allocatedVirtualCapital-increase fail-closed
        // check, added separately, rejects the other direction; see
        // persistFailsClosedWhenTheNewAllocatedVirtualCapitalExceedsTheExistingOne
        // below) still proves a real state change is genuinely observed.
        BigDecimal defaultAllocatedCapital = new BigDecimal("2000");
        AccountLedger first = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, first);
        AccountLedger loadedFirst = loadWithLock(file, "KIS", "acct-1", defaultAllocatedCapital);
        assertEquals(new BigDecimal("1000"), loadedFirst.allocatedVirtualCapital());

        AccountLedger second = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("500"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, second);
        AccountLedger loadedSecond = loadWithLock(file, "KIS", "acct-1", defaultAllocatedCapital);

        // Static, stateless methods -- no instance to have cached the
        // first value; this proves it, not merely asserts it by
        // construction.
        assertEquals(new BigDecimal("500"), loadedSecond.allocatedVirtualCapital());
    }

    @Test
    void aCorruptFileFailsClosedRatherThanReturningAFreshLedger(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Files.writeString(file, "this is not valid JSON {{{");

        assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")));
    }

    /**
     * The JSON literal {@code null} is valid JSON, so {@code
     * MAPPER.readValue(raw, AccountLedger.class)} returns a plain Java
     * {@code null} without throwing -- left unchecked, the very next
     * access ({@code ledger.venue()}) would throw a raw {@code
     * NullPointerException} instead of this method's own intended
     * {@code IllegalStateException} fail-closed contract.
     */
    @Test
    void aFileContainingTheJsonLiteralNullFailsClosed(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Files.writeString(file, "null");

        assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")));
    }

    /**
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
        ObjectMapper mapper = fixtureMapper();
        Files.writeString(file, mapper.writeValueAsString(ledger) + "null");

        assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("1000")));
    }

    @Test
    void aFileWithTwoConcatenatedValidLedgerObjectsFailsClosed(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        ObjectMapper mapper = fixtureMapper();
        String single = mapper.writeValueAsString(ledger);
        Files.writeString(file, single + single);

        assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("1000")));
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
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")));
    }

    /**
     * {@code persist}'s non-atomic fallback path is not a single
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
        ObjectMapper mapper = fixtureMapper();
        Files.writeString(tmp, mapper.writeValueAsString(orphaned));
        // ledgerPath itself deliberately never created -- simulates a
        // crash after the old file was replaced but before (or during) the
        // new content becoming durable at ledgerPath in the non-atomic
        // REPLACE_EXISTING fallback.

        assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")));
    }

    @Test
    void loadStillBootstrapsFreshWhenTheLedgerIsMissingAndNoTmpFileExists(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        // No .tmp sibling at all -- the ordinary, expected "never
        // persisted yet" case must still bootstrap fresh, not fail
        // closed just because a file happens to be missing.

        AccountLedger ledger = loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000"));

        assertEquals(new BigDecimal("100000"), ledger.allocatedVirtualCapital());
    }

    /**
     * {@code load}'s missing-ledger branch actually distinguishes three
     * outcomes, not two: genuinely absent {@code .tmp} (fresh bootstrap,
     * proven above), a confirmed-present {@code .tmp} (fail closed, proven
     * above), and -- proven here, the one previously untested of the
     * three -- {@code .tmp}'s own existence cannot be positively
     * determined at all (also fails closed, via the same {@code
     * tmpCheckFailure} branch). Without this test, someone could
     * revert that branch's {@code catch (IOException tmpCheckFailure)} to
     * a silent {@code return freshLedger(...)} and every existing test
     * would still pass, even though an undetermined state would then
     * silently bootstrap an empty ledger, discarding another process's
     * real, committed reservations. Reuses this file's own established
     * self-referential-symlink technique (see {@link
     * #persistTreatsAnUndeterminableTmpPathAsPreexistingRatherThanDeletingIt})
     * to reproduce a genuine {@code FileSystemException} from {@code
     * Files.readAttributes} rather than the absent-file {@code
     * NoSuchFileException} the other two branches turn on.
     */
    @Test
    @EnabledOnOs({OS.LINUX, OS.MAC})
    void loadFailsClosedWhenTheTmpFilesExistenceCannotBeDetermined(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Path tmp = tempDir.resolve("ledger.json.tmp");
        Files.createSymbolicLink(tmp, tmp);

        assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")),
                "an undetermined .tmp existence state must fail closed, not silently bootstrap a fresh ledger");
    }

    /**
     * Nothing upstream of {@code load} validates that a loaded ledger
     * file's own recorded identity actually matches the {@code (venue,
     * accountId)} the caller requested -- a future path-resolution bug
     * (Task C) or file mix-up could otherwise silently use one account's
     * real, currently-committed exposure to gate a different account's
     * orders.
     */
    @Test
    void loadFailsClosedWhenTheLoadedLedgersVenueDoesNotMatchTheRequestedOne(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledgerForKis = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, ledgerForKis);

        assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "BINGX", "acct-1", new BigDecimal("1000")));
    }

    @Test
    void loadFailsClosedWhenTheLoadedLedgersAccountIdDoesNotMatchTheRequestedOne(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledgerForAcct1 = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, ledgerForAcct1);

        assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-2", new BigDecimal("1000")));
    }

    @Test
    void loadSucceedsWhenTheLoadedLedgersIdentityMatchesTheRequestedOneExactly(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, ledger);

        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("1000"));

        assertEquals(ledger, reloaded);
    }

    /**
     * {@code defaultAllocatedCapital} must be strictly positive -- a zero
     * or negative value is meaningless for a risk budget and would
     * otherwise flow straight into a freshly-bootstrapped ledger.
     */
    @Test
    void loadRejectsAZeroOrNegativeDefaultAllocatedCapital(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");

        assertThrows(
                IllegalArgumentException.class,
                () -> loadWithLock(file, "KIS", "acct-1", BigDecimal.ZERO));
        assertThrows(
                IllegalArgumentException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("-1")));
    }

    /**
     * Grounded directly in CLAUDE.md's own "never weaken risk limits...
     * without explicit human approval" rule: {@code
     * defaultAllocatedCapital} is only ever consulted when bootstrapping a
     * brand new ledger -- for an existing one, returning the stored
     * {@code allocatedVirtualCapital} as-is with no comparison against the
     * currently-configured default would silently defeat an operator's
     * own attempt to reduce a risk budget, since lowering {@code
     * defaultAllocatedCapital} in configuration would have no effect for
     * as long as a larger, previously-persisted value remained on disk.
     */
    @Test
    void loadFailsClosedWhenAnExistingLedgersAllocatedCapitalExceedsTheConfiguredDefault(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledgerWithALargeStoredAllocation = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("500000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, ledgerWithALargeStoredAllocation);

        // An operator reducing the configured default below what's already
        // on disk must not have that reduction silently ignored.
        assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")));
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
        persistWithLock(file, ledger);

        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("500000"));

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

        // Checks the exception's own cause message, not just its type --
        // this test's own hand-written JSON field names (e.g. "notional")
        // aren't compiler-checked against LedgerReservation's real record
        // component names, so a typo or a future rename could make
        // Jackson reject the file as an unknown property instead --
        // AccountLedgerStore.load still throws IllegalStateException
        // either way, so assertThrows alone would keep passing for the
        // wrong reason, silently no longer proving this test's own real
        // subject (the record's own notional-positivity invariant).
        // Confirmed empirically (a standalone probe, not assumed) that
        // Jackson's own ValueInstantiationException wrapper embeds the
        // record's real validation message directly in its own message.
        IllegalStateException thrown = assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")));
        assertTrue(
                thrown.getCause() != null && thrown.getCause().getMessage() != null
                        && thrown.getCause().getMessage().contains("notional must be positive"),
                "expected LedgerReservation's own notional-positivity check to be the real cause; was: "
                        + thrown.getCause());
    }

    /**
     * {@link AccountLedger}'s own compact constructor rejects a
     * non-positive {@code allocatedVirtualCapital} -- exercised directly
     * here; the file-based path is pinned separately by {@link
     * #loadFailsClosedWhenTheLedgerFileHoldsANegativeAllocatedVirtualCapital}.
     * Previously only {@code load}'s two separate checks constrained this
     * field (that {@code defaultAllocatedCapital} itself is positive, and
     * that a stored value doesn't <i>exceed</i> the configured default) --
     * neither actually rejects a stored zero or negative value, since
     * either is always less than a positive default and so passes both
     * checks. This record's own Javadoc already declares it "the single
     * structural enforcement point" for exactly this class of invariant
     * (the same principle {@link LedgerReservation#notional} and the
     * duplicate-{@code clientOrderId}/reconciliation-alarm-pair checks
     * above already apply) -- a corrupted or hand-edited ledger file can
     * produce exactly this state, so the fix belongs on the record itself,
     * not as a further special case inside {@code load}.
     */
    @Test
    void anAllocatedVirtualCapitalOfZeroOrNegativeIsRejectedByAccountLedgerItself() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new AccountLedger(
                        "KIS", "acct-1", BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                        null, null, null, List.of()));
        assertThrows(
                IllegalArgumentException.class,
                () -> new AccountLedger(
                        "KIS", "acct-1", new BigDecimal("-1"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                        null, null, null, List.of()));
    }

    /**
     * The same invariant proven through a real ledger file on disk (a
     * corrupted or hand-edited file is exactly the scenario this record-
     * level validation exists to catch) -- proves {@link
     * AccountLedgerStore#load} fails closed rather than needing any
     * special-case allocated-capital-sign validation logic of its own,
     * the same reasoning already applied to the negative-{@code notional}
     * case above.
     */
    @Test
    void loadFailsClosedWhenTheLedgerFileHoldsANegativeAllocatedVirtualCapital(@TempDir Path tempDir)
            throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Files.writeString(
                file,
                "{\"venue\":\"KIS\",\"accountId\":\"acct-1\",\"allocatedVirtualCapital\":-500,"
                        + "\"lastReconciledDailyPnlPercent\":0,\"lastReconciledWeeklyPnlPercent\":0,"
                        + "\"lastReconciledMonthlyPnlPercent\":0,\"reservations\":[]}");

        // See loadFailsClosedWhenTheLedgerFileHoldsANegativeNotional's own
        // comment above for why this checks the cause message, not just
        // the exception type.
        IllegalStateException thrown = assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")));
        assertTrue(
                thrown.getCause() != null && thrown.getCause().getMessage() != null
                        && thrown.getCause().getMessage().contains("allocatedVirtualCapital must be positive"),
                "expected AccountLedger's own allocatedVirtualCapital-positivity check to be the real cause; was: "
                        + thrown.getCause());
    }

    /**
     * {@link AccountLedger}'s own compact constructor rejects a
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
        // See loadFailsClosedWhenTheLedgerFileHoldsANegativeNotional's own
        // comment above for why these check the cause message, not just
        // the exception type.
        IllegalStateException trippedAtOnly = assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")),
                "trippedAt alone (no reason) must fail closed");
        assertTrue(
                trippedAtOnly.getCause() != null && trippedAtOnly.getCause().getMessage() != null
                        && trippedAtOnly.getCause().getMessage().contains("must both be null or both be non-null"),
                "expected AccountLedger's own half-populated-alarm-pair check to be the real cause; was: "
                        + trippedAtOnly.getCause());

        Files.writeString(
                file,
                "{\"venue\":\"KIS\",\"accountId\":\"acct-1\",\"allocatedVirtualCapital\":100000,"
                        + "\"lastReconciledDailyPnlPercent\":0,\"lastReconciledWeeklyPnlPercent\":0,"
                        + "\"lastReconciledMonthlyPnlPercent\":0,"
                        + "\"reconciliationAlarmReason\":\"ledger exposure diverged from real account\","
                        + "\"reservations\":[]}");
        IllegalStateException reasonOnly = assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")),
                "reason alone (no trippedAt) must fail closed -- the more dangerous direction, since it would"
                        + " otherwise be read as no alarm tripped at all");
        assertTrue(
                reasonOnly.getCause() != null && reasonOnly.getCause().getMessage() != null
                        && reasonOnly.getCause().getMessage().contains("must both be null or both be non-null"),
                "expected AccountLedger's own half-populated-alarm-pair check to be the real cause; was: "
                        + reasonOnly.getCause());
    }

    /**
     * {@link AccountLedger}'s own compact constructor rejects two
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

        // See loadFailsClosedWhenTheLedgerFileHoldsANegativeNotional's own
        // comment above for why this checks the cause message, not just
        // the exception type.
        IllegalStateException thrown = assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")));
        assertTrue(
                thrown.getCause() != null && thrown.getCause().getMessage() != null
                        && thrown.getCause().getMessage().contains("duplicate clientOrderId"),
                "expected AccountLedger's own duplicate-clientOrderId check to be the real cause; was: "
                        + thrown.getCause());
    }

    @Test
    void persistRefusesToOverwriteAnExistingLedgerForADifferentAccount(@TempDir Path tempDir) {
        // persist() takes no separate "expected identity" parameter the
        // way load() does -- the only signal available to it is the
        // existing file's own previously-persisted content, if any. This
        // protects against a caller-side bug (Task C, not yet wired) that
        // resolves the wrong AccountLedger object against an existing path
        // already holding a DIFFERENT account's real, committed
        // reservations -- the same class of mix-up load()'s own identity
        // check exists to catch on the read side, closed here on the
        // write side too, before Task C's first real caller exists to make
        // the mistake.
        Path file = tempDir.resolve("ledger.json");
        AccountLedger existing = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, existing);
        AccountLedger differentAccount = new AccountLedger(
                "KIS", "acct-2", new BigDecimal("99"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(IllegalStateException.class, () -> persistWithLock(file, differentAccount));

        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("42"));
        assertEquals(existing, reloaded, "the existing ledger must be untouched by the rejected persist() call");
    }

    /**
     * Grounded directly in CLAUDE.md's own "never weaken risk limits...
     * without explicit human approval" rule -- the same rule {@link
     * AccountLedgerStore#load}'s own stored-vs-configured-default
     * comparison already applies on the read side (see {@link
     * #loadFailsClosedWhenAnExistingLedgersAllocatedCapitalExceedsTheConfiguredDefault}
     * above). Without this check, persist() could be used to silently
     * raise the persisted risk budget simply by writing a larger {@code
     * allocatedVirtualCapital} -- no approval step, no audit trail beyond
     * an ordinary write. This is the write-side analogue, and the existing
     * file must be left completely untouched by the rejected call, the
     * same guarantee {@link
     * #persistRefusesToOverwriteAnExistingLedgerForADifferentAccount}
     * proves for the identity-mismatch case above.
     */
    @Test
    void persistFailsClosedWhenTheNewAllocatedVirtualCapitalExceedsTheExistingOne(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger existing = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, existing);
        AccountLedger increased = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1001"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        IllegalStateException thrown =
                assertThrows(IllegalStateException.class, () -> persistWithLock(file, increased));
        assertTrue(
                thrown.getMessage().contains("increased allocatedVirtualCapital"),
                "expected the allocatedVirtualCapital-increase fail-closed check to be the real cause; was: "
                        + thrown.getMessage());

        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("1000"));
        assertEquals(existing, reloaded, "the existing ledger must be untouched by the rejected persist() call");
    }

    /**
     * The boundary case immediately adjacent to the fail-closed check
     * above: a new value exactly equal to (not merely less than) the
     * existing one must still be accepted -- the check is "greater than",
     * not "greater than or equal to", mirroring {@link
     * #loadSucceedsWhenAnExistingLedgersAllocatedCapitalExactlyEqualsTheConfiguredDefault}'s
     * own boundary on the read side.
     */
    @Test
    void persistSucceedsWhenTheNewAllocatedVirtualCapitalExactlyEqualsTheExistingOne(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger existing = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, existing);
        AccountLedger unchanged = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), new BigDecimal("1"), BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        persistWithLock(file, unchanged);

        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("1000"));
        assertEquals(unchanged, reloaded);
    }

    /** A genuine reduction must also succeed -- only an increase is rejected. */
    @Test
    void persistSucceedsWhenTheNewAllocatedVirtualCapitalIsLowerThanTheExistingOne(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger existing = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, existing);
        AccountLedger reduced = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("999"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        persistWithLock(file, reduced);

        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("1000"));
        assertEquals(reduced, reloaded);
    }

    @Test
    void persistRefusesToOverwriteAnExistingFileWithUnparseableContent(@TempDir Path tempDir) throws IOException {
        // Same protective intent as
        // persistRefusesToOverwriteAnExistingLedgerForADifferentAccount --
        // if this call cannot positively confirm the existing content is a
        // matching-identity ledger (here: cannot even parse it as a
        // ledger at all), it must not silently overwrite it. A directory
        // occupying ledgerPath is a separate, already-covered case (see
        // persistPreservesItsTmpFileWhenTheNonAtomicFallbackMoveItselfFails
        // -- that scenario is deliberately NOT rejected by this same
        // identity check, since a directory was never a valid ledger to
        // begin with and persist()'s own existing write/move machinery
        // already fails loudly on it).
        Path file = tempDir.resolve("ledger.json");
        Files.writeString(file, "not valid json at all");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(IllegalStateException.class, () -> persistWithLock(file, ledger));

        assertEquals(
                "not valid json at all",
                Files.readString(file),
                "the existing unparseable file must be preserved, not overwritten");
    }

    /**
     * The write-side counterpart to {@link #aFileContainingTheJsonLiteralNullFailsClosed}:
     * the JSON literal {@code null} parses without throwing, so {@code
     * verifyIdentityConsistency} must reject it explicitly rather than
     * dereference a Java {@code null} and leak a raw {@link
     * NullPointerException} past this class's own {@link
     * IllegalStateException} fail-closed contract.
     */
    @Test
    void persistRefusesToOverwriteAnExistingFileHoldingTheJsonLiteralNull(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Files.writeString(file, "null");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(IllegalStateException.class, () -> persistWithLock(file, ledger));

        assertEquals("null", Files.readString(file), "the existing file must be preserved, not overwritten");
        assertFalse(
                Files.exists(tempDir.resolve("ledger.json.tmp")),
                "persist must fail closed before ever creating its tmp file");
    }

    @Test
    @EnabledOnOs({OS.LINUX, OS.MAC})
    void persistFailsClosedBeforeWritingWhenTheLedgerPathsExistenceCannotBeDetermined(@TempDir Path tempDir)
            throws IOException {
        // verifyIdentityConsistency's own determination-failure branch,
        // exercised at ledgerPath itself rather than the .tmp path -- the
        // write-side identity check's own previously-untested branch. A
        // self-referential symlink at ledgerPath forces
        // the same real FileSystemException (not NoSuchFileException)
        // this file's other symlink-based tests already rely on, so
        // persist() cannot positively confirm ledgerPath is either absent
        // or a matching-identity ledger -- it must fail closed, and must
        // do so BEFORE ever creating the candidate tmp file (verified
        // directly here, not just that the call throws).
        Path file = tempDir.resolve("ledger.json");
        Files.createSymbolicLink(file, file);
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(IllegalStateException.class, () -> persistWithLock(file, ledger));

        assertFalse(
                Files.exists(tempDir.resolve("ledger.json.tmp"), LinkOption.NOFOLLOW_LINKS),
                "persist() must fail closed before ever creating a tmp file when it cannot determine ledgerPath's"
                        + " own existing state");
    }

    @Test
    void persistCreatesParentDirectoriesIfNeeded(@TempDir Path tempDir) {
        Path file = tempDir.resolve("nested").resolve("dir").resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        persistWithLock(file, ledger);

        assertTrue(Files.exists(file));
    }

    @Test
    void writesAreAtomicNoTempFileLeftBehindAfterASuccessfulPersist(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        persistWithLock(file, ledger);

        assertTrue(Files.exists(file));
        assertFalse(Files.exists(tempDir.resolve("ledger.json.tmp")), "the temp file must be renamed away, not left behind");
    }

    /**
     * {@code ledgerPath} missing while a leftover {@code .tmp} sibling
     * exists is exactly the state {@link AccountLedgerStore#load}'s own
     * missing-ledger-plus-leftover-{@code .tmp} check treats as evidence
     * of an interrupted {@code persist()} requiring human resolution --
     * the {@code .tmp} may be the only surviving copy of another
     * process's real, committed reservations. {@code persist} must honor
     * that same evidence rather than silently consume it via its own
     * ordinary {@code TRUNCATE_EXISTING} write path: a real, reachable
     * sequence given this class's own documented standalone-{@code
     * persist}-call use case (not only a {@code load} + mutate + {@code
     * persist} cycle) -- a host crash mid non-atomic-fallback-move can
     * leave {@code ledgerPath} genuinely missing with its {@code .tmp}
     * source still lingering, and a caller invoking {@code persist} alone
     * after restart (without ever calling {@code load} first) would
     * otherwise reach this exact state.
     */
    @Test
    void persistFailsClosedWhenLedgerPathIsMissingButALeftoverTmpFileExists(@TempDir Path tempDir)
            throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Path tmp = tempDir.resolve("ledger.json.tmp");
        Files.writeString(tmp, "a real ledger another process may still need -- not this call's to touch");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(IllegalStateException.class, () -> persistWithLock(file, ledger));

        assertEquals(
                "a real ledger another process may still need -- not this call's to touch",
                Files.readString(tmp),
                "persist() must never overwrite a leftover .tmp when ledgerPath is also missing");
        assertFalse(Files.exists(file), "persist() must not create ledgerPath either when refusing to proceed");
    }

    /**
     * The different-outcome sibling of {@link
     * #persistFailsClosedWhenLedgerPathIsMissingButALeftoverTmpFileExists}
     * immediately above: when {@code ledgerPath}'s own existence cannot be
     * determined (here, a self-referential symlink) while a leftover
     * {@code .tmp} exists, {@code persist()} fails closed via {@code
     * verifyIdentityConsistency}'s own earlier {@code Files.readAttributes}
     * check, not the {@code tmpPreexisted} branch's own {@code
     * NoSuchFileException}-only handling. Linux/macOS only -- reliable
     * self-referential symlink creation is not reproducible on Windows.
     */
    @Test
    @EnabledOnOs({OS.LINUX, OS.MAC})
    void persistFailsClosedWhenLedgerPathsExistenceCannotBeDeterminedAndATmpFileAlsoExists(@TempDir Path tempDir)
            throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Path tmp = tempDir.resolve("ledger.json.tmp");
        Files.writeString(tmp, "a real ledger another process may still need -- not this call's to touch");
        Files.createSymbolicLink(file, file);
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("1000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        IllegalStateException thrown =
                assertThrows(IllegalStateException.class, () -> persistWithLock(file, ledger));

        // verifyIdentityConsistency's own, earlier determination-failure
        // check on this same ledgerPath fires first -- see this test's
        // own Javadoc for why the tmpPreexisted branch's own analogous
        // check is never actually reached here.
        assertTrue(
                thrown.getMessage().contains("its existing state could not be determined"),
                "expected verifyIdentityConsistency's own determination-failure message, since it runs before"
                        + " and independently of the tmpPreexisted branch this test originally targeted; was: "
                        + thrown.getMessage());
        assertEquals(
                "a real ledger another process may still need -- not this call's to touch",
                Files.readString(tmp),
                "the leftover .tmp must still be untouched regardless of which fail-closed check fired");
    }

    /**
     * A leftover {@code .tmp} file from an earlier interrupted {@code
     * persist()} must still be overwritable on retry, not rejected with
     * {@link FileAlreadyExistsException} -- proven directly here: a
     * stale, garbage {@code .tmp} file is written by hand first, then
     * {@code persist} is called normally and must still succeed,
     * consuming/replacing it. {@code ledgerPath} itself is seeded with a
     * prior, valid ledger first -- the ordinary retry scenario this test
     * covers requires {@code ledgerPath} to already exist (see {@link
     * #persistFailsClosedWhenLedgerPathIsMissingButALeftoverTmpFileExists}
     * immediately above for the different, fail-closed scenario when it
     * does not).
     */
    @Test
    void persistOverwritesALeftoverTmpFileFromAnEarlierInterruptedAttempt(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Path tmp = tempDir.resolve("ledger.json.tmp");
        AccountLedger priorLedger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("500"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(file, priorLedger);
        Files.writeString(tmp, "stale garbage from an earlier interrupted persist() call");
        // 500 -> 200 (a decrease, not an increase) -- persist()'s own
        // allocatedVirtualCapital-increase fail-closed check, added
        // separately, rejects the other direction; see
        // persistFailsClosedWhenTheNewAllocatedVirtualCapitalExceedsTheExistingOne
        // below. This test's own subject is the leftover-.tmp-consumption
        // behavior, not that check, and a decrease still proves the
        // reloaded content genuinely reflects this call's own write.
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("200"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        persistWithLock(file, ledger);

        assertTrue(Files.exists(file), "persist must succeed despite the leftover .tmp file, not throw");
        assertFalse(Files.exists(tmp), "the stale .tmp file must be consumed/replaced, not left behind");
        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("500"));
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

        persistWithLock(file, ledger, flakyMover);

        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("42"));
        assertEquals(new BigDecimal("42"), reloaded.allocatedVirtualCapital());
    }

    /**
     * {@code persist}'s non-atomic fallback is deliberately narrow -- only {@link
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

        assertThrows(IllegalStateException.class, () -> persistWithLock(file, ledger, brokenMover));
        assertFalse(Files.exists(file), "the ledger must not be replaced by a non-atomic fallback here");
        // Confirming ledgerPath was never created does not by itself prove
        // the tmp file this same persist() call created was cleaned up too
        // -- see persistCleansUpItsOwnTmpFileOnFailureSoASubsequentLoadStillBootstrapsFresh
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

        assertThrows(IllegalStateException.class, () -> persistWithLock(file, ledger, brokenMover));

        AccountLedger reloaded = loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000"));

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
                () -> persistWithLock(file, ledger, atomicMoveNotSupportedMover));

        assertTrue(
                Files.exists(tempDir.resolve("ledger.json.tmp")),
                "tmp must survive a fallback-move failure -- it may be the only remaining copy of valid data");
        assertThrows(
                IllegalStateException.class,
                () -> loadWithLock(file, "KIS", "acct-1", new BigDecimal("100000")),
                "load must still fail closed after a fallback-move failure, never silently bootstrap empty");
    }

    /**
     * A genuine, pre-existing {@code .tmp} left by an earlier, different
     * {@code persist()} attempt (crashed, or itself failed with an {@code
     * IOException}) -- exactly the evidence {@link AccountLedgerStore#load}'s
     * own missing-ledger-plus-leftover-{@code .tmp} fail-closed check
     * depends on -- must never be cleaned up by a later, unrelated {@code
     * persist()} call's own failure, regardless of which step of that
     * later call fails.
     *
     * <p>Proven here at the level that is actually deterministic and
     * portable to reproduce via public {@code java.nio.file} APIs: an
     * <b>empty directory</b> standing in for a real pre-existing {@code
     * .tmp} regular file reliably makes {@code FileChannel.open(...,
     * CREATE, TRUNCATE_EXISTING, WRITE)} throw a real {@link
     * java.nio.file.FileSystemException} ("Is a directory") -- confirmed
     * via a standalone probe before writing this test, not assumed --
     * while leaving the directory itself both existing and, critically,
     * still <i>deletable</i> (unlike a non-empty directory, which {@link
     * Files#deleteIfExists} would refuse regardless of this invariant,
     * masking the real property being tested here). That combination is
     * exactly what makes this a real test of the ownership-tracking logic
     * specifically, not merely a re-confirmation that some deletion
     * attempt happens to fail: code that unconditionally called {@code
     * Files.deleteIfExists} on this exact path would succeed, silently
     * destroying the "leftover" -- this test proves that never happens.
     */
    @Test
    void persistNeverCleansUpATmpPathThatAlreadyExistedBeforeThisCall(@TempDir Path tempDir) throws IOException {
        Path file = tempDir.resolve("ledger.json");
        Path candidateTmp = tempDir.resolve("ledger.json.tmp");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        // ledgerPath is seeded with a real, matching-identity ledger first
        // so persist()'s own missing-ledger-plus-leftover-.tmp fail-closed
        // check (the tmpPreexisted branch) does not fire before
        // FileChannel.open -- this keeps the test on the real
        // ownership-tracking/outer-catch-cleanup path it targets.
        persistWithLock(file, ledger);
        Files.createDirectory(candidateTmp);

        IllegalStateException thrown =
                assertThrows(IllegalStateException.class, () -> persistWithLock(file, ledger));

        assertTrue(
                thrown.getMessage().contains("failed to persist account ledger file"),
                "expected the FileChannel.open/outer-catch failure message, not a different fail-closed check's"
                        + " own message; was: " + thrown.getMessage());
        assertTrue(
                Files.exists(candidateTmp),
                "a tmp path that already existed before this persist() call must never be cleaned up by it,"
                        + " regardless of which step of this call then fails");
    }

    @Test
    @EnabledOnOs({OS.LINUX, OS.MAC})
    // Files.createSymbolicLink can fail with an AccessDeniedException on
    // Windows without an elevated process or Developer Mode enabled (an
    // OS-level privilege requirement, not something this test can control)
    // -- this project's own CI runs on ubuntu-latest (confirmed via
    // .github/workflows), so this restriction changes nothing there; it
    // only prevents a spurious failure for a contributor running this
    // suite natively on Windows.
    // persistNeverCleansUpATmpPathThatAlreadyExistedBeforeThisCall above
    // deliberately stays unrestricted -- its own directory-based fixture
    // relies on no POSIX-only behavior.
    void persistTreatsAnUndeterminableTmpPathAsPreexistingRatherThanDeletingIt(@TempDir Path tempDir)
            throws IOException {
        // persist()'s own tmpPreexisted check previously used
        // Files.exists(candidateTmp), which swallows an I/O/permission
        // error into a plain false --
        // indistinguishable from "genuinely absent." That misreads an
        // undetermined existence as "this call is creating a new file,"
        // making it eligible for the failure-cleanup path below even
        // though it may really be another process's own leftover. A
        // self-referential symlink is a real, reproducible way to force
        // that exact undetermined state: Files.readAttributes(...) throws
        // a genuine FileSystemException ("too many levels of symbolic
        // links"), not a NoSuchFileException -- the entry is not absent,
        // its status just can't be positively resolved. Both behaviors
        // were confirmed empirically against this project's own
        // JDK/filesystem in a standalone probe before writing this test,
        // matching this file's own established discipline for OS-specific
        // behavior.
        Path file = tempDir.resolve("ledger.json");
        Path candidateTmp = tempDir.resolve("ledger.json.tmp");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        // ledgerPath is seeded with a real, matching-identity ledger first
        // so persist()'s own missing-ledger-plus-leftover-.tmp fail-closed
        // check does not fire before FileChannel.open reaches candidateTmp's
        // own determination-failure state.
        persistWithLock(file, ledger);
        Files.createSymbolicLink(candidateTmp, candidateTmp);

        IllegalStateException thrown =
                assertThrows(IllegalStateException.class, () -> persistWithLock(file, ledger));

        assertTrue(
                thrown.getMessage().contains("failed to persist account ledger file"),
                "expected the FileChannel.open/outer-catch failure message, not a different fail-closed check's"
                        + " own message; was: " + thrown.getMessage());
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
        // real production default (the 3-arg persist(Path, AccountLedger,
        // AccountLedgerLock) overload, which delegates to
        // defaultAtomicMove) directly, rather than only ever going
        // through the AtomicMover seam.
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        persistWithLock(file, ledger);

        assertTrue(Files.exists(file));
        assertFalse(Files.exists(tempDir.resolve("ledger.json.tmp")));
    }

    /**
     * {@link AccountLedgerStore#load}/{@link AccountLedgerStore#persist}
     * reject a {@code null} {@code lock} argument -- part of this class's
     * documented caller contract being structurally enforced, not merely
     * documented. Exercises the real methods directly (not through {@link
     * #loadWithLock}/{@link #persistWithLock}), since this is precisely
     * the enforcement those helpers exist to satisfy on every other call
     * site in this file.
     */
    @Test
    void loadAndPersistRejectANullLock(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        assertThrows(
                NullPointerException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("42"), null));
        assertThrows(NullPointerException.class, () -> AccountLedgerStore.persist(file, ledger, (AccountLedgerLock) null));
    }

    /**
     * The other half of the same structural enforcement: a {@code lock}
     * that was genuinely acquired but has since been fully released is
     * rejected too -- accepting it would let a caller present stale proof
     * of holding a lock it no longer does, exactly the gap this whole
     * mechanism exists to close. Proven against a real {@link
     * AccountLedgerLock}, not a fake -- acquired, then closed via the
     * same bounded retry every real caller in this file's own {@code
     * AccountLedgerLockTest}/{@code AccountLedgerLockMultiProcessTest}
     * uses, confirming the lock file is genuinely gone before this test's
     * own real subject (passing the closed instance) is exercised.
     */
    @Test
    void loadAndPersistRejectAnAlreadyClosedLock(@TempDir Path tempDir) throws InterruptedException {
        Path file = tempDir.resolve("ledger.json");
        Path lockPath = AccountLedgerStore.lockPathFor(file);
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET);
        for (int attempt = 0; attempt < 5 && Files.exists(lockPath); attempt++) {
            lock.close();
            if (Files.exists(lockPath)) {
                Thread.sleep(50);
            }
        }
        assertFalse(Files.exists(lockPath), "test setup: the lock must be genuinely released before this test's"
                + " own real subject (load/persist rejecting a closed lock) is exercised");

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("42"), lock));
        assertThrows(IllegalStateException.class, () -> AccountLedgerStore.persist(file, ledger, lock));
    }

    /**
     * A third, distinct way an {@link AccountLedgerLock} can stop being
     * valid proof of holding the lock: a real, legitimate steal by a
     * sibling (e.g. {@code staleThreshold} elapsing on a still-working
     * holder that never called {@code close()}) -- {@code
     * releaseRequested} alone cannot catch this, since the original
     * caller never observed the steal. Proven through the real
     * production path: a genuine second {@link AccountLedgerLock#acquire}
     * call, not fabricated lock-file content, performs the steal, then
     * both {@link AccountLedgerStore#load} and {@link
     * AccountLedgerStore#persist} are confirmed to reject the now-stale
     * original instance.
     */
    @Test
    void loadAndPersistRejectALockWhoseGenerationHasBeenStolen(@TempDir Path tempDir) throws InterruptedException {
        Path file = tempDir.resolve("ledger.json");
        Path lockPath = AccountLedgerStore.lockPathFor(file);
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        Duration tinyStaleThreshold = Duration.ofMillis(50);
        AccountLedgerLock original = AccountLedgerLock.acquire(lockPath, LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET);
        Thread.sleep(150); // exceed tinyStaleThreshold so the sibling below can legitimately judge it stale
        AccountLedgerLock sibling = AccountLedgerLock.acquire(lockPath, tinyStaleThreshold, LOCK_RETRY_BUDGET);

        try {
            assertThrows(
                    IllegalStateException.class,
                    () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("42"), original));
            assertThrows(
                    IllegalStateException.class, () -> AccountLedgerStore.persist(file, ledger, original));
        } finally {
            for (int attempt = 0; attempt < 5 && Files.exists(lockPath); attempt++) {
                sibling.close();
                if (Files.exists(lockPath)) {
                    Thread.sleep(50);
                }
            }
            // original's own generation was already stolen by the time this
            // runs, so its close() call is currently a real no-op (nothing
            // on disk still matches its own metadata) -- explicitly closed
            // anyway rather than relying on that being true, so this test's
            // own lock lifecycle is complete on its own terms regardless of
            // how close()'s conditional-release behavior evolves.
            original.close();
        }
    }

    /**
     * A fourth, distinct way an {@link AccountLedgerLock} can fail to be
     * valid proof for a given {@link AccountLedgerStore#load}/{@link
     * AccountLedgerStore#persist} call: a real, currently-held, non-
     * closed, correct-generation lock -- but acquired for a
     * <i>different</i> ledger's own lock path entirely. Neither {@code
     * releaseRequested} nor the generation check can catch this, since
     * both only ever examine the lock this instance itself holds, never
     * compare it against the {@code ledgerPath} the caller is actually
     * trying to use it for. Proven against two real, genuinely different
     * {@code AccountLedgerLock} instances (one legitimately acquired for
     * {@code other.json}'s own lock path), not a fabricated mismatch.
     */
    @Test
    void loadAndPersistRejectALockAcquiredForADifferentLedgerPath(@TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        Path otherFile = tempDir.resolve("other.json");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        try (AccountLedgerLock lockForOtherFile = AccountLedgerLock.acquire(
                AccountLedgerStore.lockPathFor(otherFile), LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET)) {
            assertThrows(
                    IllegalStateException.class,
                    () -> AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("42"), lockForOtherFile));
            assertThrows(
                    IllegalStateException.class,
                    () -> AccountLedgerStore.persist(file, ledger, lockForOtherFile));
        }
    }

    /**
     * The safe-direction counterpart to {@link
     * #loadAndPersistRejectALockAcquiredForADifferentLedgerPath}:
     * {@code requireLockMatchesLedgerPath} must not wrongly reject a
     * lock genuinely acquired for the correct file merely because its
     * path was constructed with a different, but equivalent, textual
     * representation (here, a redundant {@code "."} segment) --
     * {@link java.nio.file.Path#equals} does not normalize, so an
     * unnormalized comparison would treat these as different paths even
     * though they resolve to the exact same real file.
     */
    @Test
    void loadAndPersistAcceptALockAcquiredViaAnEquivalentButTextuallyDifferentPathRepresentation(
            @TempDir Path tempDir) {
        Path file = tempDir.resolve("ledger.json");
        Path realLockPath = AccountLedgerStore.lockPathFor(file);
        // Same real file as realLockPath, via a redundant "." segment --
        // Path.equals() alone judges these as different (it compares
        // segments, not resolved identity), even though
        // Path#toAbsolutePath()#normalize() collapses them to the same path.
        Path equivalentLockPath = tempDir.resolve(".").resolve(realLockPath.getFileName());
        assertNotEquals(
                realLockPath, equivalentLockPath, "test setup: these must be textually different Path objects");
        AccountLedger ledger = new AccountLedger(
                "KIS", "acct-1", new BigDecimal("42"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());

        try (AccountLedgerLock lock =
                AccountLedgerLock.acquire(equivalentLockPath, LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET)) {
            AccountLedger loaded = AccountLedgerStore.load(file, "KIS", "acct-1", new BigDecimal("42"), lock);
            assertEquals(new BigDecimal("42"), loaded.allocatedVirtualCapital());
            AccountLedgerStore.persist(file, ledger, lock);
        }
    }

    private static final Duration LOCK_STALE_THRESHOLD = Duration.ofSeconds(30);
    private static final Duration LOCK_RETRY_BUDGET = Duration.ofSeconds(5);

    /**
     * Every real call site in this file goes through these two helpers,
     * not {@link AccountLedgerStore#load}/{@link AccountLedgerStore#persist}
     * directly -- both now require a currently-held {@link
     * AccountLedgerLock} as proof of {@link AccountLedgerStore}'s own
     * documented caller contract (its class Javadoc's "Caller contract"
     * paragraph), enforced structurally rather than only documented, so
     * a test calling either without one simply would not compile. A
     * fresh lock acquired and released around each individual call is
     * sufficient proof for {@code AccountLedgerStore}'s own purposes --
     * it only requires that some non-closed lock is held at call time,
     * not the same instance across a whole load-mutate-persist sequence
     * -- and keeps this retrofit mechanical rather than requiring each
     * test to manage its own lock lifecycle. Real cross-call mutual-
     * exclusion behavior is {@link AccountLedgerLock}'s own subject,
     * covered by {@code AccountLedgerLockTest}/
     * {@code AccountLedgerLockMultiProcessTest}, not re-tested here.
     */
    private static AccountLedger loadWithLock(
            Path ledgerPath, String venue, String accountId, BigDecimal defaultAllocatedCapital) {
        try (AccountLedgerLock lock = AccountLedgerLock.acquire(AccountLedgerStore.lockPathFor(ledgerPath), LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET)) {
            return AccountLedgerStore.load(ledgerPath, venue, accountId, defaultAllocatedCapital, lock);
        }
    }

    private static void persistWithLock(Path ledgerPath, AccountLedger ledger) {
        try (AccountLedgerLock lock = AccountLedgerLock.acquire(AccountLedgerStore.lockPathFor(ledgerPath), LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET)) {
            AccountLedgerStore.persist(ledgerPath, ledger, lock);
        }
    }

    private static void persistWithLock(Path ledgerPath, AccountLedger ledger, AccountLedgerStore.AtomicMover mover) {
        try (AccountLedgerLock lock = AccountLedgerLock.acquire(AccountLedgerStore.lockPathFor(ledgerPath), LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET)) {
            AccountLedgerStore.persist(ledgerPath, ledger, mover, lock);
        }
    }

    /**
     * A fixture-only mapper, kept separate from {@code
     * AccountLedgerStore}'s own production {@code MAPPER}: these fixtures
     * need the identical {@code Instant} serialization format the
     * production mapper uses (so the JSON they hand-assemble is readable
     * by it), but must not inherit {@code FAIL_ON_TRAILING_TOKENS} --
     * several of these same tests deliberately append trailing content
     * after a valid value, which that setting would reject before the
     * fixture was ever fully written. Kept as one shared helper, not
     * three separately-maintained copies of the same configuration, so a
     * future change to the production mapper's own serialization format
     * only needs updating in one place to keep these fixtures compatible
     * with it.
     */
    private static ObjectMapper fixtureMapper() {
        return new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }
}
