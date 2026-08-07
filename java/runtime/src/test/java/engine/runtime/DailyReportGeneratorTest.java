package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.execution.Fill;
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
import java.util.List;
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
            // Captured before the boundary crossing below -- TradingLoop's
            // own fillHistory() is the ground truth for application order.
            List<Fill> expectedOrder = loop.fillHistory();

            clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
            generator.beforeTick();

            DailyReport report =
                    mapper.readValue(reportsDir.resolve("2026-08-07.json").toFile(), DailyReport.class);
            assertEquals(2, report.trades().size(), "both fills from the completed day must be reported");
            assertTrue(
                    report.endingEquity().compareTo(report.startingEquity()) < 0,
                    "equity must have dropped by the fee on each of the two fills");
            // Order, not just count -- a report that silently reversed
            // (or otherwise reordered) trades would still pass a
            // count-only assertion; comparing clientOrderId index-by-index
            // against TradingLoop's own fillHistory() (the real
            // application order) would not.
            assertEquals(2, expectedOrder.size());
            for (int i = 0; i < expectedOrder.size(); i++) {
                assertEquals(
                        expectedOrder.get(i).clientOrderId(),
                        report.trades().get(i).clientOrderId(),
                        "trade at index " + i + " must match TradingLoop's own application order");
            }
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

    /**
     * A CodeRabbit review finding on this task's own PR (#73): the
     * original {@code writeReport} discarded the completed {@link
     * DailyReport} the instant a write failed (e.g. a transient disk
     * error), because the caller always advanced to the new day
     * regardless of whether the write succeeded -- a real, permanent data
     * loss for that day. Forces a real {@link IOException} (not a mock --
     * this codebase has no mocking framework) by pointing {@code
     * reportsDirectory} at a path whose own parent already exists as a
     * plain file, so {@code Files.createDirectories} genuinely cannot
     * create it, then confirms the report is retried -- and eventually
     * succeeds with its ORIGINAL data -- once the obstruction is removed.
     */
    @Test
    void aReportThatFailsToWriteIsRetriedOnALaterTickRatherThanDiscarded(@TempDir Path tempDir) throws Exception {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        DummySignalSource signalSource =
                new DummySignalSource(SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1000);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            // A real file (not a directory) sitting exactly where the
            // reports directory needs to be created -- Files
            // .createDirectories() genuinely cannot create a directory
            // where a plain file already exists, a real IOException, not
            // a simulated one.
            Path blockingFile = tempDir.resolve("blocked");
            Files.writeString(blockingFile, "not a directory");
            Path reportsDir = blockingFile.resolve("daily");

            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
            DailyReportGenerator generator = new DailyReportGenerator(loop, reportsDir, clock);

            for (int i = 0; i < 2; i++) {
                generator.beforeTick();
                loop.tick();
                generator.afterTick();
            }

            clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
            generator.beforeTick(); // the write attempted here must fail

            assertFalse(Files.exists(reportsDir.resolve("2026-08-07.json")), "the write was obstructed -- must not exist yet");
            assertEquals(1, generator.pendingReportCount(), "the failed report must be retained, not discarded");
            assertEquals(LocalDate.of(2026, 8, 8), generator.currentDay(), "day tracking must still advance despite the write failure");

            // Remove the obstruction, then simulate an ordinary same-day
            // tick (no new boundary crossing) -- the retry must happen on
            // ANY later beforeTick() call, not only at the next boundary.
            Files.delete(blockingFile);
            generator.beforeTick();

            assertEquals(0, generator.pendingReportCount(), "the retry must have succeeded and cleared the pending queue");
            Path reportFile = reportsDir.resolve("2026-08-07.json");
            assertTrue(Files.exists(reportFile), "the retried report must now exist");
            DailyReport report = mapper.readValue(reportFile.toFile(), DailyReport.class);
            assertEquals(LocalDate.of(2026, 8, 7), report.date(), "the retried report must still be for the original day, not the day it was retried on");
            assertEquals(2, report.ticksAttempted(), "the retried report must still carry its original day's data");
        }
    }

    /**
     * A second CodeRabbit review finding on this task's own PR (#73),
     * against the fix for the finding above: the original fix still let a
     * newly-completed report attempt a DIRECT write first, only falling
     * back to the pending queue if that direct attempt failed -- so if an
     * OLDER report was already stuck in the queue (still failing) while a
     * NEWER report's own target path happened to be independently
     * writable, the newer one could get written first, breaking this
     * class's own promised chronological delivery order. This test
     * obstructs ONLY 2026-08-07's specific target file (a directory
     * sitting where that one file needs to go -- 2026-08-08's own target
     * path is completely unobstructed) and confirms 2026-08-08's report is
     * NOT written while 2026-08-07's remains stuck, only recovering both,
     * in order, once the obstruction is cleared.
     */
    @Test
    void aStillPendingOlderReportBlocksAYoungerOneFromWritingOutOfOrder(@TempDir Path tempDir) throws Exception {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        DummySignalSource signalSource =
                new DummySignalSource(SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1000);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            Path reportsDir = tempDir.resolve("daily");
            Files.createDirectories(reportsDir);
            // Obstructs ONLY 2026-08-07's own target file -- a directory
            // sitting exactly where that one file needs to be written,
            // while reportsDir itself (and 2026-08-08's own future target
            // path) stays completely normal and writable.
            Path obstructedTarget = reportsDir.resolve("2026-08-07.json");
            Files.createDirectories(obstructedTarget);

            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
            DailyReportGenerator generator = new DailyReportGenerator(loop, reportsDir, clock);

            generator.beforeTick(); // seeds 2026-08-07
            loop.tick();
            generator.afterTick();

            // Cross into 2026-08-08 -- 2026-08-07's report is built and
            // enqueued, but its write fails (obstructed target).
            clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
            generator.beforeTick();
            loop.tick();
            generator.afterTick();
            assertEquals(1, generator.pendingReportCount(), "2026-08-07's report must be stuck pending");

            // Cross into 2026-08-09 -- 2026-08-08's report is now also
            // completed and would, on its own, be perfectly writable. The
            // fix under test is that it must NOT be written yet, because
            // an older report is still stuck ahead of it in the queue.
            clock.advanceTo(Instant.parse("2026-08-09T00:05:00Z"));
            generator.beforeTick();

            assertEquals(
                    2,
                    generator.pendingReportCount(),
                    "2026-08-08's report must also be queued, not written out of order ahead of the stuck 2026-08-07 one");
            assertFalse(
                    Files.exists(reportsDir.resolve("2026-08-08.json")),
                    "2026-08-08's own target path was never obstructed -- if ordering were not enforced, it would already exist here");

            // Clear the obstruction and let an ordinary later tick retry --
            // both must now succeed, oldest first.
            Files.delete(obstructedTarget);
            generator.beforeTick();

            assertEquals(0, generator.pendingReportCount());
            assertTrue(Files.isRegularFile(reportsDir.resolve("2026-08-07.json")));
            assertTrue(Files.isRegularFile(reportsDir.resolve("2026-08-08.json")));
        }
    }

    /**
     * A CodeRabbit review finding on this task's own PR (#73): the
     * original design only ever detected a day boundary inside {@link
     * DailyReportGenerator#beforeTick()}, which only runs immediately
     * before a real {@link TradingLoop#tick()} -- so if the process
     * stops (e.g. {@code PaperTradingApp.stop()}) after a UTC day has
     * already ended but before the next scheduled tick would have
     * noticed, that day's report would never be written at all. This
     * tests {@link DailyReportGenerator#finalizeCompletedDayOnShutdown()}
     * directly: ticks happen only on 2026-08-07, the clock is advanced
     * into 2026-08-08 WITHOUT another {@code beforeTick()}/{@code tick()}/
     * {@code afterTick()} cycle (simulating the process stopping before
     * its next scheduled tick), and confirms the shutdown-only call still
     * produces 2026-08-07's report.
     */
    @Test
    void finalizeCompletedDayOnShutdownWritesADayThatEndedBeforeTheNextTickWouldHaveNoticed(@TempDir Path tempDir)
            throws Exception {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        DummySignalSource signalSource =
                new DummySignalSource(SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1000);
        KillSwitch killSwitch = new KillSwitch();

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            BingXPriceFeed priceFeed = new BingXPriceFeed(server.baseUrl());
            TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);

            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T23:58:00Z"));
            Path reportsDir = tempDir.resolve("daily");
            DailyReportGenerator generator = new DailyReportGenerator(loop, reportsDir, clock);

            generator.beforeTick();
            loop.tick();
            generator.afterTick();

            // The process "stops" here, past midnight UTC, before another
            // beforeTick() would ever have run.
            clock.advanceTo(Instant.parse("2026-08-08T00:03:00Z"));
            assertFalse(
                    Files.exists(reportsDir.resolve("2026-08-07.json")),
                    "sanity check: no ordinary tick happened after the boundary, so nothing should have written yet");

            generator.finalizeCompletedDayOnShutdown();

            Path reportFile = reportsDir.resolve("2026-08-07.json");
            assertTrue(Files.exists(reportFile), "shutdown must finalize a day that already ended, not leave it unwritten");
            DailyReport report = mapper.readValue(reportFile.toFile(), DailyReport.class);
            assertEquals(LocalDate.of(2026, 8, 7), report.date());
            assertEquals(1, report.ticksAttempted());

            // Idempotent: calling it again must not error or double-write anything odd.
            generator.finalizeCompletedDayOnShutdown();
            assertEquals(LocalDate.of(2026, 8, 8), generator.currentDay());
        }
    }

    /**
     * {@link DailyReportGenerator#finalizeCompletedDayOnShutdown()} must
     * be a safe no-op when nothing has ever been tracked -- e.g. a
     * process that constructs {@code PaperTradingApp} and is stopped
     * before its first scheduled tick ever fires.
     */
    @Test
    void finalizeCompletedDayOnShutdownIsANoOpWhenNothingWasEverTracked(@TempDir Path tempDir) {
        OrderPipeline pipeline = newPipeline();
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        DummySignalSource signalSource =
                new DummySignalSource(SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), new BigDecimal("50000"), 1000);
        KillSwitch killSwitch = new KillSwitch();
        BingXPriceFeed priceFeed = new BingXPriceFeed("http://127.0.0.1:1"); // never actually called

        TradingLoop loop = new TradingLoop(pipeline, broker, signalSource, priceFeed, killSwitch, SYMBOL);
        MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
        Path reportsDir = tempDir.resolve("daily");
        DailyReportGenerator generator = new DailyReportGenerator(loop, reportsDir, clock);

        generator.finalizeCompletedDayOnShutdown(); // must not throw

        assertFalse(Files.isDirectory(reportsDir), "nothing was ever tracked -- no report should be written");
    }
}
