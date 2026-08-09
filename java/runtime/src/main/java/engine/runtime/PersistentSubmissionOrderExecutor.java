package engine.runtime;

import engine.execution.Fill;
import engine.execution.OrderExecutor;
import engine.oms.Order;
import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

/**
 * {@link OrderExecutor} decorator implementing the proactive complement to
 * {@code Reconciler}'s existing {@code ORPHANED_IN_BROKER} reactive
 * safety net (see {@code OrderExecutor}'s own Javadoc) -- persists a
 * durable, {@code clientOrderId}-keyed {@link SubmissionMarker} immediately
 * before an ambiguous {@link #submit} call to the wrapped {@link
 * OrderExecutor}, and clears it once that call returns normally (no longer
 * ambiguous -- the wrapped {@link Order}'s own resulting state, {@code
 * ACKNOWLEDGED} or {@code REJECTED}, is definitively known at that point).
 * If the wrapped call throws instead, the marker is deliberately left in
 * place -- a thrown {@code submit} is exactly the ambiguous-outcome case
 * {@code OrderExecutor}'s own Javadoc documents ("the order may or may not
 * have actually been accepted by the venue"), and this class's whole
 * purpose is making that ambiguity survive a process restart rather than
 * being lost the moment the in-memory {@link Order} reference is gone.
 *
 * <p>This class deliberately does <b>not</b> attempt to resolve a marker
 * itself, or retry anything -- see {@link SubmissionMarkerResolver} for the
 * separate, startup-time resolution step that queries real exchange state
 * before any marker is ever cleared or a fresh submission is ever allowed
 * to proceed. Splitting record/clear (this class, on the hot submit path)
 * from resolve (a startup-time, once-per-process concern) keeps each half
 * simple: this class never needs an {@code ExchangeAdapter} reference at
 * all, only the {@link OrderExecutor} interface it already depends on and a
 * {@link SubmissionMarkerStore}.
 *
 * <p>{@link #pollFills}/{@link #pendingOrders}/{@link #cancel} are pure
 * passthroughs to the wrapped {@link OrderExecutor} -- this class has
 * nothing to add to those paths, since {@code SUBMISSION_UNKNOWN} is
 * specifically about the ambiguity a thrown {@code submit} produces, not
 * about fill resolution or cancellation.
 *
 * <p>Composable with either concrete {@code OrderExecutor} -- most usefully
 * {@code engine.execution.ExchangeOrderExecutor}, the only implementation
 * whose {@code submit} can genuinely throw ambiguously against a real
 * network call, but nothing here is BingX-specific or even {@code
 * ExchangeAdapter}-aware, matching this codebase's "compose small,
 * venue-agnostic pieces" convention (mirrors {@code PaperBroker}/{@code
 * ExchangeOrderExecutor} both implementing the same interface).
 */
final class PersistentSubmissionOrderExecutor implements OrderExecutor {

    private final OrderExecutor delegate;
    private final SubmissionMarkerStore store;

    PersistentSubmissionOrderExecutor(OrderExecutor delegate, SubmissionMarkerStore store) {
        this.delegate = Objects.requireNonNull(delegate, "delegate is required");
        this.store = Objects.requireNonNull(store, "store is required");
    }

    @Override
    public Optional<Fill> submit(Order order, BigDecimal referencePrice) {
        Objects.requireNonNull(order, "order is required");
        // Recorded BEFORE the delegate call, deliberately -- a marker
        // written only after a throw would itself be lost to the exact
        // ambiguity (did the process crash before or after the venue saw
        // the request?) this class exists to survive. See class Javadoc.
        store.record(order.clientOrderId(), order.symbol());
        Optional<Fill> result = delegate.submit(order, referencePrice);
        // Only reached if delegate.submit did not throw -- the order's
        // outcome (ACKNOWLEDGED/REJECTED, both definitively known via
        // `order`'s own resulting state) is no longer ambiguous.
        store.clear(order.clientOrderId());
        return result;
    }

    @Override
    public List<Fill> pollFills(String symbol, BigDecimal referencePrice) {
        return delegate.pollFills(symbol, referencePrice);
    }

    @Override
    public Map<UUID, Order> pendingOrders() {
        return delegate.pendingOrders();
    }

    @Override
    public void cancel(Order order) {
        delegate.cancel(order);
    }
}
