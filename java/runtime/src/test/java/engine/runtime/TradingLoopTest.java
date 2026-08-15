package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
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
import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
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

        // LIMIT at a price deliberately unmarketable at this test's price
        // (50000, below) -- not GUARDED_MARKET, which would fill
        // immediately and vanish from pendingOrders() just like a
        // correctly-skipped signal would, making the pendingOrders()
        // assertion below ambiguous about which case actually happened.
        // Same technique as the kill-switch test above.
        DummySignalSource signalSource = new DummySignalSource(
                SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("40000"), 1); // fires every tick
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("50000"); // marketable for the seeded order (limit 50000), not for the signal's (limit 40000)
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

    /**
     * Symbol-match validation (closes GitHub issue #70 / the release gate
     * recorded in {@code .planning/paper-trading-a-signal-source.md}):
     * {@code TradingLoop} is the layer that owns "what symbol is this loop
     * actually configured for" (it already threads {@code symbol} through
     * to both {@code priceFeed.latestPrice} and {@code orderPipeline
     * .submitIntent} today), so the check lives here rather than inside
     * any one {@link SignalSource} implementation -- see
     * {@code .planning/paper-trading-c-scheduler-entrypoint.md} for the
     * full layer-choice writeup. A plain lambda {@link SignalSource} is
     * used here (not {@link DummySignalSource}, which always shares
     * {@code TradingLoopTest}'s own {@code SYMBOL} constant by
     * construction, and not {@link FileSignalSource}, which has no
     * symbol-awareness at all) specifically so a mismatched symbol can be
     * constructed directly and cheaply.
     */
    @Test
    void tickRejectsASignalWithAMismatchedSymbolWithoutSubmittingItOrFailingTheTick() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        // LIMIT, deliberately unmarketable at this test's price (60000) --
        // same technique the class-level Javadoc and every other test in
        // this file already use: if the symbol check did NOT exist, this
        // exact intent would still reach OrderPipeline/PaperBroker and
        // become visibly pending, so pendingOrders() staying empty is a
        // real, distinguishing assertion about the check itself -- not
        // just "nothing filled", which a GUARDED_MARKET intent's immediate
        // fill (regardless of symbol) could not have distinguished.
        OrderIntent wrongSymbolIntent = new OrderIntent(
                UUID.randomUUID(),
                "ETH-USDT", // TradingLoop below is configured for SYMBOL ("BTC-USDT")
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.001"),
                new BigDecimal("50000"),
                "1d",
                Instant.now());
        SignalSource signalSource = () -> Optional.of(wrongSymbolIntent);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000"); // above the limit -> would be unmarketable if it ever reached the broker
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            loop.tick();

            assertTrue(
                    broker.pendingOrders().isEmpty(),
                    "a signal whose symbol does not match the configured symbol must never reach PaperBroker");
            // A rejected-by-symbol-mismatch signal is a defined, logged skip
            // -- not a tick failure -- exactly like the equity-depleted and
            // kill-switch-tripped skip paths above.
            assertNull(loop.lastError());
            assertNotNull(loop.lastTickAt());
        }
    }

    @Test
    void tickSubmitsASignalWithAMatchingSymbolNormally() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        // Same unmarketable-LIMIT shape as the mismatch test above, except
        // for the symbol -- isolates "does a matching signal still flow
        // through" from every other variable.
        OrderIntent matchingIntent = new OrderIntent(
                UUID.randomUUID(),
                SYMBOL, // matches TradingLoop's own configured symbol below
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.001"),
                new BigDecimal("50000"),
                "1d",
                Instant.now());
        SignalSource signalSource = () -> Optional.of(matchingIntent);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000"); // above the limit -> unmarketable, stays pending (proof of submission)
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            loop.tick();

            assertEquals(
                    1,
                    broker.pendingOrders().size(),
                    "a signal whose symbol matches the configured symbol must flow through unchanged");
            assertNull(loop.lastError());
        }
    }

    /**
     * Paper-trading bridge Task E ({@code .planning/paper-trading-e-
     * reconciliation.md}): {@link TradingLoop#submittedOrderIds()} is the
     * source of truth {@link Reconciler#check} cross-checks against {@link
     * OrderStore} and {@link PaperBroker} -- neither of those two exposes a
     * full enumeration of everything it has ever tracked, so this loop's
     * own record of "every order id I successfully registered through
     * OrderPipeline" is what fills that gap. This test proves it
     * accumulates correctly across multiple ticks, not just within one.
     */
    @Test
    void submittedOrderIdsTracksEveryOrderCreatedThroughThePipelineAcrossTicks() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        // Fresh intentId every call (see DummySignalSource's own Javadoc) --
        // fires every tick, unmarketable LIMIT so each resulting order stays
        // observably pending rather than filling and vanishing.
        DummySignalSource signalSource = new DummySignalSource(
                SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("40000"), 1);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000"); // above the limit -> unmarketable, both signals stay pending
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            assertTrue(loop.submittedOrderIds().isEmpty(), "nothing submitted yet");

            loop.tick();
            loop.tick();

            assertEquals(2, loop.submittedOrderIds().size());
            assertEquals(2, broker.pendingOrders().size());
            assertTrue(
                    broker.pendingOrders().keySet().containsAll(loop.submittedOrderIds()),
                    "every id TradingLoop recorded submitting must be a real pending order in PaperBroker");
        }
    }

    /**
     * The real, if rare, scenario {@link Reconciler}'s {@code
     * DUPLICATE_SUBMISSION_ATTEMPT} mismatch type exists to catch. A real
     * {@code FileSignalSource} would never produce this (its whole job is
     * deduping by {@code intent_id}) -- this uses the same permissive
     * lambda {@link SignalSource} technique the symbol-mismatch tests above
     * already use, standing in for "if that dedup ever broke".
     */
    @Test
    void submittedOrderIdsRecordsARepeatWhenTheSameIntentIsSubmittedOnTwoSeparateTicks() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        OrderIntent repeatedIntent = new OrderIntent(
                UUID.randomUUID(),
                SYMBOL,
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.001"),
                new BigDecimal("40000"),
                "1d",
                Instant.now());
        SignalSource signalSource = () -> Optional.of(repeatedIntent);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000"); // unmarketable -> stays pending after the first successful submit
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            loop.tick(); // first submit succeeds, order registered + pending
            assertNull(loop.lastError());
            assertEquals(1, broker.pendingOrders().size());

            loop.tick(); // OrderStore.createOrder idempotently returns the
            // SAME existing order (same intentId), but PaperBroker.submit's
            // own seenClientOrderIds guard rejects the re-submit -- caught
            // by tick()'s own catch-all, not propagated.
            assertNotNull(loop.lastError(), "re-submitting the same order id must be rejected by PaperBroker");

            // Despite the second submitToBroker call failing, TradingLoop
            // still recorded the attempt (it happens before submitToBroker
            // is called) -- this is exactly the real signature
            // Reconciler#check's DUPLICATE_SUBMISSION_ATTEMPT type exists to
            // catch.
            assertEquals(2, loop.submittedOrderIds().size());
            assertEquals(repeatedIntent.intentId(), loop.submittedOrderIds().get(0));
            assertEquals(repeatedIntent.intentId(), loop.submittedOrderIds().get(1));
        }
    }

    /**
     * KIS shared account risk ledger, Task A ({@code .planning/kis-ledger-a-
     * account-state-provider.md}): the new 7-arg constructor overload's
     * {@link AccountStateProvider} seam. This proves {@link TradingLoop
     * #tick()} calls {@link AccountStateProvider#reserveForIntent} with the
     * exact same {@code intent}/{@code referencePrice} it received itself --
     * a plain lambda {@link SignalSource} (same technique the symbol-match
     * tests above already use) so the intent's object identity can be
     * asserted with {@code assertSame}, not just its field values.
     */
    @Test
    void tickCallsReserveForIntentWithTheExactIntentAndReferencePriceItReceived() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(),
                SYMBOL,
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.001"),
                new BigDecimal("50000"),
                "1d",
                Instant.now());
        SignalSource signalSource = () -> Optional.of(intent);
        KillSwitch killSwitch = new KillSwitch();
        AccountState accountState =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
        FakeAccountStateProvider accountStateProvider = new FakeAccountStateProvider(accountState);

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000"); // above the limit -> unmarketable, stays pending
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(
                    pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL, accountStateProvider);

            loop.tick();

            assertEquals(1, accountStateProvider.reserveCallCount());
            assertSame(intent, accountStateProvider.lastReservedIntent());
            assertEquals(0, new BigDecimal("60000").compareTo(accountStateProvider.lastReservedReferencePrice()));
        }
    }

    /**
     * KIS shared account risk ledger, Task A: when Risk Gateway MODIFIES
     * (clamps) an intent's requested quantity rather than fully approving
     * it, {@link AccountStateProvider#confirmReservation} must fire exactly
     * once, carrying the real approved quantity -- not the original,
     * pre-clamp requested quantity -- and {@link AccountStateProvider
     * #releaseReservation} must never fire. {@code RiskLimits.canary()}'s
     * 2% max-order-notional clamp (equity 100000 -> max notional 2000) is
     * used to force a real MODIFIED decision: 1 BTC at a 50000 limit price
     * requests a 50000 notional, clamped down to 0.04 (2000 / 50000).
     */
    @Test
    void tickConfirmsReservationWithApprovedQuantityWhenRiskGatewayModifiesTheIntent() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(),
                SYMBOL,
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("1"), // requests far more than the 2% max-notional clamp allows
                new BigDecimal("50000"),
                "1d",
                Instant.now());
        SignalSource signalSource = () -> Optional.of(intent);
        KillSwitch killSwitch = new KillSwitch();
        AccountState accountState =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
        FakeAccountStateProvider accountStateProvider = new FakeAccountStateProvider(accountState);

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000"); // above the limit -> LONG stays pending, doesn't fill
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(
                    pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL, accountStateProvider);

            loop.tick();

            assertEquals(1, broker.pendingOrders().size());
            Order order = broker.pendingOrders().values().iterator().next();
            assertEquals(0, new BigDecimal("0.04").compareTo(order.approvedQuantity()), "quantity should be clamped");

            assertEquals(1, accountStateProvider.confirmCallCount());
            assertEquals(intent.intentId(), accountStateProvider.lastConfirmedIntentId());
            assertEquals(
                    0,
                    order.approvedQuantity().compareTo(accountStateProvider.lastConfirmedQuantity()),
                    "confirmReservation must carry the real approved (clamped) quantity, not the requested one");
            assertEquals(0, new BigDecimal("60000").compareTo(accountStateProvider.lastConfirmedPrice()));
            assertEquals(
                    0,
                    accountStateProvider.releaseCallCount(),
                    "releaseReservation must never fire for an approved/modified intent");
        }
    }

    /**
     * KIS shared account risk ledger, Task A: when Risk Gateway REJECTS an
     * intent, {@link AccountStateProvider#releaseReservation} must fire
     * exactly once and {@link AccountStateProvider#confirmReservation} must
     * never fire, since no {@link Order} was ever produced. A deeply
     * negative daily PnL forces {@code RiskGateway.checkLossLimits} to
     * reject before notional is ever considered (canary's own daily loss
     * limit is -0.5%; -10% breaches it outright).
     */
    @Test
    void tickReleasesReservationAndNeverConfirmsWhenRiskGatewayRejects() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(),
                SYMBOL,
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.001"),
                new BigDecimal("50000"),
                "1d",
                Instant.now());
        SignalSource signalSource = () -> Optional.of(intent);
        KillSwitch killSwitch = new KillSwitch();
        AccountState accountState = new AccountState(
                new BigDecimal("100000"), new BigDecimal("-0.10"), BigDecimal.ZERO, BigDecimal.ZERO);
        FakeAccountStateProvider accountStateProvider = new FakeAccountStateProvider(accountState);

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(
                    pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL, accountStateProvider);

            loop.tick();

            assertTrue(broker.pendingOrders().isEmpty(), "a rejected intent must never reach the broker");
            assertEquals(1, accountStateProvider.reserveCallCount());
            assertEquals(1, accountStateProvider.releaseCallCount());
            assertEquals(intent.intentId(), accountStateProvider.lastReleasedIntentId());
            assertEquals(
                    0,
                    accountStateProvider.confirmCallCount(),
                    "confirmReservation must never fire for a rejected intent");
            assertNull(loop.lastError(), "a risk-gateway rejection is a defined skip, not a tick failure");
        }
    }

    /**
     * KIS shared account risk ledger, Task A: proves, not just asserts, the
     * "zero behavior change" claim for the existing 6-arg constructor --
     * its private {@code SyntheticAccountStateProvider}'s {@code
     * reserveForIntent} must return a result {@code equals()}-identical to
     * what the old inline {@code buildAccountState()} call would have
     * produced for the same internal state. Uses reflection to reach both
     * the private {@code buildAccountState()} method and the private
     * {@code accountStateProvider} field, the same technique the equity-
     * depletion test above already uses for {@code TradingLoop}'s private
     * {@code equity} field. No fill/equity change happens between the two
     * calls below, so any difference in the result would be a real
     * behavior change introduced by this task's extraction, not test
     * noise.
     */
    @Test
    void syntheticAccountStateProviderReserveForIntentMatchesTheOldInlineBuildAccountStateCall() throws Exception {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        DummySignalSource signalSource = new DummySignalSource(
                SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1000); // never fires
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            Method buildAccountState = TradingLoop.class.getDeclaredMethod("buildAccountState");
            buildAccountState.setAccessible(true);
            AccountState direct = (AccountState) buildAccountState.invoke(loop);

            Field providerField = TradingLoop.class.getDeclaredField("accountStateProvider");
            providerField.setAccessible(true);
            AccountStateProvider provider = (AccountStateProvider) providerField.get(loop);

            OrderIntent intent = new OrderIntent(
                    UUID.randomUUID(),
                    SYMBOL,
                    Side.LONG,
                    OrderType.LIMIT,
                    new BigDecimal("0.001"),
                    new BigDecimal("50000"),
                    "1d",
                    Instant.now());
            AccountState viaProvider = provider.reserveForIntent(intent, new BigDecimal("60000"));

            assertEquals(direct, viaProvider);
        }
    }

    /**
     * KIS shared account risk ledger, Task A -- CodeRabbit review finding
     * on PR #99: if {@link OrderPipeline#submitIntent} itself throws after
     * {@link AccountStateProvider#reserveForIntent} already succeeded (a
     * real, reachable case -- {@link engine.oms.OrderStore#createOrder}'s
     * own conflicting-retry guard throws {@code IllegalStateException} for
     * a reused {@code intentId} whose details don't match what's already
     * stored), {@code releaseReservation} must NOT fire for that attempt --
     * releasing would risk understating committed exposure if the order
     * actually was registered before the throw. See {@code TradingLoop
     * .tick()}'s own code comment at that call site for the full
     * reasoning. The exception must still propagate to {@code tick()}'s
     * own catch-all as {@code lastError}, exactly like any other tick
     * failure.
     */
    @Test
    void tickLeavesTheReservationUnresolvedWhenSubmitIntentThrowsAfterAReservationWasMade() throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        UUID sharedIntentId = UUID.randomUUID();
        OrderIntent firstIntent = new OrderIntent(
                sharedIntentId,
                SYMBOL,
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.001"),
                new BigDecimal("50000"),
                "1d",
                Instant.now());
        // Same intentId, different requested quantity -- OrderStore
        // #createOrder idempotently returns the already-stored Order for
        // this id, then its own matches() check fails (requestedQuantity
        // differs), throwing IllegalStateException. A real, if rare,
        // scenario -- see OrderStore's own Javadoc on "conflicting retry".
        OrderIntent conflictingIntent = new OrderIntent(
                sharedIntentId,
                SYMBOL,
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.002"),
                new BigDecimal("50000"),
                "1d",
                Instant.now());
        AtomicInteger tickCount = new AtomicInteger();
        SignalSource signalSource =
                () -> Optional.of(tickCount.incrementAndGet() == 1 ? firstIntent : conflictingIntent);
        KillSwitch killSwitch = new KillSwitch();
        AccountState accountState =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
        FakeAccountStateProvider accountStateProvider = new FakeAccountStateProvider(accountState);

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000"); // above the limit -> unmarketable, stays pending
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(
                    pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL, accountStateProvider);

            loop.tick(); // firstIntent: registers cleanly
            assertNull(loop.lastError());
            assertEquals(1, accountStateProvider.confirmCallCount());
            assertEquals(1, broker.pendingOrders().size());

            loop.tick(); // conflictingIntent: OrderStore#createOrder throws

            assertNotNull(
                    loop.lastError(), "the conflicting retry's IllegalStateException must surface as lastError");
            // The second reserveForIntent call happened (before the
            // throw), but neither confirmReservation nor
            // releaseReservation fired for it -- still exactly the one
            // confirmReservation from the first, clean tick above.
            assertEquals(2, accountStateProvider.reserveCallCount());
            assertEquals(1, accountStateProvider.confirmCallCount());
            assertEquals(
                    0,
                    accountStateProvider.releaseCallCount(),
                    "releaseReservation must not fire for an ambiguous submitIntent failure");
            assertEquals(1, broker.pendingOrders().size(), "the first order is unaffected by the failed retry");
        }
    }
}
