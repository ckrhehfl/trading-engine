package engine.runtime;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.time.DayOfWeek;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.Month;
import java.time.YearMonth;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.temporal.TemporalAdjusters;
import java.util.EnumSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;

/**
 * KOSPI200 index futures' real regular-session trading calendar --
 * 08:45-15:45 KST on weekdays, shortened to 08:45-15:20 KST on each
 * contract's final trading day (the second Thursday of March / June /
 * September / December, or the preceding business day if that Thursday
 * is itself a holiday -- confirmed against KRX's own contract
 * specification). See CLAUDE.md's "KIS/KOSPI200 venue integration,
 * Phase 1" section for the full design and citations.
 *
 * <p><b>Deliberately out of scope</b> (see that same CLAUDE.md section):
 * the KOSPI200 futures night session (18:00-06:00 KST) is not
 * represented here at all -- every hour of every date this class ever
 * evaluates outside the regular-session window above is treated as
 * closed, including night-session hours.
 *
 * <p><b>Fails closed on any uncertainty, not just on a definite
 * holiday</b> -- tightened across two real CodeRabbit review passes on
 * the PR that committed this design:
 * <ul>
 *   <li>A date whose year is not in the bundled fixture's {@code
 *   coveredYears} is treated as closed for its entire span, regardless
 *   of day-of-week or time -- this class has no verified holiday data
 *   for it, and "ordinary weekday, so probably open" is exactly the
 *   overclaim this rule exists to rule out.</li>
 *   <li>A date it cannot positively resolve as a final trading day or
 *   not (see {@link #isFinalTradingDay}) is treated as closed rather than
 *   falling back to the longer, ordinary close time.</li>
 * </ul>
 */
public final class KrxMarketCalendar implements TradingCalendar {

    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private static final LocalTime REGULAR_OPEN = LocalTime.of(8, 45);
    private static final LocalTime REGULAR_CLOSE = LocalTime.of(15, 45);
    private static final LocalTime FINAL_TRADING_DAY_CLOSE = LocalTime.of(15, 20);
    private static final Set<Month> QUARTERLY_CONTRACT_MONTHS =
            EnumSet.of(Month.MARCH, Month.JUNE, Month.SEPTEMBER, Month.DECEMBER);
    // Bounds the "step back to the preceding business day" search below --
    // real KRX holiday clusters (e.g. 설날/추석) never run this long, so
    // hitting this bound means something is wrong with the fixture data,
    // not a legitimately long holiday run. Failing closed (see
    // isFinalTradingDay) rather than looping unbounded either way.
    private static final int MAX_PRECEDING_BUSINESS_DAY_SEARCH_DAYS = 10;

    private static final String FIXTURE_RESOURCE_PATH = "/krx-market-holidays.json";

    private final Set<LocalDate> holidays;
    private final Set<Integer> coveredYears;

    public KrxMarketCalendar() {
        this(loadBundledFixture());
    }

    /** Package-private, for tests -- accepts a fixture built in-memory rather than the bundled resource. */
    KrxMarketCalendar(HolidayFixture fixture) {
        Objects.requireNonNull(fixture, "fixture is required");
        this.holidays = Set.copyOf(fixture.holidays());
        this.coveredYears = Set.copyOf(fixture.coveredYears());
    }

    @Override
    public boolean isOpen(Instant now) {
        Objects.requireNonNull(now, "now is required");
        ZonedDateTime kst = now.atZone(KST);
        LocalDate date = kst.toLocalDate();

        if (!coveredYears.contains(date.getYear())) {
            return false;
        }
        if (date.getDayOfWeek() == DayOfWeek.SATURDAY || date.getDayOfWeek() == DayOfWeek.SUNDAY) {
            return false;
        }
        if (holidays.contains(date)) {
            return false;
        }

        LocalTime time = kst.toLocalTime();
        if (time.isBefore(REGULAR_OPEN)) {
            return false;
        }

        FinalTradingDayStatus status = isFinalTradingDay(date);
        if (status == FinalTradingDayStatus.UNRESOLVED) {
            return false;
        }
        LocalTime close = status == FinalTradingDayStatus.FINAL_TRADING_DAY ? FINAL_TRADING_DAY_CLOSE : REGULAR_CLOSE;
        return time.isBefore(close);
    }

    private enum FinalTradingDayStatus {
        FINAL_TRADING_DAY,
        ORDINARY_DAY,
        UNRESOLVED
    }

    /**
     * {@code date} is a final trading day if it is the second Thursday of
     * a quarterly contract month, or -- if that Thursday itself falls on a
     * holiday -- the nearest preceding business day. Only called after
     * {@link #isOpen} has already confirmed {@code date}'s year is
     * covered and {@code date} itself is a weekday, non-holiday date, so
     * the preceding-business-day search below only ever needs the same
     * already-covered year's data.
     */
    private FinalTradingDayStatus isFinalTradingDay(LocalDate date) {
        YearMonth yearMonth = YearMonth.from(date);
        if (!QUARTERLY_CONTRACT_MONTHS.contains(yearMonth.getMonth())) {
            return FinalTradingDayStatus.ORDINARY_DAY;
        }

        LocalDate secondThursday =
                yearMonth.atDay(1).with(TemporalAdjusters.firstInMonth(DayOfWeek.THURSDAY)).plusWeeks(1);

        LocalDate candidate = secondThursday;
        int stepsBack = 0;
        while (isWeekendOrHoliday(candidate)) {
            candidate = candidate.minusDays(1);
            stepsBack++;
            if (stepsBack > MAX_PRECEDING_BUSINESS_DAY_SEARCH_DAYS) {
                return FinalTradingDayStatus.UNRESOLVED;
            }
        }

        return candidate.equals(date) ? FinalTradingDayStatus.FINAL_TRADING_DAY : FinalTradingDayStatus.ORDINARY_DAY;
    }

    private boolean isWeekendOrHoliday(LocalDate date) {
        return date.getDayOfWeek() == DayOfWeek.SATURDAY
                || date.getDayOfWeek() == DayOfWeek.SUNDAY
                || holidays.contains(date);
    }

    private static HolidayFixture loadBundledFixture() {
        try (InputStream in = KrxMarketCalendar.class.getResourceAsStream(FIXTURE_RESOURCE_PATH)) {
            if (in == null) {
                throw new IllegalStateException("bundled resource not found: " + FIXTURE_RESOURCE_PATH);
            }
            RawFixture raw = new ObjectMapper().readValue(in, RawFixture.class);
            return new HolidayFixture(
                    raw.coveredYears == null ? List.of() : raw.coveredYears,
                    raw.holidays == null
                            ? List.of()
                            : raw.holidays.stream().map(LocalDate::parse).toList());
        } catch (IOException e) {
            throw new UncheckedIOException("failed to load " + FIXTURE_RESOURCE_PATH, e);
        }
    }

    /** Plain Jackson binding target for the bundled JSON's shape -- {@code _comment} is ignored on read. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    private static final class RawFixture {
        public List<Integer> coveredYears;
        public List<String> holidays;
    }

    /** In-memory holiday data, decoupled from the bundled resource's JSON shape so tests can build one directly. */
    record HolidayFixture(List<Integer> coveredYears, List<LocalDate> holidays) {}
}
