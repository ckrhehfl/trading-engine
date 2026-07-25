package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.oms.Order;
import engine.oms.OrderState;
import engine.oms.OrderStore;
import engine.risk.AccountState;
import engine.risk.RiskGateway;
import engine.risk.RiskLimits;
import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class OrderPipelineTest {

    /**
     * Captures the exact {@link RiskDecision} instance {@link RiskGateway#evaluate}
     * returns on each call, while still delegating to the real risk logic — used
     * to prove {@link OrderPipeline} passes that literal object through rather
     * than reconstructing an equivalent one. Requires {@code RiskGateway} to be
     * non-final (see RiskGateway's Javadoc): neither it nor {@code OrderStore} can
     * otherwise be intercepted, and this module has no mocking framework.
     */
    private static final class RecordingRiskGateway extends RiskGateway {
        private RiskDecision lastDecision;

        RecordingRiskGateway(RiskLimits limits) {
            super(limits);
        }

        @Override
        public RiskDecision evaluate(OrderIntent intent, BigDecimal referencePrice, AccountState account) {
            RiskDecision decision = super.evaluate(intent, referencePrice, account);
            lastDecision = decision;
            return decision;
        }
    }

    private OrderIntent limitIntent(UUID intentId, BigDecimal quantity, BigDecimal price) {
        return new OrderIntent(
                intentId, "BTC-USDT", Side.LONG, OrderType.LIMIT, quantity, price, "15m", Instant.now());
    }

    private OrderIntent limitIntent(BigDecimal quantity, BigDecimal price) {
        return limitIntent(UUID.randomUUID(), quantity, price);
    }

    private AccountState healthyAccount(BigDecimal equity) {
        return new AccountState(equity, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
    }

    @Test
    void approvedIntentCreatesOrderRegisteredInStoreInNewState() {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = new OrderPipeline(gateway, store);
        // notional 0.01 * 60000 = 600, well under maxNotional (0.02 * 100000 = 2000) -> APPROVED
        OrderIntent intent = limitIntent(new BigDecimal("0.01"), new BigDecimal("60000"));
        AccountState account = healthyAccount(new BigDecimal("100000"));

        Optional<Order> result = pipeline.submitIntent(intent, new BigDecimal("60000"), account);

        assertTrue(result.isPresent());
        Order order = result.get();
        assertEquals(new BigDecimal("0.01"), order.approvedQuantity());
        assertEquals(OrderState.NEW, order.state());
        assertEquals(Optional.of(order), store.findByClientOrderId(intent.intentId()));
    }

    @Test
    void modifiedIntentCreatesOrderWithRiskGatewayClampedQuantityNotTheRequestedQuantity() {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = new OrderPipeline(gateway, store);
        // notional 0.1 * 60000 = 6000, over maxNotional 2000 -> clamped to 0.03333333
        OrderIntent intent = limitIntent(new BigDecimal("0.1"), new BigDecimal("60000"));
        AccountState account = healthyAccount(new BigDecimal("100000"));

        Optional<Order> result = pipeline.submitIntent(intent, new BigDecimal("60000"), account);

        assertTrue(result.isPresent());
        Order order = result.get();
        // Genuinely different from what was requested -- proves the Order
        // reflects RiskGateway's actual modification, not a pass-through of
        // the original request.
        assertNotEquals(0, order.requestedQuantity().compareTo(order.approvedQuantity()));
        assertEquals(new BigDecimal("0.03333333"), order.approvedQuantity());
        assertEquals(RiskLimits.canary().baseLeverage(), order.approvedLeverage());
    }

    @Test
    void rejectedIntentReturnsEmptyAndCreatesNoOrder() {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = new OrderPipeline(gateway, store);
        // daily loss limit for canary is -0.005; -0.006 breaches it
        OrderIntent intent = limitIntent(new BigDecimal("0.01"), new BigDecimal("60000"));
        AccountState account =
                new AccountState(new BigDecimal("100000"), new BigDecimal("-0.006"), BigDecimal.ZERO, BigDecimal.ZERO);

        Optional<Order> result = pipeline.submitIntent(intent, new BigDecimal("60000"), account);

        assertFalse(result.isPresent());
        assertFalse(store.findByClientOrderId(intent.intentId()).isPresent());
    }

    @Test
    void identicalRetrySameIntentIdReturnsTheOriginalOrderWithoutCreatingASecondOne() {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = new OrderPipeline(gateway, store);
        UUID intentId = UUID.randomUUID();
        OrderIntent intent = limitIntent(intentId, new BigDecimal("0.01"), new BigDecimal("60000"));
        AccountState account = healthyAccount(new BigDecimal("100000"));

        Optional<Order> first = pipeline.submitIntent(intent, new BigDecimal("60000"), account);
        // Same intent object, same account/price -> RiskGateway evaluates a
        // fresh but value-equal APPROVED decision each time; OrderStore's
        // existing idempotency (Order.matches) must recognize this as the
        // same request and hand back the original Order rather than a
        // second, distinct instance.
        Optional<Order> second = pipeline.submitIntent(intent, new BigDecimal("60000"), account);

        assertTrue(first.isPresent());
        assertTrue(second.isPresent());
        assertSame(first.get(), second.get());
    }

    @Test
    void conflictingRetrySameIntentIdDifferentQuantityThrowsAndDoesNotBypassOrderStoreConflictDetection() {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = new OrderPipeline(gateway, store);
        UUID intentId = UUID.randomUUID();
        OrderIntent first = limitIntent(intentId, new BigDecimal("0.01"), new BigDecimal("60000"));
        // Same intentId, genuinely different requested quantity -- a
        // conflicting retry, not an idempotent replay.
        OrderIntent conflicting = limitIntent(intentId, new BigDecimal("0.02"), new BigDecimal("60000"));
        AccountState account = healthyAccount(new BigDecimal("100000"));

        Optional<Order> firstResult = pipeline.submitIntent(first, new BigDecimal("60000"), account);
        assertTrue(firstResult.isPresent());

        assertThrows(
                IllegalStateException.class,
                () -> pipeline.submitIntent(conflicting, new BigDecimal("60000"), account));
        // The original order must still be the only one on record, untouched.
        assertEquals(
                new BigDecimal("0.01"),
                store.findByClientOrderId(intentId).orElseThrow().approvedQuantity());
    }

    @Test
    void submitIntentPassesTheLiteralRiskDecisionEvaluateReturnedIntoTheOrderNotAReconstruction() {
        RecordingRiskGateway gateway = new RecordingRiskGateway(RiskLimits.canary());
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = new OrderPipeline(gateway, store);
        // Forces MODIFIED: RiskGateway.evaluate() computes the clamped
        // quantity via a fresh BigDecimal#divide() call every invocation, so
        // it is never reference-equal to anything OrderPipeline could have
        // gotten from elsewhere (e.g. intent.quantity(), or its own
        // recomputation of the clamp) -- only a genuine pass-through of the
        // literal RiskDecision evaluate() returned can satisfy the identity
        // assertions below.
        OrderIntent intent = limitIntent(new BigDecimal("0.1"), new BigDecimal("60000"));
        AccountState account = healthyAccount(new BigDecimal("100000"));

        Optional<Order> result = pipeline.submitIntent(intent, new BigDecimal("60000"), account);

        assertTrue(result.isPresent());
        RiskDecision recorded = gateway.lastDecision;
        assertNotNull(recorded, "RecordingRiskGateway.evaluate() must have been called exactly once");
        assertEquals(Decision.MODIFIED, recorded.decision());
        // Sanity check the fixture actually clamps, so passing the decision
        // through vs. silently falling back to the request is observable.
        assertNotEquals(0, intent.quantity().compareTo(recorded.approvedQuantity()));

        Order order = result.get();
        // The provenance assertion: these are the *same BigDecimal objects*
        // recorded straight off RiskGateway.evaluate()'s return value, not
        // merely value-equal ones produced by some other path.
        assertSame(recorded.approvedQuantity(), order.approvedQuantity());
        assertSame(recorded.approvedLeverage(), order.approvedLeverage());
    }
}
