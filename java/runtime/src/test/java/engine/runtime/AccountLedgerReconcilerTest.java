package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.exchange.PositionSnapshot;
import engine.risk.AccountState;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link AccountLedgerReconciler} -- see its own class Javadoc and the
 * governing plan's "Shared KIS account risk ledger" section, "3. {@code
 * AccountLedgerReconciler}". Each test builds a real, temp-dir-backed
 * ledger file (no mocking framework anywhere in this codebase) and a real
 * {@link FakeExchangeAdapter} scripted with {@code willReturnPositions}/
 * {@code willFailPositionsWith}.
 */
class AccountLedgerReconcilerTest {

    private static final String VENUE = "KIS";
    private static final String ACCOUNT_ID = "acct-1";
    private static final Duration LOCK_STALE_THRESHOLD = Duration.ofSeconds(30);
    private static final Duration LOCK_RETRY_BUDGET = Duration.ofSeconds(5);

    @Test
    void cleanReconciliationUpdatesLastReconciledAtWithoutTrippingAnyAlarm(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of());
        KillSwitch killSwitch = new KillSwitch();
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), adapter, killSwitch, fixedClock());

        reconciler.runStartupReconciliation();

        assertFalse(killSwitch.isTripped());
        assertEquals(1, reconciler.reconciliationPassCount());
        AccountLedger onDisk = loadWithLock(ledgerPath);
        assertNotNull(onDisk.lastReconciledAt());
        assertNull(onDisk.reconciliationAlarmTrippedAt());
        assertNull(onDisk.reconciliationAlarmReason());
    }

    @Test
    void largeMismatchTripsKillSwitchAndPersistsAlarmOnTheLedger(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        // No reservations on the ledger (ledgerExposure=0), but a real position
        // reports 50000 notional -- 50% of allocatedVirtualCapital, way over the 10% threshold (10000).
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("SYM", "500", "100")));
        KillSwitch killSwitch = new KillSwitch();
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), adapter, killSwitch, fixedClock());

        reconciler.runStartupReconciliation();

        assertTrue(killSwitch.isTripped(), "this process's own KillSwitch must be tripped");
        AccountLedger onDisk = loadWithLock(ledgerPath);
        assertNotNull(onDisk.reconciliationAlarmTrippedAt());
        assertNotNull(onDisk.reconciliationAlarmReason());
        assertTrue(onDisk.reconciliationAlarmReason().contains("50000"));
    }

    /**
     * Boundary case: a mismatch of exactly 10% of {@code
     * allocatedVirtualCapital} must NOT trip the alarm -- the threshold is
     * "exceeding 10%" (strict {@code >}), not "10% or more".
     */
    @Test
    void aMismatchOfExactlyTenPercentDoesNotTripTheAlarm(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        // ledgerExposure=0, realExposure=10000 -- mismatch is exactly 10% of allocatedVirtualCapital.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("SYM", "100", "100")));
        KillSwitch killSwitch = new KillSwitch();
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), adapter, killSwitch, fixedClock());

        reconciler.runStartupReconciliation();

        assertFalse(killSwitch.isTripped(), "a mismatch of exactly 10% must not trip the kill switch");
        AccountLedger onDisk = loadWithLock(ledgerPath);
        assertNull(onDisk.reconciliationAlarmTrippedAt());
        assertNull(onDisk.reconciliationAlarmReason());
    }

    /** Complement to the exactly-10% case above: a mismatch one cent over 10% must trip. */
    @Test
    void aMismatchJustOverTenPercentTripsTheAlarm(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        // ledgerExposure=0, realExposure=10000.01 -- one cent over the 10% threshold (10000).
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("SYM", "1", "10000.01")));
        KillSwitch killSwitch = new KillSwitch();
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), adapter, killSwitch, fixedClock());

        reconciler.runStartupReconciliation();

        assertTrue(killSwitch.isTripped(), "a mismatch one cent over 10% must trip the kill switch");
        AccountLedger onDisk = loadWithLock(ledgerPath);
        assertNotNull(onDisk.reconciliationAlarmTrippedAt());
        assertNotNull(onDisk.reconciliationAlarmReason());
    }

    @Test
    void incompletePositionDataTripsTheAlarmWithoutComputingAPossiblyWrongMismatch(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        // avgPrice is null -- real exposure for this position cannot be trusted.
        adapter.willReturnPositions(
                List.of(new PositionSnapshot("SYM", "LONG", new BigDecimal("1"), null, BigDecimal.ONE, BigDecimal.ZERO, null)));
        KillSwitch killSwitch = new KillSwitch();
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), adapter, killSwitch, fixedClock());

        reconciler.runStartupReconciliation();

        assertTrue(killSwitch.isTripped());
        AccountLedger onDisk = loadWithLock(ledgerPath);
        assertNotNull(onDisk.reconciliationAlarmTrippedAt());
        assertTrue(onDisk.reconciliationAlarmReason().contains("positionAmt/avgPrice"));
    }

    @Test
    void aCleanPassNeverClearsAPreExistingAlarm(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        Instant earlierAlarm = Instant.parse("2026-08-01T00:00:00Z");
        AccountLedger withAlarm = new AccountLedger(
                VENUE, ACCOUNT_ID, new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                earlierAlarm, earlierAlarm, "a previous reconciliation mismatch", List.of());
        persistWithLock(ledgerPath, withAlarm);
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of()); // no mismatch this time -- would be "clean" on its own
        KillSwitch killSwitch = new KillSwitch();
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), adapter, killSwitch, fixedClock());

        reconciler.runStartupReconciliation();

        assertFalse(killSwitch.isTripped(), "a clean pass must not itself trip the kill switch");
        AccountLedger onDisk = loadWithLock(ledgerPath);
        assertEquals(earlierAlarm, onDisk.reconciliationAlarmTrippedAt(), "a clean pass must never clear a pre-existing alarm");
        assertEquals("a previous reconciliation mismatch", onDisk.reconciliationAlarmReason());
        assertTrue(onDisk.lastReconciledAt().isAfter(earlierAlarm), "lastReconciledAt must still be updated");
    }

    /**
     * The real, end-to-end propagation proof this whole design exists for:
     * a mismatch this reconciler detects and persists must actually be
     * observed by a completely separate {@link SharedKisAccountLedger}
     * instance pointed at the same file -- not merely that the reconciler
     * itself writes the two alarm fields.
     */
    @Test
    void aSecondIndependentlyConstructedSharedKisAccountLedgerObservesTheAlarmAndRefusesFurtherReservations(
            @TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("SYM", "500", "100"))); // 50000 notional, over threshold
        KillSwitch killSwitch = new KillSwitch();
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), adapter, killSwitch, fixedClock());
        reconciler.runStartupReconciliation();
        assertTrue(killSwitch.isTripped());

        // A completely independent instance -- standing in for a sibling kis-paper process trading a
        // different symbol against the same real account, which never saw this reconciler run.
        SharedKisAccountLedger sibling = new SharedKisAccountLedger(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(), "OTHER-SYM", Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("10"), null,
                "1d", Instant.now());

        AccountState result = sibling.reserveForIntent(intent, new BigDecimal("100"));

        assertEquals(0, BigDecimal.ONE.compareTo(result.equity()), "equity must be floored to a small positive constant");
        AccountLedger onDisk = loadWithLock(ledgerPath);
        assertTrue(
                onDisk.reservations().isEmpty(),
                "no reservation may be created for a sibling process while the reconciler-tripped alarm is active");
    }

    // ---- day-boundary cadence ----

    @Test
    void runOnUtcDayBoundaryFiresExactlyOnceEachTimeTheUtcDayAdvances(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of());
        MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), adapter, new KillSwitch(), clock);

        // The very first call ever made just seeds tracking state -- mirrors DailyReportGenerator#beforeTick's
        // own identical "nothing to finalize yet" behavior on its first call.
        reconciler.runOnUtcDayBoundary();
        assertEquals(0, reconciler.reconciliationPassCount());

        // Same day, repeated calls -- must not fire again.
        clock.advanceTo(Instant.parse("2026-08-07T12:00:00Z"));
        reconciler.runOnUtcDayBoundary();
        assertEquals(0, reconciler.reconciliationPassCount());

        // Crosses into 2026-08-08 -- fires exactly once.
        clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
        reconciler.runOnUtcDayBoundary();
        assertEquals(1, reconciler.reconciliationPassCount());

        // Same new day, repeated calls -- must not fire again.
        clock.advanceTo(Instant.parse("2026-08-08T18:00:00Z"));
        reconciler.runOnUtcDayBoundary();
        assertEquals(1, reconciler.reconciliationPassCount());

        // Crosses into 2026-08-09 -- fires again.
        clock.advanceTo(Instant.parse("2026-08-09T00:05:00Z"));
        reconciler.runOnUtcDayBoundary();
        assertEquals(2, reconciler.reconciliationPassCount());
    }

    @Test
    void runStartupReconciliationSeedsTheTrackedDaySoTheFirstSameDayBoundaryCallDoesNotReReconcile(
            @TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of());
        MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:00:05Z"));
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), adapter, new KillSwitch(), clock);

        reconciler.runStartupReconciliation();
        assertEquals(1, reconciler.reconciliationPassCount());

        clock.advanceTo(Instant.parse("2026-08-07T12:00:00Z"));
        reconciler.runOnUtcDayBoundary();
        assertEquals(1, reconciler.reconciliationPassCount(), "same UTC day as the startup pass -- must not re-run");

        clock.advanceTo(Instant.parse("2026-08-08T00:00:05Z"));
        reconciler.runOnUtcDayBoundary();
        assertEquals(2, reconciler.reconciliationPassCount(), "a new UTC day must trigger exactly one more pass");
    }

    /**
     * A failed pass must not advance the tracked day -- the very next call
     * (the next scheduled tick, in production) retries rather than waiting
     * an entire day for the next boundary.
     */
    @Test
    void aFailedPassDoesNotAdvanceTheTrackedDayAndIsRetriedOnTheNextCall(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of());
        MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:00:05Z"));
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), adapter, new KillSwitch(), clock);
        reconciler.runStartupReconciliation();
        assertEquals(1, reconciler.reconciliationPassCount());

        clock.advanceTo(Instant.parse("2026-08-08T00:00:05Z"));
        adapter.willFailPositionsWith(new RuntimeException("simulated getPositions failure"));

        assertThrows(RuntimeException.class, () -> reconciler.runOnUtcDayBoundary());
        assertEquals(1, reconciler.reconciliationPassCount(), "a failed pass must not count as completed");

        // Same day, adapter now healthy -- must retry (not wait for the next boundary).
        adapter.willReturnPositions(List.of());
        reconciler.runOnUtcDayBoundary();
        assertEquals(2, reconciler.reconciliationPassCount());
    }

    private static PositionSnapshot position(String symbol, String positionAmt, String avgPrice) {
        return new PositionSnapshot(
                symbol, "LONG", new BigDecimal(positionAmt), new BigDecimal(avgPrice), BigDecimal.ONE, BigDecimal.ZERO,
                null);
    }

    private static java.time.Clock fixedClock() {
        return java.time.Clock.fixed(Instant.parse("2026-08-07T00:00:00Z"), ZoneOffset.UTC);
    }

    private static AccountLedger loadWithLock(Path ledgerPath) {
        try (AccountLedgerLock lock = AccountLedgerLock.acquire(
                AccountLedgerStore.lockPathFor(ledgerPath), LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET)) {
            return AccountLedgerStore.load(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"), lock);
        }
    }

    private static void persistWithLock(Path ledgerPath, AccountLedger ledger) {
        try (AccountLedgerLock lock = AccountLedgerLock.acquire(
                AccountLedgerStore.lockPathFor(ledgerPath), LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET)) {
            AccountLedgerStore.persist(ledgerPath, ledger, lock);
        }
    }

    /** Mirrors {@code DailyReportGeneratorTest}/{@code PaperTradingAppTest}'s own identical {@code MutableClock} test double. */
    private static final class MutableClock extends java.time.Clock {
        private Instant instant;

        MutableClock(Instant instant) {
            this.instant = instant;
        }

        void advanceTo(Instant newInstant) {
            this.instant = newInstant;
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public java.time.Clock withZone(ZoneId zone) {
            throw new UnsupportedOperationException("not needed by these tests");
        }

        @Override
        public Instant instant() {
            return instant;
        }
    }
}
