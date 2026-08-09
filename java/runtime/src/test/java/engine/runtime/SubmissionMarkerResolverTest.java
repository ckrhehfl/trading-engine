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
 * integration.md} for the full design.
 *
 * <p><b>Revised after a real, correctly-identified CodeRabbit review finding
 * on this PR (marked Critical):</b> the original design cleared a marker
 * whenever {@code getPositions()} showed no matching non-zero position for
 * that marker's symbol, reasoning that this meant the submission "most
 * likely never reached the exchange." That reasoning was wrong: an order
 * that <i>was</i> accepted but is still open and unfilled (e.g. a
 * {@code LIMIT} order resting away from the market) also produces zero
 * matching position -- a position only exists once quantity is actually
 * filled. Auto-clearing in that case would have permitted a fresh resubmit
 * of an order that is, in fact, already live at the exchange -- a real
 * duplicate-order risk, the exact failure mode this whole mechanism exists
 * to prevent. There is no currently-available signal (see the class
 * Javadoc's own "why {@code getPositions()}, not {@code queryOrder}"
 * section -- unchanged, still true) that can positively rule out "the order
 * is open and unfilled." Every persisted marker is therefore now
 * <b>always</b> treated as unresolved and left recorded -- {@code
 * getPositions()} is still called and logged, purely as diagnostic context
 * for whichever human ends up investigating, but it no longer drives any
 * clear/no-clear decision.
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

        assertTrue(resolution.unresolvedMarkers().isEmpty());
    }

    /**
     * The specific scenario the fixed critical finding names: an order that
     * really was accepted but is still open/unfilled shows zero matching
     * position, exactly like an order that never reached the exchange at
     * all -- both must be treated identically (unresolved, never cleared).
     */
    @Test
    void aMarkerWithNoMatchingPositionIsStillTreatedAsUnresolvedNeverAutoCleared(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID id = UUID.randomUUID();
        store.record(id, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of()); // no positions at all -- ambiguous, not proof of absence

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertEquals(1, resolution.unresolvedMarkers().size());
        assertEquals(id, resolution.unresolvedMarkers().get(0).clientOrderId());
        assertEquals(1, store.all().size(), "an unresolved marker must never be silently dropped or auto-cleared");
    }

    @Test
    void aMarkerWithAMatchingNonZeroPositionIsAlsoUnresolvedAndNotCleared(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID id = UUID.randomUUID();
        store.record(id, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0.01")));

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertEquals(1, resolution.unresolvedMarkers().size());
        assertEquals(id, resolution.unresolvedMarkers().get(0).clientOrderId());
        assertEquals(1, store.all().size());
    }

    @Test
    void aZeroSizedPositionForTheSymbolStillLeavesTheMarkerUnresolved(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID id = UUID.randomUUID();
        store.record(id, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0")));

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertEquals(1, resolution.unresolvedMarkers().size());
        assertEquals(1, store.all().size());
    }

    @Test
    void aPositionForADifferentSymbolDoesNotAffectAMarkerForAnotherSymbol(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID id = UUID.randomUUID();
        store.record(id, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("ETH-USDT", "1.0")));

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertEquals(1, resolution.unresolvedMarkers().size());
    }

    @Test
    void multipleMarkersAreAllUnresolvedRegardlessOfIndividualPositionMatch(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID noPositionId = UUID.randomUUID();
        UUID matchingPositionId = UUID.randomUUID();
        store.record(noPositionId, "ETH-USDT");
        store.record(matchingPositionId, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0.01")));

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertEquals(2, resolution.unresolvedMarkers().size());
        assertEquals(2, store.all().size(), "neither marker is ever auto-cleared, regardless of position match");
    }

    @Test
    void doesNotCallGetPositionsAtAllWhenThereAreNoMarkersToResolve(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willFailPositionsWith(new RuntimeException("must not be called"));

        // Must not throw -- proves getPositions() was never invoked.
        SubmissionMarkerResolver.resolve(store, adapter);
    }

    /**
     * {@code getPositions()} is now purely diagnostic (see class Javadoc) --
     * its own failure must not block startup from reaching a safe state.
     * Every marker is unresolved regardless of whether the diagnostic call
     * itself succeeded.
     */
    @Test
    void getPositionsFailureIsToleratedAndAllMarkersAreStillReportedUnresolved(@TempDir Path tempDir) {
        SubmissionMarkerStore store = new SubmissionMarkerStore(tempDir.resolve("markers.json"));
        UUID id = UUID.randomUUID();
        store.record(id, "BTC-USDT");
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willFailPositionsWith(new RuntimeException("network error"));

        SubmissionMarkerResolver.Resolution resolution = SubmissionMarkerResolver.resolve(store, adapter);

        assertEquals(1, resolution.unresolvedMarkers().size());
        assertEquals(id, resolution.unresolvedMarkers().get(0).clientOrderId());
    }
}
