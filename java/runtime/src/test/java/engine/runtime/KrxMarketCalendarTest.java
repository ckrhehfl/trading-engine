package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * All KST wall-clock test times below were converted to their exact UTC
 * {@link Instant} via a real {@code Asia/Seoul} zone conversion (not
 * hand-computed), to avoid an off-by-nine-hours mistake silently making
 * these tests meaningless.
 */
class KrxMarketCalendarTest {

    // Matches java/runtime/src/main/resources/krx-market-holidays.json --
    // coveredYears=[2026], including 2026-01-01 (New Year's Day) and
    // 2026-06-03 (지방선거일, a real KRX-wide market holiday -- see the
    // bundled fixture's own citation).
    private static final KrxMarketCalendar.HolidayFixture FIXTURE_2026 = new KrxMarketCalendar.HolidayFixture(
            List.of(2026), List.of(LocalDate.parse("2026-01-01"), LocalDate.parse("2026-06-03")));

    @Test
    void openOnOrdinaryWeekdayWithinRegularHours() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-10 (Wed) 10:00 KST -- ordinary weekday, ordinary hours.
        assertTrue(calendar.isOpen(Instant.parse("2026-06-10T01:00:00Z")));
    }

    @Test
    void closedOnWeekend() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-06 (Sat) 10:00 KST.
        assertFalse(calendar.isOpen(Instant.parse("2026-06-06T01:00:00Z")));
    }

    @Test
    void closedOnListedHoliday() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-01-01 (Thu) 10:00 KST -- New Year's Day, a listed holiday.
        assertFalse(calendar.isOpen(Instant.parse("2026-01-01T01:00:00Z")));
    }

    @Test
    void closedOnElectionDayHoliday() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-03 (Wed) 10:00 KST -- 제9회 전국동시지방선거일, an ordinary-
        // looking weekday that KRX closed for -- real CodeRabbit review
        // finding that this date was missing from both the fixture and
        // this test's own mirror of it.
        assertFalse(calendar.isOpen(Instant.parse("2026-06-03T01:00:00Z")));
    }

    @Test
    void closedBeforeRegularOpen() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-10 (Wed) 08:00 KST -- before the 08:45 open.
        assertFalse(calendar.isOpen(Instant.parse("2026-06-09T23:00:00Z")));
    }

    @Test
    void openJustBeforeOrdinaryCloseOnNonFinalTradingDay() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-10 (Wed) 15:44 KST -- the day before June's final
        // trading day (2026-06-11), so the ordinary 15:45 close applies.
        assertTrue(calendar.isOpen(Instant.parse("2026-06-10T06:44:00Z")));
    }

    @Test
    void closedJustAfterOrdinaryCloseOnNonFinalTradingDay() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-10 (Wed) 15:46 KST.
        assertFalse(calendar.isOpen(Instant.parse("2026-06-10T06:46:00Z")));
    }

    @Test
    void openOnFinalTradingDayMorning() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-11 (Thu) 10:00 KST -- June's real final trading day
        // (second Thursday), not colliding with any 2026 holiday.
        assertTrue(calendar.isOpen(Instant.parse("2026-06-11T01:00:00Z")));
    }

    @Test
    void openJustBeforeShortenedCloseOnFinalTradingDay() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-11 (Thu) 15:19 KST -- one minute before the final
        // trading day's shortened 15:20 close.
        assertTrue(calendar.isOpen(Instant.parse("2026-06-11T06:19:00Z")));
    }

    @Test
    void closedJustAfterShortenedCloseOnFinalTradingDay() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-11 (Thu) 15:21 KST -- one minute after the final
        // trading day's shortened 15:20 close (would still be open under
        // the ordinary 15:45 close -- this is the case that most directly
        // proves the shortened close is actually applied).
        assertFalse(calendar.isOpen(Instant.parse("2026-06-11T06:21:00Z")));
    }

    /**
     * {@code KrxMarketCalendar.isOpen} uses {@code time.isBefore(open)}/
     * {@code time.isBefore(close)} -- open is inclusive, close is
     * exclusive. This system gates real order submission, so the exact
     * boundary instants are pinned directly rather than only ever tested
     * one minute away from them (a real CodeRabbit review finding: a
     * one-minute margin would silently survive someone swapping {@code
     * isBefore} for {@code !isAfter}, which would incorrectly treat the
     * close instant itself as still open).
     */
    @Test
    void openAtExactRegularOpenInstantClosedOneSecondBefore() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-10 (Wed) 08:45:00 KST exactly -- open is inclusive.
        assertTrue(calendar.isOpen(Instant.parse("2026-06-09T23:45:00Z")));
        // 08:44:59 KST -- one second before, still closed.
        assertFalse(calendar.isOpen(Instant.parse("2026-06-09T23:44:59Z")));
    }

    @Test
    void closedAtExactOrdinaryCloseInstantOpenOneSecondBefore() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-10 (Wed) 15:45:00 KST exactly -- close is exclusive.
        assertFalse(calendar.isOpen(Instant.parse("2026-06-10T06:45:00Z")));
        // 15:44:59 KST -- one second before, still open.
        assertTrue(calendar.isOpen(Instant.parse("2026-06-10T06:44:59Z")));
    }

    @Test
    void closedAtExactShortenedCloseInstantOpenOneSecondBeforeOnFinalTradingDay() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2026-06-11 (Thu, final trading day) 15:20:00 KST exactly.
        assertFalse(calendar.isOpen(Instant.parse("2026-06-11T06:20:00Z")));
        // 15:19:59 KST -- one second before, still open.
        assertTrue(calendar.isOpen(Instant.parse("2026-06-11T06:19:59Z")));
    }

    /**
     * Forces {@code isFinalTradingDay}'s preceding-business-day search to
     * exceed {@code MAX_PRECEDING_BUSINESS_DAY_SEARCH_DAYS} (10) by marking
     * March 2030's second Thursday (2030-03-14) and the 9 weekdays before
     * it (2030-03-04 through 2030-03-13, skipping the real weekend at
     * 03-09/03-10) all as holidays -- 11 consecutive weekend-or-holiday
     * days, one more than the bound allows. The query date itself
     * (2030-03-15, the Friday immediately after) is deliberately NOT one
     * of the listed holidays, so {@code isOpen} reaches the final-trading-
     * day check via the ordinary path rather than short-circuiting on its
     * own {@code holidays.contains(date)} check -- this is what actually
     * proves the {@code UNRESOLVED} fail-closed branch itself, not just
     * "some other reason this date is closed" (a real CodeRabbit review
     * finding: this branch had no test coverage at all before).
     */
    @Test
    void failsClosedWhenFinalTradingDayCannotBeResolvedWithinTheSearchBound() {
        KrxMarketCalendar.HolidayFixture fixture = new KrxMarketCalendar.HolidayFixture(
                List.of(2030),
                List.of(
                        LocalDate.parse("2030-03-04"),
                        LocalDate.parse("2030-03-05"),
                        LocalDate.parse("2030-03-06"),
                        LocalDate.parse("2030-03-07"),
                        LocalDate.parse("2030-03-08"),
                        LocalDate.parse("2030-03-11"),
                        LocalDate.parse("2030-03-12"),
                        LocalDate.parse("2030-03-13"),
                        LocalDate.parse("2030-03-14")));
        KrxMarketCalendar calendar = new KrxMarketCalendar(fixture);

        // 2030-03-15 (Fri) 10:00 KST -- an ordinary, non-holiday weekday in
        // the same quarterly month, during regular hours.
        assertFalse(calendar.isOpen(Instant.parse("2030-03-15T01:00:00Z")));
    }

    @Test
    void closedForEveryDateInAnUncoveredYear() {
        KrxMarketCalendar calendar = new KrxMarketCalendar(FIXTURE_2026);
        // 2025-06-10 (an ordinary-looking Tuesday, 10:00 KST) -- but 2025
        // is not in coveredYears, so this must fail closed regardless of
        // how ordinary the date otherwise looks.
        assertFalse(calendar.isOpen(Instant.parse("2025-06-10T01:00:00Z")));
    }

    @Test
    void finalTradingDayShiftsToPrecedingBusinessDayWhenSecondThursdayIsAHoliday() {
        // Synthetic fixture: 2030-03-14 (the real second Thursday of
        // March 2030) is marked as a holiday, so the final trading day
        // must shift back to 2030-03-13 (Wednesday) -- proves the
        // preceding-business-day search, not just the ordinary case.
        KrxMarketCalendar.HolidayFixture fixture = new KrxMarketCalendar.HolidayFixture(
                List.of(2030), List.of(LocalDate.parse("2030-03-14")));
        KrxMarketCalendar calendar = new KrxMarketCalendar(fixture);

        // The shifted day (03-13) now carries the shortened 15:20 close.
        assertTrue(calendar.isOpen(Instant.parse("2030-03-13T06:19:00Z")));
        assertFalse(calendar.isOpen(Instant.parse("2030-03-13T06:21:00Z")));
        // The original second Thursday (03-14) is a holiday -- closed all day.
        assertFalse(calendar.isOpen(Instant.parse("2030-03-14T01:00:00Z")));
    }

    @Test
    void bundledResourceLoadsWithoutError() {
        // Sanity check for the real, committed fixture -- KrxMarketCalendar's
        // no-arg constructor must not throw, and the 2026 dates it's known
        // to cover must behave as expected.
        KrxMarketCalendar calendar = new KrxMarketCalendar();

        assertTrue(calendar.isOpen(Instant.parse("2026-06-10T01:00:00Z")));
        assertFalse(calendar.isOpen(Instant.parse("2026-01-01T01:00:00Z")));
        // 2026-06-10 (Wed) 20:00 KST -- night-session hours (18:00-06:00
        // KST) are deliberately out of scope (see class Javadoc) and must
        // be closed, not just untested.
        assertFalse(calendar.isOpen(Instant.parse("2026-06-10T11:00:00Z")));
    }
}
