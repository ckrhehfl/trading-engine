package engine.runtime;

import engine.execution.PaperBroker;
import engine.oms.OrderStore;
import engine.risk.RiskGateway;
import engine.risk.RiskLimits;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.Clock;
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
 * <p><b>Daily reporting</b> (Paper-trading bridge Task D -- see
 * {@code .planning/paper-trading-d-daily-reporting.md}): every scheduled
 * cycle now runs {@link DailyReportGenerator#beforeTick()}, then
 * {@link TradingLoop#tick()}, then {@link DailyReportGenerator#afterTick()}
 * -- {@code beforeTick()} detects a UTC day boundary and, when one is
 * crossed, writes the just-completed day's report to
 * {@code var/live/reports/daily/<date>.json} before the new day's tick
 * runs. See {@code DailyReportGenerator}'s own class Javadoc for the full
 * design (day-boundary detection, restart-mid-day behavior, trade/error
 * attribution).
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
    static final String ENV_REPORTS_DIRECTORY = "PAPER_TRADING_REPORTS_DIR";

    private final TradingLoop tradingLoop;
    private final DailyReportGenerator dailyReportGenerator;
    private final long tickIntervalSeconds;
    private final ScheduledExecutorService executor;
    private volatile ScheduledFuture<?> scheduledTask;

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
        this(symbol, bingxBaseUrl, signalPath, tickIntervalSeconds, resolveReportsDirectory(null));
    }

    /**
     * Same as the 4-arg constructor, with an explicit {@code reportsDirectory}
     * (see {@link DailyReportGenerator}) instead of the default
     * {@code var/live/reports/daily}.
     */
    public PaperTradingApp(
            String symbol, String bingxBaseUrl, Path signalPath, long tickIntervalSeconds, Path reportsDirectory) {
        this(symbol, bingxBaseUrl, signalPath, tickIntervalSeconds, reportsDirectory, Clock.systemUTC());
    }

    /**
     * Test/manual-verification-only overload (package-private -- not part
     * of this class's real operational API, same status as {@link
     * #tradingLoop()}/{@link #dailyReportGenerator()} below): lets a test
     * inject a {@link Clock} so {@link DailyReportGenerator} day-boundary
     * crossings can be driven deterministically instead of waiting on real
     * wall-clock time.
     */
    PaperTradingApp(
            String symbol,
            String bingxBaseUrl,
            Path signalPath,
            long tickIntervalSeconds,
            Path reportsDirectory,
            Clock clock) {
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(bingxBaseUrl, "bingxBaseUrl is required");
        Objects.requireNonNull(signalPath, "signalPath is required");
        Objects.requireNonNull(reportsDirectory, "reportsDirectory is required");
        Objects.requireNonNull(clock, "clock is required");
        if (tickIntervalSeconds <= 0) {
            throw new IllegalArgumentException("tickIntervalSeconds must be positive, was " + tickIntervalSeconds);
        }
        this.tickIntervalSeconds = tickIntervalSeconds;

        RiskGateway riskGateway = new RiskGateway(RiskLimits.canary());
        OrderStore orderStore = new OrderStore();
        OrderPipeline orderPipeline = new OrderPipeline(riskGateway, orderStore);
        PaperBroker paperBroker = new PaperBroker(FEE_BPS, SLIPPAGE_BPS);
        FileSignalSource signalSource = new FileSignalSource(signalPath);
        BingXPriceFeed priceFeed = new BingXPriceFeed(bingxBaseUrl);
        KillSwitch killSwitch = new KillSwitch();

        this.tradingLoop = new TradingLoop(orderPipeline, paperBroker, signalSource, priceFeed, killSwitch, symbol);
        this.dailyReportGenerator = new DailyReportGenerator(tradingLoop, reportsDirectory, clock);
        this.executor = Executors.newSingleThreadScheduledExecutor(r -> new Thread(r, "paper-trading-loop"));

        log.info(
                "PaperTradingApp constructed: symbol={} bingxBaseUrl={} signalPath={} tickIntervalSeconds={}"
                        + " reportsDirectory={} riskTier=canary",
                symbol,
                bingxBaseUrl,
                signalPath,
                tickIntervalSeconds,
                reportsDirectory);
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
     *   <li>{@code PAPER_TRADING_REPORTS_DIR} (optional, default {@code
     *       var/live/reports/daily}, resolved relative to the JVM's
     *       working directory -- same repository-root-relative convention
     *       as {@code PAPER_TRADING_SIGNAL_PATH} above; see {@link
     *       DailyReportGenerator})
     * </ul>
     */
    public static PaperTradingApp fromEnvironment() {
        String symbol = firstNonBlank(System.getenv(ENV_SYMBOL), DEFAULT_SYMBOL);
        String bingxBaseUrl = requireNonBlank(System.getenv(ENV_BINGX_BASE_URL), ENV_BINGX_BASE_URL);
        Path signalPath = resolveSignalPath(System.getenv(ENV_SIGNAL_PATH), symbol);
        long tickIntervalSeconds = resolveTickIntervalSeconds(System.getenv(ENV_TICK_INTERVAL_SECONDS));
        Path reportsDirectory = resolveReportsDirectory(System.getenv(ENV_REPORTS_DIRECTORY));
        return new PaperTradingApp(symbol, bingxBaseUrl, signalPath, tickIntervalSeconds, reportsDirectory);
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

    /**
     * {@code var/live/reports/daily} when {@code raw} is null/blank --
     * matches this task's own governing plan's stated path exactly.
     * Resolved relative to the JVM's working directory when {@code raw}
     * is relative (the default is), same convention as
     * {@link #resolveSignalPath}.
     */
    static Path resolveReportsDirectory(String raw) {
        if (raw != null && !raw.isBlank()) {
            return Path.of(raw);
        }
        return Path.of("var", "live", "reports", "daily");
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

    /**
     * Package-private, not {@code private} -- see {@link #dailyReportGenerator()}'s
     * Javadoc for why: this is the real per-cycle unit of work (day-boundary
     * check, then the trading tick, then day-accumulator update), reused
     * by both the real scheduler ({@link #start()}) and tests/manual-run
     * harnesses that want a full cycle without waiting on the scheduler.
     */
    void runTick() {
        dailyReportGenerator.beforeTick();
        tradingLoop.tick();
        dailyReportGenerator.afterTick();
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

    /** Test/manual-verification-only accessor -- same status as {@link #tradingLoop()} above. */
    DailyReportGenerator dailyReportGenerator() {
        return dailyReportGenerator;
    }

    public static void main(String[] args) {
        PaperTradingApp app = fromEnvironment();
        Runtime.getRuntime().addShutdownHook(new Thread(app::stop, "paper-trading-shutdown"));
        app.start();
    }
}
