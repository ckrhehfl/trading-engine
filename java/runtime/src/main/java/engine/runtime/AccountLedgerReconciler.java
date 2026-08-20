package engine.runtime;

import engine.exchange.ExchangeAdapter;
import engine.exchange.PositionSnapshot;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Periodic real-account reconciliation for the shared {@link AccountLedger}
 * -- see the governing plan's "Shared KIS account risk ledger" section,
 * "3. {@code AccountLedgerReconciler}". Compares the ledger's own committed
 * exposure ({@code Σ reservations[].notional}, the same {@code quantity ×
 * price} figure {@link SharedKisAccountLedger#reserveForIntent} already
 * produces -- still contract-multiplier-unaware, unchanged here) against a
 * real figure derived from {@link ExchangeAdapter#getPositions()} ({@code Σ
 * |positionAmt| × avgPrice} across every position the account holds, using
 * the same simplistic formula deliberately, for an apples-to-apples
 * comparison against an already-known-imperfect number on both sides).
 *
 * <p><b>On a mismatch exceeding 10% of {@code allocatedVirtualCapital}</b>
 * (the threshold itself is already human-approved, not decided here): trips
 * this process's own {@link KillSwitch} immediately, and writes {@code
 * reconciliationAlarmTrippedAt}/{@code reconciliationAlarmReason} onto the
 * shared {@link AccountLedger}, under the same {@link AccountLedgerLock}
 * {@link SharedKisAccountLedger}'s own reserve/confirm/release methods use.
 * <b>No new cross-process signaling is built here</b> -- {@link
 * SharedKisAccountLedger#reserveForIntent} already reloads the full ledger
 * under lock on every call and already checks {@code
 * reconciliationAlarmTrippedAt} (confirmed by reading that class's real,
 * merged code before writing this one), returning a floored-near-zero-equity
 * snapshot without creating a reservation once it's set -- {@code
 * RiskGateway}'s existing, unmodified clamped-quantity-rounds-to-zero
 * rejection path does the actual blocking from there. This class's only job
 * is detecting the mismatch and recording it; every other process sharing
 * this ledger file observes the alarm on its own next reserve/confirm/
 * release call.
 *
 * <p><b>Never auto-clears.</b> A clean reconciliation pass never clears a
 * pre-existing alarm -- {@code reconciliationAlarmTrippedAt}/{@code Reason}
 * are copied forward unchanged from the loaded ledger whenever this pass
 * finds no new problem. Matches {@code SubmissionMarkerResolver}'s own
 * established "never silently auto-resolve an ambiguous safety state"
 * precedent: no clearing mechanism exists anywhere in this class. A human
 * must investigate and edit the ledger file directly to clear both fields.
 *
 * <p><b>Missing/null position data fails closed, not silently to zero.</b>
 * If any position {@link ExchangeAdapter#getPositions()} returns has a null
 * {@code positionAmt} or {@code avgPrice} (both are nullable on {@link
 * PositionSnapshot} in practice), the real exposure figure cannot be
 * trusted -- rather than silently treating that position as contributing
 * zero exposure (which would understate the real figure and could mask a
 * genuine problem), this trips the alarm immediately with a reason naming
 * the incomplete data, without computing or comparing a possibly-wrong
 * numeric mismatch at all.
 *
 * <p><b>Cadence, two call sites, no scheduler of its own</b> (matches
 * {@link DailyReportGenerator}'s own "not itself a scheduler" design):
 *
 * <ul>
 *   <li>{@link #runStartupReconciliation()} -- called once, by {@code
 *       PaperTradingApp#forKisPaper}, right after {@link
 *       SharedKisAccountLedger#bootstrapOrLoad} and before the ledger is
 *       handed to {@code TradingLoop}'s constructor. Runs a pass
 *       unconditionally and seeds this instance's own day-tracking state to
 *       "today," so the very next {@link #runOnUtcDayBoundary()} call (same
 *       day, from the first scheduled tick) does not immediately re-run.
 *   <li>{@link #runOnUtcDayBoundary()} -- called from {@code
 *       PaperTradingApp#runTick()} on every tick. Mirrors {@link
 *       DailyReportGenerator#beforeTick()}'s own exact day-boundary
 *       technique ({@code LocalDate.ofInstant(clock.instant(),
 *       ZoneOffset.UTC)} compared against a locally tracked day): the very
 *       first call ever made on an instance (only reachable if a caller
 *       never called {@link #runStartupReconciliation()} first) just seeds
 *       tracking state without reconciling, matching {@code
 *       DailyReportGenerator}'s own identical "first call just seeds"
 *       behavior; a later call where "today" has advanced runs a pass.
 *       <b>Deliberately its own separate day-tracking field</b>, not shared
 *       with {@code DailyReportGenerator}'s own internal state -- the same
 *       "two separate classes for two separate concerns" pattern already
 *       established between {@code DailyReportGenerator} and {@code
 *       PendingDailyReportStore}.
 * </ul>
 *
 * <p><b>If a pass throws, the tracked day is not advanced</b> -- a
 * transient failure (e.g. {@code getPositions()} erroring) is retried on
 * every subsequent {@link #runOnUtcDayBoundary()} call until a pass
 * actually completes, rather than silently going unreconciled for the rest
 * of that day. This is the opposite choice from {@link
 * DailyReportGenerator}'s own "day tracking advances regardless of write
 * success" rule, deliberately: an unreconciled account-exposure check is a
 * materially higher-stakes gap than one day's report being late.
 *
 * <p>Package-private -- like every other class in the shared-ledger group
 * (see {@link AccountLedger}'s own class Javadoc), owned entirely by {@code
 * PaperTradingApp}'s KIS-specific wiring.
 */
final class AccountLedgerReconciler {

    private static final Logger log = LoggerFactory.getLogger(AccountLedgerReconciler.class);

    /** Same production default as {@link SharedKisAccountLedger#DEFAULT_STALE_THRESHOLD} -- reused, not duplicated. */
    static final Duration DEFAULT_STALE_THRESHOLD = SharedKisAccountLedger.DEFAULT_STALE_THRESHOLD;

    /** Same production default as {@link SharedKisAccountLedger#DEFAULT_RETRY_BUDGET} -- reused, not duplicated. */
    static final Duration DEFAULT_RETRY_BUDGET = SharedKisAccountLedger.DEFAULT_RETRY_BUDGET;

    private static final BigDecimal MISMATCH_THRESHOLD_PERCENT = new BigDecimal("0.10");

    private final Path ledgerPath;
    private final String venue;
    private final String accountId;
    private final BigDecimal defaultAllocatedCapital;
    private final ExchangeAdapter adapter;
    private final KillSwitch killSwitch;
    private final Clock clock;
    private final Duration staleThreshold;
    private final Duration retryBudget;

    private LocalDate currentDay;
    private int reconciliationPassCount;

    AccountLedgerReconciler(
            Path ledgerPath,
            String venue,
            String accountId,
            BigDecimal defaultAllocatedCapital,
            ExchangeAdapter adapter,
            KillSwitch killSwitch,
            Clock clock) {
        this(
                ledgerPath,
                venue,
                accountId,
                defaultAllocatedCapital,
                adapter,
                killSwitch,
                clock,
                DEFAULT_STALE_THRESHOLD,
                DEFAULT_RETRY_BUDGET);
    }

    /** Test-only overload -- lets a test override the lock's stale threshold/retry budget, mirroring {@link SharedKisAccountLedger}'s own identical pattern. */
    AccountLedgerReconciler(
            Path ledgerPath,
            String venue,
            String accountId,
            BigDecimal defaultAllocatedCapital,
            ExchangeAdapter adapter,
            KillSwitch killSwitch,
            Clock clock,
            Duration staleThreshold,
            Duration retryBudget) {
        this.ledgerPath = Objects.requireNonNull(ledgerPath, "ledgerPath is required");
        this.venue = Objects.requireNonNull(venue, "venue is required");
        this.accountId = Objects.requireNonNull(accountId, "accountId is required");
        this.defaultAllocatedCapital =
                Objects.requireNonNull(defaultAllocatedCapital, "defaultAllocatedCapital is required");
        this.adapter = Objects.requireNonNull(adapter, "adapter is required");
        this.killSwitch = Objects.requireNonNull(killSwitch, "killSwitch is required");
        this.clock = Objects.requireNonNull(clock, "clock is required");
        this.staleThreshold = Objects.requireNonNull(staleThreshold, "staleThreshold is required");
        this.retryBudget = Objects.requireNonNull(retryBudget, "retryBudget is required");
    }

    /** See class Javadoc, "Cadence" -- the startup call site. */
    void runStartupReconciliation() {
        reconcileNow();
        currentDay = LocalDate.ofInstant(clock.instant(), ZoneOffset.UTC);
    }

    /** See class Javadoc, "Cadence" -- the ongoing, per-tick call site. */
    void runOnUtcDayBoundary() {
        LocalDate today = LocalDate.ofInstant(clock.instant(), ZoneOffset.UTC);
        if (currentDay == null) {
            currentDay = today;
            return;
        }
        if (today.isAfter(currentDay)) {
            reconcileNow();
            currentDay = today;
        }
        // today.isBefore(currentDay): a backwards clock adjustment -- deliberately not treated as a boundary,
        // matching DailyReportGenerator#beforeTick's own identical handling of the same case.
    }

    /**
     * Runs one real reconciliation pass unconditionally: acquire the lock,
     * reload the ledger, compare exposure, trip+persist on a real mismatch
     * (or on incomplete position data), otherwise persist an updated {@code
     * lastReconciledAt} only -- see class Javadoc for the full contract.
     * Propagates any failure uncaught (lock acquisition, {@code
     * getPositions()}, or the store's own fail-closed checks) -- this is a
     * one-shot pass, not a per-tick call with its own retry/never-throw
     * contract; both call sites above decide for themselves what a thrown
     * exception means (startup: refuse to start; ongoing: retry next tick).
     */
    private void reconcileNow() {
        Path lockPath = AccountLedgerStore.lockPathFor(ledgerPath);
        try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, staleThreshold, retryBudget)) {
            AccountLedger ledger = AccountLedgerStore.load(ledgerPath, venue, accountId, defaultAllocatedCapital, lock);
            BigDecimal ledgerExposure = totalNotional(ledger.reservations());
            List<PositionSnapshot> positions = adapter.getPositions();
            Instant now = clock.instant();

            String incompleteDataReason = firstIncompletePositionReason(positions);
            AccountLedger updated;
            if (incompleteDataReason != null) {
                killSwitch.trip();
                log.error(
                        "AccountLedgerReconciler: {} -- cannot compute a trustworthy real exposure figure; tripping"
                                + " this process's KillSwitch and recording the alarm on the shared ledger. A human"
                                + " must investigate and manually clear reconciliationAlarmTrippedAt/"
                                + "reconciliationAlarmReason before this account resumes trading.",
                        incompleteDataReason);
                updated = withReconciliation(ledger, now, now, incompleteDataReason);
            } else {
                BigDecimal realExposure = totalRealExposure(positions);
                BigDecimal mismatch = ledgerExposure.subtract(realExposure).abs();
                BigDecimal threshold = ledger.allocatedVirtualCapital().multiply(MISMATCH_THRESHOLD_PERCENT);
                if (mismatch.compareTo(threshold) > 0) {
                    String reason = "ledger exposure " + ledgerExposure + " diverges from real account exposure "
                            + realExposure + " by " + mismatch + ", exceeding 10% of allocatedVirtualCapital ("
                            + threshold + ")";
                    killSwitch.trip();
                    log.error(
                            "AccountLedgerReconciler: reconciliation mismatch exceeded threshold: {} -- tripping"
                                    + " this process's KillSwitch and recording the alarm on the shared ledger. A"
                                    + " human must investigate and manually clear reconciliationAlarmTrippedAt/"
                                    + "reconciliationAlarmReason before this account resumes trading.",
                            reason);
                    updated = withReconciliation(ledger, now, now, reason);
                } else {
                    log.info(
                            "AccountLedgerReconciler: clean reconciliation -- ledgerExposure={} realExposure={}"
                                    + " mismatch={} threshold={}",
                            ledgerExposure,
                            realExposure,
                            mismatch,
                            threshold);
                    // Never auto-clears -- see class Javadoc. A pre-existing alarm (if any) is carried forward
                    // unchanged; lastReconciledAt is the only field this branch ever updates.
                    updated = withReconciliation(
                            ledger, now, ledger.reconciliationAlarmTrippedAt(), ledger.reconciliationAlarmReason());
                }
            }
            AccountLedgerStore.persist(ledgerPath, updated, lock);
        }
        reconciliationPassCount++;
    }

    private static AccountLedger withReconciliation(
            AccountLedger ledger, Instant lastReconciledAt, Instant alarmTrippedAt, String alarmReason) {
        return new AccountLedger(
                ledger.venue(),
                ledger.accountId(),
                ledger.allocatedVirtualCapital(),
                ledger.lastReconciledDailyPnlPercent(),
                ledger.lastReconciledWeeklyPnlPercent(),
                ledger.lastReconciledMonthlyPnlPercent(),
                lastReconciledAt,
                alarmTrippedAt,
                alarmReason,
                ledger.reservations());
    }

    private static BigDecimal totalNotional(List<LedgerReservation> reservations) {
        BigDecimal total = BigDecimal.ZERO;
        for (LedgerReservation reservation : reservations) {
            total = total.add(reservation.notional());
        }
        return total;
    }

    /** {@code null} if every position has both fields populated; otherwise a reason naming the first offending symbol. See class Javadoc, "Missing/null position data fails closed." */
    private static String firstIncompletePositionReason(List<PositionSnapshot> positions) {
        for (PositionSnapshot position : positions) {
            if (position.positionAmt() == null || position.avgPrice() == null) {
                return "a position returned by ExchangeAdapter#getPositions() is missing positionAmt/avgPrice"
                        + " (symbol=" + position.symbol() + ")";
            }
        }
        return null;
    }

    /** Caller must have already confirmed via {@link #firstIncompletePositionReason} that no field is null. */
    private static BigDecimal totalRealExposure(List<PositionSnapshot> positions) {
        BigDecimal total = BigDecimal.ZERO;
        for (PositionSnapshot position : positions) {
            total = total.add(position.positionAmt().abs().multiply(position.avgPrice()));
        }
        return total;
    }

    /** Test-only accessor -- how many reconciliation passes have actually completed (lock acquired, ledger loaded and re-persisted) on this instance. */
    int reconciliationPassCount() {
        return reconciliationPassCount;
    }
}
