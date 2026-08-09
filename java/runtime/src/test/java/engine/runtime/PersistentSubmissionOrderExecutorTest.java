package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.execution.Fill;
import engine.execution.OrderExecutor;
import engine.oms.Order;
import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link PersistentSubmissionOrderExecutor} decorates any {@link
 * engine.execution.OrderExecutor} with a durable, {@code clientOrderId}-
 * keyed marker recorded immediately before an ambiguous {@code submit} call
 * -- see that class's own Javadoc and {@code .planning/paper-trading-h-vst-
 * integration.md} for the full {@code SUBMISSION_UNKNOWN} design. Every
 * {@link Order} here goes through {@link Order#fromApprovedDecision},
 * matching {@code BingXAdapterTest}/{@code PaperBrokerTest}'s own
 * established pattern -- proving this suite never takes the OMS-mediated-
 * flows-only shortcut CLAUDE.md's Priority #7 rule guards against.
 */
class PersistentSubmissionOrderExecutorTest {

    private static Order order(Side side, String quantity) {
        UUID id = UUID.randomUUID();
        OrderIntent intent = new OrderIntent(
                id, "BTC-USDT", side, OrderType.GUARDED_MARKET, new BigDecimal(quantity), null, "1d", Instant.now());
        RiskDecision decision = new RiskDecision(
                id, Decision.APPROVED, null, new BigDecimal(quantity), new BigDecimal("2"), Instant.now());
        return Order.fromApprovedDecision(intent, decision);
    }

    @Test
    void constructorRejectsNullArgs(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        FakeOrderExecutor delegate = new FakeOrderExecutor();
        assertThrows(NullPointerException.class, () -> new PersistentSubmissionOrderExecutor(null, store));
        assertThrows(NullPointerException.class, () -> new PersistentSubmissionOrderExecutor(delegate, null));
    }

    @Test
    void aSuccessfulSubmitRecordsThenClearsTheMarkerLeavingNoTraceBehind(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        FakeOrderExecutor delegate = new FakeOrderExecutor();
        PersistentSubmissionOrderExecutor executor = new PersistentSubmissionOrderExecutor(delegate, store);
        Order order = order(Side.LONG, "0.01");

        executor.submit(order, new BigDecimal("60000"));

        assertEquals(1, delegate.submitCallCount());
        assertTrue(store.all().isEmpty(), "a submit that returned normally is no longer ambiguous -- must clear");
    }

    @Test
    void anAmbiguousSubmitThatThrowsLeavesTheMarkerRecordedAndRethrows(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        FakeOrderExecutor delegate = new FakeOrderExecutor();
        delegate.throwOnNextSubmit(new RuntimeException("network timeout"));
        PersistentSubmissionOrderExecutor executor = new PersistentSubmissionOrderExecutor(delegate, store);
        Order order = order(Side.LONG, "0.01");

        assertThrows(RuntimeException.class, () -> executor.submit(order, new BigDecimal("60000")));

        assertEquals(1, store.all().size());
        assertEquals(order.clientOrderId(), store.all().get(0).clientOrderId());
        assertEquals("BTC-USDT", store.all().get(0).symbol());
    }

    @Test
    void theMarkerIsRecordedBeforeDelegateSubmitIsEverCalled(@TempDir Path tempDir) {
        // Proves the marker exists even if the process crashes mid-call --
        // not just "eventually recorded after the fact". Verified here by
        // constructing a delegate that itself checks the store's state
        // (via a second reference to the same file) at the moment it's
        // invoked.
        Path file = tempDir.resolve("markers.json");
        SubmissionMarkerStore store = new SubmissionMarkerStore(file);
        Order order = order(Side.LONG, "0.01");

        OrderExecutor delegate = new OrderExecutor() {
            @Override
            public Optional<Fill> submit(Order o, BigDecimal referencePrice) {
                SubmissionMarkerStore observedDuringCall = new SubmissionMarkerStore(file);
                assertEquals(
                        1,
                        observedDuringCall.all().size(),
                        "marker must already be durable on disk before delegate.submit runs");
                return Optional.empty();
            }

            @Override
            public List<Fill> pollFills(String symbol, BigDecimal referencePrice) {
                return List.of();
            }

            @Override
            public Map<UUID, Order> pendingOrders() {
                return Map.of();
            }

            @Override
            public void cancel(Order o) {}
        };
        PersistentSubmissionOrderExecutor executor = new PersistentSubmissionOrderExecutor(delegate, store);

        executor.submit(order, new BigDecimal("60000"));
    }

    @Test
    void pollFillsPendingOrdersAndCancelAllDelegateUnchanged(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        FakeOrderExecutor delegate = new FakeOrderExecutor();
        PersistentSubmissionOrderExecutor executor = new PersistentSubmissionOrderExecutor(delegate, store);
        Order order = order(Side.LONG, "0.01");
        executor.submit(order, new BigDecimal("60000"));

        assertEquals(1, executor.pendingOrders().size());
        assertTrue(executor.pollFills("BTC-USDT", new BigDecimal("60000")).isEmpty());

        executor.cancel(order);
        assertTrue(executor.pendingOrders().isEmpty());
    }
}
