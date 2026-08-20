package engine.runtime;

import engine.risk.AccountState;
import engine.schemas.OrderIntent;
import java.math.BigDecimal;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The real {@link AccountStateProvider} multiple {@code kis-paper} OS
 * processes trading against the same real KIS account share -- composes
 * {@link AccountLedgerLock} + {@link AccountLedgerStore} into the {@code
 * equity = allocatedVirtualCapital - Σ(reservations.notional)} read {@link
 * engine.risk.RiskGateway} sees. See the governing plan's "Shared KIS
 * account risk ledger" section, "2. The shared ledger", and {@link
 * AccountStateProvider}'s own Javadoc for the reservation lifecycle
 * (pessimistic reserve, then exactly one of confirm/release) this class
 * implements.
 *
 * <p>Each of the three {@link AccountStateProvider} methods below is its
 * own independent critical section: acquire {@link AccountLedgerLock} ->
 * {@link AccountLedgerStore#load} -> mutate the in-memory snapshot ->
 * (usually) {@link AccountLedgerStore#persist} -> release, via try-with-
 * resources. No explicit extra {@link AccountLedgerLock#requireHeld()}
 * call is made here between {@code load} and {@code persist} -- both of
 * those methods already call {@code requireHeld()} internally on every
 * invocation ({@code load} once, {@code persist} twice: once at its own
 * entry, once again immediately before its actual write), which is
 * exactly the "hold lock across a read then a later write" discipline
 * {@code AccountLedgerStore}'s own class Javadoc documents. Adding a
 * third, redundant call here would not narrow any window further.
 *
 * <p><b>Reconciliation alarm</b>: if the loaded ledger's {@code
 * reconciliationAlarmTrippedAt} is non-null (set by a future {@code
 * AccountLedgerReconciler}, not built by this class -- see the governing
 * plan's Task D), {@link #reserveForIntent} returns a floored-near-zero-
 * equity snapshot without ever creating a reservation. This class does not
 * itself reject the order -- {@link engine.risk.RiskGateway#evaluate}'s
 * own existing, unmodified "clamped quantity rounds to zero -&gt; reject"
 * path does that, given a near-zero equity to clamp against.
 *
 * <p><b>Non-positive effective price</b>: mirrors {@link
 * engine.risk.RiskGateway#evaluate}'s own price resolution ({@code
 * intent.limitPrice()} if present, else the given reference price). If
 * that price is {@code <= 0}, no reservation is created (a zero or
 * negative notional would be meaningless, and {@link LedgerReservation}'s
 * own compact constructor rejects a non-positive {@code notional}
 * outright) -- {@code RiskGateway}'s own existing {@code price.signum()
 * <= 0} rejection handles the actual rejection, given an ordinary,
 * unmodified snapshot of the ledger's current equity.
 *
 * <p><b>Over-budget reservations are declined outright, not merely
 * floored after the fact.</b> {@link AccountLedgerLock} alone only
 * prevents a <i>lost update</i> (two concurrent callers clobbering each
 * other's write) -- it does not, by itself, prevent the <i>sum</i> of
 * sequentially-persisted pessimistic reservations from exceeding {@code
 * allocatedVirtualCapital}, since each {@link #reserveForIntent} call is
 * its own independent critical section and a pessimistic reservation is
 * deliberately sized to the full pre-clamp {@code intent.quantity()} (see
 * {@link AccountStateProvider}'s own Javadoc), not to whatever capital
 * happens to remain. Before persisting, {@link #reserveForIntent}
 * computes the hypothetical combined total the candidate reservation
 * would produce (after {@link #reserveMonotonic}'s own merge) and, if
 * that total would exceed {@code allocatedVirtualCapital}, declines to
 * persist it at all -- same treatment as the alarm-tripped/non-positive-
 * price cases above: a floored near-zero-equity snapshot, no reservation
 * recorded, {@code RiskGateway}'s own unmodified clamped-quantity-rounds-
 * to-zero path doing the actual rejecting. This is what keeps the
 * <i>on-disk</i> committed total within budget at every persisted state,
 * not just once outstanding reservations are eventually confirmed/
 * released back down.
 *
 * <p><b>Account-identifier logging</b>: no log statement in this class
 * ever includes {@code ledgerPath} or {@code accountId} -- both either
 * are, or embed, a real KIS account identifier, and CLAUDE.md's
 * Non-negotiable Rules forbid raw account identifiers in logs. {@code
 * venue} alone is not account-identifying and is logged freely.
 *
 * <p><b>Idempotency</b>: {@link #reserveForIntent} never reduces an
 * existing reservation for a repeated {@code intentId} -- see {@link
 * #reserveMonotonic} and {@link AccountStateProvider}'s own Javadoc for
 * the exact rule and why. {@link #confirmReservation}/{@link
 * #releaseReservation} tolerate a missing reservation for the given
 * {@code intentId} as a no-op (logged, not thrown) -- the only path that
 * lawfully produces a missing entry is {@link #reserveForIntent} itself
 * skipping creation (the alarm or non-positive-price cases above), which
 * always leads to {@code RiskGateway} rejecting the intent and {@code
 * TradingLoop} calling {@link #releaseReservation} on nothing.
 */
final class SharedKisAccountLedger implements AccountStateProvider {

    private static final Logger log = LoggerFactory.getLogger(SharedKisAccountLedger.class);

    /**
     * Matches {@link AccountLedgerLock}'s own class Javadoc, "This
     * project's own proposed real default (~30s, per the governing
     * plan)" -- comfortably larger than this class's own real,
     * measured critical-section duration (a handful of small in-memory
     * list operations plus the lock's own internal {@code
     * requireHeld()} I/O), matching {@code AccountLedgerLockTest}'s
     * established {@code GENEROUS_STALE_THRESHOLD} convention.
     */
    static final Duration DEFAULT_STALE_THRESHOLD = Duration.ofSeconds(30);

    /**
     * Matches {@code AccountLedgerStoreTest}'s own established {@code
     * LOCK_RETRY_BUDGET} convention for ordinary (non-stress-test)
     * production-shaped use.
     */
    static final Duration DEFAULT_RETRY_BUDGET = Duration.ofSeconds(5);

    /**
     * The equity floor {@link #reserveForIntent} returns instead of a
     * non-positive value -- {@link AccountState}'s own constructor
     * rejects {@code equity <= 0} outright. Deliberately small relative
     * to any real KOSPI200/individual-stock-futures contract notional
     * (index points or share price times a real per-contract multiplier,
     * always many orders of magnitude larger than 1 KRW) so {@code
     * RiskGateway}'s own clamped-quantity-rounds-to-zero rejection is
     * what actually blocks the order, not an artificially generous floor.
     */
    private static final BigDecimal MIN_EQUITY = BigDecimal.ONE;

    private static final String HOSTNAME = resolveHostname();

    private final Path ledgerPath;
    private final String venue;
    private final String accountId;
    private final BigDecimal defaultAllocatedCapital;
    private final Duration staleThreshold;
    private final Duration retryBudget;

    SharedKisAccountLedger(Path ledgerPath, String venue, String accountId, BigDecimal defaultAllocatedCapital) {
        this(ledgerPath, venue, accountId, defaultAllocatedCapital, DEFAULT_STALE_THRESHOLD, DEFAULT_RETRY_BUDGET);
    }

    /** Test-only overload -- lets a test override the lock's stale threshold/retry budget. */
    SharedKisAccountLedger(
            Path ledgerPath,
            String venue,
            String accountId,
            BigDecimal defaultAllocatedCapital,
            Duration staleThreshold,
            Duration retryBudget) {
        this.ledgerPath = Objects.requireNonNull(ledgerPath, "ledgerPath is required");
        this.venue = Objects.requireNonNull(venue, "venue is required");
        this.accountId = Objects.requireNonNull(accountId, "accountId is required");
        this.defaultAllocatedCapital =
                Objects.requireNonNull(defaultAllocatedCapital, "defaultAllocatedCapital is required");
        this.staleThreshold = Objects.requireNonNull(staleThreshold, "staleThreshold is required");
        this.retryBudget = Objects.requireNonNull(retryBudget, "retryBudget is required");
    }

    /**
     * Bootstraps a brand new ledger file at {@code ledgerPath} with {@code
     * allocatedVirtualCapital = realBalance} if none exists yet, or, if one
     * already exists (a sibling {@code kis-paper} process for a different
     * symbol got there first), leaves it completely untouched and uses its
     * already-persisted {@code allocatedVirtualCapital} as-is -- see the
     * governing task's own "Bootstrapping allocatedVirtualCapital" design
     * note. Always ensures {@code ledgerPath} exists as a real, persisted
     * file on disk before returning: {@link AccountLedgerStore#load} never
     * itself persists a freshly-bootstrapped in-memory ledger, so this
     * method calls {@link AccountLedgerStore#persist} unconditionally
     * (safe even when the file already existed -- {@code persist}'s own
     * "refuses to silently raise {@code allocatedVirtualCapital}" and
     * identity checks both pass trivially when persisting back the exact
     * object that was just loaded, unchanged).
     *
     * <p>The returned instance's own {@code defaultAllocatedCapital} is
     * fixed to whatever this call determined ({@code realBalance} on a
     * fresh bootstrap, or the already-persisted value otherwise) --
     * {@link #reserveForIntent}/{@link #confirmReservation}/{@link
     * #releaseReservation} reuse that same fixed value on every future
     * {@link AccountLedgerStore#load} call for this instance's lifetime,
     * rather than re-querying the real KIS balance on every tick (the
     * whole reason this design uses a virtual ledger with periodic
     * reconciliation instead of a live balance check on every risk
     * decision -- see the governing plan's own "Decisions already made").
     *
     * <p><b>A real, disclosed consequence of reusing {@link
     * AccountLedgerStore#load}'s own existing fail-closed contract as-is,
     * not a gap introduced by this method</b>: if {@code realBalance}
     * (today's real, just-queried KIS balance) is smaller than an
     * already-persisted {@code allocatedVirtualCapital} on disk, {@code
     * load} throws {@link IllegalStateException} rather than silently
     * keeping the larger, stale figure -- the same "never weaken risk
     * limits... without explicit human approval" protection {@code
     * AccountLedgerStore#load}'s own Javadoc already documents, applied
     * here without any special-casing. A caller of this method (i.e.
     * {@code PaperTradingApp#forKisPaper}) that hits this must refuse to
     * start, matching every other fail-closed startup check in this
     * codebase -- not attempted to be silently downgraded here.
     */
    static SharedKisAccountLedger bootstrapOrLoad(Path ledgerPath, String venue, String accountId, BigDecimal realBalance) {
        Objects.requireNonNull(ledgerPath, "ledgerPath is required");
        Objects.requireNonNull(venue, "venue is required");
        Objects.requireNonNull(accountId, "accountId is required");
        Objects.requireNonNull(realBalance, "realBalance is required");
        Path lockPath = AccountLedgerStore.lockPathFor(ledgerPath);
        try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, DEFAULT_STALE_THRESHOLD, DEFAULT_RETRY_BUDGET)) {
            AccountLedger ledger = AccountLedgerStore.load(ledgerPath, venue, accountId, realBalance, lock);
            AccountLedgerStore.persist(ledgerPath, ledger, lock);
            // Deliberately omits ledgerPath/accountId -- both embed or are a
            // real KIS account identifier, and CLAUDE.md's Non-negotiable
            // Rules forbid raw account identifiers in logs. venue alone is
            // not account-identifying.
            log.info(
                    "shared account ledger ready for venue={}: allocatedVirtualCapital={} existingReservations={}",
                    venue,
                    ledger.allocatedVirtualCapital(),
                    ledger.reservations().size());
            return new SharedKisAccountLedger(ledgerPath, venue, accountId, ledger.allocatedVirtualCapital());
        }
    }

    @Override
    public AccountState reserveForIntent(OrderIntent intent, BigDecimal referencePrice) {
        Objects.requireNonNull(intent, "intent is required");
        Objects.requireNonNull(referencePrice, "referencePrice is required");
        Path lockPath = AccountLedgerStore.lockPathFor(ledgerPath);
        try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, staleThreshold, retryBudget)) {
            AccountLedger ledger = AccountLedgerStore.load(ledgerPath, venue, accountId, defaultAllocatedCapital, lock);
            if (ledger.reconciliationAlarmTrippedAt() != null) {
                // venue/accountId deliberately omitted -- see class Javadoc's account-identifier logging note.
                log.warn(
                        "reserveForIntent: reconciliation alarm is tripped (reason={}) -- returning a floored"
                                + " near-zero-equity snapshot without creating a reservation for intent {}",
                        ledger.reconciliationAlarmReason(),
                        intent.intentId());
                return flooredNearZeroEquitySnapshot(ledger);
            }

            BigDecimal price = effectivePrice(intent, referencePrice);
            if (price.signum() <= 0) {
                return accountStateFor(ledger);
            }

            BigDecimal notional = intent.quantity().multiply(price);
            LedgerReservation candidate = new LedgerReservation(
                    intent.intentId(), intent.symbol(), ProcessHandle.current().pid(), HOSTNAME, notional, Instant.now());
            List<LedgerReservation> updatedReservations = reserveMonotonic(ledger.reservations(), candidate);
            BigDecimal hypotheticalTotal = totalNotional(updatedReservations);
            if (hypotheticalTotal.compareTo(ledger.allocatedVirtualCapital()) > 0) {
                // Real safety property, not merely defensive: the lock alone
                // only prevents a LOST update (two concurrent callers
                // clobbering each other's write) -- it does not by itself
                // prevent the SUM of sequentially-persisted pessimistic
                // reservations from exceeding allocatedVirtualCapital, since
                // each reserveForIntent call is its own independent critical
                // section and a pessimistic reservation is deliberately
                // sized to the full pre-clamp intent.quantity() (see
                // AccountStateProvider's own Javadoc), not to whatever
                // capital happens to remain. Declining to persist this
                // candidate entirely -- rather than persisting it and
                // relying solely on the equity floor below to eventually
                // get corrected via confirmReservation/releaseReservation --
                // is what actually keeps the ON-DISK committed total within
                // allocatedVirtualCapital at every persisted state, not just
                // at quiescence. Matches the existing alarm-tripped/non-
                // positive-price cases above: no reservation, a floored
                // near-zero snapshot, RiskGateway's own unmodified clamped-
                // quantity-rounds-to-zero path does the actual rejecting.
                log.warn(
                        "reserveForIntent: declining to reserve for intent {} -- hypothetical combined committed"
                                + " notional {} would exceed allocatedVirtualCapital {}; returning a floored"
                                + " near-zero-equity snapshot instead of persisting an over-budget reservation",
                        intent.intentId(),
                        hypotheticalTotal,
                        ledger.allocatedVirtualCapital());
                return flooredNearZeroEquitySnapshot(ledger);
            }
            AccountLedger updated = withReservations(ledger, updatedReservations);
            AccountLedgerStore.persist(ledgerPath, updated, lock);
            return accountStateFor(updated);
        }
    }

    @Override
    public void confirmReservation(UUID intentId, BigDecimal approvedQuantity, BigDecimal price) {
        Objects.requireNonNull(intentId, "intentId is required");
        Objects.requireNonNull(approvedQuantity, "approvedQuantity is required");
        Objects.requireNonNull(price, "price is required");
        Path lockPath = AccountLedgerStore.lockPathFor(ledgerPath);
        try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, staleThreshold, retryBudget)) {
            AccountLedger ledger = AccountLedgerStore.load(ledgerPath, venue, accountId, defaultAllocatedCapital, lock);
            BigDecimal approvedNotional = approvedQuantity.multiply(price);
            List<LedgerReservation> updatedReservations = new ArrayList<>();
            boolean found = false;
            for (LedgerReservation r : ledger.reservations()) {
                if (r.clientOrderId().equals(intentId)) {
                    found = true;
                    if (approvedNotional.signum() > 0) {
                        updatedReservations.add(new LedgerReservation(
                                r.clientOrderId(), r.symbol(), r.processId(), r.hostname(), approvedNotional, r.reservedAt()));
                    }
                    // else: drop the reservation entirely -- LedgerReservation requires notional > 0,
                    // and a zero/negative approved notional means nothing to keep committed.
                } else {
                    updatedReservations.add(r);
                }
            }
            if (!found) {
                // venue/accountId deliberately omitted -- see class Javadoc's account-identifier logging note.
                log.warn(
                        "confirmReservation: no matching reservation found for intentId {} -- nothing to confirm"
                                + " (expected only if reserveForIntent's own reservation was skipped, e.g. a"
                                + " tripped reconciliation alarm, a non-positive effective price, or an over-budget"
                                + " decline)",
                        intentId);
                return;
            }
            AccountLedger updated = withReservations(ledger, updatedReservations);
            AccountLedgerStore.persist(ledgerPath, updated, lock);
        }
    }

    @Override
    public void releaseReservation(UUID intentId) {
        Objects.requireNonNull(intentId, "intentId is required");
        Path lockPath = AccountLedgerStore.lockPathFor(ledgerPath);
        try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, staleThreshold, retryBudget)) {
            AccountLedger ledger = AccountLedgerStore.load(ledgerPath, venue, accountId, defaultAllocatedCapital, lock);
            boolean present = ledger.reservations().stream().anyMatch(r -> r.clientOrderId().equals(intentId));
            if (!present) {
                // venue/accountId deliberately omitted -- see class Javadoc's account-identifier logging note.
                log.debug("releaseReservation: no matching reservation found for intentId {} -- nothing to release", intentId);
                return;
            }
            List<LedgerReservation> updatedReservations =
                    ledger.reservations().stream().filter(r -> !r.clientOrderId().equals(intentId)).toList();
            AccountLedger updated = withReservations(ledger, updatedReservations);
            AccountLedgerStore.persist(ledgerPath, updated, lock);
        }
    }

    /** Mirrors {@link engine.risk.RiskGateway#evaluate}'s own price resolution -- see class Javadoc. */
    private static BigDecimal effectivePrice(OrderIntent intent, BigDecimal referencePrice) {
        return intent.limitPrice() != null ? intent.limitPrice() : referencePrice;
    }

    /**
     * The monotonic idempotency rule {@link AccountStateProvider}'s own
     * Javadoc requires of {@link #reserveForIntent}: a repeated call for
     * an {@code intentId} that already has a recorded reservation may only
     * leave its notional unchanged or raise it (to {@code
     * max(existing, candidate)}), never reduce it. Only {@link
     * #confirmReservation}/{@link #releaseReservation} may ever reduce
     * what is recorded for a given {@code intentId}.
     */
    private static List<LedgerReservation> reserveMonotonic(List<LedgerReservation> existing, LedgerReservation candidate) {
        List<LedgerReservation> result = new ArrayList<>();
        boolean found = false;
        for (LedgerReservation r : existing) {
            if (r.clientOrderId().equals(candidate.clientOrderId())) {
                found = true;
                result.add(candidate.notional().compareTo(r.notional()) > 0 ? candidate : r);
            } else {
                result.add(r);
            }
        }
        if (!found) {
            result.add(candidate);
        }
        return result;
    }

    private static AccountLedger withReservations(AccountLedger ledger, List<LedgerReservation> reservations) {
        return new AccountLedger(
                ledger.venue(),
                ledger.accountId(),
                ledger.allocatedVirtualCapital(),
                ledger.lastReconciledDailyPnlPercent(),
                ledger.lastReconciledWeeklyPnlPercent(),
                ledger.lastReconciledMonthlyPnlPercent(),
                ledger.lastReconciledAt(),
                ledger.reconciliationAlarmTrippedAt(),
                ledger.reconciliationAlarmReason(),
                reservations);
    }

    private static BigDecimal totalNotional(List<LedgerReservation> reservations) {
        return reservations.stream().map(LedgerReservation::notional).reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    private static BigDecimal computeEquity(AccountLedger ledger) {
        BigDecimal equity = ledger.allocatedVirtualCapital().subtract(totalNotional(ledger.reservations()));
        return equity.compareTo(MIN_EQUITY) > 0 ? equity : MIN_EQUITY;
    }

    private static AccountState accountStateFor(AccountLedger ledger) {
        return new AccountState(
                computeEquity(ledger),
                ledger.lastReconciledDailyPnlPercent(),
                ledger.lastReconciledWeeklyPnlPercent(),
                ledger.lastReconciledMonthlyPnlPercent());
    }

    private static AccountState flooredNearZeroEquitySnapshot(AccountLedger ledger) {
        return new AccountState(
                MIN_EQUITY,
                ledger.lastReconciledDailyPnlPercent(),
                ledger.lastReconciledWeeklyPnlPercent(),
                ledger.lastReconciledMonthlyPnlPercent());
    }

    /** Attribution/debugging only on {@link LedgerReservation}, matching {@link AccountLedgerLock}'s own identical convention -- resolved once, not a correctness input. */
    private static String resolveHostname() {
        try {
            return InetAddress.getLocalHost().getHostName();
        } catch (UnknownHostException e) {
            return "unknown-host";
        }
    }
}
