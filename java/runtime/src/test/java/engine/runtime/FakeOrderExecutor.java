package engine.runtime;

import engine.execution.Fill;
import engine.execution.OrderExecutor;
import engine.oms.Order;
import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Hand-written {@link OrderExecutor} test double for {@link
 * PersistentSubmissionOrderExecutorTest} -- this codebase's established
 * no-mocking-framework convention (every other test double in this repo,
 * e.g. {@code engine.execution.FakeExchangeAdapter}, is hand-written the
 * same way). Delegation itself is trivial (this decorator's whole point);
 * what matters for these tests is precise, scriptable control over whether
 * {@link #submit} throws.
 */
final class FakeOrderExecutor implements OrderExecutor {

    private final Map<UUID, Order> pending = new LinkedHashMap<>();
    private volatile RuntimeException nextSubmitFailure;
    private final AtomicInteger submitCallCount = new AtomicInteger();

    void throwOnNextSubmit(RuntimeException exception) {
        this.nextSubmitFailure = exception;
    }

    int submitCallCount() {
        return submitCallCount.get();
    }

    @Override
    public Optional<Fill> submit(Order order, BigDecimal referencePrice) {
        submitCallCount.incrementAndGet();
        if (nextSubmitFailure != null) {
            RuntimeException failure = nextSubmitFailure;
            nextSubmitFailure = null;
            throw failure;
        }
        pending.put(order.clientOrderId(), order);
        return Optional.empty();
    }

    @Override
    public List<Fill> pollFills(String symbol, BigDecimal referencePrice) {
        return List.of();
    }

    @Override
    public Map<UUID, Order> pendingOrders() {
        return Map.copyOf(pending);
    }

    @Override
    public void cancel(Order order) {
        pending.remove(order.clientOrderId());
    }
}
