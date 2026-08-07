package engine.runtime;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.schemas.OrderIntent;
import engine.schemas.SchemaObjectMapper;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Reads a single {@link OrderIntent}, serialized as JSON via
 * {@link SchemaObjectMapper}'s shared conventions, from a file written by a
 * separate process -- this is the file-based Python-to-Java signal bridge
 * decided in {@code .planning/paper-trading-a-signal-source.md} (Task A of
 * the paper-trading-bridge plan): a later, separate task's Python daily
 * runner writes roughly one new signal file per day; {@link TradingLoop}
 * ticks far more often than that via a scheduler. The dedup behavior below
 * (deliver each distinct {@code intentId} exactly once) is what makes most
 * of those ticks correctly see nothing new, even though the file itself is
 * present and valid the whole time.
 *
 * <p>Deliberately tolerant of every condition short of a genuinely new,
 * valid signal -- a missing file, a malformed/unparseable file, or a file
 * that still holds the last-delivered signal all resolve to
 * {@link Optional#empty()}, never an exception. {@link TradingLoop#tick()}
 * does have its own catch-all, but this class does not rely on that as its
 * primary error handling -- a bad signal file must not even register as a
 * "tick failed" event the way an unrelated bug would.
 *
 * <p>Takes a {@link Path}, not a bare {@code String} -- matches
 * {@code engine.schemas.SchemaCompatTest}'s own convention for fixture
 * paths (the only existing file-path-typed precedent in this codebase;
 * {@link BingXPriceFeed}'s {@code baseUrl} is a URL string, not a
 * filesystem path, so it isn't a comparable precedent here).
 */
public final class FileSignalSource implements SignalSource {

    private static final Logger log = LoggerFactory.getLogger(FileSignalSource.class);

    private final Path signalFilePath;
    private final ObjectMapper objectMapper = SchemaObjectMapper.create();

    private volatile UUID lastDeliveredIntentId;

    public FileSignalSource(Path signalFilePath) {
        this.signalFilePath = Objects.requireNonNull(signalFilePath, "signalFilePath is required");
    }

    /**
     * See class Javadoc for the full contract. Order of checks: missing
     * file (empty, no log -- an ordinary, expected steady state between
     * daily writes) &gt; unreadable/unparseable file (empty, logged warning)
     * &gt; already-delivered {@code intentId} (empty, no log -- also an
     * ordinary steady state) &gt; genuinely new intent (delivered, remembered).
     */
    @Override
    public synchronized Optional<OrderIntent> nextSignal() {
        String raw;
        try {
            raw = Files.readString(signalFilePath);
        } catch (NoSuchFileException e) {
            return Optional.empty();
        } catch (IOException e) {
            log.warn("failed to read signal file {}, treating as no signal: {}", signalFilePath, e.toString());
            return Optional.empty();
        }

        OrderIntent intent;
        try {
            intent = objectMapper.readValue(raw, OrderIntent.class);
        } catch (IOException | RuntimeException e) {
            // IOException covers malformed JSON and missing-required-field
            // schema violations (OrderIntent's @JsonProperty(required =
            // true) fields); RuntimeException covers OrderIntent's own
            // compact-constructor validation (e.g. LIMIT without a
            // limitPrice), which Jackson normally wraps into a
            // JsonMappingException (itself an IOException) but is caught
            // unwrapped here too as defense in depth -- either way, this
            // must never propagate into TradingLoop.tick().
            log.warn(
                    "failed to parse signal file {} as an OrderIntent, treating as no signal: {}",
                    signalFilePath,
                    e.toString());
            return Optional.empty();
        }

        if (intent.intentId().equals(lastDeliveredIntentId)) {
            return Optional.empty();
        }
        lastDeliveredIntentId = intent.intentId();
        return Optional.of(intent);
    }
}
