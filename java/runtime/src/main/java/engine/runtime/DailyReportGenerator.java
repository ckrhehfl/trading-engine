package engine.runtime;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.execution.Fill;
import engine.schemas.SchemaObjectMapper;
import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Clock;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
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
 * <p><b>Write failures never propagate</b>: a failure while writing the
 * report file is caught, logged at {@code ERROR} (maximally visible --
 * "no missing daily reports" is a hard Paper Trading Pass Criterion, so a
 * silently-swallowed write failure is exactly the failure mode that
 * criterion exists to catch), and swallowed -- matching
 * {@link TradingLoop#tick()}'s own "never propagate out of a scheduled
 * cycle" contract. The write itself is atomic (temp file + {@code
 * ATOMIC_MOVE}), the same {@code .tmp}-then-rename convention {@code
 * python/live/generate_daily_signal.py} already uses for its own signal
 * file, so a reader never observes a half-written report.
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
            return;
        }
        if (today.isAfter(currentDay)) {
            LocalDate finishedDay = currentDay;
            writeReport(buildReport(finishedDay));
            startNewDay(today);
        }
        // today.isBefore(currentDay): a backwards clock adjustment (NTP
        // sync, manual change) -- deliberately not treated as a boundary;
        // the day already being tracked keeps accumulating rather than
        // finalizing early on a clock glitch.
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

    private void writeReport(DailyReport report) {
        try {
            Files.createDirectories(reportsDirectory);
            String fileName = report.date() + ".json";
            Path target = reportsDirectory.resolve(fileName);
            Path tmp = reportsDirectory.resolve(fileName + ".tmp");
            String json = objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(report);
            Files.writeString(tmp, json);
            Files.move(tmp, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
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
        } catch (IOException e) {
            // Deliberately swallowed, not rethrown -- see class Javadoc's
            // "Write failures never propagate" section.
            log.error("failed to write daily report for {}: {}", report.date(), e.toString(), e);
        }
    }

    /** Test/manual-verification-only accessor -- mirrors {@code PaperTradingApp.tradingLoop()}'s existing precedent. */
    LocalDate currentDay() {
        return currentDay;
    }
}
