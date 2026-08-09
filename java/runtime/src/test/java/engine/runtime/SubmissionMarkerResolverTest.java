package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.exchange.PositionSnapshot;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link SubmissionMarkerResolver} is the startup-time (or kill-switch-
 * reset-time) resolution step for any persisted {@link SubmissionMarker} --
 * see that class's own Javadoc and {@code .planning/paper-trading-h-vst-
 * integration.md} for the full design, including the documented judgment
 * call on why this uses {@code getPositions()} rather than {@code
 * queryOrder()} (a marker that never captured an {@code exchangeOrderId}
 * cannot be queried by one).
 */
class SubmissionMarkerResolverTest {

    private static PositionSnapshot position(String symbol, String positionAmt) {
        return new PositionSnapshot(
                symbol, "LONG", new BigDecimal(positionAmt), new BigDecimal("60000"), new BigDecimal("2"),
                BigDecimal.ZERO, new BigDecimal("40000"));
    }

    @Test
    void noMarkersMeansNothingToResolve(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertTrue(resolution.clearedAsSafe().isEmpty());
        assertTrue(resolution.requiresHumanReview().isEmpty());
    }

    /**
     * "Pre-acceptance failure" scenario from the governing task brief: the
     * ambiguous submit never actually reached the exchange, so no matching
     * position exists -- safe to clear the marker and allow a fresh submit.
     */
    @Test
    void aMarkerWithNoMatchingPositionIsClearedAsSafe(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID id = UUID.randomUUID();
        store.record(id, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of()); // no positions at all

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertEquals(1, resolution.clearedAsSafe().size());
        assertEquals(id, resolution.clearedAsSafe().get(0).clientOrderId());
        assertTrue(resolution.requiresHumanReview().isEmpty());
        assertTrue(store.all().isEmpty(), "the marker must actually be cleared from durable storage, not just reported");
    }

    /**
     * "Post-acceptance timeout" scenario from the governing task brief: the
     * order really was accepted by the exchange (a real position now exists
     * for that symbol) -- must NOT auto-clear (which would permit a future
     * duplicate resubmit) and must NOT silently retry; flagged for human
     * review instead, and the marker stays recorded.
     */
    @Test
    void aMarkerWithAMatchingNonZeroPositionRequiresHumanReviewAndIsNotCleared(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID id = UUID.randomUUID();
        store.record(id, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0.01")));

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertTrue(resolution.clearedAsSafe().isEmpty());
        assertEquals(1, resolution.requiresHumanReview().size());
        assertEquals(id, resolution.requiresHumanReview().get(0).clientOrderId());
        assertEquals(1, store.all().size(), "an unresolved marker must not be silently dropped");
    }

    @Test
    void aZeroSizedPositionForTheSymbolDoesNotCountAsMatchingSoTheMarkerIsClearedAsSafe(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID id = UUID.randomUUID();
        store.record(id, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0")));

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertEquals(1, resolution.clearedAsSafe().size());
    }

    @Test
    void aPositionForADifferentSymbolDoesNotResolveAMarkerForAnotherSymbol(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID id = UUID.randomUUID();
        store.record(id, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("ETH-USDT", "1.0")));

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertEquals(1, resolution.clearedAsSafe().size(), "an unrelated symbol's position must not block clearing");
    }

    @Test
    void multipleMarkersAreEachResolvedIndependentlyAgainstOneSharedPositionsCall(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID safeId = UUID.randomUUID();
        UUID reviewId = UUID.randomUUID();
        store.record(safeId, "ETH-USDT");
        store.record(reviewId, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0.01")));

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertEquals(1, resolution.clearedAsSafe().size());
        assertEquals(safeId, resolution.clearedAsSafe().get(0).clientOrderId());
        assertEquals(1, resolution.requiresHumanReview().size());
        assertEquals(reviewId, resolution.requiresHumanReview().get(0).clientOrderId());
    }

    @Test
    void doesNotCallGetPositionsAtAllWhenThereAreNoMarkersToResolve(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willFailPositionsWith(new RuntimeException("must not be called"));

        // Must not throw -- proves getPositions() was never invoked.
        SubmissionMarkerResolver.resolve(store, adapter);
    }
}
