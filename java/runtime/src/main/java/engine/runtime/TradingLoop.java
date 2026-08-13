package engine.runtime;

import engine.execution.Fill;
import engine.execution.OrderExecutor;
import engine.execution.PaperBroker;
import engine.oms.Order;
import engine.risk.AccountState;
import engine.risk.RiskGateway;
import engine.schemas.OrderIntent;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The actual running loop that drives {@link OrderPipeline} against a
 * pluggable {@link OrderExecutor} -- {@link PaperBroker} (the internal
 * simulator) is the only implementation that exists today, but this class
 * depends only on the interface, so a future real-exchange wrapper can be
 * swapped in without ever touching this class's control-flow logic (see
 * {@code .planning/paper-trading-f-order-executor.md}) -- and against a
 * pluggable {@link PriceFeed} ({@link BingXPriceFeed} is the only
 * implementation that exists today, polling a REST endpoint; same
 * extraction precedent as {@link OrderExecutor}), using a pluggable {@link
 * SignalSource} for its signals -- e.g. {@link DummySignalSource}
 * (explicitly not real, see that class's Javadoc, and CLAUDE.md's "Strategy
 * Research Methodology" section, for why) or {@link FileSignalSource} (a
 * real strategy's output, delivered by a separate process). This class
 * depends only on the {@code SignalSource}/{@code OrderExecutor}
 * interfaces, not on any one implementation of either -- see
 * {@code .planning/paper-trading-a-signal-source.md} and
 * {@code .planning/paper-trading-f-order-executor.md}. Paper-only today: no
 * {@code BingXAdapter}/live wiring exists in this class or anywhere it's
 * called from -- see .planning/08b-trading-loop.md's "Deliberately out of
 * scope" section.
 *
 * <p>{@link #tick()} is the entire unit of work, meant to be invoked
 * repeatedly by an external scheduler (a
 * {@code java.util.concurrent.ScheduledExecutorService}, for instance --
 * deliberately not wired up inside this class itself, so the core logic
 * stays directly, deterministically callable from tests without waiting on
 * a real scheduler). Every exception raised anywhere inside a single
 * {@code tick()} call is caught inside {@code tick()} itself and recorded
 * in {@link #lastError()} rather than propagated -- a single bad tick (a
 * flaky price-feed response, for instance) must never kill whatever is
 * calling {@code tick()} on a schedule. This -- not OS-level process
 * supervision, which is out of scope here (see the planning doc) -- is
 * what "24/7 supervision" means at this stage.
 *
 * <p><b>Reconciliation on construction</b>: this class does not assume any
 * prior state. It reads {@link OrderExecutor#pendingOrders()} fresh every
 * {@code tick()} (via the unconditional {@code pollFills} call below)
 * rather than caching anything from before it was constructed -- correct
 * for today's paper loop, since {@code PaperBroker}/{@code OrderStore} are
 * both in-memory and non-durable already, so "start clean" is the only
 * state there is. A live {@code OrderExecutor} swapped in later would
 * instead need a real reconciliation step here (querying
 * {@code ExchangeAdapter.getPositions()}/{@code getBalance()} as ground
 * truth) -- not built, but this class's shape (no persisted/cached state
 * of its own beyond what it reads from {@code OrderExecutor} each tick)
 * doesn't make that swap-in awkward.
 *
 * <p><b>Equity tracking</b> is intentionally minimal, not a PnL engine: it
 * starts at a fixed value and is only ever adjusted downward by the fee on
 * each {@link Fill}, whether from a fresh submission or a price-driven
 * reconciliation. There is no unrealized-PnL, mark-to-market, or
 * multi-day PnL bucketing here -- {@link AccountState}'s daily/weekly/
 * monthly figures are all derived from the same single running total,
 * which is a real simplification (compare {@link RiskGateway}'s own
 * equivalent Javadoc note on monthly/hard-stop/emergency-stop), not a bug.
 * It exists purely so {@link RiskGateway#evaluate} has a real, moving
 * number to evaluate against instead of a hardcoded constant every tick.
 *
 * <p><b>Symbol-match validation</b> (closes GitHub issue #70 / the release
 * gate recorded in {@code .planning/paper-trading-a-signal-source.md}):
 * every signal {@link #tick()} receives from {@link SignalSource
 * #nextSignal()} is checked against this loop's own configured
 * {@link #symbol} before it is ever handed to {@link OrderPipeline
 * #submitIntent}. This class -- not any one {@code SignalSource}
 * implementation -- owns the check, deliberately: {@code symbol} is
 * already this loop's own single source of truth for "what am I actually
 * trading" (it is what {@link #priceFeed} is polled with and what
 * {@code OrderPipeline}/{@code OrderExecutor} are driven with two lines
 * later), so the invariant belongs to the class that already holds it,
 * not duplicated into every current or future {@code SignalSource}
 * implementation. A mismatch is a defined, logged skip -- exactly like
 * the kill-switch-tripped and equity-depleted skips above -- never a
 * thrown exception and never a {@link #lastError()}. See
 * {@code .planning/paper-trading-c-scheduler-entrypoint.md} for the full
 * writeup of why this layer was chosen over validating inside
 * {@link FileSignalSource} itself.
 *
 * <p><b>Fill history</b> ({@link #fillHistory()}) and <b>kill-switch
 * state</b> ({@link #killSwitchTripped()}) are both read-only accessors
 * added for {@code DailyReportGenerator} (Paper-trading bridge Task D --
 * see {@code .planning/paper-trading-d-daily-reporting.md}), which needs
 * both to build a per-UTC-day report but has no reason to hold its own
 * separate reference to a {@code KillSwitch}, and no other way to learn
 * "what trades happened" -- this class was the only place that already
 * saw every {@link Fill} as it happened. {@code fillHistory} is an
 * unbounded in-memory list for this instance's lifetime (not persisted,
 * not reset except by constructing a fresh {@code TradingLoop} -- same
 * "starts clean" restart story as everything else on this class, see
 * above); {@code DailyReportGenerator} is the one that slices it into
 * daily windows, this class has no notion of a day boundary itself.
 *
 * <p><b>Unbounded growth, considered and deliberately not addressed here
 * (CodeRabbit review finding on Task D's own PR #73):</b> a genuinely
 * high-frequency strategy retaining every {@link Fill} for a process's
 * entire uptime could eventually matter for memory. At this project's
 * actual current scale it does not: the only strategy with a paper-
 * trading policy exception (`daily-tsmom-ensemble`, see CLAUDE.md) is a
 * daily-bar strategy producing at most a small handful of fills per day,
 * against a single symbol, over the Paper Trading Pass Criteria's own
 * 30-45 day window -- tens of {@code Fill} records, not thousands. A
 * "consume unreported fills, retain only fills belonging to a not-yet-
 * durably-written report" redesign was considered and rejected for this
 * task: it would require {@code TradingLoop} itself to learn about report
 * write success/failure, reopening exactly the day-boundary/report
 * coupling this class's own design deliberately keeps out of it (see
 * "Trade attribution" in {@code DailyReportGenerator}'s class Javadoc --
 * that class owns all notion of "day," this one does not). Revisit if
 * this project ever runs a materially higher-frequency strategy or a
 * materially longer-lived process than its current single-symbol,
 * daily-bar, weeks-long paper-trading scope.
 *
 * <p><b>Submitted-order-id tracking</b> (paper-trading bridge Task E, see
 * {@code .planning/paper-trading-e-reconciliation.md}): every client order
 * id that {@link OrderPipeline#submitIntent} successfully registers is
 * appended to an internal history, exposed read-only via {@link
 * #submittedOrderIds()}. This exists purely to feed {@link Reconciler
 * #check}, which cross-checks it against {@link engine.oms.OrderStore} and
 * {@link OrderExecutor}'s own bookkeeping -- neither of those two classes
 * exposes a full enumeration of everything it has ever tracked, so this
 * loop's own record fills that gap. It is a plain, unbounded {@code
 * List<UUID>} -- fine for this project's currently-known usage (a
 * daily-cadence strategy produces, at most, a handful of entries per day
 * over a 30-45 day paper-trading run), not designed to be pruned or bounded
 * for a much higher-frequency signal source.
 */
public final class TradingLoop {

    private static final Logger log = LoggerFactory.getLogger(TradingLoop.class);
    private static final BigDecimal INITIAL_EQUITY = new BigDecimal("100000");
    private static final int PNL_PERCENT_SCALE = 8;

    private final OrderPipeline orderPipeline;
    private final OrderExecutor orderExecutor;
    private final SignalSource signalSource;
    private final PriceFeed priceFeed;
    private final KillSwitch killSwitch;
    private final String symbol;

    private BigDecimal equity = INITIAL_EQUITY;
    private boolean lastLoggedTripState = false;
    private boolean equityDepletedLogged = false;
    private final List<Fill> fillHistory = new ArrayList<>();
    private final List<UUID> submittedOrderIds = new ArrayList<>();

    private volatile Instant lastTickAt;
    private volatile Throwable lastError;

    public TradingLoop(
            OrderPipeline orderPipeline,
            OrderExecutor orderExecutor,
            SignalSource signalSource,
            PriceFeed priceFeed,
            KillSwitch killSwitch,
            String symbol) {
        this.orderPipeline = Objects.requireNonNull(orderPipeline, "orderPipeline is required");
        this.orderExecutor = Objects.requireNonNull(orderExecutor, "orderExecutor is required");
        this.signalSource = Objects.requireNonNull(signalSource, "signalSource is required");
        this.priceFeed = Objects.requireNonNull(priceFeed, "priceFeed is required");
        this.killSwitch = Objects.requireNonNull(killSwitch, "killSwitch is required");
        this.symbol = Objects.requireNonNull(symbol, "symbol is required");
    }

    /**
     * One tick of the loop: (1) check/log the kill switch's state, (2)
     * fetch the latest price and reconcile any pending orders against it
     * regardless of whether a new signal fires this tick, (3) if the
     * switch isn't tripped, ask for a new signal and submit it through
     * {@link OrderPipeline}/{@link OrderExecutor} if one is produced. Never
     * throws -- see class Javadoc.
     */
    public synchronized void tick() {
        try {
            boolean tripped = killSwitch.isTripped();
            if (tripped != lastLoggedTripState) {
                log.info(
                        "kill switch {}: {}",
                        tripped ? "TRIPPED" : "RESET",
                        tripped
                                ? "new signal generation suspended; existing pending orders still reconcile"
                                : "new signal generation resumed");
                lastLoggedTripState = tripped;
            }

            BigDecimal price = priceFeed.latestPrice(symbol);
            applyFills(orderExecutor.pollFills(symbol, price));

            if (!tripped) {
                // Equity only ever moves down here (see class Javadoc), and
                // AccountState itself rejects a non-positive equity outright
                // -- without this guard, a depleted account would hit that
                // rejection inside buildAccountState() below on every future
                // signal-firing tick, forever, routed through the
                // catch-all as an unrecoverable-looking "tick failed" error
                // rather than the expected, self-explanatory condition it
                // actually is. This is a defined skip, not a failure, so it
                // does not touch lastError.
                if (equity.signum() <= 0) {
                    if (!equityDepletedLogged) {
                        log.warn("equity depleted ({}); skipping signal submission until process restart", equity);
                        equityDepletedLogged = true;
                    }
                } else {
                    Optional<OrderIntent> signal = signalSource.nextSignal();
                    if (signal.isPresent()) {
                        OrderIntent intent = signal.get();
                        if (!intent.symbol().equals(symbol)) {
                            // See class Javadoc, "Symbol-match validation".
                            // A defined, logged skip -- not a tick failure
                            // -- so lastError is untouched, same treatment
                            // as the kill-switch/equity-depleted skips
                            // above.
                            log.warn(
                                    "signal {} has symbol {} but this loop is configured for {}; rejecting,"
                                            + " not submitting",
                                    intent.intentId(),
                                    intent.symbol(),
                                    symbol);
                        } else {
                            Optional<Order> order = orderPipeline.submitIntent(intent, price, buildAccountState());
                            if (order.isPresent()) {
                                // Recorded before submitToBroker() below, not
                                // after -- see Reconciler's own Javadoc: a
                                // submission *attempt* is what
                                // DUPLICATE_SUBMISSION_ATTEMPT detects, and
                                // that must be true even when the broker call
                                // itself goes on to fail (e.g. PaperBroker's
                                // own duplicate-id guard).
                                submittedOrderIds.add(order.get().clientOrderId());
                                submitToBroker(order.get(), price);
                            }
                        }
                    }
                }
            }
            lastError = null;
        } catch (Exception e) {
            log.warn("tick failed, will retry next scheduled tick", e);
            lastError = e;
        } finally {
            lastTickAt = Instant.now();
        }
    }

    /** Health-check surface for this priority -- see class Javadoc. */
    public Instant lastTickAt() {
        return lastTickAt;
    }

    /** Health-check surface for this priority -- see class Javadoc. Null if the last tick succeeded. */
    public Throwable lastError() {
        return lastError;
    }

    public synchronized BigDecimal currentEquity() {
        return equity;
    }

    /**
     * Every {@link Fill} this loop has ever applied, in application order,
     * since this instance was constructed. See class Javadoc's "Fill
     * history" section for why this exists and its restart caveat.
     */
    public synchronized List<Fill> fillHistory() {
        return List.copyOf(fillHistory);
    }

    /**
     * Delegates to this loop's own {@link KillSwitch#isTripped()}. See
     * class Javadoc's "Fill history" section for why this accessor exists.
     */
    public boolean killSwitchTripped() {
        return killSwitch.isTripped();
    }

    /**
     * Every client order id this loop has ever successfully registered
     * through {@link OrderPipeline#submitIntent} -- including a repeat of
     * the same id, if one ever occurs (see class Javadoc, "Submitted-
     * order-id tracking"). A snapshot copy is returned; mutating it does
     * not affect this loop's own internal history.
     */
    public synchronized List<UUID> submittedOrderIds() {
        return List.copyOf(submittedOrderIds);
    }

    /**
     * {@link OrderPipeline#submitIntent} already registered {@code order}
     * in {@code OrderStore} by the time this is called -- that registration
     * happens atomically with {@link RiskGateway#evaluate}, deliberately
     * (see {@code OrderPipeline}'s own Javadoc on why it's the one path
     * that ever calls {@code Order.fromApprovedDecision}), so it cannot be
     * deferred until after a successful broker submission without
     * reopening exactly the provenance gap {@code OrderPipeline} exists to
     * close. If {@link OrderExecutor#submit} throws here, {@code order} is
     * left registered in {@code OrderStore} but never reaches
     * {@code OrderExecutor.pendingOrders()} -- an orphan that will never
     * receive a fill. A structural fix (rollback, or a deferred-persistence
     * redesign of {@code OrderPipeline}/{@code OrderStore}'s public
     * contract) is out of scope for this class -- see
     * .planning/08b-trading-loop.md's "Judgment calls" section for why,
     * and CLAUDE.md's Priority #8/#10 entries for the closely related,
     * already-tracked {@code OrderStore.createOrder} visibility gap. This
     * at least makes the orphan diagnosable via logs instead of silent;
     * the exception is rethrown so it still surfaces through {@code tick()}'s
     * own catch-all / {@link #lastError()}.
     */
    private void submitToBroker(Order order, BigDecimal price) {
        try {
            Optional<Fill> fill = orderExecutor.submit(order, price);
            fill.ifPresent(f -> applyFills(List.of(f)));
        } catch (RuntimeException e) {
            log.error(
                    "order {} was registered in OrderStore but OrderExecutor.submit failed -- "
                            + "it is now orphaned and will never receive a fill: {}",
                    order.clientOrderId(),
                    e.toString());
            throw e;
        }
    }

    private synchronized void applyFills(List<Fill> fills) {
        for (Fill fill : fills) {
            equity = equity.subtract(fill.fee());
            fillHistory.add(fill);
        }
    }

    private synchronized AccountState buildAccountState() {
        BigDecimal pnlPercent =
                equity.subtract(INITIAL_EQUITY).divide(INITIAL_EQUITY, PNL_PERCENT_SCALE, RoundingMode.HALF_UP);
        return new AccountState(equity, pnlPercent, pnlPercent, pnlPercent);
    }
}
