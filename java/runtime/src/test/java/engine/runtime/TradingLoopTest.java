package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.execution.PaperBroker;
import engine.oms.Order;
import engine.oms.OrderState;
import engine.oms.OrderStore;
import engine.risk.AccountState;
import engine.risk.RiskGateway;
import engine.risk.RiskLimits;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.Side;
import java.io.IOException;
import java.lang.reflect.Field;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/**
 * Real {@link RiskGateway}/{@link OrderStore}/{@link OrderPipeline}/
 * {@link PaperBroker} instances wired together throughout -- this codebase
 * has no mocking framework, so these are closer to small integration tests
 * than narrow unit tests, matching {@code engine.exchange.BingXAdapterTest}'s
 * and {@code OrderPipelineTest}'s existing style. {@link FakeBingXTradesServer}
 * stands in for the real BingX host, same technique as
 * {@link BingXPriceFeedTest}.
 */
class TradingLoopTest {

    private static final String SYMBOL = "BTC-USDT";

    private OrderPipeline newPipeline() {
        return new OrderPipeline(new RiskGateway(RiskLimits.canary()), new OrderStore());
    }

    @Test
    void tickWithSignalPresentSubmitsAnOrderToPaperBroker() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        // LIMIT, not marketable at the price this tick serves -- stays
        // pending, so its presence in pendingOrders() is directly
        // observable proof a signal was actually submitted through the
        // pipeline, not just "the tick ran without throwing".
        DummySignalSource signalSource = new DummySignalSource(
                SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000"); // above the limit -> LONG not marketable
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            loop.tick();

            assertEquals(1, broker.pendingOrders().size());
            assertNull(loop.lastError());
            assertNotNull(loop.lastTickAt());
        }
    }

    @Test
    void tickWithNoSignalStillAppliesPriceUpdateToExistingPendingOrders() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);

        // A pending order seeded directly through the pipeline/broker,
        // simulating one that existed before this TradingLoop was
        // constructed (e.g. surviving a restart) -- TradingLoop's
        // reconciliation story is "query PaperBroker.pendingOrders() fresh",
        // not "assume nothing existed before" (see .planning/08b-trading-loop.md).
        OrderIntent seedIntent = new OrderIntent(
                UUID.randomUUID(),
                SYMBOL,
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.001"),
                new BigDecimal("50000"),
                "15m",
                Instant.now());
        AccountState seedAccount =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
        Order seedOrder = pipeline.submitIntent(seedIntent, new BigDecimal("60000"), seedAccount).orElseThrow();
        broker.submit(seedOrder, new BigDecimal("60000")); // not marketable -> stays pending
        assertEquals(1, broker.pendingOrders().size());

        // everyNthCall=5 -> will not fire on call 1 (the only tick below).
        // Its own limit (40000) is deliberately unmarketable even at this
        // test's reconciling price (50000) -- if the signal path
        // erroneously fired anyway, its order would remain visibly
        // pending, making pendingOrders().isEmpty() below a real assertion
        // about "no new order", not just "the seed order filled".
        DummySignalSource signalSource = new DummySignalSource(
                SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("40000"), 5);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("50000"); // marketable for the seeded pending order
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            loop.tick();

            assertTrue(broker.pendingOrders().isEmpty());
            assertEquals(OrderState.FILLED, seedOrder.state());
            assertNull(loop.lastError());
        }
    }

    @Test
    void tickRecoversAfterAPriceFeedExceptionOnANextTick() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        DummySignalSource signalSource = new DummySignalSource(
                SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.001"), null, 1000); // never fires
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWith(500, "internal server error"); // BingXPriceFeed throws on this
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            loop.tick(); // must not throw out to the caller

            assertNotNull(loop.lastError());
            assertNotNull(loop.lastTickAt());

            server.respondWithPrice("60000"); // now succeeds
            loop.tick();

            assertNull(loop.lastError());
        }
    }

    @Test
    void killSwitchTrippedMidRunBlocksNewSignalsButStillReconcilesAnAlreadyPendingOrder() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        // Fires every tick, always the same LIMIT shape -- used to prove
        // both halves: (a) before trip, a signal creates a real pending
        // order; (b) after trip, an identical signal must NOT create a
        // second one.
        DummySignalSource signalSource = new DummySignalSource(
                SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            server.respondWithPrice("60000"); // unmarketable for limit 50000 LONG
            loop.tick(); // before trip: signal fires, order created, stays pending
            assertEquals(1, broker.pendingOrders().size());
            Order pendingOrder = broker.pendingOrders().values().iterator().next();

            killSwitch.trip();
            loop.tick(); // tripped, price unchanged (still unmarketable): must NOT add a 2nd pending order
            assertEquals(
                    1,
                    broker.pendingOrders().size(),
                    "kill switch must block new signal submission once tripped");

            server.respondWithPrice("50000"); // now marketable
            loop.tick(); // still tripped: price update must still reconcile the pre-existing pending order

            assertTrue(broker.pendingOrders().isEmpty());
            assertEquals(OrderState.FILLED, pendingOrder.state());
        }
    }

    @Test
    void tickSkipsSignalSubmissionWhenEquityIsDepletedButStillReconcilesAnExistingPendingOrder() throws Exception {
        // RiskGateway's own per-order notional clamp (a fraction of
        // *current* equity, recomputed every tick) makes actually driving
        // equity to zero through the real order flow converge
        // asymptotically rather than ever really reach zero within a fast
        // test -- so this directly forces the private equity field instead,
        // to exercise the guard on its own terms. See
        // .planning/08b-trading-loop.md for why this edge case is real but
        // hard to reach through legitimate use.
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);

        // Seeded while equity is still healthy, exactly like the
        // no-signal-tick test above -- simulates an order that was already
        // pending before equity happened to run out.
        OrderIntent seedIntent = new OrderIntent(
                UUID.randomUUID(),
                SYMBOL,
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.001"),
                new BigDecimal("50000"),
                "15m",
                Instant.now());
        AccountState seedAccount =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
        Order seedOrder = pipeline.submitIntent(seedIntent, new BigDecimal("60000"), seedAccount).orElseThrow();
        broker.submit(seedOrder, new BigDecimal("60000")); // not marketable -> stays pending
        assertEquals(1, broker.pendingOrders().size());

        DummySignalSource signalSource = new DummySignalSource(
                SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.001"), null, 1); // fires every tick
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("50000"); // now marketable for the seeded order
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            Field equityField = TradingLoop.class.getDeclaredField("equity");
            equityField.setAccessible(true);
            equityField.set(loop, BigDecimal.ZERO);

            loop.tick();

            // Without the guard, this tick would have called
            // buildAccountState() -> new AccountState(...), which rejects a
            // non-positive equity with IllegalArgumentException -- caught by
            // tick()'s own catch-all and recorded as lastError. The guard
            // means the *signal* path is instead a defined, non-error skip
            // -- but price-update reconciliation (step 2) is unconditional
            // and must still run regardless, exactly like the kill-switch
            // case: the seeded pending order still fills.
            assertTrue(
                    broker.pendingOrders().isEmpty(),
                    "no new signal order should be submitted while equity is depleted, and the pre-existing"
                            + " pending order should have been reconciled away by the price update");
            assertEquals(OrderState.FILLED, seedOrder.state());
            assertNull(loop.lastError());
            // Zero fees on this PaperBroker -- filling the seed order
            // doesn't change equity, so it should still read exactly zero.
            assertEquals(0, BigDecimal.ZERO.compareTo(loop.currentEquity()));
        }
    }
}
