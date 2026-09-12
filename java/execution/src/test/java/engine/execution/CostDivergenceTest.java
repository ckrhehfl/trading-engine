package engine.execution;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class CostDivergenceTest {

    /**
     * The canonical log line, asserted below and **read by
     * `python/tests/test_cost_divergence.py`** so the two languages cannot
     * drift apart silently.
     *
     * <p>The reader and the writer are joined only by this format; nothing
     * checks it at compile time, and a Python test written against a
     * hand-copied example would keep passing while the real integration
     * broke. Same technique as the BTC-USDT step table, which is pinned
     * across the language boundary for the same reason.
     *
     * <p>The id is a deliberately synthetic low-entropy UUID rather than a
     * real one from the VST account. `gitleaks` flags a real UUID's entropy
     * as a possible generic API key and blocks the commit -- correctly
     * enough in spirit, since a committed test has no business carrying a
     * live account's identifiers.
     */
    static final String CANONICAL_LOG_LINE =
            "cost_divergence clientOrderId=00000000-0000-4000-8000-000000000001"
                    + " symbol=BTC-USDT notional=7.93808 modelledFee=0.00396904"
                    + " realisedFee=0.0055 cumulativeQty=0.0001"
                    + " modelledFeeBps=5 realisedFeeBps=6.928628"
                    + " divergenceBps=1.928628 observedAt=2026-09-12T10:07:00.351091713Z";

    private CostDivergence canonical() {
        return new CostDivergence(
                UUID.fromString("00000000-0000-4000-8000-000000000001"),
                "BTC-USDT",
                new BigDecimal("7.93808"),
                new BigDecimal("0.00396904"),
                new BigDecimal("0.0055"),
                new BigDecimal("0.0001"),
                Instant.parse("2026-09-12T10:07:00.351091713Z"));
    }

    @Test
    void toLogLineProducesTheCanonicalFormat() {
        assertEquals(CANONICAL_LOG_LINE, canonical().toLogLine());
    }

    @Test
    void bpsAreComputedFromTheFiguresRatherThanAssumed() {
        // 0.00396904 / 7.93808 = 5bps exactly -- the modelled constant,
        // recovered rather than restated, so a wrong feeBps shows up here.
        assertEquals(0, new BigDecimal("5").compareTo(canonical().modelledFeeBps()));
        // 0.0055 / 7.93808 = 6.928628bps. Written out to six places
        // because a hand-rounded 6.928 was the first version of this
        // test and it failed -- which is what pinning against the real
        // implementation is for.
        assertEquals(0, new BigDecimal("6.928628").compareTo(canonical().realisedFeeBps()));
        assertEquals(0, new BigDecimal("1.928628").compareTo(canonical().divergenceBps()));
    }

    @Test
    void divergenceIsPositiveWhenTheVenueChargedMoreThanModelled() {
        // The direction that quietly erodes a backtested edge, so its sign
        // must not be ambiguous.
        assertEquals(1, canonical().divergenceBps().signum());
    }

    @Test
    void aNonPositiveNotionalIsRejectedRatherThanDividedBy() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new CostDivergence(
                        UUID.randomUUID(), "BTC-USDT", BigDecimal.ZERO,
                        BigDecimal.ONE, BigDecimal.ONE, BigDecimal.ONE, Instant.now()));
    }
}
