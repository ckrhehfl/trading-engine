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
 * {@link VstPreflight} is the real, four-step startup check that must run
 * before {@code ExchangeOrderExecutor} is ever constructed in {@code
 * bingx-vst} mode -- see this class's own Javadoc and
 * {@code .planning/paper-trading-h-vst-integration.md} for the full design.
 * Every test here drives {@link VstPreflight#run} against a real
 * {@link FakeExchangeAdapter} test double -- no mocking framework, matching
 * this codebase's established convention.
 */
class VstPreflightTest {

    private static BalanceSnapshot vstBalance(String balance) {
        return new BalanceSnapshot(
                new BigDecimal(balance), new BigDecimal(balance), new BigDecimal(balance), BigDecimal.ZERO,
                BigDecimal.ZERO, "VST");
    }

    private static PositionSnapshot position(String symbol, String positionAmt) {
        return new PositionSnapshot(
                symbol, "LONG", new BigDecimal(positionAmt), new BigDecimal("60000"), new BigDecimal("2"),
                BigDecimal.ZERO, new BigDecimal("40000"));
    }

    @Test
    void refusesToStartWhenBalanceAssetIsNotExactlyVst() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(new BalanceSnapshot(
                new BigDecimal("1000"), new BigDecimal("1000"), new BigDecimal("1000"), BigDecimal.ZERO,
                BigDecimal.ZERO, "USDT"));

        IllegalStateException exception = assertThrows(IllegalStateException.class, () -> VstPreflight.run(adapter));
        assertTrue(exception.getMessage().contains("VST"), "message should explain the expected asset");
        assertTrue(exception.getMessage().contains("USDT"), "message should include what was actually observed");
    }

    @Test
    void refusesToStartWhenBalanceAssetIsNull() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(new BalanceSnapshot(
                new BigDecimal("1000"), new BigDecimal("1000"), new BigDecimal("1000"), BigDecimal.ZERO,
                BigDecimal.ZERO, null));

        assertThrows(IllegalStateException.class, () -> VstPreflight.run(adapter));
    }

    @Test
    void refusalMessageNeverIncludesTheWordKeyOrSecret() {
        // Cheap, direct proof that this class's own exception path can
        // never leak a credential-shaped string -- it never has access to
        // apiKey/apiSecret in the first place (constructed with only an
        // ExchangeAdapter reference), but this asserts the observable
        // contract, not just the absence of a field.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(new BalanceSnapshot(
                new BigDecimal("1000"), new BigDecimal("1000"), new BigDecimal("1000"), BigDecimal.ZERO,
                BigDecimal.ZERO, "USDT"));

        IllegalStateException exception = assertThrows(IllegalStateException.class, () -> VstPreflight.run(adapter));
        String lower = exception.getMessage().toLowerCase();
        assertFalse(lower.contains("apikey"));
        assertFalse(lower.contains("secret"));
    }

    @Test
    void passesAssetCheckAndStartsCleanWhenNoPositionsExist() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willReturnPositions(List.of());

        VstPreflight.Result result = VstPreflight.run(adapter);

        assertEquals(0, new BigDecimal("5000").compareTo(result.balance().balance()));
        assertEquals("VST", result.balance().asset());
        assertFalse(result.killSwitchShouldStartTripped());
    }

    @Test
    void passesAssetCheckAndStartsCleanWhenAllPositionsAreExactlyZero() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0")));

        VstPreflight.Result result = VstPreflight.run(adapter);

        assertFalse(result.killSwitchShouldStartTripped());
    }

    @Test
    void startsWithKillSwitchTrippedWhenANonZeroPositionExistsAtStartup() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0.01")));

        VstPreflight.Result result = VstPreflight.run(adapter);

        assertTrue(
                result.killSwitchShouldStartTripped(),
                "an unexplained pre-existing position must force a tripped start -- no restart-recovery/"
                        + "reconciliation-against-real-positions exists elsewhere in this codebase");
    }

    @Test
    void startsWithKillSwitchTrippedWhenANonZeroShortPositionExists() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willReturnPositions(List.of(position("BTC-USDT", "-0.01")));

        VstPreflight.Result result = VstPreflight.run(adapter);

        assertTrue(result.killSwitchShouldStartTripped());
    }

    @Test
    void doesNotHardFailOnASmallBalanceRelativeToCanarySizingOnlyLogsInformationally() {
        // Informational only per this class's own Javadoc -- BingX itself
        // rejects on insufficient margin (mapped to order.reject()), so this
        // must not throw or trip the kill switch on a small balance alone.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("1")); // far below the ~2000 canary-sizing baseline
        adapter.willReturnPositions(List.of());

        VstPreflight.Result result = VstPreflight.run(adapter);

        assertFalse(result.killSwitchShouldStartTripped());
    }

    @Test
    void balanceFailurePropagatesUncaught() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willFailBalanceWith(new RuntimeException("network error"));

        assertThrows(RuntimeException.class, () -> VstPreflight.run(adapter));
    }

    @Test
    void positionsFailurePropagatesUncaughtAfterAValidBalanceCheck() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willFailPositionsWith(new RuntimeException("network error"));

        assertThrows(RuntimeException.class, () -> VstPreflight.run(adapter));
    }

    @Test
    void rejectsNullAdapter() {
        assertThrows(NullPointerException.class, () -> VstPreflight.run(null));
    }
}
