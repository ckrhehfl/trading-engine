package engine.runtime;

import engine.exchange.BalanceSnapshot;
import engine.exchange.ExchangeAdapter;
import engine.exchange.ExchangeException;
import engine.exchange.PositionMode;
import engine.exchange.PositionSnapshot;
import engine.risk.RiskLimits;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.List;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Real startup check that must run against a real {@link ExchangeAdapter}
 * before {@code engine.execution.ExchangeOrderExecutor} is ever constructed
 * in {@code bingx-vst} mode -- see {@code .planning/paper-trading-h-vst-
 * integration.md} for the full design and {@link PaperTradingApp}'s own
 * Javadoc for where this is wired in.
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
 *       the last point below). Also logs, informationally only -- never a
 *       hard gate -- if the balance looks small relative to canary-tier
 *       sizing (~2% of the {@code 100000} sim-equity baseline {@code
 *       TradingLoop} uses): BingX itself will reject an under-margined
 *       order (which {@code ExchangeOrderExecutor} already maps cleanly to
 *       {@code order.reject()}), so an under-funded VST account would just
 *       silently never trade rather than error outright -- logging loudly
 *       here means that gets noticed instead.
 *   <li>{@link ExchangeAdapter#getPositions()} -- any non-zero position
 *       found is state this process has no restart-recovery or
 *       reconciliation-against-real-positions story for anywhere else in
 *       this codebase. Rather than proceed to normal tick-driven trading
 *       against unknown pre-existing state, {@link Result#killSwitchShouldStartTripped()}
 *       comes back {@code true} -- the caller is expected to trip {@code
 *       KillSwitch} immediately after construction, requiring a deliberate
 *       human reset before any new signal is ever submitted. If a non-zero
 *       position is found, step 4 (leverage enforcement, below) is skipped
 *       entirely -- moot given no order can be submitted until a human
 *       resets the kill switch, and a leverage change while a position is
 *       open is commonly rejected by exchanges (not documented for BingX
 *       specifically, but not worth risking).
 *   <li><b>Position mode (added 2026-09-11, GitHub issue #157):</b> only
 *       when starting clean, sets the account to {@link
 *       PositionMode#ONE_WAY}. Nothing in this codebase had ever called
 *       {@code setPositionMode}, so the account ran in BingX's hedge
 *       default -- where a {@code SHORT} order meant to <em>close</em> a
 *       long opens a second position beside it instead. Confirmed on the
 *       real VST account: closing a 0.0001 long left the long untouched
 *       and a 0.0001 short next to it, margin posted on both, while the
 *       OMS recorded the close as {@code FILLED}. One-way is the mode
 *       every strategy here was written for, since {@code
 *       metrics.position.PositionTracker} keeps a single signed position
 *       and nets an opposite fill against it. Propagates on failure, for
 *       the same reason leverage does. See {@code
 *       .planning/position-truth-discuss.md} §9.
 *   <li><b>Leverage enforcement (added after a real, correctly-identified
 *       CodeRabbit review finding on this PR):</b> only when starting clean
 *       (step 3 found no pre-existing position), actively sets the real
 *       exchange-side leverage for {@code symbol}. Both {@code LONG} and
 *       {@code SHORT} are passed, which {@code BingXAdapter} now maps to
 *       {@code side=BOTH} in one-way mode (CLAUDE.md's Exchange API Facts:
 *       leverage takes {@code BOTH} in one-way and {@code LONG}/{@code
 *       SHORT} in hedge), to {@code RiskLimits.canary()
 *       .baseLeverage()} -- closing a real, empirically-confirmed gap: this
 *       project's own real VST verification run found a fresh account's
 *       real default leverage was {@code 20X}, entirely independent of and
 *       unenforced by {@code RiskGateway}'s own approved {@code 1x}, because
 *       nothing previously called {@code setLeverage} anywhere in this
 *       codebase. Fails closed (propagates) if either call fails, exactly
 *       like the asset check -- this process must not begin normal
 *       tick-driven trading believing leverage is constrained when it may
 *       not actually be.
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

    /** Injectable so a test can exercise the retry without actually waiting. */
    @FunctionalInterface
    public interface Sleeper {
        void sleep(long millis) throws InterruptedException;
    }

    /**
     * Attempts before giving up. Three, not more: the condition this exists
     * for clears in seconds, and a longer budget mostly delays a genuine
     * failure's report.
     */
    public static final int DEFAULT_ATTEMPTS = 3;

    /**
     * BingX rejects a request that arrives more than 5s after the timestamp
     * it was signed with, so the wait is that window plus a margin -- long
     * enough for cold-start contention to ease, short enough that a real
     * outage is reported promptly.
     */
    public static final long DEFAULT_RETRY_DELAY_MILLIS = 6_000L;

    /**
     * {@link #run} with a bounded retry for a <em>transient venue</em>
     * failure, and no retry for anything else.
     *
     * <h2>Why this exists, and why it does not contradict {@link #run}'s
     * "exceptions propagate uncaught"</h2>
     *
     * <p>A real restart died here on 2026-09-09. Both loop JVMs started at
     * once on a 955 MB instance while the previous Gradle JVMs were still
     * winding down, and {@code getBalance()} returned {@code code=109400
     * msg=timestamp is invalid} -- BingX requires a request to arrive within
     * 5s of the timestamp it was signed with, and cold-start contention
     * exceeded that. The machine's clock was not the problem: 392ms drift,
     * NTP synchronised, and the identical call succeeded seconds later once
     * load eased.
     *
     * <p>{@link #run}'s contract is unchanged and still what a caller gets by
     * default. What changes is that the <em>startup path</em> no longer treats
     * a few seconds of load contention as a reason to refuse to start, which
     * is not a safety property -- it is a loop that fails to come back.
     *
     * <p><b>Fail-closed is preserved, deliberately and narrowly.</b> Only
     * {@link ExchangeException} is retried: a venue or transport condition.
     * {@link IllegalStateException} -- the "balance asset is not VST, this may
     * not be a demo account" refusal -- is <b>never</b> retried, because
     * retrying a deliberate safety stop is how a safety stop becomes a delay.
     * When the attempt budget is exhausted the original exception is rethrown,
     * so a real outage still refuses to start.
     */
    public static Result runWithRetry(
            ExchangeAdapter adapter, String symbol, int attempts, Sleeper sleeper) {
        Objects.requireNonNull(sleeper, "sleeper is required");
        if (attempts < 1) {
            throw new IllegalArgumentException("attempts must be at least 1, got " + attempts);
        }
        ExchangeException last = null;
        for (int attempt = 1; attempt <= attempts; attempt++) {
            try {
                return run(adapter, symbol);
            } catch (ExchangeException e) {
                last = e;
                log.warn(
                        "VstPreflight attempt {} of {} failed against the venue: {}",
                        attempt,
                        attempts,
                        e.toString());
                if (attempt < attempts) {
                    try {
                        sleeper.sleep(DEFAULT_RETRY_DELAY_MILLIS);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        // Both facts survive: the venue error that caused the
                        // retry, and the interrupt that stopped it. Restoring
                        // the flag alone loses the shutdown signal from the
                        // exception chain, leaving a caller unable to tell an
                        // exchange outage from a deliberate stop.
                        e.addSuppressed(interrupted);
                        throw e;
                    }
                }
            }
        }
        throw last;
    }

    /** {@link #runWithRetry} with this class's own defaults and a real sleep. */
    public static Result runWithRetry(ExchangeAdapter adapter, String symbol) {
        return runWithRetry(adapter, symbol, DEFAULT_ATTEMPTS, Thread::sleep);
    }

    /**
     * Runs all five checks against {@code adapter} in order. Throws {@link
     * IllegalStateException} (step 1 only) rather than returning a failure
     * result -- an asset mismatch is not a recoverable condition this class
     * has any safe fallback for, so it must stop construction outright, not
     * hand back a value a careless caller could ignore. Any exception thrown
     * by {@code adapter.getBalance()}/{@code adapter.getPositions()}/{@code
     * adapter.setLeverage} itself (e.g. a real network failure) propagates
     * uncaught -- this is a one-shot startup check, not a per-tick call with
     * its own retry/never-throw contract like {@code OrderExecutor
     * #pollFills}.
     *
     * <p>The startup path calls {@link #runWithRetry} instead, which wraps
     * this in a bounded retry for a transient venue failure only. This
     * method's own contract is unchanged: it retries nothing.
     */
    /**
     * Reads the account's real mode and refuses to continue unless it is
     * {@link PositionMode#ONE_WAY}.
     *
     * <p>Called on <b>both</b> paths, because setting a mode and knowing one
     * are different things. On a clean start the set happens first and this
     * confirms it took; with a pre-existing position no set is possible --
     * a venue will not change mode while a position is open -- and this is
     * the only thing standing between a hedge account and an adapter built
     * for one-way, which would send {@code positionSide=BOTH} the moment a
     * human reset the kill switch. Raised by CodeRabbit on PR #161.
     */
    private static void requireOneWay(ExchangeAdapter adapter) {
        PositionMode actual = adapter.getPositionMode();
        if (actual != PositionMode.ONE_WAY) {
            throw new IllegalStateException(
                    "VstPreflight refusing to start: the account's real position mode is "
                            + actual
                            + ", not ONE_WAY. Every strategy here assumes a single netted position"
                            + " (metrics.position.PositionTracker), and in HEDGE an opposite-side order"
                            + " opens a second position instead of reducing one -- confirmed on the real"
                            + " VST account 2026-09-10. See GitHub issue #157.");
        }
        log.info("VstPreflight: account position mode verified as ONE_WAY");
    }

    public static Result run(ExchangeAdapter adapter, String symbol) {
        Objects.requireNonNull(adapter, "adapter is required");
        Objects.requireNonNull(symbol, "symbol is required");

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
                            + " new signal is submitted. Skipping leverage enforcement -- moot until a human"
                            + " resets the kill switch. positions={}",
                    positions);
            // No set is possible with a position open, so verification is the
            // only guard here -- and it must still run, or a kill-switch reset
            // would arm an adapter that disagrees with the account.
            requireOneWay(adapter);
            return new Result(balance, true);
        }
        log.info("VstPreflight: no pre-existing non-zero positions found, clean start");

        // Set the position mode explicitly, before anything can trade.
        //
        // Nothing in this codebase had ever called setPositionMode, so the
        // account ran in whatever BingX defaulted to -- hedge -- in which a
        // SHORT order meant to *close* a long opens a second position
        // beside it instead. Confirmed on the real VST account on
        // 2026-09-10: closing a 0.0001 long left the long untouched and a
        // 0.0001 short next to it, margin posted on both, while the OMS
        // recorded the close as FILLED.
        //
        // One-way is not a preference. It is the mode every strategy here
        // was written for: `metrics.position.PositionTracker`, which every
        // backtest runs on, keeps a single signed position and nets an
        // opposite fill against it. See `.planning/position-truth-discuss.md`
        // §9 and GitHub issue #157.
        //
        // Propagates on failure, exactly like setLeverage below and for the
        // same reason: starting anyway would mean trading while believing a
        // safeguard applied that did not -- and here the "safeguard" is what
        // every exit order means.
        //
        // Deliberately after the pre-existing-position check: a venue will
        // not change position mode while a position is open, and that branch
        // already starts the kill switch tripped, so attempting it there
        // would turn a handled condition into a crash.
        adapter.setPositionMode(PositionMode.ONE_WAY);
        log.info(
                "VstPreflight: position mode set to ONE_WAY -- an opposite-side order now reduces the"
                        + " position rather than opening a second one (see GitHub issue #157)");
        requireOneWay(adapter);

        int canaryBaseLeverage = RiskLimits.canary().baseLeverage().intValueExact();
        adapter.setLeverage(symbol, Side.LONG, canaryBaseLeverage);
        adapter.setLeverage(symbol, Side.SHORT, canaryBaseLeverage);
        log.info(
                "VstPreflight: real exchange-side leverage for {} set to {}x (LONG and SHORT, hedge mode) --"
                        + " matching RiskGateway's own canary-tier base leverage",
                symbol,
                canaryBaseLeverage);
        return new Result(balance, false);
    }
}
