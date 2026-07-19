package engine.oms;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;

class OrderStoreTest {

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

    private RiskDecision approved(UUID intentId) {
        return new RiskDecision(
                intentId, Decision.APPROVED, null, new BigDecimal("0.5"), new BigDecimal("2"), Instant.now());
    }

    @Test
    void createOrderReturnsNewOrderForNewClientOrderId() {
        OrderStore store = new OrderStore();
        UUID id = UUID.randomUUID();

        Order order = store.createOrder(limitIntent(id), approved(id));

        assertEquals(id, order.clientOrderId());
    }

    @Test
    void duplicateCreateOrderReturnsTheSameOrderInstance() {
        OrderStore store = new OrderStore();
        UUID id = UUID.randomUUID();
        OrderIntent intent = limitIntent(id);
        RiskDecision decision = approved(id);

        Order first = store.createOrder(intent, decision);
        Order second = store.createOrder(intent, decision);

        assertSame(first, second);
    }

    @Test
    void retryWithDifferentApprovedQuantityIsRejectedAsConflicting() {
        OrderStore store = new OrderStore();
        UUID id = UUID.randomUUID();
        OrderIntent intent = limitIntent(id);
        store.createOrder(intent, approved(id));

        RiskDecision differentQuantity =
                new RiskDecision(id, Decision.APPROVED, null, new BigDecimal("0.9"), new BigDecimal("2"), Instant.now());

        assertThrows(IllegalStateException.class, () -> store.createOrder(intent, differentQuantity));
    }

    @Test
    void retryWithDifferentRequestedQuantityIsRejectedEvenIfApprovedQuantityCoincidentallyMatches() {
        OrderStore store = new OrderStore();
        UUID id = UUID.randomUUID();
        OrderIntent original = limitIntent(id); // requests 0.5
        store.createOrder(original, approved(id)); // Risk Gateway approves 0.5

        OrderIntent differentRequest =
                new OrderIntent(
                        id,
                        "BTC-USDT",
                        Side.LONG,
                        OrderType.LIMIT,
                        new BigDecimal("0.9"), // requested 0.9 this time
                        new BigDecimal("65000"),
                        "15m",
                        Instant.now());
        // Risk Gateway happens to approve the same 0.5 again — the
        // approved-quantity fingerprint alone would miss this conflict.
        RiskDecision sameApprovedQuantity = approved(id);

        assertThrows(
                IllegalStateException.class,
                () -> store.createOrder(differentRequest, sameApprovedQuantity));
    }

    @Test
    void retryWithNowRejectedDecisionIsRejectedAsConflicting() {
        OrderStore store = new OrderStore();
        UUID id = UUID.randomUUID();
        OrderIntent intent = limitIntent(id);
        store.createOrder(intent, approved(id));

        RiskDecision nowRejected =
                new RiskDecision(id, Decision.REJECTED, "reassessed as too risky", null, null, Instant.now());

        // computeIfAbsent never re-invokes fromApprovedDecision for an
        // existing key, so this must be caught by the conflict check, not
        // by Order.fromApprovedDecision's own REJECTED guard.
        assertThrows(IllegalStateException.class, () -> store.createOrder(intent, nowRejected));
    }

    @Test
    void findByClientOrderIdReturnsCreatedOrder() {
        OrderStore store = new OrderStore();
        UUID id = UUID.randomUUID();
        Order created = store.createOrder(limitIntent(id), approved(id));

        assertSame(created, store.findByClientOrderId(id).orElseThrow());
    }

    @Test
    void findByClientOrderIdIsEmptyForUnknownId() {
        OrderStore store = new OrderStore();
        assertTrue(store.findByClientOrderId(UUID.randomUUID()).isEmpty());
    }

    @Test
    void concurrentCreateOrderWithSameIdProducesExactlyOneOrderForEveryCaller()
            throws InterruptedException {
        OrderStore store = new OrderStore();
        UUID id = UUID.randomUUID();
        OrderIntent intent = limitIntent(id);
        RiskDecision decision = approved(id);

        int threadCount = 16;
        ExecutorService pool = Executors.newFixedThreadPool(threadCount);
        List<Callable<Order>> tasks = new ArrayList<>();
        for (int i = 0; i < threadCount; i++) {
            tasks.add(() -> store.createOrder(intent, decision));
        }

        List<Future<Order>> futures = pool.invokeAll(tasks);
        pool.shutdown();
        assertTrue(pool.awaitTermination(5, TimeUnit.SECONDS));

        List<Order> results = new ArrayList<>();
        for (Future<Order> future : futures) {
            // .get() rethrows if any task threw — a swallowed exception
            // would otherwise let this test pass with fewer than
            // threadCount successful calls.
            results.add(getOrThrowUnchecked(future));
        }

        assertEquals(threadCount, results.size());
        Order first = results.get(0);
        for (Order result : results) {
            assertSame(first, result);
        }
    }

    private static Order getOrThrowUnchecked(Future<Order> future) {
        try {
            return future.get();
        } catch (Exception e) {
            throw new AssertionError("createOrder task failed", e);
        }
    }
}
