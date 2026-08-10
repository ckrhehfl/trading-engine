package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.oms.Order;
import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link MarkerRecordingSubmissionListener} is the real {@code
 * engine.execution.SubmissionListener} implementation backing durable
 * {@code SUBMISSION_UNKNOWN} handling in {@code bingx-vst} mode --
 * replaces the earlier {@code PersistentSubmissionOrderExecutor} decorator
 * design (a real CodeRabbit review finding on this PR correctly caught
 * that design as a third {@code OrderExecutor} implementation; see
 * {@code engine.execution.SubmissionListener}'s own Javadoc and {@code
 * .planning/paper-trading-h-vst-integration.md} for the full account).
 * This class is a thin delegate to {@link SubmissionMarkerStore} -- all
 * the real persistence logic is already tested by {@code
 * SubmissionMarkerStoreTest}, so these tests only cover this class's own
 * delegation contract.
 */
class MarkerRecordingSubmissionListenerTest {

    private static Order order(String quantity) {
        UUID id = UUID.randomUUID();
        OrderIntent intent = new OrderIntent(
                id, "BTC-USDT", Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal(quantity), null, "1d",
                Instant.now());
        RiskDecision decision = new RiskDecision(
                id, Decision.APPROVED, null, new BigDecimal(quantity), new BigDecimal("2"), Instant.now());
        return Order.fromApprovedDecision(intent, decision);
    }

    @Test
    void constructorRejectsNullStore() {
        assertThrows(NullPointerException.class, () -> new MarkerRecordingSubmissionListener(null));
    }

    @Test
    void beforeSubmitRecordsAMarkerForTheOrder(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        MarkerRecordingSubmissionListener listener = new MarkerRecordingSubmissionListener(store);
        Order order = order("0.01");

        listener.beforeSubmit(order);

        assertEquals(1, store.all().size());
        assertEquals(order.clientOrderId(), store.all().get(0).clientOrderId());
        assertEquals(order.symbol(), store.all().get(0).symbol());
    }

    @Test
    void afterSubmitSucceededClearsTheMarkerForTheOrder(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        MarkerRecordingSubmissionListener listener = new MarkerRecordingSubmissionListener(store);
        Order order = order("0.01");

        listener.beforeSubmit(order);
        listener.afterSubmitSucceeded(order);

        assertTrue(store.all().isEmpty(), "a submission that returned normally is no longer ambiguous -- must clear");
    }

    @Test
    void aMarkerLeftByBeforeSubmitAloneStaysRecordedIfAfterSubmitSucceededIsNeverCalled(@TempDir Path tempDir) {
        // Simulates ExchangeOrderExecutor.submit's own real contract: if
        // adapter.submitOrder throws, afterSubmitSucceeded is never
        // called (see SubmissionListener's own Javadoc) -- proving this
        // listener's own state (not ExchangeOrderExecutor's own
        // behavior, already covered by ExchangeOrderExecutorTest) leaves
        // the marker durably recorded in that case.
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        MarkerRecordingSubmissionListener listener = new MarkerRecordingSubmissionListener(store);
        Order order = order("0.01");

        listener.beforeSubmit(order);

        SubmissionMarkerStore reloaded = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        assertEquals(1, reloaded.all().size(), "the marker must survive a simulated restart when never cleared");
    }
}
