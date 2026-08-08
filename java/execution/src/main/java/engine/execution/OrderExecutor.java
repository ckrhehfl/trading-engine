package engine.execution;

import engine.oms.Order;
import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * The venue-agnostic order-execution seam {@code engine.runtime.TradingLoop}
 * depends on -- extracted from what used to be {@link PaperBroker}'s own
 * concrete, final-class method signatures (submit + poll-every-tick +
 * pendingOrders + cancel), the same retrofit pattern already used for
 * {@code engine.runtime.SignalSource}/{@code DummySignalSource} (see
 * {@code .planning/paper-trading-a-signal-source.md}). {@code TradingLoop}
 * now depends only on this interface, not on {@link PaperBroker} directly,
 * so a future real-exchange execution path can be swapped in without ever
 * touching {@code TradingLoop}'s control-flow logic -- see
 * {@code .planning/paper-trading-f-order-executor.md}.
 *
 * <p><b>Extensibility invariant.</b> A new exchange/venue means writing a
 * new {@code ExchangeAdapter} implementation, never a new {@code
 * OrderExecutor} implementation. There should only ever be two {@code
 * OrderExecutor} implementations in this codebase: the internal simulator
 * ({@link PaperBroker}) and one venue-agnostic wrapper around the {@code
 * ExchangeAdapter} interface (built in a later task). If you find yourself
 * about to write a second exchange-specific {@code OrderExecutor}, stop --
 * that's the {@code ExchangeAdapter} extension point, not this one.
 *
 * <p><b>Error contract, asymmetric on purpose.</b> {@link #pollFills} must
 * <b>never throw</b> -- an implementation that encounters a single order's
 * own failure (e.g. a real exchange query failing for just that one pending
 * order) must log it and skip that order, still returning whatever fills
 * did resolve for every other order this same call. {@link #submit}, by
 * contrast, <b>may throw</b>, and callers should treat a thrown {@code
 * submit} as an <b>ambiguous</b> outcome -- the order may or may not have
 * actually been accepted by the venue. {@code TradingLoop.submitToBroker}'s
 * existing orphan-handling (logging the order as orphaned and rethrowing so
 * it surfaces through {@code tick()}'s own catch-all / {@code lastError()})
 * already does the right thing with this -- it is unchanged by this
 * interface's introduction. This asymmetry produces a useful emergent
 * property, not just an inconvenience: a real submit that times out
 * ambiguously leaves the {@link Order} registered but never reaches {@link
 * #pendingOrders()}, which {@code engine.runtime.Reconciler} already flags
 * as {@code ORPHANED_IN_BROKER} -- automatically tripping the kill switch
 * until a human looks.
 */
public interface OrderExecutor {

    /**
     * Submits {@code order} for execution against {@code referencePrice}
     * and returns an immediate {@link Fill} if one resolves synchronously
     * (e.g. {@link PaperBroker}'s own simulated marketable fill); {@link
     * Optional#empty()} if the order is accepted but not yet filled --
     * either because it legitimately stays open (an unmarketable LIMIT), or
     * because this implementation can only acknowledge a submission and
     * must be polled later via {@link #pollFills} to learn about fills (a
     * real exchange). May throw -- see this interface's own Javadoc,
     * "Error contract, asymmetric on purpose".
     */
    Optional<Fill> submit(Order order, BigDecimal referencePrice);

    /**
     * Resolves this implementation's own outstanding pending orders for
     * {@code symbol} against {@code referencePrice}, returning every
     * {@link Fill} produced by this call. Meant to be called once per
     * {@code TradingLoop} tick, regardless of whether a new signal fired
     * that tick -- reconciling previously-pending orders is unconditional.
     * Must <b>never throw</b> -- see this interface's own Javadoc, "Error
     * contract, asymmetric on purpose".
     */
    List<Fill> pollFills(String symbol, BigDecimal referencePrice);

    /**
     * Every order this implementation currently considers open/pending,
     * keyed by {@link Order#clientOrderId()}. Read-only -- a caller must
     * not assume mutating the returned map affects this implementation's
     * own internal bookkeeping.
     */
    Map<UUID, Order> pendingOrders();

    /**
     * Cancels a pending order. Implementations may throw if the
     * cancellation itself is rejected (e.g. by a real venue) -- see the
     * implementing class's own Javadoc for its specific contract.
     */
    void cancel(Order order);
}
