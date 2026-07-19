package engine.oms;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;
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
    void concurrentCreateOrderWithSameIdProducesExactlyOneOrder() throws InterruptedException {
        OrderStore store = new OrderStore();
        UUID id = UUID.randomUUID();
        OrderIntent intent = limitIntent(id);
        RiskDecision decision = approved(id);

        int threadCount = 16;
        ExecutorService pool = Executors.newFixedThreadPool(threadCount);
        CountDownLatch ready = new CountDownLatch(threadCount);
        CountDownLatch start = new CountDownLatch(1);
        List<Order> results = new CopyOnWriteArrayList<>();

        for (int i = 0; i < threadCount; i++) {
            pool.submit(
                    () -> {
                        ready.countDown();
                        try {
                            start.await();
                        } catch (InterruptedException e) {
                            Thread.currentThread().interrupt();
                        }
                        results.add(store.createOrder(intent, decision));
                    });
        }

        ready.await();
        start.countDown();
        pool.shutdown();
        assertTrue(pool.awaitTermination(5, TimeUnit.SECONDS));

        Set<Order> distinct = results.stream().collect(Collectors.toSet());
        assertEquals(1, distinct.size());
    }
}
