package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.execution.PaperBroker;
import engine.oms.Order;
import engine.oms.OrderState;
import engine.risk.AccountState;
import engine.risk.RiskGateway;
import engine.risk.RiskLimits;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.SchemaObjectMapper;
import engine.schemas.Side;
import java.io.IOException;
import java.lang.reflect.Field;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;
import java.util.function.Supplier;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link PaperTradingApp} is the {@code main()} entrypoint wiring
 * {@link RiskGateway}/{@link OrderStore}/{@link OrderPipeline}/
 * {@link PaperBroker}/{@link FileSignalSource}/{@link BingXPriceFeed}/
 * {@link KillSwitch}/{@link TradingLoop} together behind a
 * {@code ScheduledExecutorService} -- see {@code .planning/paper-trading-
 * c-scheduler-entrypoint.md}. Construction/config-resolution logic is
 * tested directly here (no env-var mocking machinery exists in this
 * codebase, so {@link PaperTradingApp#fromEnvironment()} is deliberately
 * a thin wrapper around the fully-testable constructor and the
 * package-private {@code resolve*}/{@code requireNonBlank} static
 * helpers below). The actual scheduled-loop behavior is exercised two
 * ways: a manufactured single tick driven directly (no waiting on a real
 * scheduler), and a short real {@code start()}/{@code stop()} lifecycle
 * run with a 1-second interval.
 */
class PaperTradingAppTest {

    private static final String SYMBOL = "BTC-USDT";
    private final ObjectMapper mapper = SchemaObjectMapper.create();

    private void writeIntent(Path path, OrderIntent intent) throws IOException {
        Files.writeString(path, mapper.writeValueAsString(intent));
    }

    /**
     * Bounded polling helpers (CodeRabbit review findings on this task's
     * PR) -- replace a fixed {@code Thread.sleep(...)} with short
     * polling intervals and a clear deadline, so a test fails only after
     * genuinely waiting {@code timeout}, rather than either flaking on a
     * slow CI runner (too-short fixed sleep) or wasting wall-clock time
     * on a fast one (too-generous fixed sleep). Both measure the
     * deadline via {@link System#nanoTime()}, not {@link Instant#now()}
     * -- a monotonic clock, immune to a concurrent wall-clock adjustment
     * (NTP sync, DST, manual change) artificially shortening or
     * lengthening the effective wait.
     */
    private static <T> T awaitNonNull(Supplier<T> supplier, Duration timeout) throws InterruptedException {
        long deadlineNanos = System.nanoTime() + timeout.toNanos();
        while (System.nanoTime() < deadlineNanos) {
            T value = supplier.get();
            if (value != null) {
                return value;
            }
            Thread.sleep(50);
        }
        fail("condition not met within " + timeout);
        throw new AssertionError("unreachable"); // fail() always throws; keeps the compiler happy
    }

    private static void awaitCondition(BooleanSupplier condition, Duration timeout) throws InterruptedException {
        long deadlineNanos = System.nanoTime() + timeout.toNanos();
        while (System.nanoTime() < deadlineNanos) {
            if (condition.getAsBoolean()) {
                return;
            }
            Thread.sleep(50);
        }
        fail("condition not met within " + timeout);
    }

    @Test
    void constructingFromExplicitConfigDoesNotThrowAndBuildsARealTradingLoop(@TempDir Path tempDir)
            throws IOException {
        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            Path signalPath = tempDir.resolve("latest.json"); // deliberately never written -- missing is fine

            PaperTradingApp app = new PaperTradingApp(SYMBOL, server.baseUrl(), signalPath, 60);

            assertNull(app.tradingLoop().lastTickAt(), "should not have ticked yet");
            assertNull(app.tradingLoop().lastError());
            assertEquals(0, new BigDecimal("100000").compareTo(app.tradingLoop().currentEquity()));
        }
    }

    @Test
    void constructorRejectsANonPositiveTickInterval(@TempDir Path tempDir) throws IOException {
        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            Path signalPath = tempDir.resolve("latest.json");
            assertThrows(
                    IllegalArgumentException.class,
                    () -> new PaperTradingApp(SYMBOL, server.baseUrl(), signalPath, 0));
            assertThrows(
                    IllegalArgumentException.class,
                    () -> new PaperTradingApp(SYMBOL, server.baseUrl(), signalPath, -5));
        }
    }

    @Test
    void resolveSignalPathDefaultsToTheConventionalVarLiveSignalsPathWhenRawIsNullOrBlank() {
        Path expected = Path.of("var", "live", "signals", "BTC-USDT", "daily-tsmom-ensemble", "latest.json");
        assertEquals(expected, PaperTradingApp.resolveSignalPath(null, "BTC-USDT"));
        assertEquals(expected, PaperTradingApp.resolveSignalPath("   ", "BTC-USDT"));
    }

    @Test
    void resolveSignalPathUsesTheExplicitValueWhenProvided() {
        assertEquals(Path.of("/custom/path.json"), PaperTradingApp.resolveSignalPath("/custom/path.json", "BTC-USDT"));
    }

    /**
     * Real gap found and fixed after Task 4 merged: {@code
     * KIS_SUBMISSION_MARKERS_PATH} used to be one hardcoded constant with
     * no {@code symbol} in it, so two {@code kis-paper} processes trading
     * two different KOSPI200 symbols would have collided on the same
     * submission-marker file. Two different symbols must resolve to two
     * different paths -- that difference is the entire point of this fix,
     * so it's the direct assertion, not just "a path was returned".
     */
    @Test
    void resolveKisSubmissionMarkersPathDiffersBySymbol() {
        Path first = PaperTradingApp.resolveKisSubmissionMarkersPath("101W09");
        Path second = PaperTradingApp.resolveKisSubmissionMarkersPath("101W12");

        assertEquals(Path.of("var", "live", "101W09-kis_submission_markers.json"), first);
        assertEquals(Path.of("var", "live", "101W12-kis_submission_markers.json"), second);
    }

    /**
     * Real CodeRabbit review finding on {@link
     * #resolveKisSubmissionMarkersPathDiffersBySymbol}'s own PR: a {@code
     * symbol} containing a path separator or traversal sequence could
     * otherwise resolve the marker path outside {@code var/live/}.
     */
    @Test
    void resolveKisSubmissionMarkersPathRejectsPathSeparatorsAndTraversalSegments() {
        assertThrows(IllegalArgumentException.class, () -> PaperTradingApp.resolveKisSubmissionMarkersPath("../evil"));
        assertThrows(
                IllegalArgumentException.class, () -> PaperTradingApp.resolveKisSubmissionMarkersPath("a/b"));
        assertThrows(
                IllegalArgumentException.class, () -> PaperTradingApp.resolveKisSubmissionMarkersPath("a\\b"));
        assertThrows(IllegalArgumentException.class, () -> PaperTradingApp.resolveKisSubmissionMarkersPath(".."));
        assertThrows(IllegalArgumentException.class, () -> PaperTradingApp.resolveKisSubmissionMarkersPath("."));
    }

    /**
     * {@link PaperTradingApp#resolveAccountLedgerPath} is deliberately keyed
     * by {@code (venue, accountId)}, not {@code symbol} -- unlike {@link
     * #resolveKisSubmissionMarkersPathDiffersBySymbol} above, this ledger is
     * meant to be <b>shared</b> across every {@code kis-paper} process
     * trading different symbols against the same real KIS account (see the
     * governing plan's "Shared KIS account risk ledger" section). Same
     * account, different symbol must resolve to the SAME path; different
     * account must resolve to a different one.
     */
    @Test
    void resolveAccountLedgerPathIsKeyedByVenueAndAccountNotSymbol() {
        Path forOneAccount = PaperTradingApp.resolveAccountLedgerPath("KIS", "12345678");
        Path forSameAccountAgain = PaperTradingApp.resolveAccountLedgerPath("KIS", "12345678");
        Path forADifferentAccount = PaperTradingApp.resolveAccountLedgerPath("KIS", "87654321");

        assertEquals(Path.of("var", "live", "KIS-12345678-account_ledger.json"), forOneAccount);
        assertEquals(forOneAccount, forSameAccountAgain);
        assertEquals(Path.of("var", "live", "KIS-87654321-account_ledger.json"), forADifferentAccount);
    }

    /** Same traversal/path-separator protection as {@link #resolveKisSubmissionMarkersPathRejectsPathSeparatorsAndTraversalSegments}, applied to both {@code venue} and {@code accountId}. */
    @Test
    void resolveAccountLedgerPathRejectsPathSeparatorsAndTraversalSegments() {
        assertThrows(IllegalArgumentException.class, () -> PaperTradingApp.resolveAccountLedgerPath("../evil", "acct"));
        assertThrows(IllegalArgumentException.class, () -> PaperTradingApp.resolveAccountLedgerPath("KIS", "../evil"));
        assertThrows(IllegalArgumentException.class, () -> PaperTradingApp.resolveAccountLedgerPath("a/b", "acct"));
        assertThrows(IllegalArgumentException.class, () -> PaperTradingApp.resolveAccountLedgerPath("KIS", "a\\b"));
        assertThrows(IllegalArgumentException.class, () -> PaperTradingApp.resolveAccountLedgerPath(".", "acct"));
        assertThrows(IllegalArgumentException.class, () -> PaperTradingApp.resolveAccountLedgerPath("KIS", ".."));
    }

    @Test
    void resolveTickIntervalSecondsDefaultsTo300WhenRawIsNullOrBlank() {
        assertEquals(300L, PaperTradingApp.resolveTickIntervalSeconds(null));
        assertEquals(300L, PaperTradingApp.resolveTickIntervalSeconds("   "));
    }

    @Test
    void resolveTickIntervalSecondsParsesAPositiveIntegerString() {
        assertEquals(42L, PaperTradingApp.resolveTickIntervalSeconds("42"));
    }

    @Test
    void resolveTickIntervalSecondsRejectsANonPositiveOrNonNumericValue() {
        assertThrows(IllegalStateException.class, () -> PaperTradingApp.resolveTickIntervalSeconds("0"));
        assertThrows(IllegalStateException.class, () -> PaperTradingApp.resolveTickIntervalSeconds("-5"));
        assertThrows(IllegalStateException.class, () -> PaperTradingApp.resolveTickIntervalSeconds("not-a-number"));
    }

    @Test
    void requireNonBlankThrowsWhenValueIsNullOrBlank() {
        assertThrows(IllegalStateException.class, () -> PaperTradingApp.requireNonBlank(null, "BINGX_BASE_URL"));
        assertThrows(IllegalStateException.class, () -> PaperTradingApp.requireNonBlank("   ", "BINGX_BASE_URL"));
    }

    @Test
    void requireNonBlankReturnsTheValueWhenPresent() {
        assertEquals("http://example.invalid", PaperTradingApp.requireNonBlank("http://example.invalid", "BINGX_BASE_URL"));
    }

    @Test
    void firstNonBlankFallsBackWhenNullOrBlank() {
        assertEquals("fallback", PaperTradingApp.firstNonBlank(null, "fallback"));
        assertEquals("fallback", PaperTradingApp.firstNonBlank("  ", "fallback"));
        assertEquals("explicit", PaperTradingApp.firstNonBlank("explicit", "fallback"));
    }

    /**
     * The real end-to-end proof this task's own brief asks for: a real
     * signal file, read by a real {@link FileSignalSource}, flowing
     * through a real {@link RiskGateway}/{@link OrderPipeline}/
     * {@link PaperBroker} wired up exactly the way {@link
     * PaperTradingApp#main} wires them -- driven via a single manufactured
     * tick (the package-private {@link PaperTradingApp#tradingLoop()}
     * accessor), not a real scheduler, so this stays fast and
     * deterministic. GUARDED_MARKET is used so the fill is immediate and
     * observable via the fee-only equity drop (same technique as {@code
     * TradingLoopTest}).
     */
    @Test
    void aManuallyDrivenTickReadsARealSignalFileAndProducesARealFill(@TempDir Path tempDir) throws IOException {
        Path signalPath = tempDir.resolve("latest.json");
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(), SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.01"), null, "1d",
                Instant.now());
        writeIntent(signalPath, intent);

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            PaperTradingApp app = new PaperTradingApp(SYMBOL, server.baseUrl(), signalPath, 60);

            app.tradingLoop().tick();

            assertNull(app.tradingLoop().lastError());
            // GUARDED_MARKET fills adjusted by slippage against the trader
            // (see PaperBroker.tryFill's own Javadoc) -- fillPrice =
            // 60000 * (1 + SLIPPAGE_BPS/10000) = 60000 * 1.0002 = 60012,
            // notional = 0.01 * 60012 = 600.12, fee = notional *
            // FEE_BPS/10000 = 600.12 * 5/10000 = 0.3006.
            BigDecimal fillPrice = new BigDecimal("60000")
                    .multiply(BigDecimal.ONE.add(PaperTradingApp.SLIPPAGE_BPS.divide(new BigDecimal("10000"))));
            BigDecimal notional = new BigDecimal("0.01").multiply(fillPrice);
            BigDecimal expectedFee = notional.multiply(PaperTradingApp.FEE_BPS).divide(new BigDecimal("10000"));
            BigDecimal expectedEquity = new BigDecimal("100000").subtract(expectedFee);
            assertEquals(0, expectedEquity.compareTo(app.tradingLoop().currentEquity()));
        }
    }

    @Test
    void startSchedulesRecurringTicksAndStopShutsDownCleanly(@TempDir Path tempDir) throws Exception {
        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            Path signalPath = tempDir.resolve("latest.json"); // never written -- ticks are no-ops but still real ticks
            PaperTradingApp app = new PaperTradingApp(SYMBOL, server.baseUrl(), signalPath, 1);

            app.start();
            try {
                // Bounded polling, not a fixed sleep -- the initial tick fires
                // at delay 0, so this should resolve almost immediately in
                // practice; a 5s deadline is generous headroom against a
                // loaded CI runner without making a slow environment flake.
                Instant firstTick = awaitNonNull(() -> app.tradingLoop().lastTickAt(), Duration.ofSeconds(5));
                assertNull(app.tradingLoop().lastError());

                // Proves RECURRING scheduling, not just a single initial
                // tick (a CodeRabbit review finding on this task's PR: the
                // test's own name claimed "recurring" but the original
                // assertion only ever observed one tick) -- wait for a
                // later lastTickAt() than the first one already observed.
                awaitCondition(
                        () -> {
                            Instant latest = app.tradingLoop().lastTickAt();
                            return latest != null && latest.isAfter(firstTick);
                        },
                        Duration.ofSeconds(5));
                assertNull(app.tradingLoop().lastError());
            } finally {
                app.stop();
            }

            // Safe to call more than once (e.g. a shutdown hook racing an
            // explicit stop() elsewhere).
            app.stop();
        }
    }

    @Test
    void startCannotBeCalledTwice(@TempDir Path tempDir) throws IOException {
        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            Path signalPath = tempDir.resolve("latest.json");
            PaperTradingApp app = new PaperTradingApp(SYMBOL, server.baseUrl(), signalPath, 60);

            app.start();
            try {
                assertThrows(IllegalStateException.class, app::start);
            } finally {
                app.stop();
            }
        }
    }

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

    /**
     * A CodeRabbit review finding on this task's own PR (#73): without
     * {@link PaperTradingApp#stop()} calling {@link DailyReportGenerator
     * #finalizeCompletedDayOnShutdown()}, a UTC day that ends between the
     * last real scheduled tick and the process actually stopping would
     * never get a report -- nothing would be left to notice the boundary.
     * Drives a real tick (via the package-private {@code runTick()}
     * accessor, same technique {@code aManuallyDrivenTickReadsARealSignalFileAndProducesARealFill}
     * uses) on 2026-08-07, advances the injected clock into 2026-08-08
     * WITHOUT another tick, then calls {@code stop()} directly and
     * confirms the report for 2026-08-07 exists anyway.
     */
    @Test
    void stopFinalizesADayThatEndedBeforeTheNextScheduledTickWouldHaveNoticed(@TempDir Path tempDir)
            throws IOException {
        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            Path signalPath = tempDir.resolve("latest.json"); // never written -- a quiet day is enough to prove the wiring
            Path reportsDir = tempDir.resolve("reports");
            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T23:58:00Z"));
            PaperTradingApp app =
                    new PaperTradingApp(SYMBOL, server.baseUrl(), signalPath, 300, reportsDir, clock);

            app.runTick(); // the only tick this test drives, still on 2026-08-07

            clock.advanceTo(Instant.parse("2026-08-08T00:03:00Z")); // "process stops" past midnight, before another tick
            app.stop();

            Path reportFile = reportsDir.resolve("2026-08-07.json");
            assertTrue(
                    Files.exists(reportFile),
                    "stop() must finalize a day that already ended even though no further tick ever ran");
        }
    }

    /**
     * GitHub issue #74 (round-4 CodeRabbit finding on PR #73, human-
     * approved as an accepted gap at the time, now closed): {@link
     * PaperTradingApp#stop()}'s shutdown-termination-confirmation logic
     * had no deterministic test proving finalization never runs before a
     * genuinely in-flight tick actually terminates. This test forces a
     * real tick to hang past a short, injected graceful-shutdown timeout
     * (the new {@link PaperTradingApp#PaperTradingApp(String, String,
     * Path, long, Path, Clock, Duration, Duration) Duration-accepting
     * test-only constructor overload}, matching the existing {@link Clock}
     * -injection precedent exactly) using {@link
     * FakeBingXTradesServer#hangForever()} -- a real, local HTTP server
     * that accepts the connection but never responds, so {@code
     * BingXPriceFeed}'s blocking call is genuinely still in-flight when
     * {@code stop()}'s graceful {@code awaitTermination} window elapses.
     * A generous forced-shutdown timeout then gives {@code
     * ExecutorService#shutdownNow()}'s real thread-interrupt (confirmed
     * separately, empirically, to unblock a hung {@code HttpClient.send()}
     * call in low single-digit milliseconds -- see this task's own
     * planning doc) ample room to actually resolve, so this proves the
     * "termination eventually confirmed" half of the invariant, not the
     * "never confirmed" half (the next test below).
     *
     * <p>The proof that finalization ran only <b>after</b>, never before
     * or concurrently with, the in-flight tick's own completion is the
     * written report's own {@code ticksAttempted}/{@code ticksSucceeded}/
     * {@code errors} fields: {@link DailyReportGenerator#afterTick()} is
     * what populates those, and it only ever runs (from {@link
     * PaperTradingApp#runTick()}) after {@link TradingLoop#tick()} itself
     * has returned -- if finalization had run before that, the report
     * would show a day with zero attempted ticks instead of the one real,
     * interrupted tick this test forces.
     */
    @Test
    void stopFinalizesOnlyAfterAGenuinelyInFlightTickActuallyTerminatesViaForcedShutdown(@TempDir Path tempDir)
            throws Exception {
        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.hangForever();
            Path signalPath = tempDir.resolve("latest.json"); // never written -- irrelevant, the hang happens first
            Path reportsDir = tempDir.resolve("reports");
            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T12:00:00Z"));
            // tickIntervalSeconds=300 -- long enough that only the immediate
            // (delay=0) first tick ever fires during this test's short real
            // wall-clock lifetime; a second real tick firing would let its
            // own beforeTick() notice the day boundary first, defeating the
            // point of this test (proving stop()'s OWN finalize path).
            PaperTradingApp app = new PaperTradingApp(
                    SYMBOL, server.baseUrl(), signalPath, 300, reportsDir, clock,
                    Duration.ofMillis(200), Duration.ofSeconds(3));

            app.start();
            try {
                // Confirms the immediate first tick has genuinely reached
                // and been received by the hung server -- not merely
                // scheduled -- before racing stop() against it.
                awaitCondition(() -> server.lastPath() != null, Duration.ofSeconds(5));

                clock.advanceTo(Instant.parse("2026-08-08T00:03:00Z")); // day boundary crossed while the tick is still in flight

                app.stop();

                Path reportFile = reportsDir.resolve("2026-08-07.json");
                assertTrue(
                        Files.exists(reportFile),
                        "finalization must run once the forced shutdown actually confirms termination");

                DailyReport report = mapper.readValue(reportFile.toFile(), DailyReport.class);
                assertEquals(
                        1, report.ticksAttempted(),
                        "the report must reflect the in-flight tick's own completed afterTick() bookkeeping --"
                                + " proof finalization ran strictly after that tick actually finished, not before");
                assertEquals(0, report.ticksSucceeded());
                assertEquals(1, report.errors().size());
                assertNotNull(app.tradingLoop().lastError(), "the forcibly-interrupted tick must have recorded its own error");
            } finally {
                app.stop();
            }
        }
    }

    /**
     * The other half of GitHub issue #74's acceptance criteria: finalization
     * must be SKIPPED, with an ERROR logged (see {@code stop()}'s own
     * Javadoc -- not independently assertable here, this codebase has no
     * log-capture framework in this module, see this task's own planning
     * doc), when termination cannot be confirmed even after the forced
     * shutdown.
     *
     * <p><b>Revised on a real CodeRabbit review finding on this task's own
     * PR #85</b>: an earlier version of this test reused the network-hung
     * server from the test above, paired with {@code
     * forcedShutdownTimeout=Duration.ZERO} (reasoning: {@code
     * awaitTermination(0, ...)} checks state immediately without waiting).
     * CodeRabbit found that reasoning genuinely unsound, backed by its own
     * real probe: {@code shutdownNow()} only <i>requests</i> the interrupt,
     * it does not wait for the interrupted task to actually finish -- on a
     * multi-core machine, the worker thread can process the interrupt and
     * complete the rest of {@code runTick()} on a different core, in true
     * parallel execution, while the calling thread is still between the
     * {@code shutdownNow()} call and the immediately-following {@code
     * awaitTermination(0, ...)} check; nothing about "same thread, no
     * explicit yield" rules that out. That made the old version of this
     * test intermittently flaky in the direction that matters most --
     * occasionally observing {@code terminated == true} and silently
     * failing to exercise the {@code terminated == false} path it exists to
     * prove.
     *
     * <p>Fixed by switching to a genuinely, structurally uninterruptible
     * block instead of racing a real interrupt's recovery time: a
     * background test thread acquires {@link TradingLoop}'s own intrinsic
     * lock (the same monitor {@link TradingLoop#tick()}'s {@code
     * synchronized} keyword uses) and holds it until this test explicitly
     * releases a {@link CountDownLatch}. Unlike blocking I/O or a {@code
     * java.util.concurrent.locks.Lock}, entering a plain {@code
     * synchronized} block while another thread holds the monitor does
     * <b>not</b> respond to {@code Thread#interrupt()} at all -- the
     * waiting thread's interrupt flag is set, but it keeps waiting for the
     * monitor regardless, indefinitely. {@code app.start()}'s scheduled
     * task therefore blocks trying to <i>enter</i> {@code tradingLoop.tick()}
     * -- deterministically, for as long as the test holds the lock,
     * completely independent of {@code shutdownNow()}, real network
     * timing, or CPU scheduling. The fake server is left in its default
     * (immediately-responding) mode -- unreachable here, since {@code
     * tick()} never gets far enough to call it -- and the latch is always
     * released in a {@code finally} block so the once-blocked tick, and the
     * executor thread running it, cleanly complete before this test method
     * returns.
     */
    @Test
    void stopSkipsFinalizationAndLeavesDayTrackingUnadvancedWhenTerminationCannotBeConfirmed(@TempDir Path tempDir)
            throws Exception {
        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000"); // never actually reached -- see class Javadoc above
            Path signalPath = tempDir.resolve("latest.json");
            Path reportsDir = tempDir.resolve("reports");
            MutableClock clock = new MutableClock(Instant.parse("2026-08-07T12:00:00Z"));
            PaperTradingApp app = new PaperTradingApp(
                    SYMBOL, server.baseUrl(), signalPath, 300, reportsDir, clock,
                    Duration.ofMillis(200), Duration.ofMillis(200));

            CountDownLatch monitorAcquired = new CountDownLatch(1);
            CountDownLatch releaseMonitor = new CountDownLatch(1);
            Thread monitorHolder = new Thread(
                    () -> {
                        synchronized (app.tradingLoop()) {
                            monitorAcquired.countDown();
                            try {
                                releaseMonitor.await();
                            } catch (InterruptedException e) {
                                Thread.currentThread().interrupt();
                            }
                        }
                    },
                    "test-tradingloop-monitor-holder");
            monitorHolder.start();
            try {
                assertTrue(
                        monitorAcquired.await(5, TimeUnit.SECONDS),
                        "test setup: monitor-holder thread never acquired TradingLoop's own lock");

                // beforeTick() (DailyReportGenerator's own monitor, not
                // TradingLoop's) still runs and seeds currentDay; the
                // immediate first tick then blocks trying to enter
                // TradingLoop.tick() itself, unable to proceed at all for
                // as long as the lock above is held.
                app.start();
                awaitCondition(() -> app.dailyReportGenerator().currentDay() != null, Duration.ofSeconds(5));

                clock.advanceTo(Instant.parse("2026-08-08T00:03:00Z"));

                app.stop(); // both awaits reliably observe false -- monitor-entry blocking cannot be interrupted

                Path reportFile = reportsDir.resolve("2026-08-07.json");
                assertFalse(
                        Files.exists(reportFile),
                        "finalization must be skipped -- never risk a wrong/incomplete report -- when"
                                + " termination cannot be confirmed");
                assertEquals(
                        LocalDate.parse("2026-08-07"), app.dailyReportGenerator().currentDay(),
                        "day tracking must not have advanced -- finalizeCompletedDayOnShutdown() must never have"
                                + " run");
                assertEquals(0, app.dailyReportGenerator().pendingReportCount());
            } finally {
                releaseMonitor.countDown();
                monitorHolder.join(Duration.ofSeconds(5).toMillis());
                app.stop();
            }
        }
    }

    /**
     * Paper-trading bridge Task E ({@code .planning/paper-trading-e-
     * reconciliation.md}): {@link PaperTradingApp#reconcile()} is the real
     * wiring point for {@link Reconciler#check} in this app -- a normal
     * signal-to-fill flow through the real {@link RiskGateway}/{@link
     * OrderPipeline}/{@link PaperBroker} graph must report clean and must
     * not touch the kill switch.
     */
    @Test
    void reconcileReportsCleanAfterANormalFillAndDoesNotTripTheKillSwitch(@TempDir Path tempDir) throws IOException {
        Path signalPath = tempDir.resolve("latest.json");
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(), SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.01"), null, "1d",
                Instant.now());
        writeIntent(signalPath, intent);

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            PaperTradingApp app = new PaperTradingApp(SYMBOL, server.baseUrl(), signalPath, 60);

            app.tradingLoop().tick(); // real fill, GUARDED_MARKET
            ReconciliationReport report = app.reconcile();

            assertTrue(report.isClean(), "expected a clean report after a normal fill: " + report.mismatches());
            assertFalse(app.killSwitch().isTripped());
            assertEquals(report, app.lastReconciliationReport());
        }
    }

    /**
     * Proves {@link PaperTradingApp#runTick()} (driven by {@link
     * PaperTradingApp#start()}) automatically calls {@link
     * PaperTradingApp#reconcile()} on every scheduled tick, not only when a
     * caller invokes it directly -- {@link
     * PaperTradingApp#lastReconciliationReport()} starts {@code null} and
     * becomes non-null once the real scheduler has run at least one tick.
     */
    @Test
    void startAutomaticallyRunsReconciliationAfterEveryScheduledTick(@TempDir Path tempDir) throws Exception {
        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            Path signalPath = tempDir.resolve("latest.json"); // never written -- ticks are no-ops but still real
            PaperTradingApp app = new PaperTradingApp(SYMBOL, server.baseUrl(), signalPath, 1);

            assertEquals(null, app.lastReconciliationReport());

            app.start();
            try {
                ReconciliationReport report =
                        awaitNonNull(app::lastReconciliationReport, Duration.ofSeconds(5));
                assertTrue(report.isClean());
            } finally {
                app.stop();
            }
        }
    }

    /**
     * The real, end-to-end demonstration this task's own brief asks for:
     * manufacture a genuine internal-consistency violation and confirm
     * {@link PaperTradingApp#reconcile()} both reports it and trips the
     * kill switch. An order is registered in this app's own real {@link
     * engine.oms.OrderStore} (via a second {@link OrderPipeline} pointed at
     * the same store -- a real {@link RiskGateway#evaluate} call, not a
     * hand-built {@code RiskDecision}) but deliberately never handed to
     * {@link engine.execution.PaperBroker} -- the exact orphan scenario
     * {@link TradingLoop#submitToBroker}'s own Javadoc names. Reflection is
     * used only to seed {@link TradingLoop}'s own submitted-order-id
     * history with this id, standing in for what a real {@code tick()}
     * call would have recorded -- the same technique {@code TradingLoopTest
     * }'s own equity-depleted test already uses to reach a field with no
     * other public entry point.
     */
    @Test
    void reconcileDetectsARealOrphanedOrderAndTripsTheKillSwitch(@TempDir Path tempDir) throws Exception {
        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            Path signalPath = tempDir.resolve("latest.json"); // never written
            PaperTradingApp app = new PaperTradingApp(SYMBOL, server.baseUrl(), signalPath, 60);

            OrderPipeline sideChannel = new OrderPipeline(new RiskGateway(RiskLimits.canary()), app.orderStore());
            OrderIntent orphanIntent = new OrderIntent(
                    UUID.randomUUID(), SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"),
                    new BigDecimal("40000"), "1d", Instant.now());
            AccountState account =
                    new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
            Order orphan = sideChannel.submitIntent(orphanIntent, new BigDecimal("60000"), account).orElseThrow();
            // orphan is now real and registered in app's own OrderStore
            // (state NEW) -- deliberately never submitted to PaperBroker.

            Field submittedIdsField = TradingLoop.class.getDeclaredField("submittedOrderIds");
            submittedIdsField.setAccessible(true);
            @SuppressWarnings("unchecked")
            List<UUID> submittedIds = (List<UUID>) submittedIdsField.get(app.tradingLoop());
            submittedIds.add(orphan.clientOrderId());

            ReconciliationReport report = app.reconcile();

            assertFalse(report.isClean());
            assertEquals(1, report.mismatches().size());
            assertEquals(ReconciliationMismatchType.ORPHANED_IN_BROKER, report.mismatches().get(0).type());
            assertEquals(orphan.clientOrderId(), report.mismatches().get(0).orderId());
            assertTrue(app.killSwitch().isTripped(), "a detected internal-consistency mismatch must trip the kill switch");
            assertEquals(report, app.lastReconciliationReport());
        }
    }

    // ---- Paper Trading Bridge Task H: execution mode + OrderExecutor-accepting constructor ----

    @Test
    void resolveExecutionModeDefaultsToSimulatedForNullOrBlank() {
        assertEquals(PaperTradingApp.EXECUTION_MODE_SIMULATED, PaperTradingApp.resolveExecutionMode(null));
        assertEquals(PaperTradingApp.EXECUTION_MODE_SIMULATED, PaperTradingApp.resolveExecutionMode(""));
        assertEquals(PaperTradingApp.EXECUTION_MODE_SIMULATED, PaperTradingApp.resolveExecutionMode("   "));
        assertEquals(PaperTradingApp.EXECUTION_MODE_SIMULATED, PaperTradingApp.resolveExecutionMode("simulated"));
    }

    /**
     * Real CodeRabbit review finding: an earlier version of this method
     * silently defaulted every unrecognized value to {@code simulated} --
     * with three real modes now, a typo of {@code bingx-vst}/{@code
     * kis-paper} would have silently started the wrong (inert) graph
     * instead of failing fast. See {@link PaperTradingApp#resolveExecutionMode}'s
     * own Javadoc for the full reasoning.
     */
    @Test
    void resolveExecutionModeRejectsUnrecognizedValuesRatherThanDefaultingToSimulated() {
        assertThrows(IllegalStateException.class, () -> PaperTradingApp.resolveExecutionMode("garbage"));
        assertThrows(
                IllegalStateException.class,
                () -> PaperTradingApp.resolveExecutionMode("BINGX-VST"),
                "case-sensitive by design -- only the exact documented lowercase value opts into bingx-vst mode, and"
                        + " a case-mismatched near-miss must fail loud, not silently fall back to simulated");
        assertThrows(
                IllegalStateException.class,
                () -> PaperTradingApp.resolveExecutionMode("KIS-PAPER"),
                "case-sensitive by design -- only the exact documented lowercase value opts into kis-paper mode, and"
                        + " a case-mismatched near-miss must fail loud, not silently fall back to simulated");
        assertThrows(IllegalStateException.class, () -> PaperTradingApp.resolveExecutionMode("kis-papr"));
    }

    @Test
    void resolveKisMarketDivisionDefaultsToIndexFuturesForNullOrBlank() {
        assertEquals(KisPriceFeed.MarketDivision.INDEX_FUTURES, PaperTradingApp.resolveKisMarketDivision(null));
        assertEquals(KisPriceFeed.MarketDivision.INDEX_FUTURES, PaperTradingApp.resolveKisMarketDivision(""));
        assertEquals(KisPriceFeed.MarketDivision.INDEX_FUTURES, PaperTradingApp.resolveKisMarketDivision("   "));
    }

    @Test
    void resolveKisMarketDivisionAcceptsBothEnumConstantNames() {
        assertEquals(
                KisPriceFeed.MarketDivision.INDEX_FUTURES, PaperTradingApp.resolveKisMarketDivision("INDEX_FUTURES"));
        assertEquals(
                KisPriceFeed.MarketDivision.STOCK_FUTURES, PaperTradingApp.resolveKisMarketDivision("STOCK_FUTURES"));
    }

    /**
     * Same "fail loud, don't silently default" reasoning as {@link
     * #resolveExecutionModeRejectsUnrecognizedValuesRatherThanDefaultingToSimulated}
     * above -- a typo'd market division must not silently fall back to
     * {@code INDEX_FUTURES} and risk a wrong or empty quote for a real
     * individual-stock-futures symbol.
     */
    @Test
    void resolveKisMarketDivisionRejectsUnrecognizedValues() {
        assertThrows(IllegalStateException.class, () -> PaperTradingApp.resolveKisMarketDivision("garbage"));
        assertThrows(
                IllegalStateException.class,
                () -> PaperTradingApp.resolveKisMarketDivision("index_futures"),
                "case-sensitive by design, matching Enum::valueOf's own exact-match semantics");
        assertThrows(IllegalStateException.class, () -> PaperTradingApp.resolveKisMarketDivision("JF"));
    }

    @Test
    void resolveExecutionModeReturnsBingxVstOnlyForAnExactMatch() {
        assertEquals(PaperTradingApp.EXECUTION_MODE_BINGX_VST, PaperTradingApp.resolveExecutionMode("bingx-vst"));
        assertEquals(PaperTradingApp.EXECUTION_MODE_BINGX_VST, PaperTradingApp.resolveExecutionMode("  bingx-vst  "));
    }

    @Test
    void resolveExecutionModeReturnsKisPaperOnlyForAnExactMatch() {
        assertEquals(PaperTradingApp.EXECUTION_MODE_KIS_PAPER, PaperTradingApp.resolveExecutionMode("kis-paper"));
        assertEquals(PaperTradingApp.EXECUTION_MODE_KIS_PAPER, PaperTradingApp.resolveExecutionMode("  kis-paper  "));
    }

    /**
     * Verifies the actual safety-critical invariant this task's brief calls
     * out explicitly: the VST host is a hardcoded {@code private static
     * final} constant, unreachable via any environment variable or
     * argument. Reflection is the only way to observe it directly (by
     * design -- see the field's own Javadoc for why it's {@code private}),
     * matching this test suite's own established precedent for reaching a
     * field with no other access point ({@code
     * reconcileDetectsARealOrphanedOrderAndTripsTheKillSwitch} above uses
     * the same technique for {@code TradingLoop.submittedOrderIds}).
     */
    @Test
    void bingxVstBaseUrlIsHardcodedToTheDocumentedVstHost() throws Exception {
        Field field = PaperTradingApp.class.getDeclaredField("BINGX_VST_BASE_URL");
        field.setAccessible(true);
        assertEquals("https://open-api-vst.bingx.com", field.get(null));
    }

    @Test
    void orderExecutorAcceptingConstructorRejectsNullOrderExecutor(@TempDir Path tempDir) throws IOException {
        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            Path signalPath = tempDir.resolve("latest.json");
            assertThrows(
                    NullPointerException.class,
                    () -> new PaperTradingApp(
                            SYMBOL, server.baseUrl(), signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(),
                            null));
        }
    }

    /**
     * The real point of the OrderExecutor-accepting constructor: a caller
     * (a test here, {@code forBingXVst} in real production use) can inject
     * any {@link engine.execution.OrderExecutor} instance, and that exact
     * instance is used directly -- no separate {@link PaperBroker} is ever
     * constructed internally. Proven via reference identity
     * ({@code assertSame} against {@link PaperTradingApp#orderExecutor()}),
     * not a hand-written fake's own call count -- a real {@link
     * PaperBroker} is injected here specifically so this test uses only
     * the two real, canonical {@code OrderExecutor} implementations (see
     * {@code engine.execution.OrderExecutor}'s own "exactly two
     * implementations" invariant), matching a real CodeRabbit review
     * finding on this PR that a hand-written {@code FakeOrderExecutor}
     * test double was itself a third implementation, even though
     * test-only.
     */
    @Test
    void constructingWithAnExplicitOrderExecutorUsesThatExactInstanceInsteadOfBuildingAPaperBroker(
            @TempDir Path tempDir) throws IOException {
        Path signalPath = tempDir.resolve("latest.json");
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(), SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.01"), null, "1d",
                Instant.now());
        writeIntent(signalPath, intent);
        PaperBroker injectedExecutor = new PaperBroker(new BigDecimal("5"), new BigDecimal("2"));

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            PaperTradingApp app = new PaperTradingApp(
                    SYMBOL, server.baseUrl(), signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(),
                    injectedExecutor);

            app.tradingLoop().tick();

            assertNull(app.tradingLoop().lastError());
            assertSame(
                    injectedExecutor, app.orderExecutor(),
                    "the exact injected OrderExecutor instance must be used, not a freshly-built PaperBroker");
        }
    }

    /**
     * The OrderExecutor-accepting constructor also always wires {@link
     * FileSignalSource}'s persisted delivered-marker file (see that
     * class's own Javadoc) -- proven here by a real tick that delivers a
     * signal, then confirming the sibling marker file now exists and
     * records that same {@code intentId}.
     */
    @Test
    void orderExecutorAcceptingConstructorPersistsTheDeliveredSignalMarkerFile(@TempDir Path tempDir)
            throws IOException {
        Path signalPath = tempDir.resolve("latest.json");
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(), SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.01"), null, "1d",
                Instant.now());
        writeIntent(signalPath, intent);

        try (FakeBingXTradesServer server = new FakeBingXTradesServer()) {
            server.respondWithPrice("60000");
            PaperTradingApp app = new PaperTradingApp(
                    SYMBOL, server.baseUrl(), signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(),
                    new PaperBroker(new BigDecimal("5"), new BigDecimal("2")));

            app.tradingLoop().tick();

            Path markerFile = signalPath.resolveSibling("delivered.marker");
            assertTrue(Files.exists(markerFile), "the persisted delivered-marker file must be written");
            assertEquals(intent.intentId().toString(), Files.readString(markerFile));
        }
    }

    // ---- KIS/KOSPI200 Phase 1 Task 4: PriceFeed-accepting constructor + TradingCalendar gating ----

    /**
     * Same reflection technique as {@link #bingxVstBaseUrlIsHardcodedToTheDocumentedVstHost}
     * above, for the analogous KIS constant -- see that test's own Javadoc
     * for why reflection is the only access point by design.
     */
    @Test
    void kisPaperBaseUrlIsHardcodedToTheDocumentedPaperHost() throws Exception {
        Field field = PaperTradingApp.class.getDeclaredField("KIS_PAPER_BASE_URL");
        field.setAccessible(true);
        assertEquals("https://openapivts.koreainvestment.com:29443", field.get(null));
    }

    @Test
    void priceFeedAcceptingConstructorRejectsNullArguments(@TempDir Path tempDir) {
        Path signalPath = tempDir.resolve("latest.json");
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));
        PaperBroker executor = new PaperBroker(new BigDecimal("5"), new BigDecimal("2"));
        FakeTradingCalendar calendar = new FakeTradingCalendar(true);

        assertThrows(
                NullPointerException.class,
                () -> new PaperTradingApp(
                        null, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), calendar,
                        executor));
        assertThrows(
                NullPointerException.class,
                () -> new PaperTradingApp(
                        SYMBOL, null, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), calendar,
                        executor));
        assertThrows(
                NullPointerException.class,
                () -> new PaperTradingApp(
                        SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), null,
                        executor));
        assertThrows(
                NullPointerException.class,
                () -> new PaperTradingApp(
                        SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), calendar,
                        null));
    }

    /**
     * Mirrors {@link #constructingWithAnExplicitOrderExecutorUsesThatExactInstanceInsteadOfBuildingAPaperBroker}
     * above, for the {@code PriceFeed}-accepting constructor: proves both
     * the injected {@link OrderExecutor} and the injected {@link
     * TradingCalendar} are the exact instances used (reference identity for
     * the executor via {@code assertSame}; for the calendar, via its own
     * call-count -- {@link TradingCalendar} exposes no accessor on {@link
     * PaperTradingApp} to compare identity against directly, so observing
     * that it was actually consulted is the real proof it's wired in, not
     * ignored).
     */
    @Test
    void constructingWithPriceFeedConstructorUsesTheInjectedOrderExecutorAndTradingCalendar(@TempDir Path tempDir)
            throws IOException {
        Path signalPath = tempDir.resolve("latest.json");
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(), SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("1"), null, "1d",
                Instant.now());
        writeIntent(signalPath, intent);
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));
        PaperBroker injectedExecutor = new PaperBroker(new BigDecimal("5"), new BigDecimal("2"));
        FakeTradingCalendar calendar = new FakeTradingCalendar(true);

        PaperTradingApp app = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), calendar,
                injectedExecutor);

        app.runTick();

        assertSame(
                injectedExecutor, app.orderExecutor(),
                "the exact injected OrderExecutor instance must be used, not a freshly-built PaperBroker");
        assertTrue(calendar.callCount() > 0, "runTick() must consult the injected TradingCalendar");
        assertNull(app.tradingLoop().lastError());
    }

    /**
     * Same guarantee as {@link #orderExecutorAcceptingConstructorPersistsTheDeliveredSignalMarkerFile}
     * above, for the {@code PriceFeed}-accepting constructor -- {@code
     * kis-paper} is a real venue where a redelivered signal means a real
     * second order, same reasoning as {@code bingx-vst}. Also proves the
     * real CodeRabbit review fix this constructor now carries: the marker
     * filename is KIS-specific ({@code kis-delivered.marker}), not the
     * shared {@code delivered.marker} name the {@code bingx-vst} overload
     * uses -- checking that the shared name was NOT also written is what
     * actually proves the two venues' delivery state can never collide,
     * not just that some marker file exists.
     */
    @Test
    void priceFeedAcceptingConstructorPersistsAKisSpecificDeliveredSignalMarkerFile(@TempDir Path tempDir)
            throws IOException {
        Path signalPath = tempDir.resolve("latest.json");
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(), SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("1"), null, "1d",
                Instant.now());
        writeIntent(signalPath, intent);
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));

        PaperTradingApp app = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(),
                new FakeTradingCalendar(true), new PaperBroker(new BigDecimal("5"), new BigDecimal("2")));

        app.runTick();

        Path kisMarkerFile = signalPath.resolveSibling("kis-delivered.marker");
        assertTrue(Files.exists(kisMarkerFile), "the KIS-specific delivered-marker file must be written");
        assertEquals(intent.intentId().toString(), Files.readString(kisMarkerFile));
        Path sharedBingxStyleMarkerFile = signalPath.resolveSibling("delivered.marker");
        assertFalse(
                Files.exists(sharedBingxStyleMarkerFile),
                "must not also write the bingx-vst-style shared marker name -- that would defeat the whole point of"
                        + " a venue-specific filename");
    }

    // ---- KIS shared account risk ledger (Task C): the 9-arg PriceFeed + AccountStateProvider overload ----

    @Test
    void nineArgKisConstructorRejectsNullAccountStateProvider(@TempDir Path tempDir) {
        Path signalPath = tempDir.resolve("latest.json");
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));
        PaperBroker executor = new PaperBroker(new BigDecimal("5"), new BigDecimal("2"));
        FakeTradingCalendar calendar = new FakeTradingCalendar(true);

        assertThrows(
                NullPointerException.class,
                () -> new PaperTradingApp(
                        SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), calendar,
                        executor, null));
    }

    /**
     * Mirrors {@link #constructingWithPriceFeedConstructorUsesTheInjectedOrderExecutorAndTradingCalendar}
     * above, for the new 9-arg overload's own added {@link
     * AccountStateProvider} parameter: proves the exact injected instance
     * -- not {@code TradingLoop}'s own private synthetic fallback -- is
     * what actually gets consulted, via {@link FakeAccountStateProvider}'s
     * own call-count instrumentation (the same technique {@code
     * TradingLoopTest} already established for this fake).
     */
    @Test
    void constructingWithNineArgKisConstructorUsesTheInjectedAccountStateProvider(@TempDir Path tempDir)
            throws IOException {
        Path signalPath = tempDir.resolve("latest.json");
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(), SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("1"), null, "1d",
                Instant.now());
        writeIntent(signalPath, intent);
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));
        FakeTradingCalendar calendar = new FakeTradingCalendar(true);
        AccountState fixedAccountState =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
        FakeAccountStateProvider accountStateProvider = new FakeAccountStateProvider(fixedAccountState);

        PaperTradingApp app = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), calendar,
                new PaperBroker(new BigDecimal("5"), new BigDecimal("2")), accountStateProvider);

        app.runTick();

        assertEquals(1, accountStateProvider.reserveCallCount(), "the injected AccountStateProvider must be consulted");
        assertEquals(intent.intentId(), accountStateProvider.lastReservedIntent().intentId());
    }

    /**
     * Regression test for KIS Ledger Task C's own hard requirement:
     * {@code forKisPaper()}'s unconditional {@code killSwitch.trip()} call
     * (see that method's own Javadoc -- it fires regardless of preflight/
     * marker state, specifically because of the still-open KOSPI200
     * contract-multiplier gap) must keep fully blocking new-signal
     * submission even with a real, shared {@link SharedKisAccountLedger}
     * wired all the way through to {@link TradingLoop} -- not merely that
     * {@link KillSwitch#trip()} itself works (that is already covered
     * generically elsewhere and would be true trivially). {@code
     * forKisPaper()} itself cannot be invoked directly in a test (it reads
     * real environment variables with no mocking framework available, and
     * -- by this class's own deliberate, documented "no configuration
     * surface" design -- always points at the real, hardcoded {@code
     * KIS_PAPER_BASE_URL} host; see this class's own Javadoc, "Execution
     * mode", and the identical, pre-existing untestability of {@code
     * forBingXVst()} this class's own top Javadoc already discloses:
     * "Construction/config-resolution logic is tested directly here...
     * fromEnvironment() is deliberately a thin wrapper around the
     * fully-testable constructor"). This test instead exercises the exact
     * same real, production {@code PaperTradingApp}+{@code TradingLoop}+
     * {@code KillSwitch}+{@code SharedKisAccountLedger} graph {@code
     * forKisPaper()} wires (via the 9-arg constructor above, with a real,
     * temp-file-backed {@link SharedKisAccountLedger}, not a fake), then
     * replicates {@code forKisPaper()}'s own trailing {@code
     * app.killSwitch().trip()} call verbatim -- proving the trip genuinely
     * prevents the real signal-processing path from ever reaching {@code
     * SharedKisAccountLedger#reserveForIntent} or producing an order, with
     * a real ledger file genuinely present on disk throughout.
     */
    @Test
    void unconditionalKillSwitchTripStillBlocksNewSignalSubmissionWithASharedLedgerPresent(@TempDir Path tempDir)
            throws IOException {
        Path signalPath = tempDir.resolve("latest.json");
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(), SYMBOL, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("1"), null, "1d",
                Instant.now());
        writeIntent(signalPath, intent);
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));
        FakeTradingCalendar calendar = new FakeTradingCalendar(true);
        Path ledgerPath = tempDir.resolve("KIS-acct-1-account_ledger.json");
        SharedKisAccountLedger accountStateProvider =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, "KIS", "acct-1", new BigDecimal("100000"));
        assertTrue(Files.exists(ledgerPath), "the shared ledger file must be a real, persisted file on disk");

        PaperTradingApp app = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), calendar,
                new PaperBroker(new BigDecimal("5"), new BigDecimal("2")), accountStateProvider);

        // Mirrors forKisPaper()'s own unconditional trip, verbatim -- see
        // that method's own Javadoc for why this must remain unconditional.
        app.killSwitch().trip();

        app.runTick();

        assertTrue(app.killSwitch().isTripped());
        assertNotNull(app.tradingLoop().lastTickAt(), "tick() itself still runs (fill polling), only new-signal processing is gated");
        assertTrue(
                app.tradingLoop().submittedOrderIds().isEmpty(),
                "no order may be submitted while the kill switch is tripped, even with a real signal available and a"
                        + " real shared ledger present");
        assertTrue(
                app.orderStore().findByClientOrderId(intent.intentId()).isEmpty(),
                "the pending signal must never have reached OrderPipeline/OrderStore at all");
    }

    // ---- KIS shared account risk ledger (Task D): AccountLedgerReconciler gating in runTick() ----

    /**
     * {@code simulated}/{@code bingx-vst} (the 4-arg and {@code
     * OrderExecutor}-accepting constructors) and the two other, reconciler-
     * less KIS constructors (the {@code PriceFeed} 8-arg and {@code
     * PriceFeed}+{@code AccountStateProvider} 9-arg overloads) must all
     * leave {@link PaperTradingApp#accountLedgerReconciler()} {@link
     * Optional#empty()} -- a structural fact, not merely "looks unchanged":
     * there is no code path inside any of these constructor bodies that
     * could ever populate it, since only the new 11-arg overload accepts an
     * {@link AccountLedgerReconciler} at all.
     */
    @Test
    void nonKisLedgerConstructorsAlwaysLeaveAccountLedgerReconcilerEmpty(@TempDir Path tempDir) {
        Path signalPath = tempDir.resolve("latest.json");

        PaperTradingApp simulated = new PaperTradingApp(SYMBOL, "http://localhost:1", signalPath, 60);
        assertEquals(Optional.empty(), simulated.accountLedgerReconciler());

        PaperTradingApp bingxVstStyle = new PaperTradingApp(
                SYMBOL, "http://localhost:1", signalPath, 60, tempDir.resolve("reports-vst"), Clock.systemUTC(),
                new PaperBroker(new BigDecimal("5"), new BigDecimal("2")));
        assertEquals(Optional.empty(), bingxVstStyle.accountLedgerReconciler());

        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));
        FakeTradingCalendar calendar = new FakeTradingCalendar(true);
        PaperTradingApp kisWithoutLedger = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports-kis8"), Clock.systemUTC(), calendar,
                new PaperBroker(new BigDecimal("5"), new BigDecimal("2")));
        assertEquals(Optional.empty(), kisWithoutLedger.accountLedgerReconciler());

        AccountState fixedAccountState =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
        FakeAccountStateProvider accountStateProvider = new FakeAccountStateProvider(fixedAccountState);
        PaperTradingApp kisWithLedgerNoReconciler = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports-kis9"), Clock.systemUTC(), calendar,
                new PaperBroker(new BigDecimal("5"), new BigDecimal("2")), accountStateProvider);
        assertEquals(Optional.empty(), kisWithLedgerNoReconciler.accountLedgerReconciler());
    }

    /**
     * Contrast case to the test above, proving the gate is real and
     * reachable, not merely present-but-dead code: the new 11-arg
     * constructor populates {@link PaperTradingApp#accountLedgerReconciler()},
     * and {@link PaperTradingApp#runTick()} actually drives a real {@link
     * AccountLedgerReconciler} pass through it once a UTC day boundary is
     * crossed -- proven via {@link AccountLedgerReconciler#reconciliationPassCount()},
     * not merely "no exception was thrown."
     */
    @Test
    void elevenArgKisConstructorPopulatesAccountLedgerReconcilerAndRunTickInvokesItAcrossADayBoundary(
            @TempDir Path tempDir) {
        Path signalPath = tempDir.resolve("latest.json"); // never written -- irrelevant to this test
        Path ledgerPath = tempDir.resolve("KIS-acct-recon-account_ledger.json");
        SharedKisAccountLedger accountStateProvider =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, "KIS", "acct-recon", new BigDecimal("100000"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of());
        KillSwitch killSwitch = new KillSwitch();
        MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, "KIS", "acct-recon", new BigDecimal("100000"), adapter, killSwitch, clock);
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));
        FakeTradingCalendar calendar = new FakeTradingCalendar(true);

        PaperTradingApp app = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), clock, calendar,
                new PaperBroker(new BigDecimal("5"), new BigDecimal("2")), accountStateProvider, killSwitch,
                reconciler);

        assertTrue(app.accountLedgerReconciler().isPresent());
        assertSame(reconciler, app.accountLedgerReconciler().get());

        app.runTick(); // first call ever -- seeds the reconciler's own day-tracking state, no pass yet
        assertEquals(0, reconciler.reconciliationPassCount());

        clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
        app.runTick(); // crosses a UTC day boundary -- must invoke a real reconciliation pass
        assertEquals(1, reconciler.reconciliationPassCount());
    }

    /**
     * {@link AccountLedgerReconciler#runOnUtcDayBoundary()} throwing must
     * never propagate out of {@link PaperTradingApp#runTick()} -- an
     * uncaught exception from a {@code scheduleAtFixedRate} task would
     * silently cancel every future execution of the whole scheduled loop.
     */
    @Test
    void runTickCatchesAndLogsAnAccountLedgerReconciliationFailureRatherThanPropagatingIt(@TempDir Path tempDir) {
        Path signalPath = tempDir.resolve("latest.json");
        Path ledgerPath = tempDir.resolve("KIS-acct-recon-fail-account_ledger.json");
        SharedKisAccountLedger accountStateProvider = SharedKisAccountLedger.bootstrapOrLoad(
                ledgerPath, "KIS", "acct-recon-fail", new BigDecimal("100000"));
        FakeExchangeAdapter adapter = new FakeExchangeAdapter();
        adapter.willReturnPositions(List.of());
        KillSwitch killSwitch = new KillSwitch();
        MutableClock clock = new MutableClock(Instant.parse("2026-08-07T00:05:00Z"));
        AccountLedgerReconciler reconciler = new AccountLedgerReconciler(
                ledgerPath, "KIS", "acct-recon-fail", new BigDecimal("100000"), adapter, killSwitch, clock);
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));
        FakeTradingCalendar calendar = new FakeTradingCalendar(true);
        PaperTradingApp app = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), clock, calendar,
                new PaperBroker(new BigDecimal("5"), new BigDecimal("2")), accountStateProvider, killSwitch,
                reconciler);

        app.runTick(); // seeds the reconciler's own day tracking (day 1) -- no pass yet
        clock.advanceTo(Instant.parse("2026-08-08T00:05:00Z"));
        adapter.willFailPositionsWith(new RuntimeException("simulated getPositions failure"));

        app.runTick(); // must not throw, even though the reconciler's own pass fails

        assertEquals(0, reconciler.reconciliationPassCount(), "the failed pass must not count as completed");
        assertNotNull(app.tradingLoop().lastTickAt(), "the rest of the tick must still have run normally");
    }

    /**
     * The real point of threading {@link TradingCalendar} through {@link
     * PaperTradingApp#runTick()} (see that method's own Javadoc): when the
     * calendar reports closed, {@link TradingLoop#tick()} must never run at
     * all -- proven here via {@link TradingLoop#lastTickAt()} staying
     * {@code null} (it is only ever set inside {@code tick()}'s own
     * {@code finally} block, so a null value after a real {@code runTick()}
     * call is direct proof {@code tick()} itself was never entered, not
     * merely that it produced no observable side effect). {@code
     * beforeTick()}/{@code afterTick()}/{@link PaperTradingApp#reconcile()}
     * must still run regardless -- proven by a clean {@link
     * ReconciliationReport} being recorded even though no tick ran.
     */
    @Test
    void runTickSkipsTradingLoopTickWhenTradingCalendarReportsClosedButStillRunsSurroundingBookkeeping(
            @TempDir Path tempDir) throws IOException {
        Path signalPath = tempDir.resolve("latest.json"); // never written -- irrelevant, tick() must not even look
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));
        FakeTradingCalendar calendar = new FakeTradingCalendar(false);

        PaperTradingApp app = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), calendar,
                new PaperBroker(new BigDecimal("5"), new BigDecimal("2")));

        app.runTick();

        assertNull(app.tradingLoop().lastTickAt(), "TradingLoop.tick() must never run while the market is closed");
        assertTrue(calendar.callCount() > 0, "the calendar must actually have been consulted");
        assertNotNull(
                app.lastReconciliationReport(), "reconcile() must still run unconditionally after a skipped tick");
        assertTrue(app.lastReconciliationReport().isClean());
    }

    /** Complement to the skip test above: an open calendar must let the tick run normally. */
    @Test
    void runTickRunsTradingLoopTickWhenTradingCalendarReportsOpen(@TempDir Path tempDir) throws IOException {
        Path signalPath = tempDir.resolve("latest.json"); // never written -- an empty tick is enough to prove entry
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("350"));
        FakeTradingCalendar calendar = new FakeTradingCalendar(true);

        PaperTradingApp app = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), calendar,
                new PaperBroker(new BigDecimal("5"), new BigDecimal("2")));

        app.runTick();

        assertNotNull(app.tradingLoop().lastTickAt(), "TradingLoop.tick() must run while the market is open");
    }

    /**
     * Real CodeRabbit review finding, fixed: a closed market must gate
     * only new-signal processing, not fill polling for an already-pending
     * order -- an order can resolve at the exchange at any time, not only
     * during this loop's own configured trading hours. Seeds a real
     * pending order directly through {@code app}'s own {@link
     * #orderStore()}-backed pipeline (same technique {@code
     * TradingLoopTest#tickWithNoSignalStillAppliesPriceUpdateToExistingPendingOrders}
     * uses), submits it to the same {@link PaperBroker} instance {@code
     * app} was constructed with at a non-marketable price, then closes the
     * calendar and drives one {@code runTick()} at a marketable price --
     * the order filling despite the closed calendar is direct proof
     * {@link TradingLoop#pollPendingFills()} actually ran, not just that
     * {@code runTick()} didn't throw. {@link TradingLoop#lastTickAt()}
     * staying {@code null} throughout (same assertion as the skip test
     * above) proves this happened without {@code tick()}'s own new-signal
     * path ever running.
     */
    @Test
    void runTickPollsPendingFillsEvenWhenTradingCalendarReportsClosed(@TempDir Path tempDir) throws Exception {
        Path signalPath = tempDir.resolve("latest.json"); // never written -- irrelevant to this test
        PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
        FakeTradingCalendar calendar = new FakeTradingCalendar(false);
        // Deliberately below the seeded order's own 50000 limit -- see below.
        FakePriceFeed priceFeed = new FakePriceFeed(new BigDecimal("40000"));

        PaperTradingApp app = new PaperTradingApp(
                SYMBOL, priceFeed, signalPath, 60, tempDir.resolve("reports"), Clock.systemUTC(), calendar, broker);

        // Seeded through app's own OrderStore (via a side-channel pipeline,
        // same technique reconcileDetectsARealOrphanedOrderAndTripsTheKillSwitch
        // above uses) and directly registered as submitted (submittedOrderIds
        // below), so reconcile() -- which still runs after every runTick(),
        // closed market or not -- finds no mismatch; this test is about
        // TradingCalendar gating, not internal-consistency reconciliation.
        OrderPipeline seedPipeline = new OrderPipeline(new RiskGateway(RiskLimits.canary()), app.orderStore());
        OrderIntent seedIntent = new OrderIntent(
                UUID.randomUUID(), SYMBOL, Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"),
                new BigDecimal("50000"), "15m", Instant.now());
        AccountState seedAccount =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
        Order seedOrder = seedPipeline.submitIntent(seedIntent, new BigDecimal("60000"), seedAccount).orElseThrow();
        broker.submit(seedOrder, new BigDecimal("60000")); // not marketable at 60000 -> stays pending
        assertEquals(1, broker.pendingOrders().size());
        Field submittedIdsField = TradingLoop.class.getDeclaredField("submittedOrderIds");
        submittedIdsField.setAccessible(true);
        @SuppressWarnings("unchecked")
        List<UUID> submittedIds = (List<UUID>) submittedIdsField.get(app.tradingLoop());
        submittedIds.add(seedOrder.clientOrderId());

        app.runTick(); // priceFeed serves 40000, below the seeded LIMIT(50000) LONG order -> marketable

        assertTrue(
                broker.pendingOrders().isEmpty(),
                "the seeded pending order must have been polled and filled even though the market was closed");
        assertEquals(OrderState.FILLED, seedOrder.state());
        assertNull(
                app.tradingLoop().lastTickAt(),
                "TradingLoop.tick() itself (new-signal processing) must still never run while the market is closed");
        assertTrue(
                app.lastReconciliationReport().isClean(),
                "no internal-consistency mismatch expected -- the seeded order was registered as submitted");
    }
}
