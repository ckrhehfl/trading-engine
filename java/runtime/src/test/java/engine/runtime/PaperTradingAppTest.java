package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.fail;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.SchemaObjectMapper;
import engine.schemas.Side;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
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
     * Bounded polling helper (CodeRabbit review finding on this task's
     * PR) -- replaces a fixed {@code Thread.sleep(...)} with short
     * polling intervals and a clear deadline, so this test fails only
     * after genuinely waiting {@code timeout} for {@code supplier} to
     * stop returning {@code null}, rather than either flaking on a slow
     * CI runner (too-short fixed sleep) or wasting wall-clock time on a
     * fast one (too-generous fixed sleep).
     */
    private static <T> T awaitNonNull(Supplier<T> supplier, Duration timeout) throws InterruptedException {
        Instant deadline = Instant.now().plus(timeout);
        while (Instant.now().isBefore(deadline)) {
            T value = supplier.get();
            if (value != null) {
                return value;
            }
            Thread.sleep(50);
        }
        fail("condition not met within " + timeout);
        throw new AssertionError("unreachable"); // fail() always throws; keeps the compiler happy
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
                awaitNonNull(() -> app.tradingLoop().lastTickAt(), Duration.ofSeconds(5));
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
}
