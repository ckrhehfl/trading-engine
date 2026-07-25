package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

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
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;

class OrderPipelineTest {

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

    /**
     * Provenance, part 1 (dynamic): {@link RiskGateway} and {@link OrderStore}
     * both stay exactly as they were before this module existed -- final,
     * unsubclassable, unmocked (this codebase has no mocking framework, and
     * weakening either class's finality to make one mockable would reopen
     * the same "arbitrary RiskDecision" bypass this pipeline exists to
     * close, just one level up -- a real {@link RiskGateway} subclass could
     * override {@code evaluate()} to always approve, and {@link OrderPipeline}
     * would have no way to tell). That constraint means a runtime capture of
     * the *exact* {@code RiskDecision} instance {@code evaluate()} produces
     * inside {@code submitIntent} isn't achievable without weakening a real
     * invariant -- so this test instead exploits a value RiskGateway.evaluate()
     * is documented (and verified in RiskGatewayTest) to pass through
     * completely unchanged on the APPROVED path: {@code intent.quantity()}
     * itself, object reference and all. If {@link OrderPipeline} ever
     * recomputed, copied, or reconstructed the approved quantity/leverage
     * from anywhere other than the literal RiskDecision evaluate() returned,
     * these object-identity assertions would break even though the values
     * would still look correct -- which is exactly the class of regression a
     * value-only test can't catch.
     */
    @Test
    void approvedIntentQuantityAndBaseLeverageFlowThroughByReferenceNotByValueCopy() {
        RiskLimits limits = RiskLimits.canary();
        RiskGateway gateway = new RiskGateway(limits);
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = new OrderPipeline(gateway, store);
        // A BigDecimal built fresh here, not one of the JDK's small-value
        // cached constants, so a later "== BigDecimal.ONE"-style accidental
        // pass couldn't produce a false positive.
        BigDecimal quantity = new BigDecimal("0.01");
        OrderIntent intent = limitIntent(quantity, new BigDecimal("60000"));
        AccountState account = healthyAccount(new BigDecimal("100000"));

        Optional<Order> result = pipeline.submitIntent(intent, new BigDecimal("60000"), account);

        assertTrue(result.isPresent());
        Order order = result.get();
        // RiskGateway.approve() returns intent.quantity() unchanged (see
        // RiskGateway.evaluate()'s APPROVED branch) -- so the only way this
        // Order's approvedQuantity() is the *same object* as the BigDecimal
        // this test constructed above is if that exact reference flowed,
        // untouched, all the way through evaluate() -> RiskDecision ->
        // Order.fromApprovedDecision().
        assertSame(quantity, order.approvedQuantity());
        // limits.baseLeverage() is a fixed field on the RiskLimits instance
        // this test constructed -- reused unchanged by every evaluate() call
        // against it, never recomputed.
        assertSame(limits.baseLeverage(), order.approvedLeverage());
    }

    /**
     * Provenance, part 2 (structural): the dynamic test above can't
     * distinguish a real pass-through from a hypothetical future refactor
     * that reconstructs a *new* RiskDecision purely from
     * {@code decision.approvedQuantity()}/{@code decision.approvedLeverage()}
     * getters -- Java doesn't defensively copy BigDecimal on read, so that
     * reconstruction would still carry the same object references and pass
     * every assertion above. That specific case isn't observable from
     * {@link Order}'s state under any technique (see
     * .planning/08-order-pipeline.md), so it's checked directly against
     * {@link OrderPipeline}'s own source instead: it must never construct
     * its own {@code RiskDecision}, and the single {@code evaluate()} call's
     * result must flow straight into {@code orderStore.createOrder(...)}
     * without being reassigned or rebuilt in between.
     */
    @Test
    void orderPipelineSourceNeverFabricatesARiskDecisionAndPassesTheSingleEvaluateResultDirectlyToOrderStore()
            throws IOException {
        Path source = Path.of("src/main/java/engine/runtime/OrderPipeline.java");
        assertTrue(Files.exists(source), () -> "expected to find OrderPipeline.java at " + source.toAbsolutePath());
        String code = Files.readString(source);

        assertFalse(
                code.contains("new RiskDecision("),
                "OrderPipeline must never construct its own RiskDecision -- Order.fromApprovedDecision must"
                        + " only ever receive whatever RiskGateway.evaluate() actually returned");

        long evaluateCallCount = countOccurrences(code, "riskGateway.evaluate(");
        assertEquals(1, evaluateCallCount, "expected exactly one call to riskGateway.evaluate() in OrderPipeline");

        long createOrderCallCount = countOccurrences(code, "orderStore.createOrder(");
        assertEquals(1, createOrderCallCount, "expected exactly one call to orderStore.createOrder() in OrderPipeline");

        // The local variable assigned straight from evaluate()'s return value
        // must be the same identifier passed into createOrder() -- proving
        // there's no reassignment or reconstruction of the decision between
        // the two calls, not just that both calls exist somewhere.
        assertTrue(
                Pattern.compile("RiskDecision\\s+(\\w+)\\s*=\\s*riskGateway\\.evaluate\\([^;]*\\);")
                        .matcher(code)
                        .results()
                        .anyMatch(match -> code.contains("orderStore.createOrder(intent, " + match.group(1) + ")")),
                "expected the variable assigned from riskGateway.evaluate() to be passed directly into"
                        + " orderStore.createOrder(), unmodified");
    }

    private static long countOccurrences(String haystack, String needle) {
        long count = 0;
        int index = 0;
        while ((index = haystack.indexOf(needle, index)) != -1) {
            count++;
            index += needle.length();
        }
        return count;
    }
}
