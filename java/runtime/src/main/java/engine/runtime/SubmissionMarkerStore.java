package engine.runtime;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
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
 * <p><b>Fails closed on a read/parse failure -- revised after a real,
 * correctly-identified CodeRabbit review finding on this PR.</b> Only a
 * genuinely <i>missing</i> file ({@link NoSuchFileException}) is treated as
 * "no markers recorded yet" (an ordinary, expected steady state). Any other
 * read failure or JSON parse failure now throws ({@link
 * IllegalStateException}) rather than silently starting empty -- a corrupt-
 * but-present marker file could otherwise silently discard real,
 * still-unresolved {@code SUBMISSION_UNKNOWN} state, letting the process
 * start believing nothing needs review when the opposite is true. This
 * mirrors this class's own already-existing write-failure convention
 * (below) rather than introducing a new one.
 *
 * <p><b>Writes are atomic</b> (temp file + {@code ATOMIC_MOVE}, with a
 * non-atomic-replace fallback for {@link AtomicMoveNotSupportedException}/
 * {@link FileAlreadyExistsException}) -- the exact same {@code .tmp}-then-
 * move convention {@code DailyReportGenerator} already established in this
 * codebase (matched deliberately, not reinvented), so a process crash
 * mid-write can never leave a half-written, corrupt file behind for the
 * next start's now-fail-closed {@link #load()} to (correctly) refuse to
 * proceed past.
 */
final class SubmissionMarkerStore {

    private static final Logger log = LoggerFactory.getLogger(SubmissionMarkerStore.class);
    private static final TypeReference<List<SubmissionMarker>> MARKER_LIST_TYPE = new TypeReference<>() {};

    private final Path filePath;
    private final AtomicMover atomicMover;
    private final ObjectMapper mapper = new ObjectMapper();
    private final Map<UUID, SubmissionMarker> markers = new LinkedHashMap<>();

    SubmissionMarkerStore(Path filePath) {
        this(filePath, SubmissionMarkerStore::defaultAtomicMove);
    }

    /**
     * Test-only overload (package-private -- not part of this class's real
     * operational API), mirroring {@code DailyReportGenerator}'s own
     * identical {@code AtomicMover} testability seam: lets a test force the
     * {@code ATOMIC_MOVE} attempt to fail deterministically, without needing
     * real, filesystem-specific conditions that can't be forced portably.
     */
    SubmissionMarkerStore(Path filePath, AtomicMover atomicMover) {
        this.filePath = Objects.requireNonNull(filePath, "filePath is required");
        this.atomicMover = Objects.requireNonNull(atomicMover, "atomicMover is required");
        load();
    }

    private static void defaultAtomicMove(Path source, Path target) throws IOException {
        Files.move(source, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    }

    /** Testability seam -- see the 2-arg constructor's Javadoc above. */
    @FunctionalInterface
    interface AtomicMover {
        void move(Path source, Path target) throws IOException;
    }

    private void load() {
        String raw;
        try {
            raw = Files.readString(filePath);
        } catch (NoSuchFileException e) {
            return; // no markers recorded yet -- an ordinary, expected steady state
        } catch (IOException e) {
            throw new IllegalStateException(
                    "failed to read submission marker file " + filePath + " -- refusing to start with unknown"
                            + " SUBMISSION_UNKNOWN state rather than silently treating it as empty",
                    e);
        }
        List<SubmissionMarker> loaded;
        try {
            loaded = mapper.readValue(raw, MARKER_LIST_TYPE);
        } catch (IOException e) {
            throw new IllegalStateException(
                    "failed to parse submission marker file " + filePath + " as a marker list -- refusing to"
                            + " start with unknown SUBMISSION_UNKNOWN state rather than silently treating it as"
                            + " empty",
                    e);
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
            Path tmp = filePath.resolveSibling(filePath.getFileName() + ".tmp");
            Files.writeString(tmp, mapper.writeValueAsString(List.copyOf(markers.values())));
            try {
                atomicMover.move(tmp, filePath);
            } catch (AtomicMoveNotSupportedException | FileAlreadyExistsException e) {
                // Same fallback DailyReportGenerator already established for
                // the identical class of failure (some filesystems/cross-
                // volume setups don't support ATOMIC_MOVE at all, or
                // implementation-specifically reject an existing target
                // rather than replace it) -- not atomic, but strictly
                // better than a permanent failure loop.
                log.warn(
                        "atomic move not usable for {} -> {}, falling back to a non-atomic replace: {}",
                        tmp,
                        filePath,
                        e.toString());
                Files.move(tmp, filePath, StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (IOException e) {
            // Deliberately propagates -- unlike a missing/corrupt *read*
            // (fails closed via load(), see class Javadoc), a failed
            // *write* means the caller's just-recorded (or just-cleared)
            // marker may not actually be durable, which defeats the entire
            // point of this class. A caller must know that happened, not
            // silently proceed as if persistence succeeded.
            throw new IllegalStateException("failed to persist submission marker file " + filePath, e);
        }
    }
}
