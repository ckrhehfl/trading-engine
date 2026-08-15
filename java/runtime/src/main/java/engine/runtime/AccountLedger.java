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
 * mismatch (Task D) sets both together. This record is a plain data
 * holder and does not itself enforce that the two are only ever both-null
 * or both-non-null -- callers (Task D's {@code AccountLedgerReconciler})
 * are responsible for that invariant.
 *
 * <p>{@code reservations} is defensively copied to an immutable list in
 * the compact constructor, matching {@code TradingLoop.fillHistory()}'s
 * own established "return/store an immutable snapshot, not the live
 * mutable list" convention elsewhere in this codebase.
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
    }
}
