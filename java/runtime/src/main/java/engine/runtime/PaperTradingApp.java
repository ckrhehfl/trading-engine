package engine.runtime;

import engine.execution.PaperBroker;
import engine.oms.OrderStore;
import engine.risk.RiskGateway;
import engine.risk.RiskLimits;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.util.Objects;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * {@code public static void main(String[] args)} entrypoint for the
 * paper-trading bridge -- Task C of the 5-task plan governing
 * {@code daily-tsmom-ensemble}'s human-approved move to paper trading
 * (see CLAUDE.md's "Paper Trading Policy Exception" and
 * {@code .planning/paper-trading-c-scheduler-entrypoint.md} for the full
 * design writeup). Wires together a real {@link RiskGateway} (canary
 * tier -- see CLAUDE.md's Risk Parameters, this is paper trading, the
 * conservative tier applies), {@link OrderStore}, {@link OrderPipeline},
 * {@link PaperBroker}, a {@link FileSignalSource} pointed at the signal
 * file Task B's Python runner writes, the existing {@link BingXPriceFeed}
 * (public market data only, no credentials), and {@link KillSwitch} into
 * one {@link TradingLoop}, then drives {@link TradingLoop#tick()} on a
 * fixed-rate {@code ScheduledExecutorService}.
 *
 * <p><b>Construction takes fully-resolved config values, not raw env-var
 * lookups.</b> {@link #fromEnvironment()} is a thin wrapper around the
 * constructor that resolves {@code System.getenv()} first -- this
 * codebase has no env-var mocking framework, so keeping the constructor
 * itself free of any {@code System.getenv()} call is what makes
 * construction/wiring logic directly unit-testable (see
 * {@code PaperTradingAppTest}).
 *
 * <p><b>No OS-level process supervision here</b> (systemd, restart-
 * recovery) -- this runs locally under manual/{@code tmux} supervision,
 * matching the governing plan's own explicit "runs locally, not VPS-
 * provisioned" decision. {@link #main} only handles in-process graceful
 * shutdown (a JVM shutdown hook stopping the {@code
 * ScheduledExecutorService} cleanly), not restarting a crashed process.
 *
 * <p><b>Internal consistency reconciliation</b> (paper-trading bridge Task
 * E, see {@code .planning/paper-trading-e-reconciliation.md}): {@link
 * #runTick()} calls {@link #reconcile()} after every scheduled {@link
 * TradingLoop#tick()}, regardless of whether that tick itself succeeded --
 * a tick failure and an internal-bookkeeping inconsistency are orthogonal
 * signals, and running the check unconditionally can surface exactly why a
 * tick failed (e.g. a duplicate-submission attempt). {@link #reconcile()}
 * is also public and safe to call directly (e.g. from a test, or a future
 * daily-report task) with no side effect beyond logging and, on a real
 * mismatch, tripping {@link #killSwitch} -- both idempotent.
 */
public final class PaperTradingApp {

    private static final Logger log = LoggerFactory.getLogger(PaperTradingApp.class);

    /** Matches {@code python/live/generate_daily_signal.py}'s own STRATEGY_ID -- used only to build the default signal path below. */
    static final String STRATEGY_ID = "daily-tsmom-ensemble";

    static final String DEFAULT_SYMBOL = "BTC-USDT";
    static final long DEFAULT_TICK_INTERVAL_SECONDS = 300; // 5 minutes -- see class/task planning doc for why

    /**
     * Same {@code FEE_BPS}/{@code SLIPPAGE_BPS} {@code python/live/
     * generate_daily_signal.py} documents using for consistency with the
     * pre-registered/backtested configuration (`sr-v`/`sr-ab`) -- reused
     * here on the same reasoning, not independently chosen for this
     * class. {@link PaperBroker}'s own fee/slippage simulation is a
     * separate concern from the strategy's backtested fee assumptions,
     * but using the same numbers keeps the whole bridge internally
     * consistent.
     */
    static final BigDecimal FEE_BPS = new BigDecimal("5");

    static final BigDecimal SLIPPAGE_BPS = new BigDecimal("2");

    static final String ENV_SYMBOL = "PAPER_TRADING_SYMBOL";
    static final String ENV_BINGX_BASE_URL = "BINGX_BASE_URL"; // matches the existing java/python-wide convention
    static final String ENV_SIGNAL_PATH = "PAPER_TRADING_SIGNAL_PATH";
    static final String ENV_TICK_INTERVAL_SECONDS = "PAPER_TRADING_TICK_INTERVAL_SECONDS";

    private final TradingLoop tradingLoop;
    private final OrderStore orderStore;
    private final PaperBroker paperBroker;
    private final KillSwitch killSwitch;
    private final long tickIntervalSeconds;
    private final ScheduledExecutorService executor;
    private volatile ScheduledFuture<?> scheduledTask;
    private volatile ReconciliationReport lastReconciliationReport;

    /**
     * Builds the real {@link RiskGateway}/{@link OrderStore}/
     * {@link OrderPipeline}/{@link PaperBroker}/{@link FileSignalSource}/
     * {@link BingXPriceFeed}/{@link KillSwitch}/{@link TradingLoop} graph.
     * No network call happens here -- {@link BingXPriceFeed}'s
     * constructor only stores {@code bingxBaseUrl}, it does not connect
     * until {@link TradingLoop#tick()} actually calls it -- so
     * constructing this class is always safe/fast, even against an
     * unreachable {@code bingxBaseUrl}.
     */
    public PaperTradingApp(String symbol, String bingxBaseUrl, Path signalPath, long tickIntervalSeconds) {
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(bingxBaseUrl, "bingxBaseUrl is required");
        Objects.requireNonNull(signalPath, "signalPath is required");
        if (tickIntervalSeconds <= 0) {
            throw new IllegalArgumentException("tickIntervalSeconds must be positive, was " + tickIntervalSeconds);
        }
        this.tickIntervalSeconds = tickIntervalSeconds;

        RiskGateway riskGateway = new RiskGateway(RiskLimits.canary());
        this.orderStore = new OrderStore();
        OrderPipeline orderPipeline = new OrderPipeline(riskGateway, this.orderStore);
        this.paperBroker = new PaperBroker(FEE_BPS, SLIPPAGE_BPS);
        FileSignalSource signalSource = new FileSignalSource(signalPath);
        BingXPriceFeed priceFeed = new BingXPriceFeed(bingxBaseUrl);
        this.killSwitch = new KillSwitch();

        this.tradingLoop =
                new TradingLoop(orderPipeline, this.paperBroker, signalSource, priceFeed, this.killSwitch, symbol);
        this.executor = Executors.newSingleThreadScheduledExecutor(r -> new Thread(r, "paper-trading-loop"));

        log.info(
                "PaperTradingApp constructed: symbol={} bingxBaseUrl={} signalPath={} tickIntervalSeconds={}"
                        + " riskTier=canary",
                symbol,
                bingxBaseUrl,
                signalPath,
                tickIntervalSeconds);
    }

    /**
     * Resolves configuration from environment variables and constructs a
     * real {@code PaperTradingApp} -- what {@link #main} calls. Every env
     * var:
     *
     * <ul>
     *   <li>{@code PAPER_TRADING_SYMBOL} (optional, default {@code
     *       BTC-USDT})
     *   <li>{@code BINGX_BASE_URL} (required, no default -- matches the
     *       existing java/python-wide convention of never hardcoding a
     *       BingX host in source; fails fast with a clear message if
     *       unset)
     *   <li>{@code PAPER_TRADING_SIGNAL_PATH} (optional, default {@code
     *       var/live/signals/<symbol>/daily-tsmom-ensemble/latest.json},
     *       resolved relative to the JVM's working directory -- this
     *       process must be launched with its working directory set to
     *       the repository root, matching {@code python/live/
     *       generate_daily_signal.py}'s own identical assumption for the
     *       same path)
     *   <li>{@code PAPER_TRADING_TICK_INTERVAL_SECONDS} (optional,
     *       default 300 / 5 minutes)
     * </ul>
     */
    public static PaperTradingApp fromEnvironment() {
        String symbol = firstNonBlank(System.getenv(ENV_SYMBOL), DEFAULT_SYMBOL);
        String bingxBaseUrl = requireNonBlank(System.getenv(ENV_BINGX_BASE_URL), ENV_BINGX_BASE_URL);
        Path signalPath = resolveSignalPath(System.getenv(ENV_SIGNAL_PATH), symbol);
        long tickIntervalSeconds = resolveTickIntervalSeconds(System.getenv(ENV_TICK_INTERVAL_SECONDS));
        return new PaperTradingApp(symbol, bingxBaseUrl, signalPath, tickIntervalSeconds);
    }

    static String firstNonBlank(String value, String fallback) {
        return (value == null || value.isBlank()) ? fallback : value;
    }

    static String requireNonBlank(String value, String envVarName) {
        if (value == null || value.isBlank()) {
            throw new IllegalStateException(
                    envVarName + " environment variable is required (never hardcoded in source -- see"
                            + " .github/workflows/bingx-hostname-guard.yml and CLAUDE.md's Non-negotiable Rules)");
        }
        return value;
    }

    /**
     * {@code var/live/signals/{symbol}/daily-tsmom-ensemble/latest.json}
     * when {@code raw} is null/blank -- matches {@code python/live/
     * generate_daily_signal.py}'s own {@code default_signal_path()}
     * exactly. Resolved relative to the JVM's working directory when
     * {@code raw} is relative (the default is); an absolute {@code raw}
     * override is unaffected by working directory.
     */
    static Path resolveSignalPath(String raw, String symbol) {
        if (raw != null && !raw.isBlank()) {
            return Path.of(raw);
        }
        return Path.of("var", "live", "signals", symbol, STRATEGY_ID, "latest.json");
    }

    static long resolveTickIntervalSeconds(String raw) {
        if (raw == null || raw.isBlank()) {
            return DEFAULT_TICK_INTERVAL_SECONDS;
        }
        long parsed;
        try {
            parsed = Long.parseLong(raw.trim());
        } catch (NumberFormatException e) {
            throw new IllegalStateException(
                    ENV_TICK_INTERVAL_SECONDS + " must be a positive integer, was '" + raw + "'", e);
        }
        if (parsed <= 0) {
            throw new IllegalStateException(ENV_TICK_INTERVAL_SECONDS + " must be positive, was " + parsed);
        }
        return parsed;
    }

    /**
     * Starts the scheduled loop: an immediate first tick (delay 0 -- picks
     * up any already-written signal file right away rather than waiting a
     * full interval), then every {@code tickIntervalSeconds} thereafter.
     * May only be called once per instance.
     */
    public synchronized void start() {
        if (scheduledTask != null) {
            throw new IllegalStateException("already started");
        }
        log.info("starting paper trading loop: tickIntervalSeconds={}", tickIntervalSeconds);
        scheduledTask = executor.scheduleAtFixedRate(this::runTick, 0, tickIntervalSeconds, TimeUnit.SECONDS);
    }

    private void runTick() {
        tradingLoop.tick();
        Throwable error = tradingLoop.lastError();
        if (error != null) {
            log.warn(
                    "tick completed with an error: lastTickAt={} equity={} error={}",
                    tradingLoop.lastTickAt(),
                    tradingLoop.currentEquity(),
                    error.toString());
        } else {
            log.info("tick complete: lastTickAt={} equity={}", tradingLoop.lastTickAt(), tradingLoop.currentEquity());
        }
        // Runs regardless of the tick's own outcome above -- see class
        // Javadoc, "Internal consistency reconciliation".
        reconcile();
    }

    /**
     * Runs {@link Reconciler#check} against this app's own {@link
     * OrderStore} and {@link PaperBroker}, using {@link TradingLoop
     * #submittedOrderIds()} as the known-submission history -- see class
     * Javadoc and {@code .planning/paper-trading-e-reconciliation.md} for
     * the full design and why a detected mismatch trips {@link
     * #killSwitch}. Updates {@link #lastReconciliationReport()} with the
     * result before returning it.
     */
    public ReconciliationReport reconcile() {
        ReconciliationReport report = Reconciler.check(tradingLoop.submittedOrderIds(), orderStore, paperBroker);
        lastReconciliationReport = report;
        if (!report.isClean()) {
            log.error(
                    "internal consistency check found {} mismatch(es); tripping kill switch -- see the individual"
                            + " mismatch log line(s) above for detail",
                    report.mismatches().size());
            killSwitch.trip();
        }
        return report;
    }

    /**
     * The most recent {@link #reconcile()} result, or {@code null} if
     * {@link #reconcile()} has never run yet (e.g. before the first
     * scheduled tick). Read-only -- unlike {@link #reconcile()} itself,
     * calling this has no side effect and never trips the kill switch.
     */
    public ReconciliationReport lastReconciliationReport() {
        return lastReconciliationReport;
    }

    /**
     * Cleanly stops the scheduled loop: no new tick is scheduled after
     * this returns, and any in-flight tick is given up to 10 seconds to
     * finish before a forced {@code shutdownNow()}. Called from {@link
     * #main}'s JVM shutdown hook (SIGTERM / normal JVM exit); safe to
     * call more than once (a shutdown hook racing an explicit {@code
     * stop()} elsewhere) or even if {@link #start()} was never called.
     */
    public synchronized void stop() {
        log.info("stopping paper trading loop");
        executor.shutdown();
        try {
            if (!executor.awaitTermination(10, TimeUnit.SECONDS)) {
                log.warn("paper trading loop did not stop cleanly within 10s, forcing shutdown");
                executor.shutdownNow();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            executor.shutdownNow();
        }
    }

    /**
     * Test/manual-verification-only accessor (package-private -- not part
     * of this class's real operational API) -- lets a test or a throwaway
     * manual-run harness drive a single tick directly, or read {@code
     * lastTickAt()}/{@code lastError()}/{@code currentEquity()}, without
     * waiting on the real scheduler. See {@code
     * .planning/paper-trading-c-scheduler-entrypoint.md}'s "real local
     * run" section.
     */
    TradingLoop tradingLoop() {
        return tradingLoop;
    }

    /** Test-only accessor (package-private) -- see {@link #tradingLoop()}'s own Javadoc. */
    OrderStore orderStore() {
        return orderStore;
    }

    /** Test-only accessor (package-private) -- see {@link #tradingLoop()}'s own Javadoc. */
    KillSwitch killSwitch() {
        return killSwitch;
    }

    public static void main(String[] args) {
        PaperTradingApp app = fromEnvironment();
        Runtime.getRuntime().addShutdownHook(new Thread(app::stop, "paper-trading-shutdown"));
        app.start();
    }
}
