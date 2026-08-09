package engine.execution;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.exchange.ExchangeException;
import engine.exchange.OrderStatus;
import engine.oms.Order;
import engine.oms.OrderState;
import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ExchangeOrderExecutorTest {

    private static final String SYMBOL = "BTC-USDT";

    /**
     * BigDecimal#equals is scale-sensitive (unlike Python's Decimal) -- see
     * PaperBrokerTest's identical helper for the same reasoning. compareTo
     * compares numeric value, which is the actual property under test here.
     */
    private void assertBigDecimalEquals(BigDecimal expected, BigDecimal actual) {
        assertEquals(0, expected.compareTo(actual), () -> "expected " + expected + " but was " + actual);
    }

    private Order order(Side side, String quantity) {
        UUID id = UUID.randomUUID();
        OrderIntent intent = new OrderIntent(
                id, SYMBOL, side, OrderType.GUARDED_MARKET, new BigDecimal(quantity), null, null, Instant.now());
        RiskDecision decision =
                new RiskDecision(id, Decision.APPROVED, null, new BigDecimal(quantity), new BigDecimal("2"), Instant.now());
        return Order.fromApprovedDecision(intent, decision);
    }

    // --- Required scenario 1: ack-then-fill-on-next-poll ---

    @Test
    void submitAcknowledgesThenFirstPollProducesFillWhenExchangeReportsFilled() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "2");

        Optional<Fill> submitResult = executor.submit(order, new BigDecimal("1000"));

        assertTrue(submitResult.isEmpty(), "submit must always return empty -- fills only ever surface via pollFills");
        assertEquals(OrderState.ACKNOWLEDGED, order.state());
        assertTrue(executor.pendingOrders().containsKey(order.clientOrderId()));

        adapter.scriptStatuses(
                order.clientOrderId(),
                new OrderStatus(order.exchangeOrderId(), "FILLED", new BigDecimal("2"), new BigDecimal("1005")));

        List<Fill> fills = executor.pollFills(SYMBOL, new BigDecimal("1005"));

        assertEquals(1, fills.size());
        assertBigDecimalEquals(new BigDecimal("1005"), fills.get(0).price());
        assertBigDecimalEquals(new BigDecimal("2"), fills.get(0).quantity());
        assertEquals(OrderState.FILLED, order.state());
        assertFalse(executor.pendingOrders().containsKey(order.clientOrderId()));
    }

    // --- Required scenario 2: genuine two-step partial fill, correct incremental price ---

    @Test
    void secondPartialFillUsesIncrementalPriceNotNaiveCumulativeAvgPriceReuse() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "10");
        executor.submit(order, new BigDecimal("100"));

        // First poll: 30% filled (3 of 10) at avgPrice 100.
        // Second poll: 100% filled (10 of 10), cumulative avgPrice 135 --
        // i.e. the remaining 7 units actually filled at a true price of 150
        // (3*100 + 7*150 = 300 + 1050 = 1350; 1350/10 = 135).
        adapter.scriptStatuses(
                order.clientOrderId(),
                new OrderStatus(order.exchangeOrderId(), "PARTIALLY_FILLED", new BigDecimal("3"), new BigDecimal("100")),
                new OrderStatus(order.exchangeOrderId(), "FILLED", new BigDecimal("10"), new BigDecimal("135")));

        List<Fill> firstFills = executor.pollFills(SYMBOL, new BigDecimal("100"));
        assertEquals(1, firstFills.size());
        assertBigDecimalEquals(new BigDecimal("100"), firstFills.get(0).price());
        assertBigDecimalEquals(new BigDecimal("3"), firstFills.get(0).quantity());
        assertEquals(OrderState.PARTIALLY_FILLED, order.state());

        List<Fill> secondFills = executor.pollFills(SYMBOL, new BigDecimal("135"));

        assertEquals(1, secondFills.size());
        // The naive bug would reuse status.avgPrice() (135, the CUMULATIVE
        // average) directly as this increment's own price. The correct,
        // delta-derived price is 150 -- this assertion fails under the
        // naive implementation and passes under the fixed one.
        assertBigDecimalEquals(new BigDecimal("150"), secondFills.get(0).price());
        assertBigDecimalEquals(new BigDecimal("7"), secondFills.get(0).quantity());
        assertBigDecimalEquals(new BigDecimal("1050"), secondFills.get(0).notional());
        assertEquals(OrderState.FILLED, order.state());
        assertFalse(executor.pendingOrders().containsKey(order.clientOrderId()));
    }

    // --- Required scenario 3: one order's queryOrder failure doesn't lose others' fills or propagate ---

    @Test
    void queryOrderThrowingForOnePendingOrderDoesNotLoseOtherOrdersFillsOrPropagate() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order poisoned = order(Side.LONG, "1");
        Order healthy = order(Side.LONG, "1");
        executor.submit(poisoned, new BigDecimal("100"));
        executor.submit(healthy, new BigDecimal("100"));

        adapter.throwOnNextQuery(poisoned.clientOrderId(), new ExchangeException("simulated network failure"));
        adapter.scriptStatuses(
                healthy.clientOrderId(),
                new OrderStatus(healthy.exchangeOrderId(), "FILLED", new BigDecimal("1"), new BigDecimal("100")));

        List<Fill> fills = assertDoesNotThrow(() -> executor.pollFills(SYMBOL, new BigDecimal("100")));

        assertEquals(1, fills.size());
        assertEquals(healthy.clientOrderId(), fills.get(0).clientOrderId());
        assertEquals(
                OrderState.ACKNOWLEDGED,
                poisoned.state(),
                "poisoned order's own state must be untouched by the query failure");
        assertTrue(
                executor.pendingOrders().containsKey(poisoned.clientOrderId()),
                "a transient-looking query failure must leave the order pending for a retry next poll");
        assertFalse(executor.pendingOrders().containsKey(healthy.clientOrderId()));
    }

    // --- Required scenario 4: a REJECTED submit never enters pending tracking ---

    @Test
    void submitThatResultsInRejectedNeverEntersPendingTracking() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "1");
        adapter.rejectNextSubmit("insufficient margin");

        Optional<Fill> result = executor.submit(order, new BigDecimal("100"));

        assertTrue(result.isEmpty());
        assertEquals(OrderState.REJECTED, order.state());
        assertTrue(executor.pendingOrders().isEmpty());
    }

    // --- Required scenario 5: EXPIRED after a partial fill maps to the cancel path ---

    @Test
    void expiredArrivingAfterAPartialFillMapsToTheCancelPathInsteadOfThrowing() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "10");
        executor.submit(order, new BigDecimal("100"));

        adapter.scriptStatuses(
                order.clientOrderId(),
                new OrderStatus(order.exchangeOrderId(), "PARTIALLY_FILLED", new BigDecimal("4"), new BigDecimal("100")),
                new OrderStatus(order.exchangeOrderId(), "EXPIRED", new BigDecimal("4"), new BigDecimal("100")));

        List<Fill> firstFills = executor.pollFills(SYMBOL, new BigDecimal("100"));
        assertEquals(1, firstFills.size());
        assertEquals(OrderState.PARTIALLY_FILLED, order.state());

        List<Fill> secondFills = assertDoesNotThrow(() -> executor.pollFills(SYMBOL, new BigDecimal("100")));

        assertTrue(secondFills.isEmpty(), "no new fill quantity on the EXPIRED poll (delta is zero)");
        assertEquals(
                OrderState.CANCELLED,
                order.state(),
                "Order.expire() is illegal from PARTIALLY_FILLED -- must approximate via the cancel path, not throw");
        assertFalse(executor.pendingOrders().containsKey(order.clientOrderId()));
    }

    @Test
    void expiredArrivingWhileStillAcknowledgedUsesTheRealExpirePath() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "1");
        executor.submit(order, new BigDecimal("100"));

        adapter.scriptStatuses(
                order.clientOrderId(), new OrderStatus(order.exchangeOrderId(), "EXPIRED", BigDecimal.ZERO, null));

        List<Fill> fills = executor.pollFills(SYMBOL, new BigDecimal("100"));

        assertTrue(fills.isEmpty());
        assertEquals(OrderState.EXPIRED, order.state(), "ACKNOWLEDGED -> EXPIRED is the real, legal transition here");
        assertFalse(executor.pendingOrders().containsKey(order.clientOrderId()));
    }

    // --- Required scenario 6: an unrecognized status stays pending, never guesses ---

    @Test
    void unrecognizedStatusLeavesOrderPendingAndDoesNotThrow() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "1");
        executor.submit(order, new BigDecimal("100"));

        adapter.scriptStatuses(
                order.clientOrderId(),
                new OrderStatus(order.exchangeOrderId(), "SOME_UNKNOWN_FUTURE_STATUS", BigDecimal.ZERO, null));

        List<Fill> fills = assertDoesNotThrow(() -> executor.pollFills(SYMBOL, new BigDecimal("100")));

        assertTrue(fills.isEmpty());
        assertEquals(OrderState.ACKNOWLEDGED, order.state());
        assertTrue(executor.pendingOrders().containsKey(order.clientOrderId()));
    }

    // --- Required scenario 7: fee computed from the constructor's feeBps ---

    @Test
    void feeIsComputedFromIncrementNotionalUsingConstructorFeeBps() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("10")); // 10bps
        Order order = order(Side.LONG, "2");
        executor.submit(order, new BigDecimal("1000"));

        adapter.scriptStatuses(
                order.clientOrderId(),
                new OrderStatus(order.exchangeOrderId(), "FILLED", new BigDecimal("2"), new BigDecimal("1000")));

        List<Fill> fills = executor.pollFills(SYMBOL, new BigDecimal("1000"));

        assertBigDecimalEquals(new BigDecimal("2000"), fills.get(0).notional()); // 2 * 1000
        assertBigDecimalEquals(new BigDecimal("2"), fills.get(0).fee()); // 2000 * 10bps
    }

    // --- Additional coverage beyond the seven required scenarios ---

    @Test
    void dataIntegrityGuardRefusesToFabricateAPriceWhenAvgPriceIsMissingThenRecoversOnRetry() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "5");
        executor.submit(order, new BigDecimal("100"));

        adapter.scriptStatuses(
                order.clientOrderId(),
                new OrderStatus(order.exchangeOrderId(), "PARTIALLY_FILLED", new BigDecimal("2"), null),
                new OrderStatus(order.exchangeOrderId(), "PARTIALLY_FILLED", new BigDecimal("2"), new BigDecimal("100")));

        List<Fill> firstFills = executor.pollFills(SYMBOL, new BigDecimal("100"));
        assertTrue(firstFills.isEmpty(), "must not fabricate a fill price when avgPrice is null");
        assertEquals(OrderState.ACKNOWLEDGED, order.state(), "order must be untouched, not partially advanced");
        assertTrue(executor.pendingOrders().containsKey(order.clientOrderId()));

        List<Fill> secondFills = executor.pollFills(SYMBOL, new BigDecimal("100"));
        assertEquals(1, secondFills.size(), "retrying with a valid avgPrice must now produce the fill");
        assertBigDecimalEquals(new BigDecimal("100"), secondFills.get(0).price());
        assertEquals(OrderState.PARTIALLY_FILLED, order.state());
    }

    @Test
    void dataIntegrityGuardAlsoAppliesToANonPositiveAvgPrice() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "1");
        executor.submit(order, new BigDecimal("100"));

        adapter.scriptStatuses(
                order.clientOrderId(),
                new OrderStatus(order.exchangeOrderId(), "FILLED", new BigDecimal("1"), BigDecimal.ZERO));

        List<Fill> fills = assertDoesNotThrow(() -> executor.pollFills(SYMBOL, new BigDecimal("100")));

        assertTrue(fills.isEmpty());
        assertEquals(OrderState.ACKNOWLEDGED, order.state());
        assertTrue(executor.pendingOrders().containsKey(order.clientOrderId()));
    }

    @Test
    void unexpectedOverfillFromBadVenueDataDropsOrderFromPendingWithoutPropagating() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "1"); // approvedQuantity = 1
        executor.submit(order, new BigDecimal("100"));

        // Bad venue data: reports 2 filled against an order approved for
        // only 1 -- order.fill(2) throws IllegalArgumentException (the
        // OMS's own overfill guard). This is state corruption, not a
        // transient failure, so the order must be dropped, not retried.
        adapter.scriptStatuses(
                order.clientOrderId(),
                new OrderStatus(order.exchangeOrderId(), "PARTIALLY_FILLED", new BigDecimal("2"), new BigDecimal("100")));

        List<Fill> fills = assertDoesNotThrow(() -> executor.pollFills(SYMBOL, new BigDecimal("100")));

        assertTrue(fills.isEmpty());
        assertFalse(
                executor.pendingOrders().containsKey(order.clientOrderId()),
                "a state-corruption failure must drop the order, not retry it forever");
    }

    @Test
    void submitThatThrowsPropagatesAndLeavesOrderUntracked() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "1");
        adapter.throwOnNextSubmit(new ExchangeException("simulated I/O failure"));

        assertThrows(ExchangeException.class, () -> executor.submit(order, new BigDecimal("100")));

        assertTrue(executor.pendingOrders().isEmpty());
        assertEquals(OrderState.NEW, order.state(), "the fake adapter throws before ever calling order.submit()");
    }

    @Test
    void pollFillsIgnoresPendingOrdersForOtherSymbols() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "1"); // symbol BTC-USDT via the order() helper
        executor.submit(order, new BigDecimal("100"));

        List<Fill> fills = executor.pollFills("ETH-USDT", new BigDecimal("50"));

        assertTrue(fills.isEmpty());
        assertEquals(OrderState.ACKNOWLEDGED, order.state());
        assertTrue(executor.pendingOrders().containsKey(order.clientOrderId()));
    }

    @Test
    void cancelRemovesFromPendingTrackingOnSuccess() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "1");
        executor.submit(order, new BigDecimal("100"));

        executor.cancel(order);

        assertEquals(OrderState.CANCELLED, order.state());
        assertFalse(executor.pendingOrders().containsKey(order.clientOrderId()));
    }

    @Test
    void cancelRejectedByTheExchangeThrowsAndLeavesOrderInCancelPendingStillTracked() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, new BigDecimal("0"));
        Order order = order(Side.LONG, "1");
        executor.submit(order, new BigDecimal("100"));
        adapter.throwOnNextCancel(new ExchangeException("simulated exchange rejection"));

        assertThrows(ExchangeException.class, () -> executor.cancel(order));

        assertEquals(OrderState.CANCEL_PENDING, order.state());
        assertTrue(
                executor.pendingOrders().containsKey(order.clientOrderId()),
                "still tracked -- pollFills can still resolve it later (Order.CAN_FILL includes CANCEL_PENDING)");
    }

    @Test
    void constructorRejectsNullAdapter() {
        assertThrows(NullPointerException.class, () -> new ExchangeOrderExecutor(null, BigDecimal.ZERO));
    }

    @Test
    void constructorRejectsNullFeeBps() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        assertThrows(NullPointerException.class, () -> new ExchangeOrderExecutor(adapter, null));
    }

    @Test
    void constructorRejectsNegativeFeeBps() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        assertThrows(IllegalArgumentException.class, () -> new ExchangeOrderExecutor(adapter, new BigDecimal("-1")));
    }

    @Test
    void submitRejectsZeroOrNegativeReferencePrice() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, BigDecimal.ZERO);
        Order order = order(Side.LONG, "1");

        assertThrows(IllegalArgumentException.class, () -> executor.submit(order, BigDecimal.ZERO));
        assertEquals(OrderState.NEW, order.state());
    }

    @Test
    void pollFillsRejectsZeroOrNegativeReferencePrice() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        ExchangeOrderExecutor executor = new ExchangeOrderExecutor(adapter, BigDecimal.ZERO);

        assertThrows(IllegalArgumentException.class, () -> executor.pollFills(SYMBOL, new BigDecimal("-1")));
    }
}
