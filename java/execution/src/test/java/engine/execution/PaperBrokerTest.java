package engine.execution;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.oms.Order;
import engine.oms.OrderState;
import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class PaperBrokerTest {

    /**
     * BigDecimal#equals is scale-sensitive (unlike Python's Decimal) — 1000 *
     * 1.005 produces "1005.000", not "1005.0". compareTo compares numeric
     * value, which is the actual property under test here.
     */
    private void assertBigDecimalEquals(BigDecimal expected, BigDecimal actual) {
        assertEquals(0, expected.compareTo(actual), () -> "expected " + expected + " but was " + actual);
    }

    private Order guardedMarketOrder(Side side, String quantity) {
        UUID id = UUID.randomUUID();
        OrderIntent intent = new OrderIntent(
                id, "BTC-USDT", side, OrderType.GUARDED_MARKET, new BigDecimal(quantity), null, null, Instant.now());
        RiskDecision decision = new RiskDecision(
                id, Decision.APPROVED, null, new BigDecimal(quantity), new BigDecimal("2"), Instant.now());
        return Order.fromApprovedDecision(intent, decision);
    }

    private Order limitOrder(Side side, String quantity, String limitPrice) {
        UUID id = UUID.randomUUID();
        OrderIntent intent = new OrderIntent(
                id,
                "BTC-USDT",
                side,
                OrderType.LIMIT,
                new BigDecimal(quantity),
                new BigDecimal(limitPrice),
                "15m",
                Instant.now());
        RiskDecision decision = new RiskDecision(
                id, Decision.APPROVED, null, new BigDecimal(quantity), new BigDecimal("2"), Instant.now());
        return Order.fromApprovedDecision(intent, decision);
    }

    @Test
    void guardedMarketLongFillsImmediatelyWorseHigherWithSlippage() {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("50")); // 50bps slippage
        Order order = guardedMarketOrder(Side.LONG, "1");

        Optional<Fill> fill = broker.submit(order, new BigDecimal("1000"));

        assertTrue(fill.isPresent());
        assertBigDecimalEquals(new BigDecimal("1005.0"), fill.get().price()); // 1000 * 1.005
        assertEquals(OrderState.FILLED, order.state());
    }

    @Test
    void guardedMarketShortFillsImmediatelyWorseLowerWithSlippage() {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("50"));
        Order order = guardedMarketOrder(Side.SHORT, "1");

        Optional<Fill> fill = broker.submit(order, new BigDecimal("1000"));

        assertTrue(fill.isPresent());
        assertBigDecimalEquals(new BigDecimal("995.0"), fill.get().price()); // 1000 * 0.995
        assertEquals(OrderState.FILLED, order.state());
    }

    @Test
    void limitOrderAlreadyMarketableFillsImmediatelyAtCurrentPriceNotLimit() {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("0"));
        Order order = limitOrder(Side.LONG, "1", "98"); // limit 98, market already at 96 — favorable

        Optional<Fill> fill = broker.submit(order, new BigDecimal("96"));

        assertTrue(fill.isPresent());
        assertBigDecimalEquals(new BigDecimal("96"), fill.get().price()); // price improvement, not capped at 98
        assertEquals(OrderState.FILLED, order.state());
    }

    @Test
    void limitOrderNotYetMarketableStaysPendingWithNoFill() {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("0"));
        Order order = limitOrder(Side.LONG, "1", "98"); // limit 98, market at 100 — not reached

        Optional<Fill> fill = broker.submit(order, new BigDecimal("100"));

        assertFalse(fill.isPresent());
        assertEquals(OrderState.ACKNOWLEDGED, order.state());
        assertTrue(broker.pendingOrders().containsKey(order.clientOrderId()));
    }

    @Test
    void onPriceUpdateFillsPendingLimitOrderOnceMarketable() {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("0"));
        Order order = limitOrder(Side.LONG, "1", "98");
        broker.submit(order, new BigDecimal("100")); // stays pending

        List<Fill> fills = broker.onPriceUpdate("BTC-USDT", new BigDecimal("97"));

        assertEquals(1, fills.size());
        assertBigDecimalEquals(new BigDecimal("97"), fills.get(0).price());
        assertEquals(OrderState.FILLED, order.state());
        assertFalse(broker.pendingOrders().containsKey(order.clientOrderId()));
    }

    @Test
    void onPriceUpdateIgnoresPendingOrdersForOtherSymbols() {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("0"));
        Order order = limitOrder(Side.LONG, "1", "98");
        broker.submit(order, new BigDecimal("100")); // stays pending, symbol BTC-USDT

        List<Fill> fills = broker.onPriceUpdate("ETH-USDT", new BigDecimal("50"));

        assertTrue(fills.isEmpty());
        assertEquals(OrderState.ACKNOWLEDGED, order.state());
    }

    @Test
    void limitOrderFillPriceIsNeverWorseThanLimitEvenWithSlippageConfigured() {
        // Regression: slippage must never be layered onto a LIMIT fill —
        // the exact bug CodeRabbit caught in python/backtest/fill.py.
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("50")); // 50bps slippage
        Order order = limitOrder(Side.LONG, "1", "98");

        Optional<Fill> fill = broker.submit(order, new BigDecimal("98")); // exactly marketable

        assertTrue(fill.isPresent());
        assertBigDecimalEquals(new BigDecimal("98"), fill.get().price()); // not 98 * 1.005
    }

    @Test
    void feeIsComputedFromActualFillPriceAndQuantity() {
        PaperBroker broker = new PaperBroker(new BigDecimal("10"), new BigDecimal("0")); // 10bps fee
        Order order = guardedMarketOrder(Side.LONG, "2");

        Optional<Fill> fill = broker.submit(order, new BigDecimal("1000"));

        assertBigDecimalEquals(new BigDecimal("2000"), fill.get().notional()); // 2 * 1000
        assertBigDecimalEquals(new BigDecimal("2"), fill.get().fee()); // 2000 * 10bps
    }

    @Test
    void cancelPendingOrderTransitionsToCancelledAndStopsFutureFills() {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("0"));
        Order order = limitOrder(Side.LONG, "1", "98");
        broker.submit(order, new BigDecimal("100")); // stays pending

        broker.cancel(order);

        assertEquals(OrderState.CANCELLED, order.state());
        assertFalse(broker.pendingOrders().containsKey(order.clientOrderId()));
        List<Fill> fills = broker.onPriceUpdate("BTC-USDT", new BigDecimal("50")); // would have been marketable
        assertTrue(fills.isEmpty());
    }

    @Test
    void cancelOnAlreadyFilledOrderThrows() {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("0"));
        Order order = guardedMarketOrder(Side.LONG, "1");
        broker.submit(order, new BigDecimal("1000")); // fills immediately

        assertThrows(IllegalStateException.class, () -> broker.cancel(order));
    }

    @Test
    void constructorRejectsNegativeFeeBps() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new PaperBroker(new BigDecimal("-1"), new BigDecimal("0")));
    }

    @Test
    void constructorRejectsNegativeSlippageBps() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new PaperBroker(new BigDecimal("0"), new BigDecimal("-1")));
    }

    @Test
    void submitRejectsZeroOrNegativePrice() {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("0"));
        Order order = guardedMarketOrder(Side.LONG, "1");

        assertThrows(IllegalArgumentException.class, () -> broker.submit(order, BigDecimal.ZERO));
        // Order must not have been mutated by the rejected call.
        assertEquals(OrderState.NEW, order.state());
    }

    @Test
    void onPriceUpdateRejectsZeroOrNegativePrice() {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("0"));

        assertThrows(
                IllegalArgumentException.class,
                () -> broker.onPriceUpdate("BTC-USDT", new BigDecimal("-1")));
    }

    @Test
    void submitRejectsASecondDistinctOrderInstanceSharingAClientOrderId() {
        // Simulates a caller bypassing engine.oms.OrderStore and
        // constructing two separate Order objects for the same client
        // order id — OrderStore normally prevents this, but PaperBroker
        // must not silently double-execute if it happens anyway.
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("0"));
        UUID id = UUID.randomUUID();
        OrderIntent intent = new OrderIntent(
                id, "BTC-USDT", Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("1"), null, null, Instant.now());
        RiskDecision decision = new RiskDecision(
                id, Decision.APPROVED, null, new BigDecimal("1"), new BigDecimal("2"), Instant.now());
        Order first = Order.fromApprovedDecision(intent, decision);
        Order second = Order.fromApprovedDecision(intent, decision);

        broker.submit(first, new BigDecimal("1000"));

        assertThrows(IllegalStateException.class, () -> broker.submit(second, new BigDecimal("1000")));
    }

    @Test
    void concurrentPriceUpdatesForTheSamePendingOrderFillItExactlyOnce() throws InterruptedException {
        PaperBroker broker = new PaperBroker(new BigDecimal("0"), new BigDecimal("0"));
        Order order = limitOrder(Side.LONG, "1", "100");
        broker.submit(order, new BigDecimal("101")); // stays pending

        int threadCount = 20;
        java.util.concurrent.ExecutorService pool = java.util.concurrent.Executors.newFixedThreadPool(threadCount);
        java.util.concurrent.CountDownLatch ready = new java.util.concurrent.CountDownLatch(threadCount);
        java.util.concurrent.CountDownLatch go = new java.util.concurrent.CountDownLatch(1);
        List<java.util.concurrent.Future<List<Fill>>> futures = new ArrayList<>();
        for (int i = 0; i < threadCount; i++) {
            futures.add(pool.submit(() -> {
                ready.countDown();
                go.await();
                return broker.onPriceUpdate("BTC-USDT", new BigDecimal("99")); // marketable
            }));
        }
        ready.await();
        go.countDown();

        int totalFills = 0;
        for (java.util.concurrent.Future<List<Fill>> future : futures) {
            try {
                totalFills += future.get().size();
            } catch (java.util.concurrent.ExecutionException e) {
                throw new AssertionError("onPriceUpdate must not throw under concurrent access", e);
            }
        }
        pool.shutdown();

        assertEquals(1, totalFills);
        assertEquals(OrderState.FILLED, order.state());
    }
}
