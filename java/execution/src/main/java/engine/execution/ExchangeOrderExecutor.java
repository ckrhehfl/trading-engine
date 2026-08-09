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
 * ExchangeAdapter#queryOrder} is called fresh every poll. This class tracks
 * each pending order's own cumulative filled quantity and cumulative
 * notional (both starting at zero when the order first becomes pending), so
 * a poll's own fill increment can be derived as {@code delta =
 * status.filledQuantity() - cumulativeQty} and this increment's own price as
 * {@code (newCumulativeNotional - previousCumulativeNotional) / delta} --
 * <b>never</b> {@code status.avgPrice()} reused directly, which is only
 * correct for an order's first fill. If {@code delta > 0} but {@code
 * status.avgPrice()} is null or non-positive, no price is fabricated: this
 * is logged at ERROR and the order is left pending, untouched, for a retry
 * next poll -- a real data-integrity guard, not paranoia.
 *
 * <p><b>Status-mapping table</b> (BingX's real status tokens where known --
 * see CLAUDE.md's "Exchange API Facts -- BingX" for the REST/WebSocket
 * casing inconsistency this class deliberately tolerates by checking both
 * spellings):
 *
 * <ul>
 *   <li>{@code NEW}/{@code PENDING} -- stays pending, no further action.
 *   <li>{@code PARTIALLY_FILLED} -- delta applied if any, stays pending.
 *   <li>{@code FILLED} -- delta applied, removed from pending.
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
 * <p>Every per-order failure is caught and contained, matching {@link
 * OrderExecutor}'s own "never throw for a per-order resolution failure"
 * contract: a thrown {@code queryOrder} is treated as transient-looking --
 * logged, the order stays pending for a retry next poll. A thrown {@code
 * order.fill(...)} (e.g. an unexpected overfill from bad venue data) or any
 * other state-transition failure is treated as state corruption -- logged,
 * the order is dropped from pending tracking rather than retried forever,
 * which makes it {@code ORPHANED_IN_BROKER} from {@code
 * engine.runtime.Reconciler}'s perspective on the next reconciliation pass
 * (the correct, already-existing safety net). {@link #pollFills} itself
 * never throws.
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
 * <p><b>Ambiguous-submit ({@code SUBMISSION_UNKNOWN}) handling is
 * deliberately NOT built here.</b> A persisted, restart-surviving marker for
 * an ambiguous {@code submit} outcome, and the startup/kill-switch-reset
 * logic to resolve one before any retry, is wiring/safety-layer design --
 * see the planning doc's own section on this judgment call for the full
 * reasoning. Today, an ambiguous submit is still caught by the existing,
 * already-tested mechanism {@link OrderExecutor}'s own Javadoc documents: an
 * order that throws out of {@code submit} never reaches {@link
 * #pendingOrders()}, which {@code Reconciler#check} flags as {@code
 * ORPHANED_IN_BROKER} and {@code engine.runtime.PaperTradingApp#reconcile()}
 * trips the kill switch on.
 */
public final class ExchangeOrderExecutor implements OrderExecutor {

    private static final Logger log = LoggerFactory.getLogger(ExchangeOrderExecutor.class);
    private static final BigDecimal BPS_DIVISOR = new BigDecimal("10000");

    /**
     * Scale/rounding used to derive an increment's own price from a
     * notional delta. No existing "price scale" convention exists elsewhere
     * in this codebase to reuse (only a quantity scale, {@code
     * RiskGateway.QUANTITY_SCALE=8}) -- 8 decimal places with {@code
     * HALF_UP} matches that quantity scale and this codebase's other money-
     * math rounding mode ({@code TradingLoop}/{@code DailyReportGenerator}
     * both use {@code HALF_UP}). A judgment call, documented in the
     * planning doc rather than silently picked.
     */
    private static final int PRICE_SCALE = 8;

    private static final Set<String> OPEN_STATUSES = Set.of("NEW", "PENDING");
    private static final Set<String> CANCELLED_STATUSES = Set.of("CANCELLED", "CANCELED");

    private final ExchangeAdapter adapter;
    private final BigDecimal feeBps;

    private final Map<UUID, Order> pendingOrders = new ConcurrentHashMap<>();
    private final Map<UUID, BigDecimal> cumulativeFilledQty = new ConcurrentHashMap<>();
    private final Map<UUID, BigDecimal> cumulativeNotional = new ConcurrentHashMap<>();

    public ExchangeOrderExecutor(ExchangeAdapter adapter, BigDecimal feeBps) {
        this.adapter = Objects.requireNonNull(adapter, "adapter is required");
        this.feeBps = Objects.requireNonNull(feeBps, "feeBps is required");
        if (feeBps.signum() < 0) {
            throw new IllegalArgumentException("feeBps must not be negative, was " + feeBps);
        }
    }

    @Override
    public Map<UUID, Order> pendingOrders() {
        return Collections.unmodifiableMap(pendingOrders);
    }

    @Override
    public Optional<Fill> submit(Order order, BigDecimal referencePrice) {
        Objects.requireNonNull(order, "order is required");
        Objects.requireNonNull(referencePrice, "referencePrice is required");
        requirePositivePrice(referencePrice);

        // May throw -- deliberately not caught here, see class Javadoc.
        adapter.submitOrder(order);

        if (order.state() == OrderState.ACKNOWLEDGED && order.exchangeOrderId() != null) {
            UUID id = order.clientOrderId();
            pendingOrders.put(id, order);
            cumulativeFilledQty.put(id, BigDecimal.ZERO);
            cumulativeNotional.put(id, BigDecimal.ZERO);
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
            Order order = pendingOrders.get(id);
            if (order == null || !order.symbol().equals(symbol)) {
                continue;
            }

            OrderStatus status;
            try {
                status = adapter.queryOrder(order);
            } catch (RuntimeException e) {
                log.error(
                        "order {} queryOrder failed during pollFills -- leaving pending, will retry next poll: {}",
                        id,
                        e.toString());
                continue;
            }

            try {
                Fill fill = resolveOne(order, status);
                if (fill != null) {
                    fills.add(fill);
                }
            } catch (RuntimeException e) {
                log.error(
                        "order {} failed to resolve during pollFills and is being dropped from pending tracking"
                                + " -- see this class's own Javadoc for what happens next: {}",
                        id,
                        e.toString());
                removeFromPendingTracking(id);
            }
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
        removeFromPendingTracking(order.clientOrderId());
    }

    /**
     * Resolves one pending order against a freshly-queried {@link
     * OrderStatus}: applies any new fill delta, then applies the status-
     * mapping table. Returns the {@link Fill} produced this call, or {@code
     * null} if none was produced. May throw -- see this class's own Javadoc
     * and {@link #pollFills}'s two-stage try/catch, which decides what
     * happens to the order on a thrown {@link RuntimeException} here (always
     * "drop from pending," since a failure at this stage means a state-
     * transition method rejected the venue's own reported data, not a
     * transient query failure).
     */
    private Fill resolveOne(Order order, OrderStatus status) {
        UUID id = order.clientOrderId();
        BigDecimal previousQty = cumulativeFilledQty.getOrDefault(id, BigDecimal.ZERO);
        BigDecimal reportedQty = status.filledQuantity();
        BigDecimal delta = reportedQty == null ? BigDecimal.ZERO : reportedQty.subtract(previousQty);

        Fill fill = null;
        if (delta.signum() > 0) {
            BigDecimal avgPrice = status.avgPrice();
            if (avgPrice == null || avgPrice.signum() <= 0) {
                log.error(
                        "order {} reports filledQuantity delta {} (cumulative {} -> {}) but avgPrice is {} --"
                                + " refusing to fabricate a fill price; leaving order pending for a retry next"
                                + " poll",
                        id,
                        delta,
                        previousQty,
                        reportedQty,
                        avgPrice);
                // Deliberately skip status-mapping too this poll -- see
                // class Javadoc. Nothing about this order's own tracked
                // state changes; the next poll re-derives delta from the
                // same previousQty.
                return null;
            }

            BigDecimal previousNotional = cumulativeNotional.getOrDefault(id, BigDecimal.ZERO);
            BigDecimal newNotional = reportedQty.multiply(avgPrice);
            BigDecimal incrementNotional = newNotional.subtract(previousNotional);
            BigDecimal incrementPrice = incrementNotional.divide(delta, PRICE_SCALE, RoundingMode.HALF_UP);
            BigDecimal fee = incrementNotional.multiply(feeBps).divide(BPS_DIVISOR);

            order.fill(delta); // may throw -- see method Javadoc

            cumulativeFilledQty.put(id, reportedQty);
            cumulativeNotional.put(id, newNotional);
            fill = new Fill(id, order.symbol(), incrementPrice, delta, incrementNotional, fee, Instant.now());
        }

        applyStatusMapping(order, status.status());
        return fill;
    }

    private void applyStatusMapping(Order order, String status) {
        UUID id = order.clientOrderId();

        if (OPEN_STATUSES.contains(status) || "PARTIALLY_FILLED".equals(status)) {
            return; // stays pending, nothing else to do this poll
        }
        if ("FILLED".equals(status)) {
            removeFromPendingTracking(id);
            return;
        }
        if (CANCELLED_STATUSES.contains(status)) {
            approximateCancel(order);
            removeFromPendingTracking(id);
            return;
        }
        if ("EXPIRED".equals(status)) {
            if (order.state() == OrderState.ACKNOWLEDGED) {
                order.expire();
            } else {
                log.error(
                        "order {} reports EXPIRED but is in state {} (Order.expire() requires exactly"
                                + " ACKNOWLEDGED) -- approximating via the cancel path instead; a deliberate,"
                                + " disclosed simplification, see this class's own Javadoc",
                        id,
                        order.state());
                approximateCancel(order);
            }
            removeFromPendingTracking(id);
            return;
        }
        if ("REJECTED".equals(status) || "FAILED".equals(status)) {
            // Order.reject() requires exactly SUBMITTED -- a pending order
            // tracked by this class is always at least ACKNOWLEDGED (see
            // the submit() guard), so that precondition can never legally
            // hold here. Always approximate via the cancel path, loudly.
            log.error(
                    "order {} reports {} while pending (state {}) -- Order.reject() requires exactly SUBMITTED,"
                            + " which a pending order can never legally be in here; approximating via the cancel"
                            + " path instead; a deliberate, disclosed simplification, see this class's own"
                            + " Javadoc",
                    id,
                    status,
                    order.state());
            approximateCancel(order);
            removeFromPendingTracking(id);
            return;
        }
        log.warn("order {} reports unrecognized status '{}' -- leaving pending, never guessing", id, status);
    }

    /**
     * {@code requestCancel()} then {@code confirmCancel()} -- except when
     * {@code order} is already {@code CANCEL_PENDING} (a previous {@link
     * #cancel} call's {@code adapter.cancelOrder} threw after {@code
     * requestCancel()} already ran but before {@code confirmCancel()} did,
     * see {@link #cancel}'s own Javadoc), in which case only {@code
     * confirmCancel()} runs -- calling {@code requestCancel()} again there
     * would throw, since {@code CANCEL_PENDING} is not in {@code
     * Order}'s own {@code CAN_REQUEST_CANCEL} set.
     */
    private void approximateCancel(Order order) {
        if (order.state() != OrderState.CANCEL_PENDING) {
            order.requestCancel();
        }
        order.confirmCancel();
    }

    private void removeFromPendingTracking(UUID id) {
        pendingOrders.remove(id);
        cumulativeFilledQty.remove(id);
        cumulativeNotional.remove(id);
    }

    private static void requirePositivePrice(BigDecimal price) {
        if (price.signum() <= 0) {
            throw new IllegalArgumentException("referencePrice must be positive, was " + price);
        }
    }
}
