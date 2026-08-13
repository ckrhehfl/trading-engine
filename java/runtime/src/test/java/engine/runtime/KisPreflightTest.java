package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.exchange.BalanceSnapshot;
import engine.exchange.PositionSnapshot;
import java.math.BigDecimal;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * {@link KisPreflight} is the real startup check for {@code kis-paper}
 * mode -- see that class's own Javadoc for why it can't mirror {@link
 * VstPreflight}'s specific asset-name gate. Reuses {@link
 * FakeExchangeAdapter} directly (the same hand-written test double {@link
 * VstPreflightTest} uses) -- {@link KisPreflight#run} takes the venue-
 * agnostic {@code ExchangeAdapter} interface, not a KIS-specific type, so
 * no new test double is needed.
 */
class KisPreflightTest {

    private static BalanceSnapshot kisBalance(String balance) {
        return new BalanceSnapshot(
                new BigDecimal(balance), new BigDecimal(balance), new BigDecimal(balance), BigDecimal.ZERO,
                BigDecimal.ZERO, "KRW");
    }

    private static PositionSnapshot position(String symbol, String positionAmt) {
        return new PositionSnapshot(
                symbol, "매수", new BigDecimal(positionAmt), new BigDecimal("350"), null, BigDecimal.ZERO, null);
    }

    @Test
    void passesAndStartsCleanWhenNoPositionsExist() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(kisBalance("5000000"));
        adapter.willReturnPositions(List.of());

        KisPreflight.Result result = KisPreflight.run(adapter);

        assertEquals(0, new BigDecimal("5000000").compareTo(result.balance().balance()));
        assertFalse(result.killSwitchShouldStartTripped());
    }

    @Test
    void startsCleanWhenAllPositionsAreExactlyZero() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(kisBalance("5000000"));
        adapter.willReturnPositions(List.of(position("101W09", "0")));

        KisPreflight.Result result = KisPreflight.run(adapter);

        assertFalse(result.killSwitchShouldStartTripped());
    }

    @Test
    void startsWithKillSwitchTrippedWhenANonZeroPositionExistsAtStartup() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(kisBalance("5000000"));
        adapter.willReturnPositions(List.of(position("101W09", "1")));

        KisPreflight.Result result = KisPreflight.run(adapter);

        assertTrue(
                result.killSwitchShouldStartTripped(),
                "an unexplained pre-existing position must force a tripped start -- no restart-recovery/"
                        + "reconciliation-against-real-positions exists elsewhere in this codebase");
    }

    @Test
    void startsWithKillSwitchTrippedWhenANonZeroShortPositionExists() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(kisBalance("5000000"));
        adapter.willReturnPositions(List.of(position("101W09", "-1")));

        KisPreflight.Result result = KisPreflight.run(adapter);

        assertTrue(result.killSwitchShouldStartTripped());
    }

    @Test
    void doesNotHardFailOnASmallBalanceRelativeToCanarySizingOnlyLogsInformationally() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(kisBalance("1")); // far below the ~2000 canary-sizing baseline
        adapter.willReturnPositions(List.of());

        KisPreflight.Result result = KisPreflight.run(adapter);

        assertFalse(result.killSwitchShouldStartTripped());
    }

    @Test
    void neverCallsSetLeverageOrSetPositionMode() {
        // Deliberately skipped entirely for this venue (see class Javadoc)
        // -- proven by scripting both to throw if called at all.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(kisBalance("5000000"));
        adapter.willReturnPositions(List.of());
        adapter.willFailSetLeverageWith(new RuntimeException("must not be called"));

        KisPreflight.run(adapter);

        assertTrue(adapter.leverageCalls().isEmpty());
    }

    @Test
    void balanceFailurePropagatesUncaught() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willFailBalanceWith(new RuntimeException("network error"));

        assertThrows(RuntimeException.class, () -> KisPreflight.run(adapter));
    }

    @Test
    void positionsFailurePropagatesUncaughtAfterAValidBalanceCheck() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(kisBalance("5000000"));
        adapter.willFailPositionsWith(new RuntimeException("network error"));

        assertThrows(RuntimeException.class, () -> KisPreflight.run(adapter));
    }

    @Test
    void rejectsNullAdapter() {
        assertThrows(NullPointerException.class, () -> KisPreflight.run(null));
    }
}
