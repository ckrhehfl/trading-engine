package engine.runtime;

import engine.exchange.BingXAdapter;
import engine.exchange.KisAdapter;
import engine.exchange.KisTokenProvider;
import engine.execution.ExchangeOrderExecutor;
import engine.execution.OrderExecutor;
import engine.execution.PaperBroker;
import engine.oms.OrderStore;
import engine.risk.RiskGateway;
import engine.risk.RiskLimits;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
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
     * {@link #stop()}'s own termination-wait timeouts -- see that method's
     * Javadoc for what each one gates. Production code always uses these
     * two values; only the package-private {@link #PaperTradingApp(String,
     * String, Path, long, Path, Clock, Duration, Duration) Duration-
     * accepting test-only constructor overload} (GitHub issue #74) can
     * override them, matching the existing {@link Clock}-injection
     * precedent's own "production defaults, test-only override" shape.
     */
    static final Duration DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT = Duration.ofSeconds(10);

    static final Duration DEFAULT_FORCED_SHUTDOWN_TIMEOUT = Duration.ofSeconds(5);

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
    /** See class Javadoc, "Execution mode -- kis-paper". */
    static final String EXECUTION_MODE_KIS_PAPER = "kis-paper";

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

    /**
     * Required only in {@code kis-paper} mode -- never logged anywhere in
     * this class, same discipline as the BingX credentials above. {@code
     * KIS_ACCOUNT_NO} is KIS's 8-digit {@code CANO}; {@code
     * KIS_ACCOUNT_PRODUCT_CODE} is optional, defaulting to {@code "03"}
     * (KIS's own real source uses this for a futures/options account --
     * see {@code KisAdapter}'s own Javadoc).
     */
    static final String ENV_KIS_APP_KEY = "KIS_APP_KEY";

    static final String ENV_KIS_APP_SECRET = "KIS_APP_SECRET";
    static final String ENV_KIS_ACCOUNT_NO = "KIS_ACCOUNT_NO";
    static final String ENV_KIS_ACCOUNT_PRODUCT_CODE = "KIS_ACCOUNT_PRODUCT_CODE";
    static final String DEFAULT_KIS_ACCOUNT_PRODUCT_CODE = "03";

    /**
     * The KIS paper-trading (모의투자) host, as a Java constant -- <b>not</b>
     * an environment variable, same "no configuration surface" reasoning
     * and same {@code private} visibility as {@link #BINGX_VST_BASE_URL}
     * above (see that field's own Javadoc). Used for both {@link
     * KisTokenProvider}'s token endpoint and every {@link
     * engine.exchange.KisAdapter}/{@code KisPriceFeed} call in {@code
     * kis-paper} mode -- KIS's real API serves both from the same host,
     * unlike BingX's separate price-feed-vs-order-execution split.
     */
    private static final String KIS_PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443";

    /** {@code var/live/} convention, matching {@code signals}/{@code reports/daily} -- see class Javadoc. */
    private static final Path SUBMISSION_MARKERS_PATH = Path.of("var", "live", "submission_markers.json");


    private final TradingLoop tradingLoop;
    private final DailyReportGenerator dailyReportGenerator;
    private final OrderStore orderStore;
    private final OrderExecutor orderExecutor;
    private final KillSwitch killSwitch;
    private final Clock clock;
    /**
     * Every constructor below except the new KIS-specific one (see {@link
     * #PaperTradingApp(String, PriceFeed, Path, long, Path, Clock,
     * TradingCalendar, OrderExecutor)}) hardcodes this to {@link
     * AlwaysOpenTradingCalendar} internally, not as a settable parameter --
     * this is what keeps {@code simulated}/{@code bingx-vst} mode provably
     * unaffected by this field's introduction (see CLAUDE.md's KIS/KOSPI200
     * Phase 1 design, Task 3/4): there is no code path by which either of
     * those modes could ever end up with a different calendar.
     */
    private final TradingCalendar tradingCalendar;
    private final long tickIntervalSeconds;
    private final Duration gracefulShutdownTimeout;
    private final Duration forcedShutdownTimeout;
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
        this(
                symbol,
                bingxBaseUrl,
                signalPath,
                tickIntervalSeconds,
                reportsDirectory,
                clock,
                DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT,
                DEFAULT_FORCED_SHUTDOWN_TIMEOUT);
    }

    /**
     * Test-only overload (package-private -- not part of this class's real
     * operational API, same status as the {@link Clock} overload above,
     * which this one now delegates to it): lets a test inject {@link
     * #stop()}'s own two termination-wait {@link Duration}s (see that
     * method's Javadoc) instead of always using the real {@link
     * #DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT}/{@link
     * #DEFAULT_FORCED_SHUTDOWN_TIMEOUT} values. Added to close GitHub issue
     * #74 -- a round-4 CodeRabbit finding on PR #73 (accepted as a
     * disclosed gap at the time): {@code stop()}'s shutdown-termination-
     * confirmation logic had no deterministic test forcing a real tick to
     * hang past a short configured timeout and proving both (a)
     * finalization never runs before an in-flight tick actually
     * terminates, and (b) finalization is skipped when termination can't
     * be confirmed. Always builds a real {@link PaperBroker} and a
     * marker-free {@link FileSignalSource} -- same zero-behavior-change
     * status as the {@link Clock}-only overload above; see {@link
     * #PaperTradingApp(String, String, Path, long, Path, Clock,
     * OrderExecutor) the OrderExecutor-accepting overload} below for the
     * {@code bingx-vst}-mode path, which this constructor never touches
     * (and which does not accept these two {@code Duration}s -- out of
     * scope for issue #74, see its own governing task brief).
     */
    PaperTradingApp(
            String symbol,
            String bingxBaseUrl,
            Path signalPath,
            long tickIntervalSeconds,
            Path reportsDirectory,
            Clock clock,
            Duration gracefulShutdownTimeout,
            Duration forcedShutdownTimeout) {
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(bingxBaseUrl, "bingxBaseUrl is required");
        Objects.requireNonNull(signalPath, "signalPath is required");
        Objects.requireNonNull(reportsDirectory, "reportsDirectory is required");
        Objects.requireNonNull(clock, "clock is required");
        Objects.requireNonNull(gracefulShutdownTimeout, "gracefulShutdownTimeout is required");
        Objects.requireNonNull(forcedShutdownTimeout, "forcedShutdownTimeout is required");
        if (tickIntervalSeconds <= 0) {
            throw new IllegalArgumentException("tickIntervalSeconds must be positive, was " + tickIntervalSeconds);
        }
        if (gracefulShutdownTimeout.isNegative()) {
            throw new IllegalArgumentException(
                    "gracefulShutdownTimeout must not be negative, was " + gracefulShutdownTimeout);
        }
        if (forcedShutdownTimeout.isNegative()) {
            throw new IllegalArgumentException(
                    "forcedShutdownTimeout must not be negative, was " + forcedShutdownTimeout);
        }
        this.tickIntervalSeconds = tickIntervalSeconds;
        this.gracefulShutdownTimeout = gracefulShutdownTimeout;
        this.forcedShutdownTimeout = forcedShutdownTimeout;
        this.clock = clock;
        this.tradingCalendar = new AlwaysOpenTradingCalendar();

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
     * without a real network call. Always uses the real {@link
     * #DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT}/{@link
     * #DEFAULT_FORCED_SHUTDOWN_TIMEOUT} values -- unlike the {@link Clock}-
     * only overload above, this one has no {@code Duration}-accepting
     * counterpart (out of scope for GitHub issue #74, see that overload's
     * own Javadoc).
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
        this.gracefulShutdownTimeout = DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT;
        this.forcedShutdownTimeout = DEFAULT_FORCED_SHUTDOWN_TIMEOUT;
        this.clock = clock;
        this.tradingCalendar = new AlwaysOpenTradingCalendar();

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
     * KIS-specific overload (package-private -- what {@link #forKisPaper}
     * uses, same "not part of this class's real public API" status as the
     * other package-private overloads above). Takes a {@link PriceFeed}
     * directly instead of a {@code bingxBaseUrl} string -- the existing
     * {@code OrderExecutor}-accepting overload above always builds a {@link
     * BingXPriceFeed} internally, which is wrong for this venue ({@code
     * kis-paper} needs {@code engine.runtime.KisPriceFeed} instead) -- and
     * a real {@link TradingCalendar} instead of always defaulting to {@link
     * AlwaysOpenTradingCalendar} (see CLAUDE.md's KIS/KOSPI200 Phase 1
     * design, Task 3/4: KOSPI200 futures has real fixed trading hours,
     * unlike BTC's 24/7 market). Always builds {@link FileSignalSource}
     * with a persisted delivered-marker file, same reasoning as the
     * {@code bingx-vst} overload above -- a real venue, where a redelivered
     * signal means a real second order -- but under a KIS-specific
     * filename ({@code kis-delivered.marker}, not the shared {@code
     * delivered.marker} the {@code bingx-vst} overload uses) so the two
     * venues' delivery state can never collide even if ever pointed at the
     * same {@code signalPath} (a real CodeRabbit review finding).
     */
    PaperTradingApp(
            String symbol,
            PriceFeed priceFeed,
            Path signalPath,
            long tickIntervalSeconds,
            Path reportsDirectory,
            Clock clock,
            TradingCalendar tradingCalendar,
            OrderExecutor orderExecutor) {
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(priceFeed, "priceFeed is required");
        Objects.requireNonNull(signalPath, "signalPath is required");
        Objects.requireNonNull(reportsDirectory, "reportsDirectory is required");
        Objects.requireNonNull(clock, "clock is required");
        Objects.requireNonNull(tradingCalendar, "tradingCalendar is required");
        Objects.requireNonNull(orderExecutor, "orderExecutor is required");
        if (tickIntervalSeconds <= 0) {
            throw new IllegalArgumentException("tickIntervalSeconds must be positive, was " + tickIntervalSeconds);
        }
        this.tickIntervalSeconds = tickIntervalSeconds;
        this.gracefulShutdownTimeout = DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT;
        this.forcedShutdownTimeout = DEFAULT_FORCED_SHUTDOWN_TIMEOUT;
        this.clock = clock;
        this.tradingCalendar = tradingCalendar;

        RiskGateway riskGateway = new RiskGateway(RiskLimits.canary());
        this.orderStore = new OrderStore();
        OrderPipeline orderPipeline = new OrderPipeline(riskGateway, this.orderStore);
        this.orderExecutor = orderExecutor;
        // "kis-delivered.marker", not the shared "delivered.marker" name
        // the bingx-vst overload uses below -- a real CodeRabbit review
        // finding: if an operator ever pointed both processes at the same
        // signalPath, a shared marker filename would let one venue's
        // delivery silently suppress the other's. Distinct filenames close
        // this for free (KIS_SUBMISSION_MARKERS_PATH, a few lines below in
        // forKisPaper, solves a different problem and was never meant to
        // cover this one -- see this constructor's own Javadoc).
        FileSignalSource signalSource =
                new FileSignalSource(signalPath, signalPath.resolveSibling("kis-delivered.marker"));
        this.killSwitch = new KillSwitch();

        this.tradingLoop =
                new TradingLoop(orderPipeline, this.orderExecutor, signalSource, priceFeed, this.killSwitch, symbol);
        this.dailyReportGenerator = new DailyReportGenerator(tradingLoop, reportsDirectory, clock);
        this.executor = Executors.newSingleThreadScheduledExecutor(r -> new Thread(r, "paper-trading-loop"));

        log.info(
                "PaperTradingApp constructed: symbol={} signalPath={} tickIntervalSeconds={} reportsDirectory={}"
                        + " riskTier=canary orderExecutor={} tradingCalendar={}",
                symbol,
                signalPath,
                tickIntervalSeconds,
                reportsDirectory,
                orderExecutor.getClass().getSimpleName(),
                tradingCalendar.getClass().getSimpleName());
    }

    /**
     * Resolves configuration from environment variables and constructs a
     * real {@code PaperTradingApp} -- what {@link #main} calls. Every env
     * var:
     *
     * <ul>
     *   <li>{@code PAPER_TRADING_EXECUTION_MODE} (optional, default {@code
     *       simulated} -- see class Javadoc, "Execution mode"). Read
     *       first, since it changes how every other var below is resolved.
     *   <li>{@code PAPER_TRADING_SYMBOL} -- optional with default {@code
     *       BTC-USDT} in {@code simulated}/{@code bingx-vst} mode;
     *       <b>required, no default</b>, in {@code kis-paper} mode ({@code
     *       BTC-USDT} is not a valid KIS KOSPI200 futures symbol, so this
     *       mode never silently falls back to it -- see {@link
     *       #forKisPaper})
     *   <li>{@code BINGX_BASE_URL} (required, no default, in {@code
     *       simulated}/{@code bingx-vst} mode only -- matches the existing
     *       java/python-wide convention of never hardcoding a BingX host in
     *       source; fails fast with a clear message if unset. {@code
     *       kis-paper} mode never reads this var at all -- see {@link
     *       #forKisPaper})
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
     *   <li>Only in {@code bingx-vst} mode: {@code BINGX_API_KEY}/{@code
     *       BINGX_API_SECRET} (both required, no default -- never
     *       hardcoded, matching this project's standing credential
     *       convention; see {@link #forBingXVst})
     *   <li>Only in {@code kis-paper} mode: {@code KIS_APP_KEY}/{@code
     *       KIS_APP_SECRET}/{@code KIS_ACCOUNT_NO} (all three required, no
     *       default -- same credential convention as above) and {@code
     *       KIS_ACCOUNT_PRODUCT_CODE} (optional, default {@link
     *       #DEFAULT_KIS_ACCOUNT_PRODUCT_CODE}, {@code "03"}); see {@link
     *       #forKisPaper}
     * </ul>
     */
    public static PaperTradingApp fromEnvironment() {
        String executionMode = resolveExecutionMode(System.getenv(ENV_EXECUTION_MODE));
        long tickIntervalSeconds = resolveTickIntervalSeconds(System.getenv(ENV_TICK_INTERVAL_SECONDS));
        Path reportsDirectory = resolveReportsDirectory(System.getenv(ENV_REPORTS_DIRECTORY));
        if (EXECUTION_MODE_KIS_PAPER.equals(executionMode)) {
            // No DEFAULT_SYMBOL fallback here, deliberately (a real
            // CodeRabbit review finding): DEFAULT_SYMBOL is BTC-USDT, which
            // is not a valid KIS KOSPI200 futures symbol -- silently
            // defaulting to it would send a nonsensical symbol into
            // KisAdapter/KisPreflight instead of failing fast with a clear
            // message. kis-paper never touches BINGX_BASE_URL either --
            // KisPriceFeed is built from KIS_PAPER_BASE_URL instead, so this
            // mode must not require a BingX-only env var just to start.
            String symbol = requireNonBlank(System.getenv(ENV_SYMBOL), ENV_SYMBOL);
            Path signalPath = resolveSignalPath(System.getenv(ENV_SIGNAL_PATH), symbol);
            return forKisPaper(symbol, signalPath, tickIntervalSeconds, reportsDirectory);
        }
        String symbol = firstNonBlank(System.getenv(ENV_SYMBOL), DEFAULT_SYMBOL);
        Path signalPath = resolveSignalPath(System.getenv(ENV_SIGNAL_PATH), symbol);
        String bingxBaseUrl = requireNonBlank(System.getenv(ENV_BINGX_BASE_URL), ENV_BINGX_BASE_URL);
        if (EXECUTION_MODE_BINGX_VST.equals(executionMode)) {
            return forBingXVst(symbol, bingxBaseUrl, signalPath, tickIntervalSeconds, reportsDirectory);
        }
        return new PaperTradingApp(symbol, bingxBaseUrl, signalPath, tickIntervalSeconds, reportsDirectory);
    }

    /**
     * {@link #EXECUTION_MODE_SIMULATED} only for {@code null} or blank
     * (the real "unset" case, this project's standing "fail safe to the
     * known-good default" convention); an exact (case-sensitive, after
     * trimming) match of {@link #EXECUTION_MODE_BINGX_VST}/{@link
     * #EXECUTION_MODE_KIS_PAPER} for those two; anything else -- including
     * a misspelling of a real mode, e.g. {@code "kis-papr"} -- throws
     * rather than silently falling back to {@code simulated}.
     *
     * <p><b>Deliberately tightened from an earlier version of this method
     * that defaulted every unrecognized value to {@code simulated}</b> (a
     * real CodeRabbit review finding on the PR that added {@code
     * kis-paper}): with only {@code simulated}/{@code bingx-vst} to choose
     * between, an unrecognized value had one obviously-safe fallback. With
     * three modes, that reasoning breaks -- a typo'd {@code kis-paper} or
     * {@code bingx-vst} would silently start the {@code simulated}-only
     * graph instead, which could look like a functioning deployment (no
     * startup error) while the intended real order path never runs at
     * all. Failing loud here matches this class's own established
     * "unresolved calendar/preflight state trips the kill switch rather
     * than guessing" philosophy elsewhere in this file.
     */
    static String resolveExecutionMode(String raw) {
        if (raw == null || raw.isBlank()) {
            return EXECUTION_MODE_SIMULATED;
        }
        String trimmed = raw.trim();
        if (EXECUTION_MODE_SIMULATED.equals(trimmed)) {
            return EXECUTION_MODE_SIMULATED;
        }
        if (EXECUTION_MODE_BINGX_VST.equals(trimmed)) {
            return EXECUTION_MODE_BINGX_VST;
        }
        if (EXECUTION_MODE_KIS_PAPER.equals(trimmed)) {
            return EXECUTION_MODE_KIS_PAPER;
        }
        throw new IllegalStateException(
                ENV_EXECUTION_MODE + " must be '" + EXECUTION_MODE_SIMULATED + "', '" + EXECUTION_MODE_BINGX_VST
                        + "', or '" + EXECUTION_MODE_KIS_PAPER + "' (or unset/blank, which defaults to '"
                        + EXECUTION_MODE_SIMULATED + "') -- was '" + raw + "'");
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

    /**
     * Builds the real {@code kis-paper}-mode graph -- mirrors {@link
     * #forBingXVst}'s shape (real adapter -> real preflight -> submission-
     * marker resolution -> {@link ExchangeOrderExecutor} -> construct ->
     * trip kill switch if needed -> log without credentials), not its
     * specific logic (see {@link KisPreflight}'s own Javadoc for why it
     * can't mirror {@link VstPreflight}'s asset-name gate). A real {@link
     * KisAdapter} and {@link KisPriceFeed} share one {@link
     * KisTokenProvider} (see {@code KisPriceFeed}'s own Javadoc for why
     * they can't be the same class), both pointed at the hardcoded {@link
     * #KIS_PAPER_BASE_URL} constant -- never {@code bingxBaseUrl}, and
     * there is no environment variable, argument, or other configuration
     * surface in this class that can route this path anywhere else (same
     * guarantee as {@link #forBingXVst}). Uses the real {@link
     * KrxMarketCalendar} (not {@link AlwaysOpenTradingCalendar}) so {@link
     * #runTick()} only drives {@link TradingLoop#tick()} during KOSPI200's
     * real trading hours. Never logs {@code appKey}/{@code appSecret}/
     * {@code accountNo} -- all three are read directly from {@code
     * System.getenv} into local variables only ever passed to {@link
     * KisTokenProvider}/{@link KisAdapter}'s constructors, never into a log
     * statement.
     *
     * <p><b>Three real gaps, flagged across three rounds of real CodeRabbit
     * review of the PR that added this method.</b> Two are fixed directly;
     * one is structurally mitigated (not solved) by an unconditional
     * kill-switch trip below -- matching this project's own established
     * precedent for Task 2's two similarly-deferred KIS gaps (the missing
     * client-order-id equivalent and {@code GUARDED_MARKET}'s missing
     * wire-level guard, see {@code KisAdapter}'s own Javadoc) for the one
     * gap that couldn't be fixed outright without real R3-risk architecture
     * work:
     * <ol>
     *   <li><b>Fixed: {@code FileSignalSource}'s delivered-marker file
     *   could have collided with {@code bingx-vst}'s</b> if both processes
     *   were ever pointed at the same {@code signalPath} -- {@link
     *   #resolveKisSubmissionMarkersPath} solves a different problem
     *   (durable {@code SUBMISSION_UNKNOWN} tracking), not this one. The {@link
     *   PriceFeed}-accepting constructor above now uses a KIS-specific
     *   marker filename ({@code kis-delivered.marker}) instead of the
     *   shared {@code delivered.marker} name, closing this for free even
     *   though the two venues' default {@code signalPath}s (derived from
     *   {@code symbol} via {@link #resolveSignalPath}) never actually
     *   collide in practice today ({@code kis-paper} trades a KOSPI200
     *   futures symbol, {@code bingx-vst} trades {@code BTC-USDT}).
     *   <li><b>Fixed: {@link #runTick()}'s {@link TradingCalendar} gate
     *   used to skip {@code OrderExecutor.pollFills} along with everything
     *   else in {@link TradingLoop#tick()} while the market was closed.</b>
     *   A real fill, cancel, or expiry at the exchange right at/after
     *   close would not have been reflected in this process's own {@code
     *   OrderStore}/{@code OrderExecutor} state until the market reopened
     *   -- {@link #reconcile()} runs every tick regardless, but only checks
     *   internal consistency between this process's own records, not
     *   against the exchange's live state, so it could not have caught
     *   this staleness either. {@link TradingLoop#pollPendingFills()} (new
     *   -- the same price-fetch-and-{@code pollFills} work {@code tick()}
     *   always did as its own first step, now also its own standalone
     *   method) is now called directly from {@link #runTick()}'s
     *   market-closed branch, so pending-order reconciliation runs on
     *   every tick regardless of market hours; only new-signal processing
     *   is actually gated by {@link #tradingCalendar}. This gap never
     *   existed for {@code simulated}/
     *   {@code bingx-vst} (both always use {@link AlwaysOpenTradingCalendar},
     *   so their {@code tick()} always ran) -- it was new, introduced by
     *   {@code kis-paper} being the first mode with a calendar that can
     *   actually report closed.
     *   <li><b>Mitigated, not solved: {@code RiskGateway} has no KOSPI200
     *   contract-multiplier conversion.</b> It computes notional as plain
     *   {@code quantity × price}; a real KOSPI200 futures contract is
     *   worth {@code index points × ₩250,000} (KRX's own official
     *   multiplier -- see CLAUDE.md's "KIS/KOSPI200 venue integration,
     *   Phase 1" section). Until that conversion exists and runs before
     *   {@code RiskLimits.canary()}'s percentage check, the canary 2%
     *   order-notional limit does not meaningfully bound a real KIS
     *   order's exposure. This was anticipated, not newly discovered:
     *   CLAUDE.md's own design review already named this as required
     *   "before the canary percentages mean anything here." It remains
     *   unbuilt because it is real R3-risk architecture (a change to
     *   {@code RiskGateway}'s own notional calculation, touching every
     *   venue) that needs its own {@code Discuss} pass. <b>Correcting an
     *   earlier version of this same disclosure, on an earlier review
     *   round</b>: this graph is fully order-submission-capable the moment
     *   a real signal appears at {@code signalPath} -- an earlier claim
     *   that {@code kis-paper} mode was "unaffected in practice" because it
     *   "still runs against {@code DummySignalSource}" was simply wrong;
     *   this method has always wired a real {@link FileSignalSource}, the
     *   same as {@link #forBingXVst} does. The real mitigation is below:
     *   this method now unconditionally trips {@link #killSwitch}
     *   regardless of preflight/marker state, specifically because of this
     *   gap, so no signal -- real or accidental -- can result in a
     *   submitted order without a deliberate human reset first. Remove
     *   that unconditional trip only once the contract-multiplier
     *   conversion is built and tested.
     * </ol>
     */
    private static PaperTradingApp forKisPaper(
            String symbol, Path signalPath, long tickIntervalSeconds, Path reportsDirectory) {
        String appKey = requireNonBlank(System.getenv(ENV_KIS_APP_KEY), ENV_KIS_APP_KEY);
        String appSecret = requireNonBlank(System.getenv(ENV_KIS_APP_SECRET), ENV_KIS_APP_SECRET);
        String accountNo = requireNonBlank(System.getenv(ENV_KIS_ACCOUNT_NO), ENV_KIS_ACCOUNT_NO);
        String accountProductCode =
                firstNonBlank(System.getenv(ENV_KIS_ACCOUNT_PRODUCT_CODE), DEFAULT_KIS_ACCOUNT_PRODUCT_CODE);

        KisTokenProvider tokenProvider = new KisTokenProvider(appKey, appSecret, KIS_PAPER_BASE_URL);
        KisAdapter adapter = new KisAdapter(tokenProvider, accountNo, accountProductCode, KIS_PAPER_BASE_URL);
        KisPriceFeed priceFeed = new KisPriceFeed(tokenProvider, KIS_PAPER_BASE_URL);

        KisPreflight.Result preflight = KisPreflight.run(adapter);

        SubmissionMarkerStore markerStore = new SubmissionMarkerStore(resolveKisSubmissionMarkersPath(symbol));
        SubmissionMarkerResolver.Resolution markerResolution = SubmissionMarkerResolver.resolve(markerStore, adapter);
        boolean unresolvedMarkers = !markerResolution.unresolvedMarkers().isEmpty();

        OrderExecutor orderExecutor = new ExchangeOrderExecutor(
                adapter, FEE_BPS, new MarkerRecordingSubmissionListener(markerStore));

        PaperTradingApp app = new PaperTradingApp(
                symbol,
                priceFeed,
                signalPath,
                tickIntervalSeconds,
                reportsDirectory,
                Clock.systemUTC(),
                new KrxMarketCalendar(),
                orderExecutor);

        // Unconditionally tripped, not only on a preflight/marker problem
        // (a real CodeRabbit review finding, tied directly to this method's
        // own Javadoc "still open" gap above): RiskGateway has no KOSPI200
        // contract-multiplier conversion yet, so RiskLimits.canary()'s 2%
        // order-notional limit does not meaningfully bound a real KIS
        // order's exposure. This graph is otherwise fully capable of
        // submitting a real order the moment a signal appears at
        // signalPath -- until the contract-multiplier conversion exists,
        // this process must never do that without a deliberate human
        // reset first, regardless of whether preflight/markers looked
        // clean. Remove this once that conversion is built and tested (see
        // this method's own Javadoc).
        app.killSwitch.trip();
        log.error(
                "PaperTradingApp starting in kis-paper mode with the kill switch already TRIPPED -- RiskGateway has"
                        + " no KOSPI200 contract-multiplier conversion yet, so the canary notional limit does not"
                        + " meaningfully bound a real KIS order's exposure (see forKisPaper's own Javadoc). A"
                        + " deliberate human reset is required before any signal is submitted, independent of"
                        + " preflightFoundNonZeroPosition={} and unresolvedSubmissionMarkersRequiringReview={}"
                        + " below, both still logged since they're separately meaningful once that reset happens.",
                preflight.killSwitchShouldStartTripped(),
                unresolvedMarkers);
        log.info(
                "PaperTradingApp constructed in kis-paper mode: symbol={} kisPaperBaseUrl={} balance={}"
                        + " preflightFoundNonZeroPosition={} unresolvedSubmissionMarkersRequiringReview={}",
                symbol,
                KIS_PAPER_BASE_URL,
                preflight.balance().balance(),
                preflight.killSwitchShouldStartTripped(),
                unresolvedMarkers);
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
     * {@code var/live/{symbol}-kis_submission_markers.json} -- real gap
     * found and fixed after Task 4 merged: this path used to be a single
     * hardcoded constant (no {@code symbol} in it at all), which was
     * enough to keep {@code bingx-vst} and {@code kis-paper} from
     * colliding with each other (see {@link #forKisPaper}'s own Javadoc),
     * but did nothing to stop two {@code kis-paper} processes trading two
     * different KOSPI200 symbols from colliding with <em>each other</em>
     * -- a real scenario once a real operator runs more than one KIS
     * symbol at a time, each as its own independent process (this
     * project's established one-process-per-symbol pattern, matching how
     * {@code bingx-vst}/{@code simulated} already run as separate
     * processes today). No environment-variable override, matching this
     * path's own established no-config-surface precedent -- {@code
     * symbol} is already the one input that must differ between any two
     * concurrent {@code kis-paper} processes, so deriving from it alone
     * is sufficient, same reasoning as {@link #resolveSignalPath}'s own
     * {@code symbol}-derived default.
     *
     * @throws IllegalArgumentException if {@code symbol} isn't a single
     *     safe filename segment (contains a path separator, or is exactly
     *     {@code "."}/{@code ".."}) -- real CodeRabbit review finding on
     *     this method itself: a symbol containing a path separator or
     *     traversal sequence could otherwise resolve outside {@code
     *     var/live/}. {@code symbol} is operator-configured (an env var),
     *     not attacker-controlled network input, so the practical exposure
     *     here is low -- validated anyway, since the check is cheap and
     *     removes the question entirely rather than relying on that
     *     distinction. {@link #resolveSignalPath} above has the same
     *     unvalidated raw-{@code symbol}-as-path-segment shape and was not
     *     flagged; left as-is here since fixing it is outside the scope of
     *     the marker-path collision this method exists to fix.
     */
    static Path resolveKisSubmissionMarkersPath(String symbol) {
        if (symbol.contains("/") || symbol.contains("\\") || symbol.equals(".") || symbol.equals("..")) {
            throw new IllegalArgumentException(
                    "symbol must be a single safe filename segment (no path separators or '.'/'..'), was '" + symbol
                            + "'");
        }
        return Path.of("var", "live", symbol + "-kis_submission_markers.json");
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
     *
     * <p><b>{@link TradingLoop#tick()} itself is gated on {@link
     * #tradingCalendar}</b> (added for {@code kis-paper} mode -- see
     * CLAUDE.md's KIS/KOSPI200 Phase 1 design, Task 3/4): {@code
     * simulated}/{@code bingx-vst} always use {@link
     * AlwaysOpenTradingCalendar}, so this is unconditionally {@code true}
     * for them -- provably unaffected, not just asserted. {@code
     * beforeTick()}/{@code afterTick()}/{@link #reconcile()} all still run
     * regardless of whether the market is open, matching this method's own
     * existing "regardless of the tick's own outcome" philosophy below --
     * a closed market is a defined, expected skip, not a failure.
     *
     * <p><b>A closed market skips new-signal processing only, not fill
     * polling</b> (real CodeRabbit review finding, corrected across two
     * review rounds on the PR that added this gate): a pending order can
     * resolve at the exchange at any time, including right at/after close,
     * so {@link TradingLoop#pollPendingFills()} -- the poll-and-reconcile
     * portion {@link TradingLoop#tick()} would otherwise run as its own
     * first step -- is called directly here even when the market is
     * closed. Wrapped in its own {@code try}/{@code catch} (matching this
     * class's own "a single tick's failure must never propagate"
     * philosophy) since it runs outside {@code tick()}'s own error
     * handling in this branch.
     */
    void runTick() {
        dailyReportGenerator.beforeTick();
        if (tradingCalendar.isOpen(clock.instant())) {
            tradingLoop.tick();
            Throwable error = tradingLoop.lastError();
            if (error != null) {
                log.warn(
                        "tick completed with an error: lastTickAt={} equity={} error={}",
                        tradingLoop.lastTickAt(),
                        tradingLoop.currentEquity(),
                        error.toString());
            } else {
                log.info(
                        "tick complete: lastTickAt={} equity={}", tradingLoop.lastTickAt(), tradingLoop.currentEquity());
            }
        } else {
            log.debug(
                    "market closed per {}, skipping new-signal processing -- still polling pending fills",
                    tradingCalendar.getClass().getSimpleName());
            try {
                tradingLoop.pollPendingFills();
            } catch (RuntimeException e) {
                log.warn("pollPendingFills failed while market closed, will retry next scheduled tick", e);
            }
        }
        dailyReportGenerator.afterTick();
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
     * this returns, and any in-flight tick is given up to {@link
     * #gracefulShutdownTimeout} (production default {@link
     * #DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT}, 10 seconds) to finish before a
     * forced {@code shutdownNow()} (itself given a further {@link
     * #forcedShutdownTimeout}, production default {@link
     * #DEFAULT_FORCED_SHUTDOWN_TIMEOUT}, 5 seconds, to actually confirm
     * termination -- see below). Called from {@link #main}'s JVM shutdown
     * hook (SIGTERM / normal JVM exit); safe to call more than once (a
     * shutdown hook racing an explicit {@code stop()} elsewhere) or even if
     * {@link #start()} was never called.
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
     * logged loudly rather than silently risking a wrong report. This
     * specific invariant -- finalization never runs before an in-flight
     * tick actually terminates, and is skipped outright when termination
     * can't be confirmed within {@code gracefulShutdownTimeout} plus
     * {@code forcedShutdownTimeout} -- is deterministically proven in
     * {@code PaperTradingAppTest} (GitHub issue #74), using the {@link
     * #PaperTradingApp(String, String, Path, long, Path, Clock, Duration,
     * Duration) Duration-accepting test-only constructor overload} to
     * shrink these two timeouts and a real hung fake HTTP server ({@code
     * FakeBingXTradesServer#hangForever()}) to force a real tick to block
     * past them.
     */
    public synchronized void stop() {
        log.info("stopping paper trading loop");
        executor.shutdown();
        boolean terminated;
        try {
            terminated = executor.awaitTermination(gracefulShutdownTimeout.toMillis(), TimeUnit.MILLISECONDS);
            if (!terminated) {
                log.warn(
                        "paper trading loop did not stop cleanly within {}, forcing shutdown",
                        gracefulShutdownTimeout);
                executor.shutdownNow();
                terminated = executor.awaitTermination(forcedShutdownTimeout.toMillis(), TimeUnit.MILLISECONDS);
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
