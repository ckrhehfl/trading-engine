package engine.oms;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class OrderTest {

    private OrderIntent limitIntent(UUID id) {
        return new OrderIntent(
                id,
                "BTC-USDT",
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.5"),
                new BigDecimal("65000"),
                "15m",
                Instant.now());
    }

    private RiskDecision approved(UUID intentId, BigDecimal quantity) {
        return new RiskDecision(
                intentId, Decision.APPROVED, null, quantity, new BigDecimal("2"), Instant.now());
    }

    private Order newOrder() {
        UUID id = UUID.randomUUID();
        return Order.fromApprovedDecision(limitIntent(id), approved(id, new BigDecimal("0.5")));
    }

    @Test
    void newOrderStartsInNewState() {
        assertEquals(OrderState.NEW, newOrder().state());
    }

    @Test
    void fullLifecycleLimitOrderFillsCompletely() {
        Order order = newOrder();

        order.submit();
        assertEquals(OrderState.SUBMITTED, order.state());

        order.acknowledge("EX-1");
        assertEquals(OrderState.ACKNOWLEDGED, order.state());

        order.fill(new BigDecimal("0.2"));
        assertEquals(OrderState.PARTIALLY_FILLED, order.state());
        assertEquals(new BigDecimal("0.2"), order.filledQuantity());

        order.fill(new BigDecimal("0.3"));
        assertEquals(OrderState.FILLED, order.state());
        assertEquals(new BigDecimal("0.5"), order.filledQuantity());
    }

    @Test
    void consecutivePartialFillsStayPartiallyFilledUntilComplete() {
        UUID id = UUID.randomUUID();
        Order order = Order.fromApprovedDecision(limitIntent(id), approved(id, new BigDecimal("1.0")));
        order.submit();
        order.acknowledge("EX-1");

        order.fill(new BigDecimal("0.2"));
        assertEquals(OrderState.PARTIALLY_FILLED, order.state());

        order.fill(new BigDecimal("0.3"));
        assertEquals(OrderState.PARTIALLY_FILLED, order.state());
        assertEquals(new BigDecimal("0.5"), order.filledQuantity());
    }

    @Test
    void cannotAcknowledgeBeforeSubmit() {
        Order order = newOrder();
        assertThrows(IllegalStateException.class, () -> order.acknowledge("EX-1"));
    }

    @Test
    void cannotFillBeforeAcknowledge() {
        Order order = newOrder();
        order.submit();
        assertThrows(IllegalStateException.class, () -> order.fill(new BigDecimal("0.1")));
    }

    @Test
    void cannotSubmitTwice() {
        Order order = newOrder();
        order.submit();
        assertThrows(IllegalStateException.class, order::submit);
    }

    @Test
    void submittedOrderCanBeRejectedByExchange() {
        Order order = newOrder();
        order.submit();
        order.reject("insufficient margin");
        assertEquals(OrderState.REJECTED, order.state());
    }

    @Test
    void acknowledgedOrderCanExpire() {
        Order order = newOrder();
        order.submit();
        order.acknowledge("EX-1");
        order.expire();
        assertEquals(OrderState.EXPIRED, order.state());
    }

    @Test
    void terminalStatesRejectFurtherTransitions() {
        Order order = newOrder();
        order.submit();
        order.acknowledge("EX-1");
        order.fill(new BigDecimal("0.5")); // -> FILLED, terminal

        assertThrows(IllegalStateException.class, () -> order.fill(new BigDecimal("0.1")));
        assertThrows(IllegalStateException.class, order::requestCancel);
        assertThrows(IllegalStateException.class, () -> order.reject("late"));
        assertThrows(IllegalStateException.class, order::expire);
    }

    @Test
    void overfillIsRejected() {
        Order order = newOrder();
        order.submit();
        order.acknowledge("EX-1");
        assertThrows(IllegalArgumentException.class, () -> order.fill(new BigDecimal("0.6")));
    }

    @Test
    void zeroOrNegativeFillQuantityIsRejected() {
        Order order = newOrder();
        order.submit();
        order.acknowledge("EX-1");
        assertThrows(IllegalArgumentException.class, () -> order.fill(BigDecimal.ZERO));
        assertThrows(IllegalArgumentException.class, () -> order.fill(new BigDecimal("-0.1")));
    }

    @Test
    void cancelRequestThenConfirmReachesCancelled() {
        Order order = newOrder();
        order.submit();
        order.acknowledge("EX-1");
        order.requestCancel();
        assertEquals(OrderState.CANCEL_PENDING, order.state());
        order.confirmCancel();
        assertEquals(OrderState.CANCELLED, order.state());
    }

    @Test
    void fillCanRaceACancelRequestAndStillFillCompletely() {
        Order order = newOrder();
        order.submit();
        order.acknowledge("EX-1");
        order.requestCancel();

        order.fill(new BigDecimal("0.5"));

        assertEquals(OrderState.FILLED, order.state());
    }

    @Test
    void partialFillCanRaceACancelRequestAndStayOpen() {
        Order order = newOrder();
        order.submit();
        order.acknowledge("EX-1");
        order.requestCancel();

        order.fill(new BigDecimal("0.2"));

        assertEquals(OrderState.PARTIALLY_FILLED, order.state());
    }

    @Test
    void partiallyFilledOrderCanBeCancelled() {
        Order order = newOrder();
        order.submit();
        order.acknowledge("EX-1");
        order.fill(new BigDecimal("0.2"));

        order.requestCancel();
        order.confirmCancel();

        assertEquals(OrderState.CANCELLED, order.state());
        assertEquals(new BigDecimal("0.2"), order.filledQuantity());
    }

    @Test
    void fromApprovedDecisionRejectsRejectedDecision() {
        UUID id = UUID.randomUUID();
        OrderIntent intent = limitIntent(id);
        RiskDecision rejected =
                new RiskDecision(id, Decision.REJECTED, "daily loss limit", null, null, Instant.now());

        assertThrows(
                IllegalArgumentException.class, () -> Order.fromApprovedDecision(intent, rejected));
    }

    @Test
    void fromApprovedDecisionAcceptsModifiedDecision() {
        UUID id = UUID.randomUUID();
        OrderIntent intent = limitIntent(id);
        RiskDecision modified =
                new RiskDecision(
                        id,
                        Decision.MODIFIED,
                        "reduced size",
                        new BigDecimal("0.3"),
                        new BigDecimal("1.5"),
                        Instant.now());

        Order order = Order.fromApprovedDecision(intent, modified);

        assertEquals(new BigDecimal("0.3"), order.approvedQuantity());
    }

    @Test
    void fromApprovedDecisionRejectsIntentDecisionIdMismatch() {
        OrderIntent intent = limitIntent(UUID.randomUUID());
        RiskDecision decision = approved(UUID.randomUUID(), new BigDecimal("0.5"));

        assertThrows(
                IllegalArgumentException.class, () -> Order.fromApprovedDecision(intent, decision));
    }

    @Test
    void guardedMarketOrderCanBeConstructedWithoutLimitPrice() {
        UUID id = UUID.randomUUID();
        OrderIntent intent =
                new OrderIntent(
                        id,
                        "BTC-USDT",
                        Side.SHORT,
                        OrderType.GUARDED_MARKET,
                        new BigDecimal("0.5"),
                        null,
                        "15m",
                        Instant.now());

        Order order = Order.fromApprovedDecision(intent, approved(id, new BigDecimal("0.5")));

        assertNull(order.limitPrice());
    }

    @Test
    void transitionHistoryRecordsEachStepOldestFirst() {
        Order order = newOrder();
        order.submit();
        order.acknowledge("EX-1");
        order.fill(new BigDecimal("0.5"));

        List<StateTransition> history = order.history();

        assertEquals(4, history.size());
        assertEquals(OrderState.NEW, history.get(0).state());
        assertEquals(OrderState.SUBMITTED, history.get(1).state());
        assertEquals(OrderState.ACKNOWLEDGED, history.get(2).state());
        assertEquals(OrderState.FILLED, history.get(3).state());
    }

    @Test
    void historyIsUnmodifiable() {
        Order order = newOrder();
        assertThrows(
                UnsupportedOperationException.class,
                () -> order.history().add(new StateTransition(OrderState.NEW, Instant.now())));
    }
}
