package engine.runtime;

import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Test/demo scaffolding only -- <b>not a trading strategy</b>. Emits a
 * fixed, deterministic {@link OrderIntent} (same symbol/side/type/quantity/
 * limit price every time) on every Nth call to {@link #nextSignal()},
 * {@link Optional#empty()} otherwise. This exists purely so
 * {@link TradingLoop} and the {@link OrderPipeline} -&gt;
 * {@code PaperBroker} wiring behind it can be exercised end-to-end without
 * a real strategy -- CLAUDE.md's Implementation Priority section
 * explicitly allows Priorities #6-#8 to be "built and tested with
 * dummy/mock signals independently of a validated strategy".
 *
 * <p>See CLAUDE.md's "Strategy Research Methodology" section for the
 * walk-forward validation, holdout-data, and look-ahead-bias rules a real
 * strategy would have to satisfy before paper-trading eligibility --
 * <b>none of that applies here</b>, because this class is not strategy
 * research and its output must never be promoted to paper/live trading as
 * if it were a validated signal.
 *
 * <p>Implements {@link SignalSource} -- {@code final} does not need to be
 * removed to do so; a final class can implement any number of interfaces,
 * {@code final} only forbids subclassing. No other change was needed here
 * to retrofit this class onto the extracted interface.
 */
public final class DummySignalSource implements SignalSource {

    private final String symbol;
    private final Side side;
    private final OrderType orderType;
    private final BigDecimal quantity;
    private final BigDecimal limitPrice;
    private final int everyNthCall;
    private final AtomicLong callCount = new AtomicLong(0);

    public DummySignalSource(
            String symbol, Side side, OrderType orderType, BigDecimal quantity, BigDecimal limitPrice, int everyNthCall) {
        this.symbol = Objects.requireNonNull(symbol, "symbol is required");
        this.side = Objects.requireNonNull(side, "side is required");
        this.orderType = Objects.requireNonNull(orderType, "orderType is required");
        this.quantity = Objects.requireNonNull(quantity, "quantity is required");
        this.limitPrice = limitPrice;
        if (everyNthCall <= 0) {
            throw new IllegalArgumentException("everyNthCall must be positive, was " + everyNthCall);
        }
        this.everyNthCall = everyNthCall;
        // Fail fast on a shape OrderIntent itself would reject (e.g. LIMIT
        // without a limitPrice) at construction time rather than only on
        // whichever future nextSignal() call happens to be a firing one --
        // delegates to OrderIntent's own validation instead of duplicating
        // it here.
        buildIntent();
    }

    /**
     * Returns a fresh {@link OrderIntent} (a new random {@code intentId}
     * every time -- each firing is a new signal, not a retry of a previous
     * one) on every {@code everyNthCall}-th call, counting from 1;
     * {@link Optional#empty()} on every other call.
     */
    @Override
    public Optional<OrderIntent> nextSignal() {
        long call = callCount.incrementAndGet();
        if (call % everyNthCall != 0) {
            return Optional.empty();
        }
        return Optional.of(buildIntent());
    }

    private OrderIntent buildIntent() {
        return new OrderIntent(UUID.randomUUID(), symbol, side, orderType, quantity, limitPrice, "15m", Instant.now());
    }
}
