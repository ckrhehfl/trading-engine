package engine.runtime;

import engine.exchange.BingXAdapter;
import engine.execution.ExchangeOrderExecutor;
import engine.execution.OrderExecutor;
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
 * {@code .planning/paper-trading-d-daily-reporting.md}): {@link
 * #runTick()} runs {@link DailyReportGenerator#beforeTick()}, then
 * {@link TradingLoop#tick()}, then {@link DailyReportGenerator#afterTick()}
 * -- {@code beforeTick()} detects a UTC day boundary and, when one is
 * crossed, writes the just-completed day's report to
 * {@code var/live/reports/daily/<date>.json} before the new day's tick
 * runs. See {@code DailyReportGenerator}'s own class Javadoc for the full
 * design (day-boundary detection, restart-mid-day behavior, trade/error
 * attribution).
 *
 * <p><b>Internal consistency reconciliation</b> (paper-trading bridge Task
 * E, see {@code .planning/paper-trading-e-reconciliation.md}): {@link
 * #runTick()} calls {@link #reconcile()} after every scheduled {@link
 * TradingLoop#tick()} (and after the daily-report bookkeeping above),
 * regardless of whether that tick itself succeeded -- a tick failure and
 * an internal-bookkeeping inconsistency are orthogonal signals, and
 * running the check unconditionally can surface exactly why a tick
 * failed (e.g. a duplicate-submission attempt). {@link #reconcile()} is
 * also public and safe to call directly (e.g. from a test) with no side
 * effect beyond logging and, on a real mismatch, tripping {@link
 * #killSwitch} -- both idempotent.
 *
 * <p><b>Execution mode (Paper Trading Bridge Task H -- see {@code
 * .planning/paper-trading-h-vst-integration.md}).</b> {@code
 * PAPER_TRADING_EXECUTION_MODE} selects which {@link OrderExecutor} {@link
 * #fromEnvironment()} builds: {@code simulated} (the default -- also the
 * default for unset/blank/any unrecognized value, this project's standing
 * fail-safe-to-known-good convention) constructs the exact same {@link
 * PaperBroker}-based graph this class has always built, with zero behavior
 * change; {@code bingx-vst} instead builds a real {@code BingXAdapter}-
 * backed {@link ExchangeOrderExecutor}, given a real {@link
 * MarkerRecordingSubmissionListener} (backed by {@link
 * SubmissionMarkerStore}) for durable {@code SUBMISSION_UNKNOWN} handling
 * -- composed in via {@code ExchangeOrderExecutor}'s own {@code
 * SubmissionListener} constructor parameter, not a wrapping decorator (see
 * {@code engine.execution.SubmissionListener}'s own Javadoc for why),
 * pointed at the hardcoded {@link #BINGX_VST_BASE_URL} constant
 * -- <b>there is no environment variable, argument, or other configuration
 * surface anywhere in this class that can route the order-execution path
 * to any other host.</b> This is deliberately different from {@code
 * BINGX_BASE_URL} (unchanged: the public price feed only, via {@link
 * BingXPriceFeed}) -- hardcoding the *VST* host specifically is fine per
 * the {@code bingx-hostname-guard} hook's own header comment (it only ever
 * blocks the *production* hostname literal); eliminating the configuration
 * surface entirely is a stronger guarantee than validating it would be. See
 * {@link VstPreflight} for the real startup check {@code bingx-vst} mode
 * runs before any tick-driven trading begins.
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

    /** See class Javadoc, "Execution mode". */
    static final String ENV_EXECUTION_MODE = "PAPER_TRADING_EXECUTION_MODE";

    static final String EXECUTION_MODE_SIMULATED = "simulated";
    static final String EXECUTION_MODE_BINGX_VST = "bingx-vst";

    /**
     * Required only in {@code bingx-vst} mode -- the same credentials
     * already used to verify the VST endpoint by hand (see CLAUDE.md's
     * "Verified -- authenticated, VST key" section). Never logged anywhere
     * in this class -- see class Javadoc.
     */
    static final String ENV_BINGX_API_KEY = "BINGX_API_KEY";

    static final String ENV_BINGX_API_SECRET = "BINGX_API_SECRET";

    /**
     * The VST (demo-trading) host, as a Java constant -- <b>not</b> an
     * environment variable. See class Javadoc, "Execution mode", for why
     * this is deliberately the only way the {@code bingx-vst} order-
     * execution path can ever be pointed anywhere. {@code private}: nothing
     * outside {@link #forBingXVst} ever needs this value directly (a test
     * wanting to inject a fake VST-backed executor uses the {@link
     * #PaperTradingApp(String, String, Path, long, Path, Clock,
     * OrderExecutor) OrderExecutor-accepting constructor} instead, which
     * never touches this constant at all).
     */
    private static final String BINGX_VST_BASE_URL = "https://open-api-vst.bingx.com";

    /** {@code var/live/} convention, matching {@code signals}/{@code reports/daily} -- see class Javadoc. */
    private static final Path SUBMISSION_MARKERS_PATH = Path.of("var", "live", "submission_markers.json");

    private final TradingLoop tradingLoop;
    private final DailyReportGenerator dailyReportGenerator;
    private final OrderStore orderStore;
    private final OrderExecutor orderExecutor;
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
     * wall-clock time. Always builds a real {@link PaperBroker} and a
     * marker-free {@link FileSignalSource} -- <b>zero behavior change</b>
     * from before Task H, byte-for-byte -- see {@link
     * #PaperTradingApp(String, String, Path, long, Path, Clock,
     * OrderExecutor) the OrderExecutor-accepting overload} below for the
     * {@code bingx-vst}-mode path, which this constructor never touches.
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
        this.orderStore = new OrderStore();
        OrderPipeline orderPipeline = new OrderPipeline(riskGateway, this.orderStore);
        this.orderExecutor = new PaperBroker(FEE_BPS, SLIPPAGE_BPS);
        FileSignalSource signalSource = new FileSignalSource(signalPath);
        BingXPriceFeed priceFeed = new BingXPriceFeed(bingxBaseUrl);
        this.killSwitch = new KillSwitch();

        this.tradingLoop =
                new TradingLoop(orderPipeline, this.orderExecutor, signalSource, priceFeed, this.killSwitch, symbol);
        this.dailyReportGenerator = new DailyReportGenerator(tradingLoop, reportsDirectory, clock);
        this.executor = Executors.newSingleThreadScheduledExecutor(r -> new Thread(r, "paper-trading-loop"));

        log.info(
                "PaperTradingApp constructed: symbol={} bingxBaseUrl={} signalPath={} tickIntervalSeconds={}"
                        + " reportsDirectory={} riskTier=canary executionMode={}",
                symbol,
                bingxBaseUrl,
                signalPath,
                tickIntervalSeconds,
                reportsDirectory,
                EXECUTION_MODE_SIMULATED);
    }

    /**
     * Test/manual-verification-only overload (package-private -- not part
     * of this class's real operational API), mirroring the {@link Clock}
     * overload above exactly (same pattern: a real dependency a test or the
     * real {@code bingx-vst} wiring path needs to inject, everything else
     * identical). Lets a caller supply a pre-built {@link OrderExecutor}
     * instead of always constructing a {@link PaperBroker} -- this is what
     * {@link #forBingXVst} uses to wire in a real {@code BingXAdapter}-
     * backed executor, and what a test uses to exercise this class's
     * construction/wiring logic against a fake {@link OrderExecutor}
     * without a real network call.
     *
     * <p>Unlike the {@code PaperBroker}-building overload above, this one
     * always builds {@link FileSignalSource} with a persisted delivered-
     * marker file (a sibling of {@code signalPath} named {@code
     * delivered.marker}) -- see {@code FileSignalSource}'s own Javadoc,
     * "Durable, cross-restart dedup": any caller of this overload is either
     * a test that wants realistic coverage of that behavior, or the real
     * {@code bingx-vst} path, which is exactly the case this protection
     * exists for (a real venue, where a redelivered signal means a real
     * second order).
     */
    PaperTradingApp(
            String symbol,
            String bingxBaseUrl,
            Path signalPath,
            long tickIntervalSeconds,
            Path reportsDirectory,
            Clock clock,
            OrderExecutor orderExecutor) {
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(bingxBaseUrl, "bingxBaseUrl is required");
        Objects.requireNonNull(signalPath, "signalPath is required");
        Objects.requireNonNull(reportsDirectory, "reportsDirectory is required");
        Objects.requireNonNull(clock, "clock is required");
        Objects.requireNonNull(orderExecutor, "orderExecutor is required");
        if (tickIntervalSeconds <= 0) {
            throw new IllegalArgumentException("tickIntervalSeconds must be positive, was " + tickIntervalSeconds);
        }
        this.tickIntervalSeconds = tickIntervalSeconds;

        RiskGateway riskGateway = new RiskGateway(RiskLimits.canary());
        this.orderStore = new OrderStore();
        OrderPipeline orderPipeline = new OrderPipeline(riskGateway, this.orderStore);
        this.orderExecutor = orderExecutor;
        FileSignalSource signalSource = new FileSignalSource(signalPath, signalPath.resolveSibling("delivered.marker"));
        BingXPriceFeed priceFeed = new BingXPriceFeed(bingxBaseUrl);
        this.killSwitch = new KillSwitch();

        this.tradingLoop =
                new TradingLoop(orderPipeline, this.orderExecutor, signalSource, priceFeed, this.killSwitch, symbol);
        this.dailyReportGenerator = new DailyReportGenerator(tradingLoop, reportsDirectory, clock);
        this.executor = Executors.newSingleThreadScheduledExecutor(r -> new Thread(r, "paper-trading-loop"));

        log.info(
                "PaperTradingApp constructed: symbol={} bingxBaseUrl={} signalPath={} tickIntervalSeconds={}"
                        + " reportsDirectory={} riskTier=canary orderExecutor={}",
                symbol,
                bingxBaseUrl,
                signalPath,
                tickIntervalSeconds,
                reportsDirectory,
                orderExecutor.getClass().getSimpleName());
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
     *   <li>{@code PAPER_TRADING_EXECUTION_MODE} (optional, default {@code
     *       simulated} -- see class Javadoc, "Execution mode"). Only in
     *       {@code bingx-vst} mode: {@code BINGX_API_KEY}/{@code
     *       BINGX_API_SECRET} (both required, no default -- never
     *       hardcoded, matching this project's standing credential
     *       convention; see {@link #forBingXVst})
     * </ul>
     */
    public static PaperTradingApp fromEnvironment() {
        String symbol = firstNonBlank(System.getenv(ENV_SYMBOL), DEFAULT_SYMBOL);
        String bingxBaseUrl = requireNonBlank(System.getenv(ENV_BINGX_BASE_URL), ENV_BINGX_BASE_URL);
        Path signalPath = resolveSignalPath(System.getenv(ENV_SIGNAL_PATH), symbol);
        long tickIntervalSeconds = resolveTickIntervalSeconds(System.getenv(ENV_TICK_INTERVAL_SECONDS));
        Path reportsDirectory = resolveReportsDirectory(System.getenv(ENV_REPORTS_DIRECTORY));
        String executionMode = resolveExecutionMode(System.getenv(ENV_EXECUTION_MODE));
        if (EXECUTION_MODE_BINGX_VST.equals(executionMode)) {
            return forBingXVst(symbol, bingxBaseUrl, signalPath, tickIntervalSeconds, reportsDirectory);
        }
        return new PaperTradingApp(symbol, bingxBaseUrl, signalPath, tickIntervalSeconds, reportsDirectory);
    }

    /**
     * {@code bingx-vst} only for an exact (case-sensitive, after trimming)
     * match of {@link #EXECUTION_MODE_BINGX_VST}; {@link
     * #EXECUTION_MODE_SIMULATED} for {@code null}, blank, or any other
     * value -- this project's standing "fail safe to the known-good
     * default" convention (see {@link #resolveTickIntervalSeconds}'s own
     * precedent, though that one fails loud rather than safe -- unlike a
     * malformed tick interval, an unrecognized execution mode has an
     * obviously-safe fallback, so silently defaulting is the right choice
     * here specifically).
     */
    static String resolveExecutionMode(String raw) {
        if (raw != null && EXECUTION_MODE_BINGX_VST.equals(raw.trim())) {
            return EXECUTION_MODE_BINGX_VST;
        }
        return EXECUTION_MODE_SIMULATED;
    }

    /**
     * Builds the real {@code bingx-vst}-mode graph: a real {@code
     * BingXAdapter} pointed at the hardcoded {@link #BINGX_VST_BASE_URL}
     * constant (never {@code bingxBaseUrl}, which remains the price-feed-
     * only host), a real {@link VstPreflight#run} check (fails closed on a
     * non-VST balance asset), real {@code SUBMISSION_UNKNOWN} marker
     * resolution via {@link SubmissionMarkerResolver} against any marker a
     * prior process instance left behind, and an {@link
     * ExchangeOrderExecutor} (given a real {@link
     * MarkerRecordingSubmissionListener}) as the order-execution path. The
     * kill switch
     * starts tripped if either {@link VstPreflight} found a non-zero
     * pre-existing position, or {@link SubmissionMarkerResolver} found an
     * unresolved marker requiring human review -- either is unknown state
     * this process must not start normal tick-driven trading against. Never
     * logs {@code apiKey}/{@code apiSecret} -- both are read directly from
     * {@code System.getenv} into local variables only ever passed to {@code
     * BingXAdapter}'s constructor, never into a log statement.
     */
    private static PaperTradingApp forBingXVst(
            String symbol, String bingxBaseUrl, Path signalPath, long tickIntervalSeconds, Path reportsDirectory) {
        String apiKey = requireNonBlank(System.getenv(ENV_BINGX_API_KEY), ENV_BINGX_API_KEY);
        String apiSecret = requireNonBlank(System.getenv(ENV_BINGX_API_SECRET), ENV_BINGX_API_SECRET);
        BingXAdapter adapter = new BingXAdapter(apiKey, apiSecret, BINGX_VST_BASE_URL);

        VstPreflight.Result preflight = VstPreflight.run(adapter, symbol);

        SubmissionMarkerStore markerStore = new SubmissionMarkerStore(SUBMISSION_MARKERS_PATH);
        SubmissionMarkerResolver.Resolution markerResolution = SubmissionMarkerResolver.resolve(markerStore, adapter);
        boolean unresolvedMarkers = !markerResolution.unresolvedMarkers().isEmpty();

        OrderExecutor orderExecutor = new ExchangeOrderExecutor(
                adapter, FEE_BPS, new MarkerRecordingSubmissionListener(markerStore));

        PaperTradingApp app = new PaperTradingApp(
                symbol, bingxBaseUrl, signalPath, tickIntervalSeconds, reportsDirectory, Clock.systemUTC(),
                orderExecutor);

        if (preflight.killSwitchShouldStartTripped() || unresolvedMarkers) {
            app.killSwitch.trip();
            log.error(
                    "PaperTradingApp starting in bingx-vst mode with the kill switch already TRIPPED"
                            + " (preflightFoundNonZeroPosition={}, unresolvedSubmissionMarkersRequiringReview={})"
                            + " -- a deliberate human reset is required before any new signal is submitted.",
                    preflight.killSwitchShouldStartTripped(),
                    unresolvedMarkers);
        }
        log.info(
                "PaperTradingApp constructed in bingx-vst mode: symbol={} vstBaseUrl={} balanceAsset={}",
                symbol,
                BINGX_VST_BASE_URL,
                preflight.balance().asset());
        return app;
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
        // Runs regardless of the tick's own outcome above -- see class
        // Javadoc, "Internal consistency reconciliation".
        reconcile();
    }

    /**
     * Runs {@link Reconciler#check} against this app's own {@link
     * OrderStore} and {@link OrderExecutor} (a {@link PaperBroker} in
     * {@code simulated} mode, an {@code ExchangeOrderExecutor}-based
     * executor in {@code bingx-vst} mode -- see class Javadoc, "Execution
     * mode"), using {@link TradingLoop#submittedOrderIds()} as the known-
     * submission history -- see class Javadoc and {@code .planning/paper-
     * trading-e-reconciliation.md} for the full design and why a detected
     * mismatch trips {@link #killSwitch}. Updates {@link
     * #lastReconciliationReport()} with the result before returning it.
     */
    public ReconciliationReport reconcile() {
        ReconciliationReport report = Reconciler.check(tradingLoop.submittedOrderIds(), orderStore, orderExecutor);
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
     * finish before a forced {@code shutdownNow()} (itself given a further
     * 5 seconds to actually confirm termination -- see below). Called from
     * {@link #main}'s JVM shutdown hook (SIGTERM / normal JVM exit); safe
     * to call more than once (a shutdown hook racing an explicit {@code
     * stop()} elsewhere) or even if {@link #start()} was never called.
     *
     * <p>Calls {@link DailyReportGenerator#finalizeCompletedDayOnShutdown()}
     * -- but only after real termination is confirmed, never unconditionally
     * (a CodeRabbit review finding on this task's own PR #73):
     * {@code ExecutorService.shutdownNow()} attempts to interrupt an
     * in-flight task, it does not guarantee the task actually stops before
     * this method returns. If a straggling {@link #runTick()} were still
     * running concurrently with {@code finalizeCompletedDayOnShutdown()},
     * that tick's {@code afterTick()} call could land on either side of
     * the finalize -- either silently missing from the finalized report,
     * or incorrectly counted against the day tracking that already reset
     * for after finalization. Skipping finalization entirely when
     * termination can't be confirmed avoids that race outright: the
     * completed day simply stays unwritten, no worse than any other
     * already-disclosed "process stopped without a clean tick cycle"
     * limitation (see {@code DailyReportGenerator}'s own class Javadoc),
     * logged loudly rather than silently risking a wrong report.
     */
    public synchronized void stop() {
        log.info("stopping paper trading loop");
        executor.shutdown();
        boolean terminated;
        try {
            terminated = executor.awaitTermination(10, TimeUnit.SECONDS);
            if (!terminated) {
                log.warn("paper trading loop did not stop cleanly within 10s, forcing shutdown");
                executor.shutdownNow();
                terminated = executor.awaitTermination(5, TimeUnit.SECONDS);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            executor.shutdownNow();
            terminated = false;
        }
        if (terminated) {
            dailyReportGenerator.finalizeCompletedDayOnShutdown();
        } else {
            log.error(
                    "paper trading loop did not terminate even after a forced shutdown; skipping daily-report"
                            + " finalization to avoid finalizing while a tick may still be in flight -- if a UTC"
                            + " day already ended, its report will remain unwritten unless a future process"
                            + " restart reaches that day's boundary again");
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

    /** Test-only accessor (package-private) -- see {@link #tradingLoop()}'s own Javadoc. */
    OrderStore orderStore() {
        return orderStore;
    }

    /** Test-only accessor (package-private) -- see {@link #tradingLoop()}'s own Javadoc. */
    KillSwitch killSwitch() {
        return killSwitch;
    }

    /**
     * Test/manual-verification-only accessor -- same status as {@link
     * #tradingLoop()} above. Added for Task H's real VST verification (see
     * {@code .planning/paper-trading-h-vst-integration.md}): a manual
     * verification driver needs a real {@link #cancel} call against a real
     * pending order to observe BingX's actual cancelled-status token
     * (CANCELLED vs CANCELED), which {@link TradingLoop} itself has no
     * reason to ever expose a cancel path for.
     */
    OrderExecutor orderExecutor() {
        return orderExecutor;
    }

    public static void main(String[] args) {
        PaperTradingApp app = fromEnvironment();
        Runtime.getRuntime().addShutdownHook(new Thread(app::stop, "paper-trading-shutdown"));
        app.start();
    }
}
