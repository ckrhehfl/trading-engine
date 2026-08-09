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
 * (suppress redelivering whichever {@code intentId} this instance most
 * recently delivered) is what makes most of those ticks correctly see
 * nothing new, even though the file itself is present and valid the whole
 * time. <b>This is deliberately narrower than "deliver each distinct
 * {@code intentId} exactly once" -- see "Dedup scope, stated precisely"
 * below for the exact, tested contract.</b>
 *
 * <p>Deliberately tolerant of every condition short of a genuinely new,
 * valid signal -- a missing file, a malformed/unparseable file, or a file
 * that still holds the last-delivered signal all resolve to
 * {@link Optional#empty()}, never an exception. {@link TradingLoop#tick()}
 * does have its own catch-all, but this class does not rely on that as its
 * primary error handling -- a bad signal file must not even register as a
 * "tick failed" event the way an unrelated bug would.
 *
 * <p><b>Does not itself validate {@code OrderIntent.symbol()} against
 * anything</b> -- a delivered intent's symbol is whatever the file
 * contained, unchecked here. That validation exists, deliberately one
 * layer up: see {@link TradingLoop}'s own "Symbol-match validation"
 * Javadoc section and {@code .planning/paper-trading-c-scheduler-
 * entrypoint.md} for why {@code TradingLoop} -- not this class -- owns
 * the check (closes GitHub issue #70).
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
 * disclosed here rather than asserted safe.
 *
 * <p><b>Durable, cross-restart dedup (Paper Trading Bridge Task H -- added,
 * not the "real, scoped follow-on work" this section previously called
 * it).</b> The two-arg constructor accepts an optional {@code
 * deliveredMarkerPath}. {@code null} (what the one-arg constructor always
 * passes) is exactly the single-pointer, in-memory-only behavior described
 * above, byte-for-byte -- the currently-running simulated paper-trading
 * process is unaffected by this addition since nothing changes its own
 * construction call. When non-null: the last-delivered {@code intentId} is
 * additionally persisted to that file (overwritten, not appended -- this
 * class only ever needs the single most recent value, matching {@code
 * lastDeliveredIntentId}'s own single-pointer scope) immediately after a
 * genuinely new signal is delivered, and read back at construction time to
 * seed {@code lastDeliveredIntentId} before the very first {@code
 * nextSignal()} call -- closing the real duplicate-order risk this
 * section's restart caveat describes: without this, a process restart on
 * the same UTC day after a signal fired would redeliver it, and against a
 * real venue ({@code ExchangeOrderExecutor}/{@code BingXAdapter}) that
 * means a real second order, not just an inflated simulator trade count.
 * Marker-file read/write failures are tolerated the same way the signal
 * file's own read failures are (logged, never thrown) -- a marker file
 * this class doesn't fully control the lifecycle of should degrade to
 * "less protection," never to a crash.
 */
public final class FileSignalSource implements SignalSource {

    private static final Logger log = LoggerFactory.getLogger(FileSignalSource.class);

    private final Path signalFilePath;
    private final Path deliveredMarkerPath;
    private final ObjectMapper objectMapper = SchemaObjectMapper.create();

    private volatile UUID lastDeliveredIntentId;

    public FileSignalSource(Path signalFilePath) {
        this(signalFilePath, null);
    }

    /**
     * Same as the one-arg constructor, with an optional {@code
     * deliveredMarkerPath} -- see class Javadoc, "Durable, cross-restart
     * dedup". {@code null} is exactly the one-arg constructor's behavior.
     */
    public FileSignalSource(Path signalFilePath, Path deliveredMarkerPath) {
        this.signalFilePath = Objects.requireNonNull(signalFilePath, "signalFilePath is required");
        this.deliveredMarkerPath = deliveredMarkerPath;
        this.lastDeliveredIntentId = readMarker();
    }

    /**
     * See class Javadoc for the full contract. Order of checks: missing
     * file (empty, no log -- an ordinary, expected steady state between
     * daily writes) &gt; unreadable/unparseable file (empty, logged warning)
     * &gt; already-delivered {@code intentId} (empty, no log -- also an
     * ordinary steady state) &gt; genuinely new intent (delivered, remembered,
     * and -- if {@code deliveredMarkerPath} is configured -- persisted).
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
        writeMarker(intent.intentId());
        return Optional.of(intent);
    }

    /**
     * Reads {@link #deliveredMarkerPath} (if configured) to seed {@link
     * #lastDeliveredIntentId} before this instance's very first {@link
     * #nextSignal()} call. Returns {@code null} (today's exact starting
     * state) for a null path, a missing file, or any read/parse failure --
     * a marker file this class doesn't fully control the lifecycle of
     * degrades to "less protection," matching this class's own established
     * tolerance for the signal file itself, never to a constructor
     * exception.
     */
    private UUID readMarker() {
        if (deliveredMarkerPath == null) {
            return null;
        }
        String raw;
        try {
            raw = Files.readString(deliveredMarkerPath).trim();
        } catch (NoSuchFileException e) {
            return null;
        } catch (IOException e) {
            log.warn(
                    "failed to read delivered-marker file {}, starting with no remembered intentId: {}",
                    deliveredMarkerPath,
                    e.toString());
            return null;
        }
        if (raw.isEmpty()) {
            return null;
        }
        try {
            return UUID.fromString(raw);
        } catch (IllegalArgumentException e) {
            log.warn(
                    "delivered-marker file {} does not contain a valid UUID, starting with no remembered"
                            + " intentId: {}",
                    deliveredMarkerPath,
                    e.toString());
            return null;
        }
    }

    /** No-op if {@link #deliveredMarkerPath} is null. A write failure is logged, never thrown -- see {@link #readMarker()}. */
    private void writeMarker(UUID intentId) {
        if (deliveredMarkerPath == null) {
            return;
        }
        try {
            Path parent = deliveredMarkerPath.toAbsolutePath().getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            Files.writeString(deliveredMarkerPath, intentId.toString());
        } catch (IOException e) {
            log.warn(
                    "failed to persist delivered-marker file {} for intentId {} -- a restart before this is"
                            + " fixed could redeliver this signal: {}",
                    deliveredMarkerPath,
                    intentId,
                    e.toString());
        }
    }
}
