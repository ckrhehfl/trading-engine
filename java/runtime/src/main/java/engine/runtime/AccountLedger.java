package engine.runtime;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Objects;

/**
 * The shared, durable, cross-process virtual-capital ledger multiple
 * {@code kis-paper} OS processes trading against the same real KIS account
 * read and write, under {@link AccountLedgerLock}, via {@link
 * AccountLedgerStore} -- see the governing plan's "Shared KIS account risk
 * ledger" section, "2. The shared ledger". Keyed by {@code (venue,
 * accountId)} rather than KIS-specific-only, mirroring {@code
 * ExchangeAdapter}'s own venue-agnostic precedent, so a future BingX (or
 * other venue) account ledger costs a new instantiation, not a rewrite.
 *
 * <p><b>Not built here (Task B): anything that actually reads or writes
 * one of these against a real shared file under lock.</b> This record, its
 * durable JSON form via {@link AccountLedgerStore}, and {@link
 * AccountLedgerLock} are Task B's entire scope -- standalone and unwired.
 * {@code SharedKisAccountLedger implements AccountStateProvider}, the
 * class that will actually compose the lock + store into the {@code
 * equity = allocatedVirtualCapital - Σ(reservations.notional)} read
 * {@code RiskGateway} sees, and the reconciliation alarm logic that mutates
 * {@code reconciliationAlarmTrippedAt}/{@code reconciliationAlarmReason},
 * are Task C/D, not this file.
 *
 * <p>{@code lastReconciledAt} is {@code null} until the first
 * reconciliation pass ({@code AccountLedgerReconciler}, Task D) ever runs
 * against this ledger -- there is no meaningful "reconciled at" instant
 * before that, so {@link AccountLedgerStore#load} bootstraps a fresh
 * ledger with it {@code null} rather than defaulting it to, e.g., ledger-
 * creation time (which would misleadingly claim a reconciliation happened
 * when none has). {@code lastReconciledDailyPnlPercent}/{@code
 * lastReconciledWeeklyPnlPercent}/{@code lastReconciledMonthlyPnlPercent}
 * default to {@link BigDecimal#ZERO} on a fresh ledger for the same
 * reason {@link AccountLedgerStore#load} makes the underlying judgment
 * call: no PnL has been observed yet, and {@code AccountState}'s own
 * constructor requires non-null PnL percents regardless (there is no
 * "unknown" representation available to fall back on).
 *
 * <p>{@code reconciliationAlarmTrippedAt}/{@code
 * reconciliationAlarmReason} are nullable -- {@code null} for both means
 * no alarm is currently tripped; a real, unresolved reconciliation
 * mismatch (Task D) sets both together. <b>This record's own compact
 * constructor enforces that the two are only ever both-null or both-non-
 * null</b> -- a real Minor finding, real CodeRabbit review of this PR,
 * corrected from an earlier version of this Javadoc that deferred this
 * specific invariant to Task D's {@code AccountLedgerReconciler} as a
 * caller responsibility. That deferral was wrong: this is a pure
 * structural invariant, not an alarm *policy* choice -- whatever alarm
 * policy Task D ends up implementing, a half-populated pair can never be
 * a valid state, for the same reason the duplicate-{@code clientOrderId}
 * check just below is enforced here rather than left to a caller. A
 * corrupted or hand-edited ledger file can produce exactly this state,
 * and either half-populated direction is dangerous: {@code
 * reconciliationAlarmTrippedAt} alone leaves an alarm's cause unknown;
 * {@code reconciliationAlarmReason} alone means a real, unresolved
 * reconciliation mismatch would be read as "no alarm tripped" per this
 * very paragraph's own {@code null}-means-no-alarm contract -- a real
 * weakening of kill-switch-adjacent behavior.
 *
 * <p>{@code reservations} is defensively copied to an immutable list in
 * the compact constructor, matching {@code TradingLoop.fillHistory()}'s
 * own established "return/store an immutable snapshot, not the live
 * mutable list" convention elsewhere in this codebase.
 *
 * <p><b>{@code reservations} must not contain two entries with the same
 * {@code clientOrderId}</b> -- a real Trivial finding, real CodeRabbit
 * review of this PR, fixed here for the same reason {@link
 * LedgerReservation#notional} is validated in that record itself rather
 * than left to a caller: this record is the single structural
 * enforcement point, and a duplicate is dangerous in either direction. A
 * corrupted/hand-edited ledger file, or a future retry path in Task C,
 * recording the same order twice would double-count that reservation's
 * notional in {@code Σ(reservations.notional)} (understating available
 * capital, blocking legitimate orders) -- or, on release, remove only one
 * of the two entries (understating real committed exposure, the more
 * dangerous direction). This does not conflict with this class's own
 * "Task C decides which identifier it actually keys by" deferral in
 * {@link LedgerReservation}'s Javadoc -- whatever identifier Task C
 * chooses, two live reservations for the same one can never be a valid
 * state to begin with.
 *
 * <p>Package-private, like every other class in this file's "shared
 * ledger" group except the {@link AccountStateProvider} interface itself
 * -- see the governing plan's "2. The shared ledger" section header.
 */
record AccountLedger(
        String venue,
        String accountId,
        BigDecimal allocatedVirtualCapital,
        BigDecimal lastReconciledDailyPnlPercent,
        BigDecimal lastReconciledWeeklyPnlPercent,
        BigDecimal lastReconciledMonthlyPnlPercent,
        Instant lastReconciledAt,
        Instant reconciliationAlarmTrippedAt,
        String reconciliationAlarmReason,
        List<LedgerReservation> reservations) {

    AccountLedger {
        Objects.requireNonNull(venue, "venue is required");
        Objects.requireNonNull(accountId, "accountId is required");
        Objects.requireNonNull(allocatedVirtualCapital, "allocatedVirtualCapital is required");
        Objects.requireNonNull(lastReconciledDailyPnlPercent, "lastReconciledDailyPnlPercent is required");
        Objects.requireNonNull(lastReconciledWeeklyPnlPercent, "lastReconciledWeeklyPnlPercent is required");
        Objects.requireNonNull(lastReconciledMonthlyPnlPercent, "lastReconciledMonthlyPnlPercent is required");
        Objects.requireNonNull(reservations, "reservations is required");
        reservations = List.copyOf(reservations);
        long distinctClientOrderIds =
                reservations.stream().map(LedgerReservation::clientOrderId).distinct().count();
        if (distinctClientOrderIds != reservations.size()) {
            throw new IllegalArgumentException("reservations must not contain duplicate clientOrderId values");
        }
        if ((reconciliationAlarmTrippedAt == null) != (reconciliationAlarmReason == null)) {
            throw new IllegalArgumentException(
                    "reconciliationAlarmTrippedAt and reconciliationAlarmReason must both be null or both be"
                            + " non-null -- a half-populated alarm cannot represent a valid state");
        }
    }
}
