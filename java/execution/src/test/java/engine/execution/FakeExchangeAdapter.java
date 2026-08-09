package engine.execution;

import engine.exchange.BalanceSnapshot;
import engine.exchange.ExchangeAdapter;
import engine.exchange.OrderStatus;
import engine.exchange.PositionMode;
import engine.exchange.PositionSnapshot;
import engine.oms.Order;
import engine.schemas.Side;
import java.util.ArrayDeque;
import java.util.List;
import java.util.Map;
import java.util.Queue;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Hand-written {@link ExchangeAdapter} test double for {@link
 * ExchangeOrderExecutorTest} -- this codebase has no mocking framework
 * anywhere (confirmed: {@code PaperBrokerTest}/{@code BingXAdapterTest}/
 * every other test in this repo constructs real objects directly), so this
 * follows the same convention rather than introducing one. Every internal
 * map is a {@code ConcurrentHashMap} -- this class is itself exercised
 * under concurrent access by {@code ExchangeOrderExecutorTest}'s own
 * concurrency regression test, so its bookkeeping must be at least as
 * thread-safe as the class under test.
 *
 * <p>{@code queryOrder} responses are scripted per client order id via
 * {@link #scriptStatuses}, a FIFO queue consumed exactly one entry per call
 * -- this lets a test express a multi-poll sequence (e.g. "30% filled at
 * one avgPrice, then 100% filled at a different cumulative avgPrice" for
 * the partial-fill test) by scripting the entire sequence up front, before
 * the first {@code pollFills} call. A test that polls more times than it
 * scripted responses for gets a loud {@link IllegalStateException} rather
 * than a silently-repeated or fabricated response -- script every poll a
 * test actually makes, including a trailing repeat of the steady-state
 * response if the test polls past its last state change. {@link
 * #alwaysRespond} is a separate, simpler mechanism for a test that can't
 * control the exact number of {@code queryOrder} calls (e.g. a concurrency
 * test with several racing threads): once scripted, every call for that
 * order id after any explicitly-scripted queue is exhausted returns the
 * same fixed response indefinitely.
 */
final class FakeExchangeAdapter implements ExchangeAdapter {

    private final Map<UUID, Queue<OrderStatus>> scriptedStatuses = new ConcurrentHashMap<>();
    private final Map<UUID, OrderStatus> steadyStateStatuses = new ConcurrentHashMap<>();
    private final Map<UUID, RuntimeException> queryFailures = new ConcurrentHashMap<>();
    private volatile String nextRejectReason;
    private volatile RuntimeException nextSubmitFailure;
    private volatile RuntimeException nextCancelFailure;

    void scriptStatuses(UUID clientOrderId, OrderStatus... statuses) {
        scriptedStatuses.computeIfAbsent(clientOrderId, id -> new ArrayDeque<>()).addAll(List.of(statuses));
    }

    /**
     * Every {@code queryOrder} call for {@code clientOrderId}, after any
     * explicitly-scripted sequence from {@link #scriptStatuses} is
     * exhausted, returns {@code status} -- indefinitely, for a test whose
     * exact poll count isn't controlled by the test itself (a concurrency
     * test with several racing threads).
     */
    void alwaysRespond(UUID clientOrderId, OrderStatus status) {
        steadyStateStatuses.put(clientOrderId, status);
    }

    /** The next {@code queryOrder} call for {@code clientOrderId} throws {@code exception} instead of returning. */
    void throwOnNextQuery(UUID clientOrderId, RuntimeException exception) {
        queryFailures.put(clientOrderId, exception);
    }

    /** The next {@code submitOrder} call rejects the order with {@code reason} instead of acknowledging it. */
    void rejectNextSubmit(String reason) {
        this.nextRejectReason = reason;
    }

    /** The next {@code submitOrder} call throws {@code exception} before mutating the order at all. */
    void throwOnNextSubmit(RuntimeException exception) {
        this.nextSubmitFailure = exception;
    }

    /** The next {@code cancelOrder} call throws {@code exception} after {@code order.requestCancel()} but before {@code confirmCancel()}. */
    void throwOnNextCancel(RuntimeException exception) {
        this.nextCancelFailure = exception;
    }

    @Override
    public void submitOrder(Order order) {
        if (nextSubmitFailure != null) {
            RuntimeException failure = nextSubmitFailure;
            nextSubmitFailure = null;
            throw failure;
        }
        order.submit();
        if (nextRejectReason != null) {
            String reason = nextRejectReason;
            nextRejectReason = null;
            order.reject(reason);
            return;
        }
        order.acknowledge("FAKE-" + UUID.randomUUID());
    }

    @Override
    public void cancelOrder(Order order) {
        order.requestCancel();
        if (nextCancelFailure != null) {
            RuntimeException failure = nextCancelFailure;
            nextCancelFailure = null;
            throw failure;
        }
        order.confirmCancel();
    }

    @Override
    public OrderStatus queryOrder(Order order) {
        RuntimeException failure = queryFailures.remove(order.clientOrderId());
        if (failure != null) {
            throw failure;
        }
        Queue<OrderStatus> queue = scriptedStatuses.get(order.clientOrderId());
        if (queue != null) {
            synchronized (queue) {
                if (!queue.isEmpty()) {
                    return queue.poll();
                }
            }
        }
        OrderStatus steady = steadyStateStatuses.get(order.clientOrderId());
        if (steady != null) {
            return steady;
        }
        throw new IllegalStateException("no scripted OrderStatus left for " + order.clientOrderId());
    }

    @Override
    public List<PositionSnapshot> getPositions() {
        throw new UnsupportedOperationException("ExchangeOrderExecutor must never call getPositions()");
    }

    @Override
    public BalanceSnapshot getBalance() {
        throw new UnsupportedOperationException("ExchangeOrderExecutor must never call getBalance()");
    }

    @Override
    public void setLeverage(String symbol, Side side, int leverage) {
        throw new UnsupportedOperationException("ExchangeOrderExecutor must never call setLeverage()");
    }

    @Override
    public void setPositionMode(PositionMode mode) {
        throw new UnsupportedOperationException("ExchangeOrderExecutor must never call setPositionMode()");
    }
}
