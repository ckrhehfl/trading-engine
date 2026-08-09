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

    /**
     * Documents, deliberately, a real limitation raised on CodeRabbit
     * review of PR #68 (see {@code FileSignalSource}'s class Javadoc,
     * "Dedup scope, stated precisely", and
     * {@code .planning/paper-trading-a-signal-source.md}'s "CodeRabbit
     * review findings" section for the full reasoning on why this is not
     * fixed in this task): {@code lastDeliveredIntentId} is a single
     * pointer, not a durable set of every {@code intentId} ever seen, so
     * an intent that reappears after a different one was delivered in
     * between is delivered again, not suppressed as a duplicate.
     */
    @Test
    void anIntentIdThatReappearsAfterADifferentOneWasDeliveredIsRedeliveredNotSuppressed(@TempDir Path tempDir)
            throws IOException {
        Path signalFile = tempDir.resolve("latest.json");
        OrderIntent intentA = newIntent();
        writeIntent(signalFile, intentA);

        FileSignalSource source = new FileSignalSource(signalFile);

        assertEquals(intentA.intentId(), source.nextSignal().orElseThrow().intentId());

        OrderIntent intentB = newIntent();
        writeIntent(signalFile, intentB);
        assertEquals(intentB.intentId(), source.nextSignal().orElseThrow().intentId());

        // File reverts to A -- the SAME source instance, not a restart.
        // The single-pointer contract only compares against the most
        // recently delivered id (B), so A no longer matches and is
        // delivered again.
        writeIntent(signalFile, intentA);
        assertEquals(intentA.intentId(), source.nextSignal().orElseThrow().intentId());
    }

    /**
     * Documents, deliberately, the restart half of the same limitation:
     * {@code lastDeliveredIntentId} lives only in memory, so a fresh
     * instance (standing in for a process restart -- {@code TradingLoop}/
     * {@code PaperTradingApp} construct exactly one {@code FileSignalSource}
     * per process lifetime) has no memory of what a prior instance already
     * delivered.
     */
    @Test
    void aFreshInstanceAfterARestartRedeliversAnIntentIdThePriorInstanceAlreadyDelivered(@TempDir Path tempDir)
            throws IOException {
        Path signalFile = tempDir.resolve("latest.json");
        OrderIntent intent = newIntent();
        writeIntent(signalFile, intent);

        FileSignalSource beforeRestart = new FileSignalSource(signalFile);
        assertEquals(intent.intentId(), beforeRestart.nextSignal().orElseThrow().intentId());
        assertFalse(beforeRestart.nextSignal().isPresent()); // normal dedup, same instance

        // Simulate a process restart: a brand-new FileSignalSource against
        // the same, untouched file.
        FileSignalSource afterRestart = new FileSignalSource(signalFile);
        assertEquals(
                intent.intentId(),
                afterRestart.nextSignal().orElseThrow().intentId(),
                "a fresh instance has no memory of what a prior instance already delivered");
    }

    /**
     * Task H's own fix for the real duplicate-order risk the restart test
     * above documents: an optional, additive, opt-in persisted delivered-
     * marker file. {@code null} (the 1-arg constructor, exercised by every
     * test above) means exactly today's in-memory-only behavior -- this
     * test proves the 2-arg constructor with an explicit {@code null}
     * marker path is byte-for-byte equivalent, not just "close enough."
     */
    @Test
    void aNullMarkerPathBehavesExactlyLikeTheOneArgConstructor(@TempDir Path tempDir) throws IOException {
        Path signalFile = tempDir.resolve("latest.json");
        OrderIntent intent = newIntent();
        writeIntent(signalFile, intent);

        FileSignalSource source = new FileSignalSource(signalFile, null);

        assertEquals(intent.intentId(), source.nextSignal().orElseThrow().intentId());
        assertFalse(source.nextSignal().isPresent());
    }

    /**
     * The actual point of this fix: a marker file already containing a
     * previously-delivered {@code intentId} must refuse to redeliver it on
     * the very first {@code nextSignal()} call after a simulated restart --
     * unlike {@code aFreshInstanceAfterARestartRedeliversAnIntentIdThePriorInstanceAlreadyDelivered}
     * above (which documents the {@code null}-marker-path behavior this
     * fix is deliberately additive to, not a replacement for).
     */
    @Test
    void aPreExistingMarkerFileWithAPreviouslyDeliveredIntentIdIsNotRedeliveredOnFirstCall(@TempDir Path tempDir)
            throws IOException {
        Path signalFile = tempDir.resolve("latest.json");
        Path markerFile = tempDir.resolve("delivered.marker");
        OrderIntent intent = newIntent();
        writeIntent(signalFile, intent);
        Files.writeString(markerFile, intent.intentId().toString());

        FileSignalSource source = new FileSignalSource(signalFile, markerFile);

        assertFalse(
                source.nextSignal().isPresent(),
                "a marker file that already recorded this intentId must suppress redelivery immediately");
    }

    /**
     * End-to-end proof the fix actually closes the restart gap: deliver an
     * intent through one instance (persisting the marker), then construct a
     * brand-new instance against the same files (a real restart) and
     * confirm it does NOT redeliver -- the opposite of {@code
     * aFreshInstanceAfterARestartRedeliversAnIntentIdThePriorInstanceAlreadyDelivered}'s
     * null-marker-path behavior above.
     */
    @Test
    void aSuccessfulDeliveryPersistsTheMarkerSoAFreshInstanceAfterARestartDoesNotRedeliverIt(@TempDir Path tempDir)
            throws IOException {
        Path signalFile = tempDir.resolve("latest.json");
        Path markerFile = tempDir.resolve("delivered.marker");
        OrderIntent intent = newIntent();
        writeIntent(signalFile, intent);

        FileSignalSource beforeRestart = new FileSignalSource(signalFile, markerFile);
        assertEquals(intent.intentId(), beforeRestart.nextSignal().orElseThrow().intentId());

        FileSignalSource afterRestart = new FileSignalSource(signalFile, markerFile);
        assertFalse(
                afterRestart.nextSignal().isPresent(),
                "the persisted marker must survive the simulated restart and suppress redelivery");
    }

    @Test
    void aDistinctNewIntentIsStillDeliveredNormallyWithAMarkerPathConfigured(@TempDir Path tempDir)
            throws IOException {
        Path signalFile = tempDir.resolve("latest.json");
        Path markerFile = tempDir.resolve("delivered.marker");
        OrderIntent intentA = newIntent();
        writeIntent(signalFile, intentA);

        FileSignalSource source = new FileSignalSource(signalFile, markerFile);
        assertEquals(intentA.intentId(), source.nextSignal().orElseThrow().intentId());

        OrderIntent intentB = newIntent();
        writeIntent(signalFile, intentB);
        assertEquals(intentB.intentId(), source.nextSignal().orElseThrow().intentId());

        FileSignalSource afterRestart = new FileSignalSource(signalFile, markerFile);
        assertFalse(
                afterRestart.nextSignal().isPresent(),
                "the marker file must reflect the LAST delivered intentId (B), not the first (A)");
    }
}
