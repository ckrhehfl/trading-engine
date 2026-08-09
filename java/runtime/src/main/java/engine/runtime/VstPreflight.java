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
 * Real, four-step startup check that must run against a real {@link
 * ExchangeAdapter} before {@code engine.execution.ExchangeOrderExecutor} is
 * ever constructed in {@code bingx-vst} mode -- see {@code
 * .planning/paper-trading-h-vst-integration.md} for the full design and
 * {@link PaperTradingApp}'s own Javadoc for where this is wired in.
 *
 * <ol>
 *   <li>{@link ExchangeAdapter#getBalance()} -- fail closed unless the real
 *       {@link BalanceSnapshot#asset()} is exactly {@code "VST"}. This is
 *       the second, independent safety layer alongside {@code
 *       PaperTradingApp}'s hardcoded {@code BINGX_VST_BASE_URL} constant
 *       (see that class's Javadoc): even though there is no configuration
 *       surface that could point the order-execution path at a different
 *       host, this step verifies the *account itself* really is a demo
 *       account before ever trading against it, rather than trusting the
 *       base URL alone. Throws {@link IllegalStateException} rather than
 *       silently falling back to simulated mode -- a silent fallback would
 *       be a worse failure than simply refusing to start, since an operator
 *       watching logs for "did bingx-vst mode start" would see nothing
 *       obviously wrong.
 *   <li>Logs the real balance (account state, not a secret -- safe to log,
 *       unlike the API key/secret this class never even has access to; see
 *       point 4 below). Also logs, informationally only -- never a hard
 *       gate -- if the balance looks small relative to canary-tier sizing
 *       (~2% of the {@code 100000} sim-equity baseline {@code TradingLoop}
 *       uses): BingX itself will reject an under-margined order (which
 *       {@code ExchangeOrderExecutor} already maps cleanly to {@code
 *       order.reject()}), so an under-funded VST account would just
 *       silently never trade rather than error outright -- logging loudly
 *       here means that gets noticed instead.
 *   <li>{@link ExchangeAdapter#getPositions()} -- any non-zero position
 *       found is state this process has no restart-recovery or
 *       reconciliation-against-real-positions story for anywhere else in
 *       this codebase. Rather than proceed to normal tick-driven trading
 *       against unknown pre-existing state, {@link Result#killSwitchShouldStartTripped()}
 *       comes back {@code true} -- the caller is expected to trip {@code
 *       KillSwitch} immediately after construction, requiring a deliberate
 *       human reset before any new signal is ever submitted.
 *   <li>Never logs the API key/secret anywhere in this class -- structurally
 *       guaranteed, not just a discipline: this class is constructed with
 *       only an {@link ExchangeAdapter} reference, which exposes no
 *       accessor for the credentials it was itself built with. See {@code
 *       PaperTradingApp}'s own startup log line for the audit of *that*
 *       class's log output, which this class does not touch.
 * </ol>
 */
public final class VstPreflight {

    private static final Logger log = LoggerFactory.getLogger(VstPreflight.class);

    static final String EXPECTED_ASSET = "VST";

    /**
     * Matches {@code TradingLoop.INITIAL_EQUITY} (that field is private, so
     * this is a deliberate, disclosed duplication of the same {@code 100000}
     * figure, not a shared constant -- {@code :runtime} has no shared
     * "constants" class to put it in, and introducing one for a single
     * informational-only figure would be over-engineering for what this is).
     */
    private static final BigDecimal CANARY_SIZING_BASELINE = new BigDecimal("100000");

    /** Canary tier's own max order notional percent -- see CLAUDE.md's Risk Parameters ("canary... max order notional 2%"). */
    private static final BigDecimal CANARY_SIZING_PERCENT = new BigDecimal("0.02");

    private VstPreflight() {}

    /** The result of a completed preflight run -- see class Javadoc for each field's meaning. */
    public record Result(BalanceSnapshot balance, boolean killSwitchShouldStartTripped) {}

    /**
     * Runs all four checks against {@code adapter} in order. Throws {@link
     * IllegalStateException} (step 1 only) rather than returning a failure
     * result -- an asset mismatch is not a recoverable condition this class
     * has any safe fallback for, so it must stop construction outright, not
     * hand back a value a careless caller could ignore. Any exception thrown
     * by {@code adapter.getBalance()}/{@code adapter.getPositions()} itself
     * (e.g. a real network failure) propagates uncaught -- this is a
     * one-shot startup check, not a per-tick call with its own retry/never-
     * throw contract like {@code OrderExecutor.pollFills}.
     */
    public static Result run(ExchangeAdapter adapter) {
        Objects.requireNonNull(adapter, "adapter is required");

        BalanceSnapshot balance = adapter.getBalance();
        if (!EXPECTED_ASSET.equals(balance.asset())) {
            throw new IllegalStateException(
                    "VstPreflight refusing to start: expected balance asset '" + EXPECTED_ASSET
                            + "' (BingX demo-trading currency) but observed '" + balance.asset()
                            + "' -- this looks like it may not be a demo/VST account; refusing to proceed rather"
                            + " than risk trading against real funds. See CLAUDE.md's Non-negotiable Rules.");
        }

        BigDecimal canaryOrderSizing = CANARY_SIZING_BASELINE.multiply(CANARY_SIZING_PERCENT);
        log.info(
                "VstPreflight: real VST balance={} equity={} availableMargin={} usedMargin={} unrealizedProfit={}",
                balance.balance(),
                balance.equity(),
                balance.availableMargin(),
                balance.usedMargin(),
                balance.unrealizedProfit());
        if (balance.balance() == null || balance.balance().compareTo(canaryOrderSizing) < 0) {
            log.warn(
                    "VstPreflight: real VST balance ({}) looks small relative to canary-tier sizing (~{}, {}% of"
                            + " the {} sim-equity baseline TradingLoop uses) -- informational only, not a hard"
                            + " gate here: BingX will itself reject an order on insufficient margin, which maps"
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
                    "VstPreflight: non-zero position(s) found at startup -- this process has no restart-recovery"
                            + "/reconciliation-against-real-positions story for pre-existing state; starting with"
                            + " the kill switch already TRIPPED, a deliberate human reset is required before any"
                            + " new signal is submitted. positions={}",
                    positions);
            return new Result(balance, true);
        }
        log.info("VstPreflight: no pre-existing non-zero positions found, clean start");
        return new Result(balance, false);
    }
}
