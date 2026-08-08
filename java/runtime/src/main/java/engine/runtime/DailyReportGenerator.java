package engine.runtime;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.execution.Fill;
import engine.schemas.SchemaObjectMapper;
import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Clock;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Detects UTC day boundaries across repeated {@link TradingLoop#tick()}
 * calls and writes one {@link DailyReport} per completed day to
 * {@code var/live/reports/daily/<date>.json} -- Paper-trading bridge Task
 * D (depends on Task C's {@link PaperTradingApp}). Full design writeup,
 * including the four judgment calls below, in
 * {@code .planning/paper-trading-d-daily-reporting.md}.
 *
 * <p><b>Not itself a scheduler</b> -- like {@link TradingLoop}, this class
 * has no timer of its own. {@link #beforeTick()} and {@link #afterTick()}
 * are meant to be called once each, immediately before and immediately
 * after every real {@link TradingLoop#tick()} call, by whatever drives the
 * schedule ({@link PaperTradingApp}'s {@code ScheduledExecutorService} in
 * production; a plain loop in a test). This mirrors {@code TradingLoop}'s
 * own "the entire unit of work is a method meant to be invoked repeatedly
 * by an external scheduler" design rather than introducing a second timer.
 *
 * <p><b>Day-boundary detection, and why it happens in {@code beforeTick()}
 * rather than {@code afterTick()}</b>: on every call, {@code beforeTick()}
 * computes {@code LocalDate.ofInstant(clock.instant(), ZoneOffset.UTC)}
 * ("today") and compares it against the day this generator is currently
 * tracking. The very first call ever made just seeds tracking state (no
 * prior day exists to finalize). Every later call where "today" has
 * advanced finalizes and writes the report for the day that just ended --
 * using state as it stood at that exact instant, i.e. <b>before</b> the
 * upcoming {@code tick()} call (which belongs to the new day) has any
 * chance to run. Detecting the boundary in {@code afterTick()} instead
 * would misattribute that tick's own price/fill/error effects to the day
 * that just ended rather than the new day that actually produced them.
 *
 * <p><b>Trade attribution</b>: {@link TradingLoop#fillHistory()} is a
 * single unbounded list for the loop's entire lifetime, not day-scoped.
 * This class tracks {@code fillHistory().size()} at the start of each day
 * as a baseline and reports every fill from that index onward as "this
 * day's trades" when the day ends -- correct because the list only ever
 * grows (fills are never removed), so a later {@code size()} minus the
 * baseline is exactly this day's slice, without needing to inspect each
 * {@link Fill}'s own timestamp.
 *
 * <p><b>Error attribution</b>: {@link TradingLoop#lastError()} exposes
 * only the single most recent tick's outcome, not a history -- so this
 * class builds its own day-scoped accumulator, appending one
 * {@link DailyReport.TickError} per failed tick as {@link #afterTick()}
 * observes it (paired with {@link TradingLoop#lastTickAt()} for the
 * timestamp). {@link #afterTick()} also counts every tick as either
 * succeeded or failed, which is what {@link DailyReport#uptimeFraction()}
 * (succeeded/attempted for the day) is computed from -- the only uptime
 * signal derivable from what {@code TradingLoop} already exposes, per this
 * task's own brief.
 *
 * <p><b>Restart mid-day, stated explicitly</b>: this class holds no
 * persisted state, matching {@link TradingLoop}'s own "starts clean" restart
 * story. A process restart mid-day therefore produces a <i>new</i>
 * {@code DailyReportGenerator} whose first {@code beforeTick()} call seeds
 * {@code dayStartEquity} from whatever {@link TradingLoop#currentEquity()}
 * reads <i>at that moment</i> -- indistinguishable, from this class's own
 * perspective, from the calendar day simply starting late. Any trading
 * activity from earlier in that same UTC day, before the restart, is lost
 * (the prior process's {@code TradingLoop} and this class both die with
 * it) and will not appear in that day's eventual report. This is a known,
 * disclosed limitation, not a bug -- full restart persistence is out of
 * scope for this task (see {@code .planning/paper-trading-c-scheduler-
 * entrypoint.md}'s identical "no OS-level process supervision" scope cut,
 * and {@code FileSignalSource}'s own disclosed cross-restart dedup
 * caveat). A day during which the process never ran at all produces no
 * report for that day at all -- also disclosed, not silently patched over.
 *
 * <p><b>Write failures never propagate, never discard data, and never
 * reorder</b>: a failure while writing the report file is caught and
 * logged at {@code ERROR} (maximally visible -- "no missing daily
 * reports" is a hard Paper Trading Pass Criterion, so a silently-
 * swallowed write failure is exactly the failure mode that criterion
 * exists to catch), but the completed {@link DailyReport} itself is
 * <b>not</b> discarded -- every completed report, without exception, is
 * enqueued to a small in-memory pending queue and only ever written by
 * {@link #flushPendingReports()}, which drains it strictly oldest-first
 * and stops at the first one that still fails. No report is ever written
 * directly outside that queue, so a newer, independently-writable report
 * can never land ahead of an older one that's still stuck (see
 * {@code flushPendingReports()}'s own Javadoc for the CodeRabbit review
 * finding this closes). {@link #beforeTick()}/{@link
 * #finalizeCompletedDayOnShutdown()} both call it unconditionally on
 * every invocation, so a still-pending report is retried on every later
 * tick, not only at the next day boundary. Day tracking still advances
 * to the new day immediately regardless of whether the write succeeded
 * -- a transient disk error must not stall the whole loop's day-boundary
 * detection, only that one day's report delivery. Same restart caveat as
 * everything else here: the pending queue is in-memory only, so a
 * process restart while a report is still pending loses it, same as any
 * other unwritten state this class holds -- a real, disclosed limitation
 * (a CodeRabbit review finding on this task's own PR #73 suggested a
 * durable, restart-recoverable outbox; declined as out of scope -- this
 * codebase has no durable persistence anywhere yet, see {@code
 * OrderStore}/{@code PaperBroker}'s own identical in-memory-only
 * precedent, and building one is real, scoped follow-on work, not a
 * silent addition to this task). The write itself is atomic (temp file +
 * {@code ATOMIC_MOVE}), the same {@code .tmp}-then-rename convention
 * {@code python/live/generate_daily_signal.py} already uses for its own
 * signal file, so a reader never observes a half-written report.
 */
public final class DailyReportGenerator {

    private static final Logger log = LoggerFactory.getLogger(DailyReportGenerator.class);
    private static final int UPTIME_FRACTION_SCALE = 6;

    private final TradingLoop tradingLoop;
    private final Path reportsDirectory;
    private final Clock clock;
    private final ObjectMapper objectMapper = SchemaObjectMapper.create();

    private LocalDate currentDay;
    private BigDecimal dayStartEquity;
    private int fillsBaselineIndex;
    private int ticksAttempted;
    private int ticksSucceeded;
    private final List<DailyReport.TickError> errorsThisDay = new ArrayList<>();
    // Completed-but-not-yet-durably-written reports, oldest first -- see
    // class Javadoc's "Write failures never propagate, and never discard
    // data" section.
    private final Deque<DailyReport> pendingReports = new ArrayDeque<>();

    /** Production convenience constructor -- real wall-clock UTC time. */
    public DailyReportGenerator(TradingLoop tradingLoop, Path reportsDirectory) {
        this(tradingLoop, reportsDirectory, Clock.systemUTC());
    }

    /** {@code clock} is overridable so tests can drive day-boundary crossings without waiting on real time. */
    public DailyReportGenerator(TradingLoop tradingLoop, Path reportsDirectory, Clock clock) {
        this.tradingLoop = Objects.requireNonNull(tradingLoop, "tradingLoop is required");
        this.reportsDirectory = Objects.requireNonNull(reportsDirectory, "reportsDirectory is required");
        this.clock = Objects.requireNonNull(clock, "clock is required");
    }

    /**
     * Must be called once, immediately before every real
     * {@link TradingLoop#tick()} call. See class Javadoc's "Day-boundary
     * detection" section for exactly what this does and why it runs here
     * rather than in {@link #afterTick()}.
     */
    public synchronized void beforeTick() {
        LocalDate today = LocalDate.ofInstant(clock.instant(), ZoneOffset.UTC);
        if (currentDay == null) {
            startNewDay(today);
        } else if (today.isAfter(currentDay)) {
            LocalDate finishedDay = currentDay;
            pendingReports.addLast(buildReport(finishedDay));
            startNewDay(today);
        }
        // today.isBefore(currentDay): a backwards clock adjustment (NTP
        // sync, manual change) -- deliberately not treated as a boundary;
        // the day already being tracked keeps accumulating rather than
        // finalizing early on a clock glitch.
        flushPendingReports();
    }

    /**
     * Finalizes and writes the report for the current day if it has
     * already ended by wall-clock time, without waiting for another
     * {@link #beforeTick()} call to notice. Meant to be called exactly
     * once during graceful shutdown (see {@link PaperTradingApp#stop()})
     * -- otherwise a day that finishes between the process's last real
     * tick and the process actually stopping would never get finalized
     * at all, since nothing would ever call {@link #beforeTick()} again
     * to detect it. Safe to call more than once (idempotent, matching
     * {@code PaperTradingApp.stop()}'s own idempotency) and safe to call
     * even if {@link #beforeTick()} was never called at all (nothing to
     * finalize). Deliberately does <b>not</b> finalize the still-in-
     * progress current day -- only a day that has fully ended is ever
     * written, matching every other write path in this class.
     */
    public synchronized void finalizeCompletedDayOnShutdown() {
        if (currentDay == null) {
            return; // nothing was ever tracked -- e.g. stopped before the first tick
        }
        LocalDate today = LocalDate.ofInstant(clock.instant(), ZoneOffset.UTC);
        if (today.isAfter(currentDay)) {
            LocalDate finishedDay = currentDay;
            pendingReports.addLast(buildReport(finishedDay));
            startNewDay(today);
        }
        flushPendingReports();
    }

    /** Must be called once, immediately after every real {@link TradingLoop#tick()} call. See class Javadoc. */
    public synchronized void afterTick() {
        if (currentDay == null) {
            log.warn("afterTick() called before beforeTick() ever ran; ignoring (no day is being tracked yet)");
            return;
        }
        ticksAttempted++;
        Throwable error = tradingLoop.lastError();
        if (error == null) {
            ticksSucceeded++;
        } else {
            errorsThisDay.add(new DailyReport.TickError(tradingLoop.lastTickAt(), error.toString()));
        }
    }

    private void startNewDay(LocalDate day) {
        currentDay = day;
        dayStartEquity = tradingLoop.currentEquity();
        fillsBaselineIndex = tradingLoop.fillHistory().size();
        ticksAttempted = 0;
        ticksSucceeded = 0;
        errorsThisDay.clear();
    }

    private DailyReport buildReport(LocalDate day) {
        BigDecimal endingEquity = tradingLoop.currentEquity();
        List<Fill> allFills = tradingLoop.fillHistory();
        int baseline = Math.min(fillsBaselineIndex, allFills.size());
        List<Fill> todaysTrades = List.copyOf(allFills.subList(baseline, allFills.size()));
        BigDecimal uptimeFraction = ticksAttempted == 0
                ? BigDecimal.ZERO.setScale(UPTIME_FRACTION_SCALE, RoundingMode.UNNECESSARY)
                : BigDecimal.valueOf(ticksSucceeded)
                        .divide(BigDecimal.valueOf(ticksAttempted), UPTIME_FRACTION_SCALE, RoundingMode.HALF_UP);
        return new DailyReport(
                day,
                dayStartEquity,
                endingEquity,
                todaysTrades,
                List.copyOf(errorsThisDay),
                tradingLoop.killSwitchTripped(),
                ticksAttempted,
                ticksSucceeded,
                uptimeFraction);
    }

    /**
     * The ONLY method that ever calls {@link #writeReport}: every newly
     * completed report is enqueued to {@link #pendingReports} first (never
     * written directly), and this drains that queue oldest-first,
     * removing each as it succeeds. Stops at the first one that still
     * fails, rather than skipping ahead to a later, possibly-independently-
     * writable report -- a CodeRabbit review finding on this task's own PR
     * (#73): an earlier version wrote a newly-completed report directly,
     * which could let it land ahead of an older, still-failing one if the
     * failure happened to be file-specific rather than categorical (e.g. a
     * blocked path for one date but not another), breaking the
     * chronological delivery this class's own Javadoc promises. Routing
     * every write through this single queue-then-flush path makes that
     * ordering structurally guaranteed rather than incidental.
     */
    private void flushPendingReports() {
        while (!pendingReports.isEmpty()) {
            DailyReport pending = pendingReports.peekFirst();
            if (writeReport(pending)) {
                pendingReports.removeFirst();
            } else {
                log.warn(
                        "{} daily report(s) still pending (oldest: {}); will retry on a later tick",
                        pendingReports.size(),
                        pending.date());
                break;
            }
        }
    }

    /** Returns whether the write succeeded -- never throws, see class Javadoc's "Write failures" section. */
    private boolean writeReport(DailyReport report) {
        try {
            Files.createDirectories(reportsDirectory);
            String fileName = report.date() + ".json";
            Path target = reportsDirectory.resolve(fileName);
            Path tmp = reportsDirectory.resolve(fileName + ".tmp");
            String json = objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(report);
            Files.writeString(tmp, json);
            try {
                Files.move(tmp, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            } catch (AtomicMoveNotSupportedException e) {
                // A CodeRabbit review finding on this task's own PR (#73):
                // some filesystems (network mounts, certain cross-volume
                // setups) don't support ATOMIC_MOVE at all -- without this
                // fallback, that condition never changes between retries,
                // so the report would fail this exact way forever, on
                // every future tick, rather than actually recovering. Not
                // atomic (a reader could in principle observe a moment
                // where neither the old nor the new file exists), but
                // still strictly better than a permanent failure loop --
                // logged so the degraded guarantee is visible.
                log.warn(
                        "ATOMIC_MOVE not supported for {} -> {}, falling back to a non-atomic replace: {}",
                        tmp,
                        target,
                        e.toString());
                Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING);
            }
            log.info(
                    "wrote daily report for {} to {}: trades={} errors={} ticksAttempted={} ticksSucceeded={}"
                            + " killSwitchTripped={}",
                    report.date(),
                    target,
                    report.trades().size(),
                    report.errors().size(),
                    report.ticksAttempted(),
                    report.ticksSucceeded(),
                    report.killSwitchTripped());
            return true;
        } catch (IOException e) {
            // Deliberately swallowed, not rethrown -- see class Javadoc's
            // "Write failures never propagate, and never discard data"
            // section. The report itself is retried by the caller, not
            // lost here.
            log.error("failed to write daily report for {}: {}", report.date(), e.toString(), e);
            return false;
        }
    }

    /** Test/manual-verification-only accessor -- mirrors {@code PaperTradingApp.tradingLoop()}'s existing precedent. */
    LocalDate currentDay() {
        return currentDay;
    }

    /** Test/manual-verification-only accessor -- same status as {@link #currentDay()} above. */
    int pendingReportCount() {
        return pendingReports.size();
    }
}
