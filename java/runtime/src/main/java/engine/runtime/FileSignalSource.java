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
 *
 * <p><b>Dedup scope, stated precisely (raised on CodeRabbit review of
 * PR #68 -- see {@code .planning/paper-trading-a-signal-source.md}'s
 * "CodeRabbit review findings" section):</b> {@link #lastDeliveredIntentId}
 * is a single in-memory pointer, not a durable, all-history set of every
 * {@code intentId} ever delivered. Concretely, this class only ever
 * suppresses re-delivering whichever {@code intentId} it delivered most
 * recently -- two consequences follow, both deliberate, neither hidden: (1)
 * a since-superseded {@code intentId} reappearing after a different one was
 * delivered in between ("A, then B, then A again") would be delivered a
 * second time -- exercised directly by
 * {@code FileSignalSourceTest#anIntentIdThatReappearsAfterADifferentOneWasDeliveredIsRedeliveredNotSuppressed};
 * and (2) this tracking does not survive a process restart -- a fresh
 * {@code FileSignalSource} instance (e.g. after {@code PaperTradingApp}
 * restarts) has forgotten everything a prior instance delivered, and will
 * redeliver whatever the file currently holds even if that exact
 * {@code intentId} was already delivered and acted on before the restart --
 * exercised directly by
 * {@code FileSignalSourceTest#aFreshInstanceAfterARestartRedeliversAnIntentIdThePriorInstanceAlreadyDelivered}.
 * Neither is a bug relative to this task's scope: the task that specified
 * this class ("track the last-delivered {@code intentId} internally")
 * describes exactly this single-pointer design, and it is consistent with
 * -- not a regression from -- {@link TradingLoop}'s own already-documented
 * "does not assume any prior state... 'start clean' is the only state
 * there is" restart story (see that class's Javadoc): today, an OMS/
 * broker-level restart already forgets every in-flight order regardless of
 * what this class remembers, so durable {@code intentId} tracking here
 * alone would not, by itself, prevent a duplicate order after a restart --
 * a real fix needs durable order/position state, which does not exist
 * anywhere in this codebase yet (see {@code OrderStore}/{@code PaperBroker}
 * -- both in-memory only). Building durable dedup into only this one class
 * would be a partial, inconsistent fix, not a real one. Case (1) is also,
 * separately, not expected to occur in practice given the intended Python
 * producer's own write pattern (a later, separate task; see the governing
 * plan): it either leaves the file untouched or overwrites it with a
 * brand-new, never-before-seen {@code intentId} -- never reverts to an
 * older one -- but this class's own contract does not assume or depend on
 * that producer behavior, which is exactly why both cases are tested and
 * disclosed here rather than asserted safe. Durable, cross-restart
 * idempotency (if ever needed) is real, scoped follow-on work -- most
 * naturally paired with the paper-trading bridge plan's own Task E
 * ("minimal internal reconciliation") or a dedicated durable-`OrderStore`
 * effort, not a silent addition to this task.
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
