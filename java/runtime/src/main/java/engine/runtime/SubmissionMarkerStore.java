package engine.runtime;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Durable, single-file JSON store for {@link SubmissionMarker}s, keyed by
 * {@code clientOrderId} -- the persistence layer behind {@link
 * PersistentSubmissionOrderExecutor}'s {@code SUBMISSION_UNKNOWN} handling.
 * The <b>first piece of cross-restart persistence in this codebase</b>
 * ({@code OrderStore}/{@code PaperBroker}/{@code ExchangeOrderExecutor} are
 * all in-memory only) -- deliberately kept as narrow as this one use case
 * needs (a single JSON file, loaded fully into memory, rewritten fully on
 * every mutation) rather than a general persistence framework. Real
 * production use is expected to hold at most a handful of entries at once
 * (this project's single-symbol, daily-cadence scope), so "rewrite the
 * whole file on every mutation" is not a real performance concern.
 *
 * <p>Tolerant of a missing or corrupt file at construction time -- both are
 * treated as "no markers recorded yet," logged (corrupt only) rather than
 * thrown, matching {@link FileSignalSource}'s own established tolerance
 * convention for a file this process doesn't fully control the lifecycle
 * of. A corrupt file is <b>not</b> overwritten by this tolerant read alone
 * -- it is only overwritten the next time {@link #record}/{@link #clear} is
 * actually called, so a corrupt-but-inspectable file is not silently
 * destroyed by mere construction.
 *
 * <p>All public methods are {@code synchronized} -- this store is expected
 * to be read/written from a single process's startup/tick path, not a
 * high-concurrency hot path, so simple mutual exclusion (matching, in
 * spirit, {@code TradingLoop#tick()}'s own {@code synchronized} method) is
 * sufficient and keeps the on-disk file and in-memory map from ever
 * diverging under a concurrent caller.
 */
final class SubmissionMarkerStore {

    private static final Logger log = LoggerFactory.getLogger(SubmissionMarkerStore.class);
    private static final TypeReference<List<SubmissionMarker>> MARKER_LIST_TYPE = new TypeReference<>() {};

    private final Path filePath;
    private final ObjectMapper mapper = new ObjectMapper();
    private final Map<UUID, SubmissionMarker> markers = new LinkedHashMap<>();

    SubmissionMarkerStore(Path filePath) {
        this.filePath = Objects.requireNonNull(filePath, "filePath is required");
        load();
    }

    private void load() {
        String raw;
        try {
            raw = Files.readString(filePath);
        } catch (NoSuchFileException e) {
            return; // no markers recorded yet -- an ordinary, expected steady state
        } catch (IOException e) {
            log.warn("failed to read submission marker file {}, treating as empty: {}", filePath, e.toString());
            return;
        }
        List<SubmissionMarker> loaded;
        try {
            loaded = mapper.readValue(raw, MARKER_LIST_TYPE);
        } catch (IOException e) {
            log.warn(
                    "failed to parse submission marker file {} as a marker list, treating as empty: {}",
                    filePath,
                    e.toString());
            return;
        }
        for (SubmissionMarker marker : loaded) {
            markers.put(marker.clientOrderId(), marker);
        }
    }

    /** Records (or overwrites, if already present) a marker for {@code clientOrderId}, then persists immediately. */
    synchronized void record(UUID clientOrderId, String symbol) {
        Objects.requireNonNull(clientOrderId, "clientOrderId is required");
        Objects.requireNonNull(symbol, "symbol is required");
        markers.put(clientOrderId, new SubmissionMarker(clientOrderId, symbol, Instant.now().toString()));
        persist();
    }

    /** Removes the marker for {@code clientOrderId} if one exists, then persists immediately. A no-op if none exists. */
    synchronized void clear(UUID clientOrderId) {
        Objects.requireNonNull(clientOrderId, "clientOrderId is required");
        if (markers.remove(clientOrderId) != null) {
            persist();
        }
    }

    /** Every marker currently recorded, in insertion order. A snapshot copy -- mutating it has no effect on this store. */
    synchronized List<SubmissionMarker> all() {
        return List.copyOf(markers.values());
    }

    private void persist() {
        try {
            Path parent = filePath.toAbsolutePath().getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            Files.writeString(filePath, mapper.writeValueAsString(List.copyOf(markers.values())));
        } catch (IOException e) {
            // Deliberately propagates -- unlike a missing/corrupt *read*
            // (tolerated, see load()), a failed *write* means the caller's
            // just-recorded (or just-cleared) marker may not actually be
            // durable, which defeats the entire point of this class. A
            // caller must know that happened, not silently proceed as if
            // persistence succeeded.
            throw new IllegalStateException("failed to persist submission marker file " + filePath, e);
        }
    }
}
