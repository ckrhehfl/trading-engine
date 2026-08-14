package engine.runtime;

import engine.exchange.BalanceSnapshot;
import engine.exchange.ExchangeAdapter;
import engine.exchange.PositionSnapshot;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.List;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Real startup check that must run against a real {@link ExchangeAdapter}
 * before {@code engine.execution.ExchangeOrderExecutor} is ever constructed
 * in {@code kis-paper} mode -- mirrors {@link VstPreflight}'s own shape, not
 * its specific gating logic (see CLAUDE.md's "KIS/KOSPI200 venue
 * integration, Phase 1" section for the full design and why the two
 * necessarily differ).
 *
 * <ol>
 *   <li><b>No asset-name check exists for KIS, unlike {@link
 *       VstPreflight}'s own core gate.</b> BingX's demo accounts settle in
 *       a textually distinct currency ({@code VST}), which is what {@link
 *       VstPreflight} checks. KIS has no such field anywhere in its real
 *       API (confirmed by real research during this design's own review --
 *       see CLAUDE.md). The closest available proxy instead: {@link
 *       ExchangeAdapter#getBalance()} succeeding <b>at all</b>, against the
 *       hardcoded paper-only host ({@code KIS_PAPER_BASE_URL}, a Java
 *       constant with no environment-variable override -- see {@link
 *       PaperTradingApp}'s own Javadoc, "Execution mode"), using
 *       paper-only App Key/Secret. KIS issues separate App Keys for real
 *       vs. paper trading (confirmed against KIS's own real source), so a
 *       real-trading key would be expected to fail authentication against
 *       the paper host -- this is a reasonable inference, not an
 *       empirically confirmed fact (no real KIS credentials existed for
 *       this project as of this writing); treat with the same reduced
 *       confidence CLAUDE.md already applies to every other KIS fact
 *       marked this way. Any failure here (auth rejection, network error)
 *       propagates uncaught, same fail-closed contract as {@link
 *       VstPreflight}'s own asset check -- this class never silently falls
 *       back to a different mode.
 *   <li>Logs the real balance (informational only, same "not a hard gate"
 *       treatment as {@link VstPreflight}'s own equivalent step -- KIS will
 *       itself reject an order on insufficient margin, which {@code
 *       KisAdapter.submitOrder} already maps to {@code order.reject()}).
 *   <li>{@link ExchangeAdapter#getPositions()} -- same reasoning and same
 *       {@link Result#killSwitchShouldStartTripped()} consequence as {@link
 *       VstPreflight}: this process has no restart-recovery/reconciliation
 *       story for pre-existing state, so a non-zero position found here
 *       means starting with the kill switch already tripped, requiring a
 *       deliberate human reset.
 *   <li><b>Leverage enforcement is skipped entirely here, not called as a
 *       no-op</b> (self-documenting, per {@code KisAdapter.setLeverage}'s
 *       own Javadoc -- KRX futures margin is exchange-mandated, not a
 *       user-settable multiplier the way BingX's is, so there is nothing
 *       for this step to meaningfully enforce). Real risk enforcement for
 *       this venue is {@code RiskGateway}'s own contract-multiplier
 *       notional conversion (see CLAUDE.md's {@code RiskLimits} section),
 *       not anything this class does.
 *   <li>Never logs the API key/secret anywhere in this class -- same
 *       structural guarantee as {@link VstPreflight}: constructed with only
 *       an {@link ExchangeAdapter} reference, which exposes no accessor for
 *       the credentials it was built with.
 * </ol>
 */
public final class KisPreflight {

    private static final Logger log = LoggerFactory.getLogger(KisPreflight.class);

    /** Same figure and same purely-informational role as {@link VstPreflight}'s own identically-named constant -- see that class's Javadoc for why it's a deliberate, disclosed duplication rather than a shared constant. */
    private static final BigDecimal CANARY_SIZING_BASELINE = new BigDecimal("100000");

    private static final BigDecimal CANARY_SIZING_PERCENT = new BigDecimal("0.02");

    private KisPreflight() {}

    /** The result of a completed preflight run -- see class Javadoc for each field's meaning. */
    public record Result(BalanceSnapshot balance, boolean killSwitchShouldStartTripped) {}

    /**
     * Runs both real checks against {@code adapter} in order. Any exception
     * thrown by {@code adapter.getBalance()}/{@code adapter.getPositions()}
     * itself (e.g. a real auth or network failure) propagates uncaught --
     * this is a one-shot startup check, not a per-tick call with its own
     * retry/never-throw contract like {@code OrderExecutor#pollFills}.
     */
    public static Result run(ExchangeAdapter adapter) {
        Objects.requireNonNull(adapter, "adapter is required");

        BalanceSnapshot balance = adapter.getBalance();
        BigDecimal canaryOrderSizing = CANARY_SIZING_BASELINE.multiply(CANARY_SIZING_PERCENT);
        log.info(
                "KisPreflight: real KIS balance={} equity={} availableMargin={} usedMargin={} unrealizedProfit={}",
                balance.balance(),
                balance.equity(),
                balance.availableMargin(),
                balance.usedMargin(),
                balance.unrealizedProfit());
        if (balance.balance() == null || balance.balance().compareTo(canaryOrderSizing) < 0) {
            log.warn(
                    "KisPreflight: real KIS balance ({}) looks small relative to canary-tier sizing (~{}, {}% of"
                            + " the {} sim-equity baseline TradingLoop uses) -- informational only, not a hard"
                            + " gate here: KIS will itself reject an order on insufficient margin, which maps"
                            + " cleanly to order.reject(), so an under-funded account would just silently never"
                            + " trade rather than error outright.",
                    balance.balance(),
                    canaryOrderSizing,
                    CANARY_SIZING_PERCENT.multiply(new BigDecimal("100")).setScale(0, RoundingMode.UNNECESSARY),
                    CANARY_SIZING_BASELINE);
        }

        List<PositionSnapshot> positions = adapter.getPositions();
        boolean hasNonZeroPosition =
                positions.stream().anyMatch(p -> p.positionAmt() != null && p.positionAmt().signum() != 0);
        if (hasNonZeroPosition) {
            log.error(
                    "KisPreflight: non-zero position(s) found at startup -- this process has no restart-recovery"
                            + "/reconciliation-against-real-positions story for pre-existing state; starting with"
                            + " the kill switch already TRIPPED, a deliberate human reset is required before any"
                            + " new signal is submitted. positions={}",
                    positions);
            return new Result(balance, true);
        }
        log.info("KisPreflight: no pre-existing non-zero positions found, clean start");
        return new Result(balance, false);
    }
}
