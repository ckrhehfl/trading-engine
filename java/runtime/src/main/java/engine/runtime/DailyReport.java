package engine.runtime;

import engine.execution.Fill;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

/**
 * One completed UTC day's raw paper-trading activity, written by
 * {@link DailyReportGenerator} to
 * {@code var/live/reports/daily/<date>.json} -- Paper-trading bridge Task
 * D, see {@code .planning/paper-trading-d-daily-reporting.md} for the full
 * design writeup. Deliberately raw ingredients only, not a scored "paper
 * score" -- see the governing plan's own "Paper score formula: deliberately
 * NOT defined now" decision and CLAUDE.md's "Paper Trading Pass Criteria",
 * which this feeds.
 *
 * <p>{@code startingEquity}/{@code endingEquity} are {@link TradingLoop}'s
 * own equity figure (see that class's "Equity tracking" Javadoc -- fee-
 * driven only, not a full PnL engine) sampled at this day's first and last
 * observed tick respectively. {@code trades} is every {@link Fill}
 * {@link TradingLoop} applied during this day, in application order.
 * {@code uptimeFraction} is {@code ticksSucceeded / ticksAttempted} for
 * this day (see {@link DailyReportGenerator} for exactly how a "tick" is
 * counted) -- the only uptime signal derivable from
 * {@link TradingLoop#lastError()}, which exposes only the most recent
 * tick's outcome, not a full day's history; {@code errors} is this
 * report's own answer to that same gap, accumulated tick-by-tick as
 * {@link DailyReportGenerator} observes them.
 */
public record DailyReport(
        LocalDate date,
        BigDecimal startingEquity,
        BigDecimal endingEquity,
        List<Fill> trades,
        List<TickError> errors,
        boolean killSwitchTripped,
        int ticksAttempted,
        int ticksSucceeded,
        BigDecimal uptimeFraction) {

    /** One tick's caught exception, per {@link TradingLoop#lastError()} -- see class Javadoc. */
    public record TickError(Instant occurredAt, String message) {}
}
