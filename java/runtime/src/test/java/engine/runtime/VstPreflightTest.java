package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.exchange.BalanceSnapshot;
import engine.exchange.ExchangeException;
import engine.exchange.PositionMode;
import engine.exchange.PositionSnapshot;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * {@link VstPreflight} is the real startup check that must run before
 * {@code ExchangeOrderExecutor} is ever constructed in {@code bingx-vst}
 * mode -- see this class's own Javadoc and {@code .planning/paper-trading-
 * h-vst-integration.md} for the full design. Every test here drives {@link
 * VstPreflight#run} against a real {@link FakeExchangeAdapter} test double
 * -- no mocking framework, matching this codebase's established convention.
 *
 * <p><b>Leverage enforcement (added after a real, correctly-identified
 * CodeRabbit review finding on this PR):</b> the real VST verification run
 * (see {@code .planning/paper-trading-h-vst-integration.md}) found the real
 * account's own default leverage was {@code 20X} -- entirely unrelated to
 * and unenforced by this project's own {@code RiskGateway}-approved {@code
 * 1x} (canary base leverage), because nothing previously called {@code
 * setLeverage}. {@link VstPreflight#run} now takes a {@code symbol} and,
 * when starting clean (no pre-existing position), actively sets the
 * exchange-side leverage for both {@code LONG} and {@code SHORT} (hedge
 * mode -- the confirmed real default per CLAUDE.md, and the only shape
 * {@code BingXAdapter#setLeverage} sends) to {@code RiskLimits.canary()
 * .baseLeverage()} before any order can ever be submitted -- fails closed
 * (propagates) if either call fails, exactly like the asset check.
 */
class VstPreflightTest {

    private static final String SYMBOL = "BTC-USDT";

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

        IllegalStateException exception =
                assertThrows(IllegalStateException.class, () -> VstPreflight.run(adapter, SYMBOL));
        assertTrue(exception.getMessage().contains("VST"), "message should explain the expected asset");
        assertTrue(exception.getMessage().contains("USDT"), "message should include what was actually observed");
    }

    @Test
    void refusesToStartWhenBalanceAssetIsNull() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(new BalanceSnapshot(
                new BigDecimal("1000"), new BigDecimal("1000"), new BigDecimal("1000"), BigDecimal.ZERO,
                BigDecimal.ZERO, null));

        assertThrows(IllegalStateException.class, () -> VstPreflight.run(adapter, SYMBOL));
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

        IllegalStateException exception =
                assertThrows(IllegalStateException.class, () -> VstPreflight.run(adapter, SYMBOL));
        String lower = exception.getMessage().toLowerCase();
        assertFalse(lower.contains("apikey"));
        assertFalse(lower.contains("secret"));
    }

    @Test
    void passesAssetCheckAndStartsCleanWhenNoPositionsExist() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willReturnPositions(List.of());

        VstPreflight.Result result = VstPreflight.run(adapter, SYMBOL);

        assertEquals(0, new BigDecimal("5000").compareTo(result.balance().balance()));
        assertEquals("VST", result.balance().asset());
        assertFalse(result.killSwitchShouldStartTripped());
    }

    @Test
    void passesAssetCheckAndStartsCleanWhenAllPositionsAreExactlyZero() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0")));

        VstPreflight.Result result = VstPreflight.run(adapter, SYMBOL);

        assertFalse(result.killSwitchShouldStartTripped());
    }

    @Test
    void startsWithKillSwitchTrippedWhenANonZeroPositionExistsAtStartup() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0.01")));

        VstPreflight.Result result = VstPreflight.run(adapter, SYMBOL);

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

        VstPreflight.Result result = VstPreflight.run(adapter, SYMBOL);

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

        VstPreflight.Result result = VstPreflight.run(adapter, SYMBOL);

        assertFalse(result.killSwitchShouldStartTripped());
    }

    @Test
    void balanceFailurePropagatesUncaught() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willFailBalanceWith(new RuntimeException("network error"));

        assertThrows(RuntimeException.class, () -> VstPreflight.run(adapter, SYMBOL));
    }

    @Test
    void positionsFailurePropagatesUncaughtAfterAValidBalanceCheck() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willFailPositionsWith(new RuntimeException("network error"));

        assertThrows(RuntimeException.class, () -> VstPreflight.run(adapter, SYMBOL));
    }

    @Test
    void rejectsNullAdapter() {
        assertThrows(NullPointerException.class, () -> VstPreflight.run(null, SYMBOL));
    }

    @Test
    void rejectsNullSymbol() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        assertThrows(NullPointerException.class, () -> VstPreflight.run(adapter, null));
    }

    @Test
    void startingCleanSetsLeverageForBothSidesToTheCanaryBaseLeverage() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willReturnPositions(List.of());

        VstPreflight.run(adapter, SYMBOL);

        List<FakeExchangeAdapter.LeverageCall> calls = adapter.leverageCalls();
        assertEquals(2, calls.size());
        assertTrue(calls.stream().anyMatch(c -> c.symbol().equals(SYMBOL) && c.side() == Side.LONG && c.leverage() == 1));
        assertTrue(calls.stream().anyMatch(c -> c.symbol().equals(SYMBOL) && c.side() == Side.SHORT && c.leverage() == 1));
    }

    @Test
    void aNonZeroPreExistingPositionSkipsLeverageEnforcementEntirely() {
        // A leverage change while a position is open is commonly rejected
        // by exchanges (undocumented for BingX specifically, but not worth
        // risking) -- and moot anyway, since the kill switch is about to
        // trip regardless, so no order can be submitted until a human
        // resets it. Proven here by scripting setLeverage to throw if
        // called at all.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willReturnPositions(List.of(position("BTC-USDT", "0.01")));
        adapter.willFailSetLeverageWith(new RuntimeException("must not be called"));

        VstPreflight.Result result = VstPreflight.run(adapter, SYMBOL);

        assertTrue(result.killSwitchShouldStartTripped());
        assertTrue(adapter.leverageCalls().isEmpty());
    }

    @Test
    void aSetLeverageFailurePropagatesUncaughtAndRefusesToStart() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("5000"));
        adapter.willReturnPositions(List.of());
        adapter.willFailSetLeverageWith(new RuntimeException("exchange rejected leverage change"));

        assertThrows(RuntimeException.class, () -> VstPreflight.run(adapter, SYMBOL));
    }

    // ------------------------------------------------ startup retry (2026-09-09)
    //
    // A real restart on the VPS died here. Two loop JVMs started at once on a
    // 955 MB instance while the previous Gradle JVMs were still winding down,
    // and getBalance() came back `code=109400 timestamp is invalid` -- BingX
    // requires a request to arrive within 5s of the timestamp it was signed
    // with, and cold-start contention exceeded that. The clock was fine (392 ms
    // drift, NTP synchronised) and the identical call succeeded seconds later
    // once load eased.

    private static final VstPreflight.Sleeper NO_SLEEP = millis -> {};

    @Test
    void runWithRetryRecoversFromATransientExchangeFailure() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("100"));
        adapter.willReturnPositions(List.of());
        adapter.willFailBalanceTimesThenRecover(
                1, new ExchangeException("BingX getBalance failed: code=109400 msg=timestamp is invalid"));

        VstPreflight.Result result = VstPreflight.runWithRetry(adapter, SYMBOL, 3, NO_SLEEP);

        assertFalse(result.killSwitchShouldStartTripped());
        assertEquals(2, adapter.balanceCallCount(), "should have retried exactly once");
    }

    @Test
    void runWithRetryStillFailsClosedWhenEveryAttemptFails() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("100"));
        adapter.willReturnPositions(List.of());
        adapter.willFailBalanceTimesThenRecover(
                99, new ExchangeException("BingX getBalance failed: code=109400 msg=timestamp is invalid"));

        assertThrows(
                ExchangeException.class, () -> VstPreflight.runWithRetry(adapter, SYMBOL, 3, NO_SLEEP));
        assertEquals(3, adapter.balanceCallCount(), "should have tried exactly the attempt budget");
    }

    @Test
    void runWithRetryDoesNotRetryTheNonVstAssetRefusal() {
        // The one failure that must NEVER be retried. An asset that is not VST
        // means this may not be a demo account, and retrying a deliberate
        // safety stop is how a safety stop becomes a delay.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(
                new BalanceSnapshot(
                        new BigDecimal("100"), new BigDecimal("100"), new BigDecimal("100"),
                        BigDecimal.ZERO, BigDecimal.ZERO, "USDT"));
        adapter.willReturnPositions(List.of());

        assertThrows(
                IllegalStateException.class,
                () -> VstPreflight.runWithRetry(adapter, SYMBOL, 3, NO_SLEEP));
        assertEquals(1, adapter.balanceCallCount(), "a safety refusal must not be retried");
    }

    @Test
    void runWithRetryWaitsLongEnoughForBingXsOwnSigningWindow() {
        // NO_SLEEP discards the delay, so without this the 6s constant could
        // drift below BingX's 5s signing window -- the exact thing the wait
        // exists to outlast -- and every other test here would still pass.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("100"));
        adapter.willReturnPositions(List.of());
        adapter.willFailBalanceTimesThenRecover(1, new ExchangeException("transient"));

        List<Long> waits = new ArrayList<>();
        VstPreflight.runWithRetry(adapter, SYMBOL, 3, waits::add);

        assertEquals(List.of(VstPreflight.DEFAULT_RETRY_DELAY_MILLIS), waits);
        assertTrue(
                VstPreflight.DEFAULT_RETRY_DELAY_MILLIS >= 5_000L,
                "BingX rejects a request arriving more than 5s after its timestamp;"
                        + " a shorter wait can retry straight back into the same window");
    }

    @Test
    void runWithRetryRetriesOnlyExchangeFailures() {
        // The safety-relevant half of the contract. IllegalStateException is
        // covered above as the not-a-demo-account refusal, but that is one
        // RuntimeException among many -- widening the catch to RuntimeException
        // would retry a NullPointerException or a bug, and nothing else here
        // would notice.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("100"));
        adapter.willReturnPositions(List.of());
        adapter.willFailBalanceTimesThenRecover(1, new IllegalArgumentException("not a venue failure"));

        assertThrows(
                IllegalArgumentException.class,
                () -> VstPreflight.runWithRetry(adapter, SYMBOL, 3, NO_SLEEP));
        assertEquals(1, adapter.balanceCallCount(), "only an ExchangeException may be retried");
    }

    @Test
    void runWithRetryCallsOnceWhenNothingFails() {
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("100"));
        adapter.willReturnPositions(List.of());

        VstPreflight.runWithRetry(adapter, SYMBOL, 3, NO_SLEEP);

        assertEquals(1, adapter.balanceCallCount());
    }

    // ----------------------------------------- one-way position mode (#157)

    @Test
    void aCleanStartSetsTheAccountToOneWayMode() {
        // Nothing in this codebase ever called setPositionMode, so the
        // account ran in whatever BingX defaulted to -- hedge -- where a
        // SHORT meant to close a long opens a second position instead.
        // Confirmed on the real VST account 2026-09-10.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("100"));
        adapter.willReturnPositions(List.of());

        VstPreflight.run(adapter, SYMBOL);

        assertEquals(List.of(PositionMode.ONE_WAY), adapter.positionModeCalls());
    }

    @Test
    void aFailureToSetTheModeRefusesToStart() {
        // Fails closed for the same reason setLeverage does: proceeding
        // would mean trading while believing a safeguard applied that did
        // not. Here the "safeguard" is the meaning of every exit order.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("100"));
        adapter.willReturnPositions(List.of());
        adapter.willFailPositionModeWith(new ExchangeException("mode change refused"));

        assertThrows(ExchangeException.class, () -> VstPreflight.run(adapter, SYMBOL));
    }

    @Test
    void aPreExistingPositionSkipsTheModeChange() {
        // The venue cannot change position mode while a position is open,
        // and the kill switch starts tripped in this branch anyway, so
        // attempting it would only turn a handled condition into a crash.
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnBalance(vstBalance("100"));
        adapter.willReturnPositions(List.of(position(SYMBOL, "0.5")));

        VstPreflight.Result result = VstPreflight.run(adapter, SYMBOL);

        assertTrue(result.killSwitchShouldStartTripped());
        assertEquals(List.of(), adapter.positionModeCalls());
    }
}
