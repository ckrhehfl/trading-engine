package engine.execution;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/**
 * One observation of what a fill was modelled to cost against what the
 * venue says it actually cost.
 *
 * <h2>Why this exists</h2>
 *
 * <p>Until 2026-09-12 the live loop's {@code Fill.fee} came from {@code
 * notional × feeBps} — the identical formula {@code PaperBroker} and
 * {@code python/backtest/fill.py} use. Live and backtest therefore agreed
 * on fees <em>by construction</em>, and the venue's own {@code commission}
 * field was parsed nowhere, so **no evidence about whether the cost model
 * is right could ever accumulate**.
 *
 * <p>That gap is row 2.5 of {@code
 * .planning/simulation-divergence-catalogue.md} ("`FEE_BPS = 5` … CONFIRMED
 * once — a single data point, not a distribution") and the measurement
 * {@code .planning/agent-factory-discuss.md} §6 argues should be built
 * first: realised cost drifting away from modelled cost is the honest,
 * measurable proxy for an edge being competed away, and it needs no agent
 * autonomy at all.
 *
 * <h2>What it deliberately does not do</h2>
 *
 * <p>It changes nothing about P&amp;L. {@code Fill.fee} still carries the
 * modelled figure. Switching live accounting to the realised one is a
 * behaviour change with its own consequences — not least that live and
 * backtest results would stop being comparable in a second way — and the
 * point of measuring first is to decide that on evidence rather than
 * in advance.
 */
public record CostDivergence(
        UUID clientOrderId,
        String symbol,
        BigDecimal notional,
        BigDecimal modelledFee,
        BigDecimal realisedFee,
        Instant observedAt) {

    private static final BigDecimal BPS_DIVISOR = new BigDecimal("10000");
    private static final int BPS_SCALE = 6;

    public CostDivergence {
        Objects.requireNonNull(clientOrderId, "clientOrderId is required");
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(notional, "notional is required");
        Objects.requireNonNull(modelledFee, "modelledFee is required");
        Objects.requireNonNull(realisedFee, "realisedFee is required");
        Objects.requireNonNull(observedAt, "observedAt is required");
        if (notional.signum() <= 0) {
            throw new IllegalArgumentException("notional must be positive, was " + notional);
        }
    }

    /** The venue's own fee as basis points of this fill's notional. */
    public BigDecimal realisedFeeBps() {
        return realisedFee.multiply(BPS_DIVISOR).divide(notional, BPS_SCALE, RoundingMode.HALF_UP).stripTrailingZeros();
    }

    /** The modelled fee as basis points — the constant, recovered from the figures actually used. */
    public BigDecimal modelledFeeBps() {
        return modelledFee.multiply(BPS_DIVISOR).divide(notional, BPS_SCALE, RoundingMode.HALF_UP).stripTrailingZeros();
    }

    /**
     * Realised minus modelled, in basis points. Positive means the venue
     * charged more than the model assumed — the direction that quietly
     * erodes a backtested edge.
     */
    public BigDecimal divergenceBps() {
        return realisedFeeBps().subtract(modelledFeeBps());
    }

    /** A single greppable, parseable line for the session log. */
    public String toLogLine() {
        return "cost_divergence"
                + " clientOrderId=" + clientOrderId
                + " symbol=" + symbol
                + " notional=" + notional.toPlainString()
                + " modelledFee=" + modelledFee.toPlainString()
                + " realisedFee=" + realisedFee.toPlainString()
                + " modelledFeeBps=" + modelledFeeBps().toPlainString()
                + " realisedFeeBps=" + realisedFeeBps().toPlainString()
                + " divergenceBps=" + divergenceBps().toPlainString()
                + " observedAt=" + observedAt;
    }
}
