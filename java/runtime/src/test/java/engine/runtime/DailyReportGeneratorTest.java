package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.execution.PaperBroker;
import engine.oms.OrderStore;
import engine.risk.RiskGateway;
import engine.risk.RiskLimits;
import engine.schemas.OrderType;
import engine.schemas.SchemaObjectMapper;
import engine.schemas.Side;
import java.io.IOException;
import java.lang.reflect.Field;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link DailyReportGenerator} wraps a real {@link TradingLoop} to detect
 * UTC day boundaries (see class Javadoc) across ticks driven by an
 * external scheduler, and writes {@link DailyReport} JSON to
 * {@code var/live/reports/daily/<date>.json} once each day completes --
 * Paper-trading bridge Task D, see
 * {@code .planning/paper-trading-d-daily-reporting.md}. Real
 * {@link RiskGateway}/{@link OrderStore}/{@link OrderPipeline}/
 * {@link PaperBroker}/{@link TradingLoop} instances throughout, matching
 * {@code TradingLoopTest}'s and {@code PaperTradingAppTest}'s own
 * established "no mocking framework" style; day-boundary crossings are
 * driven by a small hand-rolled {@link MutableClock}, the same category of
 * test-only fake {@code PaperTradingAppTest}'s own bounded-polling helpers
 * already use for a different problem (this codebase has no time-mocking
 * library either).
 */
class DailyReportGeneratorTest {

    private static final String SYMBOL = "BTC-USDT";
    private final ObjectMapper mapper = SchemaObjectMapper.create();

    private OrderPipeline newPipeline() {
        return new OrderPipeline(new RiskGateway(RiskLimits.canary()), new OrderStore());
    }

    private static void setEquity(TradingLoop loop, BigDecimal value) throws Exception {
        Field equityField = TradingLoop.class.getDeclaredField("equity");
        equityField.setAccessible(true);
        equityField.set(loop, value);
    }

    /** A {@link Clock} whose {@link #instant()} can be advanced by the test, standing in for real wall-clock passage. */
    private static final class MutableClock extends Clock {
        private Instant instant;

        MutableClock(Instant instant) {
            this.instant = instant;
        }

        void advanceTo(Instant newInstant) {
            this.instant = newInstant;
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            throw new UnsupportedOperationException("not needed by these tests");
        }

        @Override
        public Instant instant() {
            return instant;
        }
    }

    @Test
    void theFirstEverBeforeTickDoesNotWriteAReportAndOnlySeedsDayTrackingState(@TempDir Path tempDir)
            throws IOException {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        DummySignalSource signalSource =
                new DummySignalSource(SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1000);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
            Path reportsDir = tempDir.resolve("daily");
            DailyReportGenerator generator = new DailyReportGenerator(loop, reportsDir, clock);

            generator.beforeTick();

            assertEquals(LocalDate.of(2026, 8, 7), generator.currentDay());
            assertFalse(Files.isDirectory(reportsDir), "no report should exist before any day has actually finished");
        }
    }

    @Test
    void crossingADayBoundaryWithNoSignalsOrErrorsStillWritesAZeroActivityReportForTheDayThatEnded(
            @TempDir Path tempDir) throws Exception {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        // Never fires within this test's tick count -- a genuinely quiet day.
        DummySignalSource signalSource =
                new DummySignalSource(SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1000);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
            Path reportsDir = tempDir.resolve("daily");
            DailyReportGenerator generator = new DailyReportGenerator(loop, reportsDir, clock);

            // Three quiet ticks during 2026-08-07.
            for (int i = 0; i < 3; i++) {
                generator.beforeTick();
                loop.tick();
                generator.afterTick();
            }
            assertFalse(Files.exists(reportsDir.resolve("2026-08-07.json")), "day hasn't ended yet -- must not write early");

            // Cross into 2026-08-08 -- this beforeTick() call must finalize
            // and write 2026-08-07's report before tracking the new day.
            clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
            generator.beforeTick();

            Path reportFile = reportsDir.resolve("2026-08-07.json");
            assertTrue(Files.exists(reportFile), "report for the completed day must exist");
            DailyReport report = mapper.readValue(reportFile.toFile(), DailyReport.class);

            assertEquals(LocalDate.of(2026, 8, 7), report.date());
            assertEquals(0, new BigDecimal("100000").compareTo(report.startingEquity()));
            assertEquals(0, new BigDecimal("100000").compareTo(report.endingEquity()));
            assertTrue(report.trades().isEmpty(), "a quiet day must report zero trades, not omit the file");
            assertTrue(report.errors().isEmpty());
            assertFalse(report.killSwitchTripped());
            assertEquals(3, report.ticksAttempted());
            assertEquals(3, report.ticksSucceeded());
            assertEquals(0, new BigDecimal("1.000000").compareTo(report.uptimeFraction()));

            assertEquals(LocalDate.of(2026, 8, 8), generator.currentDay(), "new day must now be tracked");
        }
    }

    @Test
    void tradesFilledDuringTheDayAppearInThatDaysReportInApplicationOrder(@TempDir Path tempDir) throws Exception {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(new BigDecimal("5"), new BigDecimal("2"));
        // Fires a fresh GUARDED_MARKET intent every tick -> immediate fill each time.
        DummySignalSource signalSource =
                new DummySignalSource(SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.001"), null, 1);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
            Path reportsDir = tempDir.resolve("daily");
            DailyReportGenerator generator = new DailyReportGenerator(loop, reportsDir, clock);

            for (int i = 0; i < 2; i++) {
                generator.beforeTick();
                loop.tick();
                generator.afterTick();
            }
            assertTrue(
                    loop.currentEquity().compareTo(new BigDecimal("100000")) < 0,
                    "sanity check: two real fills must have already dropped equity below the starting value");

            clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
            generator.beforeTick();

            DailyReport report =
                    mapper.readValue(reportsDir.resolve("2026-08-07.json").toFile(), DailyReport.class);
            assertEquals(2, report.trades().size(), "both fills from the completed day must be reported");
            assertTrue(
                    report.endingEquity().compareTo(report.startingEquity()) < 0,
                    "equity must have dropped by the fee on each of the two fills");
        }
    }

    @Test
    void aDayWhereEveryTickFailsStillProducesAReportRatherThanSilentlySkippingTheWrite(@TempDir Path tempDir)
            throws Exception {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        DummySignalSource signalSource =
                new DummySignalSource(SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1000);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWith(500, "internal server error"); // every tick fails inside TradingLoop.tick()
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
            Path reportsDir = tempDir.resolve("daily");
            DailyReportGenerator generator = new DailyReportGenerator(loop, reportsDir, clock);

            for (int i = 0; i < 4; i++) {
                generator.beforeTick();
                loop.tick(); // never throws out -- TradingLoop's own catch-all -- but always records lastError()
                generator.afterTick();
            }

            clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
            generator.beforeTick(); // must still write, not skip, despite every tick this day having failed

            Path reportFile = reportsDir.resolve("2026-08-07.json");
            assertTrue(reportFile.toFile().exists(), "a day where every tick errored must still produce a report file");
            DailyReport report = mapper.readValue(reportFile.toFile(), DailyReport.class);

            assertEquals(4, report.ticksAttempted());
            assertEquals(0, report.ticksSucceeded());
            assertEquals(4, report.errors().size(), "one error entry per failed tick");
            assertTrue(report.trades().isEmpty());
            assertEquals(0, new BigDecimal("0.000000").compareTo(report.uptimeFraction()));
            assertEquals(0, new BigDecimal("100000").compareTo(report.startingEquity()));
            assertEquals(0, new BigDecimal("100000").compareTo(report.endingEquity()));
        }
    }

    @Test
    void killSwitchTrippedStateAtDayEndIsReflectedInThatDaysReport(@TempDir Path tempDir) throws Exception {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        DummySignalSource signalSource =
                new DummySignalSource(SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1000);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
            Path reportsDir = tempDir.resolve("daily");
            DailyReportGenerator generator = new DailyReportGenerator(loop, reportsDir, clock);

            generator.beforeTick();
            loop.tick();
            generator.afterTick();

            killSwitch.trip();

            generator.beforeTick();
            loop.tick();
            generator.afterTick();

            clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
            generator.beforeTick();

            DailyReport report =
                    mapper.readValue(reportsDir.resolve("2026-08-07.json").toFile(), DailyReport.class);
            assertTrue(report.killSwitchTripped(), "kill switch was tripped by day's end -- report must reflect that");
        }
    }

    /**
     * Documents and tests the "restart mid-day" assumption stated in
     * {@code .planning/paper-trading-d-daily-reporting.md}: a fresh
     * {@code DailyReportGenerator} (as a real process restart would
     * produce, given neither it nor {@link TradingLoop} persist any state)
     * always seeds its day-start equity from whatever {@link TradingLoop
     * #currentEquity()} reads at that moment -- NOT from the true
     * beginning of the UTC day, which this generator has no way to know
     * ended earlier this same day. A restart mid-day is therefore
     * indistinguishable, from this generator's perspective, from the
     * calendar day simply starting late.
     */
    @Test
    void aFreshGeneratorSeedsDayStartEquityFromWhateverTheTradingLoopReadsAtConstructionNotTrueMidnight(
            @TempDir Path tempDir) throws Exception {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        DummySignalSource signalSource =
                new DummySignalSource(SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1000);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            // Simulate: real trading already happened earlier today, in a
            // now-gone prior process, before this "restart" -- equity is
            // not the fresh-start 100000 a brand-new TradingLoop would
            // otherwise show.
            setEquity(loop, new BigDecimal("97000"));

            // Well into the middle of the day, not midnight -- a restart,
            // not a fresh day.
            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T14:30:00Z"));
            Path reportsDir = tempDir.resolve("daily");
            DailyReportGenerator generator = new DailyReportGenerator(loop, reportsDir, clock);

            generator.beforeTick();
            loop.tick();
            generator.afterTick();

            clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
            generator.beforeTick();

            DailyReport report =
                    mapper.readValue(reportsDir.resolve("2026-08-07.json").toFile(), DailyReport.class);
            assertEquals(
                    0,
                    new BigDecimal("97000").compareTo(report.startingEquity()),
                    "starting equity must reflect the restart moment, not the true (unknowable) start of the UTC day");
        }
    }
}
