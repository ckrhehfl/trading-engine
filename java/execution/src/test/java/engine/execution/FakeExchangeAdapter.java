package engine.execution;

import engine.exchange.BalanceSnapshot;
import engine.exchange.ExchangeAdapter;
import engine.exchange.OrderStatus;
import engine.exchange.PositionMode;
import engine.exchange.PositionSnapshot;
import engine.oms.Order;
import engine.schemas.Side;
import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Queue;
import java.util.UUID;

/**
 * Hand-written {@link ExchangeAdapter} test double for {@link
 * ExchangeOrderExecutorTest} -- this codebase has no mocking framework
 * anywhere (confirmed: {@code PaperBrokerTest}/{@code BingXAdapterTest}/
 * every other test in this repo constructs real objects directly), so this
 * follows the same convention rather than introducing one.
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
 * response if the test polls past its last state change.
 */
final class FakeExchangeAdapter implements ExchangeAdapter {

    private final Map<UUID, Queue<OrderStatus>> scriptedStatuses = new HashMap<>();
    private final Map<UUID, RuntimeException> queryFailures = new HashMap<>();
    private String nextRejectReason;
    private RuntimeException nextSubmitFailure;
    private RuntimeException nextCancelFailure;

    void scriptStatuses(UUID clientOrderId, OrderStatus... statuses) {
        scriptedStatuses.computeIfAbsent(clientOrderId, id -> new ArrayDeque<>()).addAll(List.of(statuses));
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
        if (queue == null || queue.isEmpty()) {
            throw new IllegalStateException("no scripted OrderStatus left for " + order.clientOrderId());
        }
        return queue.poll();
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
