package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.SchemaObjectMapper;
import engine.schemas.Side;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link FileSignalSource} is the bridge between the Python-produced
 * {@code OrderIntent} JSON file (a separate, later task) and
 * {@link TradingLoop} -- these tests write real {@code OrderIntent} JSON
 * (via the same {@link SchemaObjectMapper} the production class uses, so
 * the wire shape actually matches what the schema-compat tests already
 * verify) to a real temp file and drive {@link FileSignalSource} against
 * it directly, no mocking framework (matches this codebase's existing
 * style, e.g. {@code TradingLoopTest}'s own Javadoc note on that).
 */
class FileSignalSourceTest {

    private final ObjectMapper mapper = SchemaObjectMapper.create();

    private OrderIntent newIntent() {
        return new OrderIntent(
                UUID.randomUUID(),
                "BTC-USDT",
                Side.LONG,
                OrderType.GUARDED_MARKET,
                new BigDecimal("0.5"),
                null,
                "1d",
                Instant.now());
    }

    private void writeIntent(Path path, OrderIntent intent) throws IOException {
        Files.writeString(path, mapper.writeValueAsString(intent));
    }

    @Test
    void freshlyWrittenValidSignalIsDeliveredExactlyOnceOnFirstCall(@TempDir Path tempDir) throws IOException {
        Path signalFile = tempDir.resolve("latest.json");
        OrderIntent intent = newIntent();
        writeIntent(signalFile, intent);

        FileSignalSource source = new FileSignalSource(signalFile);

        assertEquals(intent.intentId(), source.nextSignal().orElseThrow().intentId());
    }

    @Test
    void secondCallAgainstTheSameUnchangedFileReturnsEmpty(@TempDir Path tempDir) throws IOException {
        Path signalFile = tempDir.resolve("latest.json");
        writeIntent(signalFile, newIntent());

        FileSignalSource source = new FileSignalSource(signalFile);

        assertTrue(source.nextSignal().isPresent());
        assertFalse(source.nextSignal().isPresent());
    }

    @Test
    void noFilePresentReturnsEmptyWithoutThrowing(@TempDir Path tempDir) {
        Path signalFile = tempDir.resolve("does-not-exist.json");

        FileSignalSource source = new FileSignalSource(signalFile);

        assertFalse(source.nextSignal().isPresent());
    }

    @Test
    void malformedFileReturnsEmptyWithoutThrowing(@TempDir Path tempDir) throws IOException {
        Path signalFile = tempDir.resolve("latest.json");
        Files.writeString(signalFile, "this is not valid JSON at all {{{");

        FileSignalSource source = new FileSignalSource(signalFile);

        // No assertion library/appender for verifying the WARN log call
        // exists in this codebase yet (grepped for TempDir/Logger/appender
        // usage across java/ -- none found) -- per this task's own scope,
        // that's acceptable: the real behavioral guarantee under test is
        // "never propagate the exception", verified here by the absence of
        // a thrown exception reaching this test. The log.warn call itself
        // is verified by reading FileSignalSource's source directly.
        assertFalse(source.nextSignal().isPresent());
    }

    @Test
    void aFileMissingARequiredFieldReturnsEmptyWithoutThrowing(@TempDir Path tempDir) throws IOException {
        // Syntactically valid JSON, but missing the required "quantity"
        // field -- OrderIntent's own @JsonProperty(required = true)
        // enforces this at deserialization time, distinct from the
        // not-JSON-at-all case above.
        Path signalFile = tempDir.resolve("latest.json");
        Files.writeString(
                signalFile,
                "{\"intent_id\":\"a1a1a1a1-1111-4111-8111-111111111111\",\"symbol\":\"BTC-USDT\","
                        + "\"side\":\"LONG\",\"order_type\":\"GUARDED_MARKET\","
                        + "\"created_at\":\"2026-07-20T04:00:00Z\"}");

        FileSignalSource source = new FileSignalSource(signalFile);

        assertFalse(source.nextSignal().isPresent());
    }

    @Test
    void aSequenceOfDistinctIntentsIsEachDeliveredExactlyOnceInOrder(@TempDir Path tempDir) throws IOException {
        Path signalFile = tempDir.resolve("latest.json");
        OrderIntent intentX = newIntent();
        writeIntent(signalFile, intentX);

        FileSignalSource source = new FileSignalSource(signalFile);

        assertEquals(intentX.intentId(), source.nextSignal().orElseThrow().intentId());

        OrderIntent intentY = newIntent();
        writeIntent(signalFile, intentY); // overwrite with a new intent

        assertEquals(intentY.intentId(), source.nextSignal().orElseThrow().intentId());

        // polled again, unchanged since intentY was delivered -> empty
        assertFalse(source.nextSignal().isPresent());
    }
}
