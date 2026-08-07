package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.execution.PaperBroker;
import engine.oms.Order;
import engine.oms.OrderStore;
import engine.risk.AccountState;
import engine.risk.RiskGateway;
import engine.risk.RiskLimits;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/**
 * Real {@link OrderStore}/{@link RiskGateway}/{@link OrderPipeline}/
 * {@link PaperBroker} instances throughout, matching {@code
 * TradingLoopTest}'s own established "small integration test, real
 * components, no mocking framework" style -- see {@link Reconciler}'s own
 * Javadoc for the full design writeup, and
 * {@code .planning/paper-trading-e-reconciliation.md} for why each of
 * these four mismatch types is checkable (or not) given what {@link
 * OrderStore} and {@link PaperBroker} actually expose.
 */
class ReconcilerTest {

    private static final String SYMBOL = "BTC-USDT";

    private OrderPipeline newPipeline(OrderStore store) {
        return new OrderPipeline(new RiskGateway(RiskLimits.canary()), store);
    }

    private OrderIntent limitIntent(BigDecimal limitPrice) {
        return new OrderIntent(
                UUID.randomUUID(),
                SYMBOL,
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.001"),
                limitPrice,
                "1d",
                Instant.now());
    }

    private AccountState account() {
        return new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
    }

    @Test
    void aPendingOrderConsistentlyKnownToBothStoresReportsNoMismatches() {
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = newPipeline(store);
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);

        // Unmarketable at 60000 -> stays pending in both OrderStore
        // (ACKNOWLEDGED) and PaperBroker (pendingOrders()).
        OrderIntent intent = limitIntent(new BigDecimal("40000"));
        Order order = pipeline.submitIntent(intent, new BigDecimal("60000"), account()).orElseThrow();
        broker.submit(order, new BigDecimal("60000"));

        ReconciliationReport report = Reconciler.check(List.of(order.clientOrderId()), store, broker);

        assertTrue(report.isClean(), "expected no mismatches, got: " + report.mismatches());
        assertTrue(report.mismatches().isEmpty());
    }

    @Test
    void aLegitimatelyFilledOrderCorrectlyAbsentFromPendingOrdersIsNotAMismatch() {
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = newPipeline(store);
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);

        OrderIntent intent = limitIntent(new BigDecimal("60000")); // marketable immediately
        Order order = pipeline.submitIntent(intent, new BigDecimal("60000"), account()).orElseThrow();
        broker.submit(order, new BigDecimal("60000")); // fills immediately, never enters pendingOrders()

        ReconciliationReport report = Reconciler.check(List.of(order.clientOrderId()), store, broker);

        assertTrue(report.isClean(), "a legitimately filled order must not be flagged: " + report.mismatches());
    }

    @Test
    void anOrderRegisteredInOrderStoreButNeverSubmittedToTheBrokerIsReportedAsOrphaned() {
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = newPipeline(store);
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO); // never touched below

        OrderIntent intent = limitIntent(new BigDecimal("40000"));
        Order order = pipeline.submitIntent(intent, new BigDecimal("60000"), account()).orElseThrow();
        // Deliberately do NOT call broker.submit(order, ...) here -- this is
        // exactly the orphan scenario TradingLoop.submitToBroker's own
        // Javadoc names: an Order registered in OrderStore that never
        // reached PaperBroker (e.g. because PaperBroker.submit threw).

        ReconciliationReport report = Reconciler.check(List.of(order.clientOrderId()), store, broker);

        assertFalse(report.isClean());
        assertEquals(1, report.mismatches().size());
        ReconciliationMismatch mismatch = report.mismatches().get(0);
        assertEquals(ReconciliationMismatchType.ORPHANED_IN_BROKER, mismatch.type());
        assertEquals(order.clientOrderId(), mismatch.orderId());
    }

    @Test
    void submittingTheSameOrderIdTwiceIsReportedAsADuplicateSubmissionAttempt() {
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = newPipeline(store);
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);

        OrderIntent intent = limitIntent(new BigDecimal("40000"));
        Order order = pipeline.submitIntent(intent, new BigDecimal("60000"), account()).orElseThrow();
        broker.submit(order, new BigDecimal("60000"));

        // The known-submission history recorded this id twice -- what
        // TradingLoop#submittedOrderIds would contain if the same signal
        // were (incorrectly) submitted on two separate ticks. Constructed
        // directly here to isolate Reconciler#check's own logic from a
        // full TradingLoop replay -- TradingLoopTest's own
        // submittedOrderIdsRecordsARepeatWhenTheSameIntentIsSubmittedTwice
        // exercises the real end-to-end path that would produce this list.
        List<UUID> submittedTwice = List.of(order.clientOrderId(), order.clientOrderId());

        ReconciliationReport report = Reconciler.check(submittedTwice, store, broker);

        assertFalse(report.isClean());
        assertEquals(1, report.mismatches().size());
        assertEquals(ReconciliationMismatchType.DUPLICATE_SUBMISSION_ATTEMPT, report.mismatches().get(0).type());
        assertEquals(order.clientOrderId(), report.mismatches().get(0).orderId());
    }

    @Test
    void aKnownSubmittedIdWithNoOrderStoreRecordAtAllIsReportedAsMissing() {
        OrderStore store = new OrderStore(); // empty
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO); // empty

        UUID phantomId = UUID.randomUUID();
        ReconciliationReport report = Reconciler.check(List.of(phantomId), store, broker);

        assertFalse(report.isClean());
        assertEquals(1, report.mismatches().size());
        assertEquals(ReconciliationMismatchType.MISSING_FROM_ORDER_STORE, report.mismatches().get(0).type());
        assertEquals(phantomId, report.mismatches().get(0).orderId());
    }

    @Test
    void aPendingBrokerOrderNeverRecordedAsSubmittedIsReportedAsUntracked() {
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = newPipeline(store);
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);

        OrderIntent intent = limitIntent(new BigDecimal("40000")); // stays pending
        Order order = pipeline.submitIntent(intent, new BigDecimal("60000"), account()).orElseThrow();
        broker.submit(order, new BigDecimal("60000"));

        // Checked with an EMPTY known-submitted-ids list -- simulates the
        // tracked submission history having missed this id somehow (e.g. a
        // caller that pushes an Order into PaperBroker outside the tracked
        // TradingLoop -> OrderPipeline flow).
        ReconciliationReport report = Reconciler.check(List.of(), store, broker);

        assertFalse(report.isClean());
        assertEquals(1, report.mismatches().size());
        assertEquals(ReconciliationMismatchType.UNTRACKED_IN_BROKER, report.mismatches().get(0).type());
        assertEquals(order.clientOrderId(), report.mismatches().get(0).orderId());
    }

    @Test
    void independentMismatchesOnTheSameOrderIdAreBothReported() {
        OrderStore store = new OrderStore(); // empty
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO); // empty
        UUID missingId = UUID.randomUUID();

        // missingId appears twice in the submitted-ids history (a
        // DUPLICATE_SUBMISSION_ATTEMPT finding) AND has no OrderStore
        // record at all (a MISSING_FROM_ORDER_STORE finding) -- two
        // independent, real findings about the exact same id, and both
        // must surface, not just the first one found.
        ReconciliationReport report = Reconciler.check(List.of(missingId, missingId), store, broker);

        assertEquals(2, report.mismatches().size());
        List<ReconciliationMismatchType> types =
                report.mismatches().stream().map(ReconciliationMismatch::type).toList();
        assertTrue(types.contains(ReconciliationMismatchType.DUPLICATE_SUBMISSION_ATTEMPT));
        assertTrue(types.contains(ReconciliationMismatchType.MISSING_FROM_ORDER_STORE));
    }

    @Test
    void anEmptyEverythingReportsClean() {
        OrderStore store = new OrderStore();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);

        ReconciliationReport report = Reconciler.check(List.of(), store, broker);

        assertTrue(report.isClean());
    }
}
