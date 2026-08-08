package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.oms.Order;
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
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;
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
}
