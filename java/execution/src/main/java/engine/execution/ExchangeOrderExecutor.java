package engine.execution;

import engine.exchange.ExchangeAdapter;
import engine.exchange.OrderStatus;
import engine.oms.Order;
import engine.oms.OrderState;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * {@link OrderExecutor} wrapping the venue-agnostic {@link ExchangeAdapter}
 * <b>interface</b> -- never a concrete adapter such as {@code BingXAdapter}.
 * This is the class named in {@link OrderExecutor}'s own Javadoc as "one
 * venue-agnostic wrapper around the {@code ExchangeAdapter} interface" --
 * a third exchange means a new {@code ExchangeAdapter} implementation, never
 * a new {@code OrderExecutor}, and this class is why: it works against
 * every current and future {@code ExchangeAdapter} unchanged. See
 * {@code .planning/paper-trading-g-exchange-order-executor.md} for the full
 * design writeup.
 *
 * <p><b>submit.</b> Delegates to {@link ExchangeAdapter#submitOrder}, which
 * mutates {@code order} itself ({@code submit()} then {@code acknowledge()}
 * or {@code reject()} -- {@code ExchangeAdapter}'s own general contract, not
 * BingX-specific). This class relies only on {@code order}'s resulting state
 * after that call, never on any transport-level detail. An order is tracked
 * as pending only if it comes back {@link OrderState#ACKNOWLEDGED} <b>and</b>
 * carries a non-null {@link Order#exchangeOrderId()} -- a submit that results
 * in {@code REJECTED} must never enter pending tracking, and polling an order
 * with no real exchange order id would send a malformed query. {@code submit}
 * always returns {@link Optional#empty()}, even for a venue that could
 * theoretically fill near-instantly -- fills only ever surface via {@link
 * #pollFills}, keeping the contract uniform and conservative across both
 * {@code OrderExecutor} implementations. If {@code adapter.submitOrder}
 * itself throws, this method does not catch or retry it -- per {@link
 * OrderExecutor}'s own "Error contract, asymmetric on purpose" Javadoc, a
 * thrown {@code submit} is an ambiguous outcome and {@code
 * engine.runtime.TradingLoop#submitToBroker} already handles that correctly
 * (logs the order as orphaned, rethrows).
 *
 * <p><b>pollFills.</b> Per pending order for {@code symbol}: {@link
 * ExchangeAdapter#queryOrder} is called fresh every poll (deliberately
 * outside any internal lock -- see "Atomicity and concurrency" below). This
 * class tracks each pending order's own cumulative filled quantity and
 * cumulative notional (both starting at zero when the order first becomes
 * pending), so a poll's own fill increment can be derived as {@code delta =
 * status.filledQuantity() - cumulativeQty} and this increment's own price as
 * {@code (newCumulativeNotional - previousCumulativeNotional) / delta} --
 * <b>never</b> {@code status.avgPrice()} reused directly, which is only
 * correct for an order's first fill. If {@code delta > 0} but {@code
 * status.avgPrice()} is null or non-positive, no price is fabricated: this
 * is logged at ERROR and the order is left pending, untouched, for a retry
 * next poll -- a genuinely transient-looking gap (the venue may simply not
 * have populated the field yet), not fatal corruption.
 *
 * <p><b>Data-integrity validation, routed through the state-corruption
 * path.</b> Some venue-reported combinations are not merely incomplete but
 * outright impossible under normal operation, and are treated differently
 * from the soft avgPrice-missing case above: they throw, which this class's
 * per-order failure containment (see below) turns into a dropped order
 * rather than a silent accounting error.
 * <ul>
 *   <li>A previously-recorded nonzero cumulative quantity regressing to
 *       {@code null} or to a smaller value on a later poll -- filled
 *       quantity must never decrease once real progress has been recorded.
 *       (A {@code null} {@code filledQuantity} on an order with zero
 *       recorded progress so far is treated as "no evidence of any fill
 *       yet," equivalent to zero -- not itself an error, since a fresh
 *       order's first poll(s) legitimately have nothing to report.)
 *   <li>A positive filled-quantity delta whose corresponding notional delta
 *       is not strictly positive -- a real cumulative {@code avgPrice} can
 *       never make total notional fail to increase as quantity increases,
 *       so this combination is internally inconsistent.
 *   <li>A {@code FILLED} status arriving while {@link Order#state()} did
 *       not actually reach {@link OrderState#FILLED} after this poll's own
 *       delta was applied (e.g. the venue's own reported quantity never
 *       matches this project's {@code approvedQuantity}) -- the status
 *       <i>string</i> alone is never trusted to discard pending tracking.
 * </ul>
 * A fill legitimately produced earlier in the same {@code pollFills} call
 * (i.e. {@code order.fill(delta)} already succeeded before a downstream
 * status-mapping validation above throws) is still surfaced to the caller --
 * see "Per-order failure containment" below for why discarding it would be
 * a real accounting gap, not just a missed edge case.
 *
 * <p><b>Status-mapping table</b> (BingX's real status tokens where known --
 * see CLAUDE.md's "Exchange API Facts -- BingX" for the REST/WebSocket
 * casing inconsistency this class deliberately tolerates by checking both
 * spellings):
 *
 * <ul>
 *   <li>{@code NEW}/{@code PENDING} -- stays pending, no further action.
 *   <li>{@code PARTIALLY_FILLED} -- delta applied if any, stays pending.
 *   <li>{@code FILLED} -- delta applied; removed from pending only if
 *       {@link Order#state()} is actually {@link OrderState#FILLED} (see
 *       "Data-integrity validation" above).
 *   <li>{@code CANCELLED}/{@code CANCELED} -- delta applied first if any,
 *       then the cancel path ({@code requestCancel()}/{@code
 *       confirmCancel()}), removed from pending.
 *   <li>{@code EXPIRED} while {@link Order#state()} is exactly {@code
 *       ACKNOWLEDGED} -- the real {@code expire()} transition, removed from
 *       pending.
 *   <li>{@code EXPIRED} or {@code REJECTED}/{@code FAILED} arriving when
 *       {@code expire()}/{@code reject()} are not legal from the order's
 *       current state (i.e. it already has a partial fill, so it is {@code
 *       PARTIALLY_FILLED}, not {@code ACKNOWLEDGED}/{@code SUBMITTED}) --
 *       approximated via the cancel path instead, logged loudly as a
 *       deliberate, disclosed simplification. {@code Order.expire()}
 *       requires exactly {@code ACKNOWLEDGED} and {@code Order.reject()}
 *       requires exactly {@code SUBMITTED}; a pending order tracked by this
 *       class is always at least {@code ACKNOWLEDGED} (see the {@code
 *       submit} guard above), so {@code reject()} is in fact never legal
 *       for any order this method is ever called with -- the cancel-path
 *       approximation is the only reachable outcome for {@code
 *       REJECTED}/{@code FAILED}, not merely a fallback. Widening the OMS
 *       state machine to add real transitions for these cases is explicitly
 *       out of scope here -- see the planning doc.
 *   <li>Any unrecognized status -- stays pending, logged at WARN, never
 *       guessed.
 * </ul>
 *
 * <p><b>Per-order failure containment</b>, matching {@link OrderExecutor}'s
 * own "never throw for a per-order resolution failure" contract: a thrown
 * {@code queryOrder} is treated as transient-looking -- logged, the order
 * stays pending for a retry next poll. A thrown {@code order.fill(...)}
 * (e.g. an unexpected overfill from bad venue data) or a thrown
 * data-integrity validation (see above) is treated as state corruption --
 * logged, the order is dropped from pending tracking rather than retried
 * forever, which makes it {@code ORPHANED_IN_BROKER} from {@code
 * engine.runtime.Reconciler}'s perspective on the next reconciliation pass
 * (the correct, already-existing safety net) if {@code OrderStore} still
 * considers it open. Critically, a fill that was legitimately produced
 * earlier in the same resolution (i.e. {@code order.fill(delta)} already
 * succeeded) is still returned to the caller even when a later
 * status-mapping validation flags the same order for removal -- the two
 * are different concerns ("did money/quantity legitimately move, and must
 * it be reported" vs. "should this order keep being polled"), and
 * discarding an already-real fill just because a later check also found a
 * problem would silently under-count equity/fill history. {@link
 * #pollFills} itself never throws.
 *
 * <p><b>Atomicity and concurrency.</b> Every pending order's own mutable
 * bookkeeping (the {@link Order} reference, cumulative filled quantity, and
 * cumulative notional) lives in one {@code PendingOrderState} object per
 * order id, stored in a single {@code ConcurrentHashMap}. {@link
 * #pollFills} resolves and mutates a given order's state via {@code
 * computeIfPresent}, which serializes every invocation for the same key --
 * concurrent {@code pollFills}/{@code cancel} calls racing on the same
 * pending order can no longer interleave or double-apply the same fill
 * delta (mirrors {@link PaperBroker#pollFills}'s own {@code
 * computeIfPresent} pattern and its identical reasoning). {@link
 * ExchangeAdapter#queryOrder} itself -- a real network call in production --
 * is deliberately performed <b>outside</b> that atomic block: holding a
 * {@code ConcurrentHashMap} bin lock across blocking I/O would be its own
 * hazard, and it is unnecessary for correctness -- the fill-delta
 * computation inside the atomic block always reads the map's own
 * <i>current</i> cumulative state at the moment it actually runs, not a
 * value captured before the lock was acquired, so a second, slightly-stale
 * concurrent poll for the same order simply computes a smaller (often zero)
 * delta against the already-updated state rather than double-crediting it.
 *
 * <p><b>Fee is modeled, not exchange-reported</b> -- {@link OrderStatus}
 * carries no commission field. This class computes a fee from this
 * increment's own notional using the {@code feeBps} given to its
 * constructor, matching {@link PaperBroker}'s own fee model exactly (same
 * formula, same rounding) so the two loops' daily-report equity series stay
 * comparable. This is an approximation, not a claim that it matches what
 * the venue actually charges -- see the planning doc for the plan to
 * capture and evaluate a real commission field in a later task.
 *
 * <p><b>Ambiguous-submit ({@code SUBMISSION_UNKNOWN}) handling is composed
 * in via {@link SubmissionListener}, not built directly into this class.</b>
 * Originally (Paper Trading Bridge Task H, first pass) this was deliberately
 * left entirely unbuilt here, with a separate decorator (`engine.runtime.
 * PersistentSubmissionOrderExecutor`) wrapping this class from the outside
 * to add durable marker recording. A real CodeRabbit review finding on that
 * same task correctly caught that the decorator was a **third** class
 * implementing {@link OrderExecutor}, against this interface's own
 * documented "exactly two implementations" invariant. The corrected design:
 * {@link #submit} calls {@link SubmissionListener#beforeSubmit} immediately
 * before {@link ExchangeAdapter#submitOrder}, and {@link
 * SubmissionListener#afterSubmitSucceeded} immediately after it returns
 * normally (never if it throws) -- see that interface's own Javadoc for the
 * precise contract. The default ({@code SubmissionListener.NO_OP}, via the
 * 2-arg constructor) makes this exactly as inert as before this feature
 * existed; a real, durable listener (`engine.runtime.
 * MarkerRecordingSubmissionListener`, backed by a JSON marker file) is
 * injected only in {@code bingx-vst} mode. Independent of this composition
 * point: an ambiguous submit is still caught by the existing, already-tested
 * reactive mechanism {@link OrderExecutor}'s own Javadoc documents: an order
 * that throws out of {@code submit} never reaches {@link #pendingOrders()},
 * which {@code Reconciler#check} flags as {@code ORPHANED_IN_BROKER} and
 * {@code engine.runtime.PaperTradingApp#reconcile()} trips the kill switch
 * on. {@code SubmissionListener} is the *proactive* complement to that
 * reactive backstop, not a replacement for it.
 */
public final class ExchangeOrderExecutor implements OrderExecutor {

    private static final Logger log = LoggerFactory.getLogger(ExchangeOrderExecutor.class);
    private static final BigDecimal BPS_DIVISOR = new BigDecimal("10000");

    /**
     * Scale/rounding used to derive an increment's own price from a
     * notional delta. No existing "price scale" convention exists elsewhere
     * in this codebase to reuse directly -- the closest precedent is
     * {@code RiskGateway.QUANTITY_SCALE=8} (used for a *quantity* division,
     * not price) and {@code TradingLoop}/{@code DailyReportGenerator}'s
     * shared use of {@code RoundingMode.HALF_UP} for their own money-math
     * divisions. {@code PRICE_SCALE=8} with {@code HALF_UP} matches both of
     * those precedents as closely as the codebase's own conventions allow.
     * A judgment call, documented in the planning doc rather than picked
     * silently.
     */
    private static final int PRICE_SCALE = 8;

    private static final Set<String> OPEN_STATUSES = Set.of("NEW", "PENDING");
    private static final Set<String> CANCELLED_STATUSES = Set.of("CANCELLED", "CANCELED");

    private final ExchangeAdapter adapter;
    private final BigDecimal feeBps;
    private final SubmissionListener submissionListener;

    private final Map<UUID, PendingOrderState> pendingOrders = new ConcurrentHashMap<>();

    public ExchangeOrderExecutor(ExchangeAdapter adapter, BigDecimal feeBps) {
        this(adapter, feeBps, SubmissionListener.NO_OP);
    }

    /**
     * Same as the 2-arg constructor, with an explicit {@link
     * SubmissionListener} -- see that interface's own Javadoc for the
     * full contract and why it exists (durable {@code SUBMISSION_UNKNOWN}
     * marking, composed in rather than wrapped around this class as a
     * competing {@code OrderExecutor} implementation).
     */
    public ExchangeOrderExecutor(ExchangeAdapter adapter, BigDecimal feeBps, SubmissionListener submissionListener) {
        this.adapter = Objects.requireNonNull(adapter, "adapter is required");
        this.feeBps = Objects.requireNonNull(feeBps, "feeBps is required");
        this.submissionListener = Objects.requireNonNull(submissionListener, "submissionListener is required");
        if (feeBps.signum() < 0) {
            throw new IllegalArgumentException("feeBps must not be negative, was " + feeBps);
        }
    }

    @Override
    public Map<UUID, Order> pendingOrders() {
        Map<UUID, Order> snapshot = new LinkedHashMap<>();
        pendingOrders.forEach((id, state) -> snapshot.put(id, state.order));
        return Collections.unmodifiableMap(snapshot);
    }

    @Override
    public Optional<Fill> submit(Order order, BigDecimal referencePrice) {
        Objects.requireNonNull(order, "order is required");
        Objects.requireNonNull(referencePrice, "referencePrice is required");
        requirePositivePrice(referencePrice);

        // See SubmissionListener's own Javadoc for the precise contract:
        // beforeSubmit always runs; afterSubmitSucceeded runs only if
        // adapter.submitOrder returns normally (does not throw) -- a
        // thrown submitOrder leaves the ambiguity beforeSubmit recorded,
        // deliberately unresolved.
        submissionListener.beforeSubmit(order);
        // May throw -- deliberately not caught here, see class Javadoc.
        adapter.submitOrder(order);
        submissionListener.afterSubmitSucceeded(order);

        if (order.state() == OrderState.ACKNOWLEDGED && order.exchangeOrderId() != null) {
            // One fully-constructed object published atomically -- no
            // window where pendingOrders() could observe this order before
            // its cumulative bookkeeping exists.
            pendingOrders.put(order.clientOrderId(), new PendingOrderState(order));
        }
        return Optional.empty();
    }

    @Override
    public List<Fill> pollFills(String symbol, BigDecimal referencePrice) {
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(referencePrice, "referencePrice is required");
        requirePositivePrice(referencePrice);

        List<Fill> fills = new ArrayList<>();
        for (UUID id : new ArrayList<>(pendingOrders.keySet())) {
            PendingOrderState snapshot = pendingOrders.get(id);
            if (snapshot == null || !snapshot.order.symbol().equals(symbol)) {
                continue;
            }

            OrderStatus status;
            try {
                // Deliberately outside the atomic computeIfPresent block
                // below -- see class Javadoc, "Atomicity and concurrency".
                status = adapter.queryOrder(snapshot.order);
            } catch (RuntimeException e) {
                log.error(
                        "order {} queryOrder failed during pollFills -- leaving pending, will retry next poll: {}",
                        id,
                        e.toString());
                continue;
            }

            List<Fill> producedThisOrder = new ArrayList<>(1);
            try {
                pendingOrders.computeIfPresent(id, (orderId, current) -> {
                    ResolveResult result = resolveOne(current, status);
                    if (result.fill() != null) {
                        producedThisOrder.add(result.fill());
                    }
                    return result.removeFromPending() ? null : current;
                });
            } catch (RuntimeException e) {
                // Only a thrown order.fill() (a state-transition rejection
                // with nothing yet to preserve) reaches here -- see class
                // Javadoc, "Per-order failure containment": a status-mapping
                // validation failure is instead caught inside resolveOne
                // itself so an already-legitimate fill is never discarded.
                log.error(
                        "order {} failed to resolve during pollFills and is being dropped from pending tracking"
                                + " -- see this class's own Javadoc for what happens next: {}",
                        id,
                        e.toString());
                pendingOrders.remove(id);
            }
            fills.addAll(producedThisOrder);
        }
        return fills;
    }

    @Override
    public void cancel(Order order) {
        Objects.requireNonNull(order, "order is required");
        // May throw -- a BingX-rejected cancel leaves the order in
        // CANCEL_PENDING (requestCancel() already ran inside
        // adapter.cancelOrder, confirmCancel() never did) and this class
        // deliberately does NOT remove it from pending tracking in that
        // case, so a later pollFills can still resolve it (Order.CAN_FILL
        // includes CANCEL_PENDING -- see class Javadoc).
        adapter.cancelOrder(order);
        pendingOrders.remove(order.clientOrderId());
    }

    /**
     * Resolves one pending order against a freshly-queried {@link
     * OrderStatus}: validates and applies any new fill delta, then applies
     * the status-mapping table. Returns the {@link Fill} produced this call
     * (or {@code null} if none was produced) together with whether the
     * order should be removed from pending tracking. May throw -- but only
     * for a failure with nothing yet to preserve (the delta/notional
     * validations below, or {@code order.fill(...)} itself, all run before
     * any fill is ever computed); a validation failure inside the
     * status-mapping stage is instead caught locally so an already-real
     * fill is still returned -- see class Javadoc, "Per-order failure
     * containment".
     */
    private ResolveResult resolveOne(PendingOrderState state, OrderStatus status) {
        Order order = state.order;
        UUID id = order.clientOrderId();
        BigDecimal previousQty = state.cumulativeFilledQty;
        BigDecimal previousNotional = state.cumulativeNotional;
        BigDecimal reportedQty = status.filledQuantity();

        BigDecimal delta;
        if (reportedQty == null) {
            if (previousQty.signum() > 0) {
                throw new IllegalStateException(
                        "order " + id + " previously recorded filledQuantity=" + previousQty + " but this"
                                + " poll reports a null filledQuantity -- filled quantity must never regress"
                                + " to unknown; corrupt/inconsistent venue data");
            }
            delta = BigDecimal.ZERO; // no evidence of any fill yet -- treated as absent, not fabricated
        } else {
            delta = reportedQty.subtract(previousQty);
            if (delta.signum() < 0) {
                throw new IllegalStateException(
                        "order " + id + " reports filledQuantity " + reportedQty + " which is less than"
                                + " the " + previousQty + " already recorded -- filled quantity must never"
                                + " decrease; corrupt/inconsistent venue data");
            }
        }

        Fill fill = null;
        if (delta.signum() > 0) {
            BigDecimal avgPrice = status.avgPrice();
            if (avgPrice == null || avgPrice.signum() <= 0) {
                log.error(
                        "order {} reports filledQuantity delta {} (cumulative {} -> {}) but avgPrice is {} --"
                                + " refusing to fabricate a fill price; leaving order pending for a retry"
                                + " next poll",
                        id,
                        delta,
                        previousQty,
                        reportedQty,
                        avgPrice);
                // Deliberately skip status-mapping too this poll -- nothing
                // about this order's own tracked state changes; the next
                // poll re-derives delta from the same previousQty.
                return new ResolveResult(null, false);
            }

            BigDecimal newNotional = reportedQty.multiply(avgPrice);
            BigDecimal incrementNotional = newNotional.subtract(previousNotional);
            if (incrementNotional.signum() <= 0) {
                throw new IllegalStateException(
                        "order " + id + " reports a non-positive increment notional (" + incrementNotional
                                + ") for a positive filled-quantity delta (" + delta + ") -- a real"
                                + " cumulative avgPrice can never make total notional fail to strictly"
                                + " increase as quantity increases; corrupt/inconsistent venue data");
            }
            BigDecimal incrementPrice = incrementNotional.divide(delta, PRICE_SCALE, RoundingMode.HALF_UP);
            BigDecimal fee = incrementNotional.multiply(feeBps).divide(BPS_DIVISOR);

            order.fill(delta); // may throw -- nothing computed above is preserved yet if it does

            state.cumulativeFilledQty = reportedQty;
            state.cumulativeNotional = newNotional;
            fill = new Fill(id, order.symbol(), incrementPrice, delta, incrementNotional, fee, Instant.now());
        }

        boolean remove;
        try {
            remove = applyStatusMapping(order, status.status());
        } catch (RuntimeException e) {
            log.error(
                    "order {} status-mapping validation failed after this poll's own fill (if any) was"
                            + " already applied -- that fill is still real and reported, but the order is"
                            + " being dropped from pending tracking: {}",
                    id,
                    e.toString());
            remove = true;
        }
        return new ResolveResult(fill, remove);
    }

    /** Returns {@code true} if {@code order} should be removed from pending tracking. May throw -- see {@link #resolveOne}. */
    private boolean applyStatusMapping(Order order, String status) {
        UUID id = order.clientOrderId();

        if (OPEN_STATUSES.contains(status) || "PARTIALLY_FILLED".equals(status)) {
            return false;
        }
        if ("FILLED".equals(status)) {
            if (order.state() != OrderState.FILLED) {
                throw new IllegalStateException(
                        "order " + id + " exchange status reports FILLED but our own state is "
                                + order.state() + " -- a fill delta didn't complete it (a real quantity"
                                + " mismatch between the venue's own order and this project's"
                                + " approvedQuantity); the status string alone is never trusted to discard"
                                + " pending tracking");
            }
            return true;
        }
        if (CANCELLED_STATUSES.contains(status)) {
            approximateCancel(order);
            return true;
        }
        if ("EXPIRED".equals(status)) {
            if (order.state() == OrderState.ACKNOWLEDGED) {
                order.expire();
            } else {
                log.error(
                        "order {} reports EXPIRED but is in state {} (Order.expire() requires exactly"
                                + " ACKNOWLEDGED) -- approximating via the cancel path instead; a"
                                + " deliberate, disclosed simplification, see this class's own Javadoc",
                        id,
                        order.state());
                approximateCancel(order);
            }
            return true;
        }
        if ("REJECTED".equals(status) || "FAILED".equals(status)) {
            // Order.reject() requires exactly SUBMITTED -- a pending order
            // tracked by this class is always at least ACKNOWLEDGED (see
            // the submit() guard), so that precondition can never legally
            // hold here. Always approximate via the cancel path, loudly.
            log.error(
                    "order {} reports {} while pending (state {}) -- Order.reject() requires exactly"
                            + " SUBMITTED, which a pending order can never legally be in here;"
                            + " approximating via the cancel path instead; a deliberate, disclosed"
                            + " simplification, see this class's own Javadoc",
                    id,
                    status,
                    order.state());
            approximateCancel(order);
            return true;
        }
        log.warn("order {} reports unrecognized status '{}' -- leaving pending, never guessing", id, status);
        return false;
    }

    /**
     * {@code requestCancel()} then {@code confirmCancel()} -- except when
     * {@code order} is already {@code CANCEL_PENDING} (a previous {@link
     * #cancel} call's {@code adapter.cancelOrder} threw after {@code
     * requestCancel()} already ran but before {@code confirmCancel()} did,
     * see {@link #cancel}'s own Javadoc), in which case only {@code
     * confirmCancel()} runs -- calling {@code requestCancel()} again there
     * would throw, since {@code CANCEL_PENDING} is not in {@code Order}'s
     * own {@code CAN_REQUEST_CANCEL} set.
     */
    private void approximateCancel(Order order) {
        if (order.state() != OrderState.CANCEL_PENDING) {
            order.requestCancel();
        }
        order.confirmCancel();
    }

    private static void requirePositivePrice(BigDecimal price) {
        if (price.signum() <= 0) {
            throw new IllegalArgumentException("referencePrice must be positive, was " + price);
        }
    }

    /**
     * One pending order's own mutable bookkeeping, all in a single object
     * so it can be registered/read/updated/removed atomically per order id
     * -- see class Javadoc, "Atomicity and concurrency". Mutated only from
     * within a {@code pendingOrders.computeIfPresent} callback.
     */
    private static final class PendingOrderState {
        private final Order order;
        private BigDecimal cumulativeFilledQty = BigDecimal.ZERO;
        private BigDecimal cumulativeNotional = BigDecimal.ZERO;

        private PendingOrderState(Order order) {
            this.order = order;
        }
    }

    private record ResolveResult(Fill fill, boolean removeFromPending) {}
}
