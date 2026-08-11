package engine.runtime;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import engine.schemas.SchemaObjectMapper;
import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Durable, single-file JSON store for {@link DailyReportGenerator}'s
 * pending-report retry queue -- GitHub issue #75 ("{@code
 * DailyReportGenerator.pendingReports} is in-memory-only -- a report can be
 * permanently lost on restart"). Mirrors {@link SubmissionMarkerStore}'s
 * established design (Paper Trading Bridge Task H, the first durable,
 * cross-restart persistence in this codebase) deliberately, not a new
 * design from scratch: a single JSON file, loaded fully into memory on
 * construction, rewritten fully on every mutation, atomic writes (temp file
 * + {@code ATOMIC_MOVE}, with a non-atomic-replace fallback for {@link
 * AtomicMoveNotSupportedException}/{@link FileAlreadyExistsException} --
 * the same fallback both {@code SubmissionMarkerStore} and {@link
 * DailyReportGenerator#writeReport} already use). Real production use is
 * expected to hold at most a handful of entries at once (this project's
 * single-symbol, daily-cadence scope, same as {@code SubmissionMarkerStore}'s
 * own reasoning), so "rewrite the whole file on every mutation" is not a
 * real performance concern.
 *
 * <p><b>Ordered, not keyed -- deliberately different from {@code
 * SubmissionMarkerStore}'s map-keyed-by-clientOrderId shape.</b> {@link
 * DailyReportGenerator}'s pending queue is a FIFO: oldest-first delivery is
 * a load-bearing invariant of that class (see its own {@code
 * flushPendingReports} Javadoc and {@code
 * DailyReportGeneratorTest#aStillPendingOlderReportBlocksAYoungerOneFromWritingOutOfOrder}).
 * This store therefore persists an ordered {@code List<DailyReport>} and
 * preserves that order across a reload, rather than de-duplicating or
 * keying by date the way {@code SubmissionMarkerStore} keys by {@code
 * clientOrderId}. In practice at most one {@link DailyReport} per calendar
 * date is ever appended (a {@code DailyReportGenerator} only ever builds
 * one report per day), but this store does not itself assume or enforce
 * that -- it simply persists whatever order it is given.
 *
 * <p><b>Fails SAFE (not closed) on a read/parse failure -- a deliberate,
 * disclosed divergence from {@code SubmissionMarkerStore}'s own fail-closed
 * choice, not an oversight.</b> {@code SubmissionMarkerStore} throws on a
 * corrupt-but-present file because silently starting empty there could let
 * a live order get resubmitted (a real duplicate-order risk). The stakes
 * here point the other way: refusing to start the whole paper-trading
 * process over one corrupt local bookkeeping file would forfeit not just
 * the possibly-already-lost pending report(s) in that file, but every
 * later day's report too -- directly working against the very "no missing
 * daily reports" goal this store exists to serve, and (per {@code
 * PaperTradingApp}'s {@code scheduleAtFixedRate} usage) an uncaught
 * exception escaping this class during a tick would silently cancel all
 * future scheduled ticks, not just this one. So a missing file ({@link
 * NoSuchFileException}) OR any other read/parse failure (corrupt JSON, a
 * path whose parent unexpectedly isn't a directory, etc.) is treated the
 * same way: logged loudly at {@code ERROR} and the in-memory queue starts
 * empty, never thrown. The same reasoning applies to a *write* (persist)
 * failure -- also caught and logged rather than propagated, for the
 * identical "must never crash the scheduler" reason {@link
 * DailyReportGenerator#writeReport} itself already never throws. A persist
 * failure never rolls back the in-memory mutation that triggered it, so
 * this process's own within-lifetime retry behavior (the only guarantee
 * that existed before issue #75) is preserved even when durability itself
 * is degraded (e.g. a disk-full condition) -- durability is a best-effort
 * enhancement layered on top of that pre-existing guarantee, not a
 * replacement for it.
 */
final class PendingDailyReportStore {

    private static final Logger log = LoggerFactory.getLogger(PendingDailyReportStore.class);
    private static final TypeReference<List<DailyReport>> REPORT_LIST_TYPE = new TypeReference<>() {};

    private final Path filePath;
    private final AtomicMover atomicMover;
    private final ObjectMapper mapper = SchemaObjectMapper.create();
    private final List<DailyReport> pending = new ArrayList<>();

    PendingDailyReportStore(Path filePath) {
        this(filePath, PendingDailyReportStore::defaultAtomicMove);
    }

    /**
     * Test-only overload (package-private -- not part of this class's real
     * operational API), mirroring {@code SubmissionMarkerStore}'s and
     * {@code DailyReportGenerator}'s own identical {@code AtomicMover}
     * testability seam: lets a test force the {@code ATOMIC_MOVE} attempt
     * to fail deterministically, without needing real, filesystem-specific
     * conditions that can't be forced portably.
     */
    PendingDailyReportStore(Path filePath, AtomicMover atomicMover) {
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
            return; // nothing pending on disk yet -- an ordinary, expected steady state
        } catch (IOException e) {
            log.error(
                    "failed to read pending daily report file {} -- starting with an empty pending-report queue"
                            + " rather than refusing to start (see GitHub issue #75); any report(s) previously"
                            + " durable in this file may be lost: {}",
                    filePath,
                    e.toString(),
                    e);
            return;
        }
        List<DailyReport> loaded;
        try {
            loaded = mapper.readValue(raw, REPORT_LIST_TYPE);
        } catch (IOException e) {
            log.error(
                    "failed to parse pending daily report file {} as a report list -- starting with an empty"
                            + " pending-report queue rather than refusing to start (see GitHub issue #75); any"
                            + " report(s) previously durable in this file may be lost: {}",
                    filePath,
                    e.toString(),
                    e);
            return;
        }
        // A real CodeRabbit review finding on this task's own PR: a file
        // containing literal JSON `null` parses successfully (Jackson maps
        // it to a null List, not a parse exception) -- pending.addAll(null)
        // would then throw a NullPointerException straight out of this
        // constructor, defeating this class's own "fails safe, never
        // throws" contract for exactly the corrupt-input case it exists to
        // guard against. Likewise a well-formed array containing a null
        // element (e.g. `[null]`) parses to a list containing a null
        // DailyReport, which would NPE later (persist()'s own
        // serialization, or a caller like DailyReportGenerator reading
        // pending.date()) rather than here. Both are treated the same as
        // any other malformed input: logged, empty queue, never thrown.
        if (loaded == null || loaded.stream().anyMatch(Objects::isNull)) {
            log.error(
                    "pending daily report file {} parsed to a null list or contained a null entry -- starting with"
                            + " an empty pending-report queue rather than risking a NullPointerException later (see"
                            + " GitHub issue #75); any report(s) previously durable in this file may be lost",
                    filePath);
            return;
        }
        pending.addAll(loaded);
    }

    /** Appends {@code report} to the end of the durable queue, then persists immediately (best-effort, see class Javadoc). */
    synchronized void append(DailyReport report) {
        Objects.requireNonNull(report, "report is required");
        pending.add(report);
        persist();
    }

    /** Removes the oldest (first) entry, then persists immediately (best-effort). A no-op if the queue is already empty. */
    synchronized void removeOldest() {
        if (!pending.isEmpty()) {
            pending.remove(0);
            persist();
        }
    }

    /** Every report currently pending, oldest first. A snapshot copy -- mutating it has no effect on this store. */
    synchronized List<DailyReport> all() {
        return List.copyOf(pending);
    }

    private void persist() {
        try {
            Path parent = filePath.toAbsolutePath().getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            Path tmp = filePath.resolveSibling(filePath.getFileName() + ".tmp");
            Files.writeString(tmp, mapper.writerWithDefaultPrettyPrinter().writeValueAsString(pending));
            try {
                atomicMover.move(tmp, filePath);
            } catch (AtomicMoveNotSupportedException | FileAlreadyExistsException e) {
                // Same fallback DailyReportGenerator/SubmissionMarkerStore
                // already established for the identical class of failure
                // (some filesystems/cross-volume setups don't support
                // ATOMIC_MOVE at all, or implementation-specifically reject
                // an existing target rather than replace it) -- not atomic,
                // but strictly better than a permanent failure loop.
                log.warn(
                        "atomic move not usable for {} -> {}, falling back to a non-atomic replace: {}",
                        tmp,
                        filePath,
                        e.toString());
                Files.move(tmp, filePath, StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (IOException e) {
            // Deliberately swallowed, not rethrown -- see class Javadoc's
            // "Fails SAFE" section. The in-memory queue this mutation just
            // updated is unaffected; only the durable copy may now be
            // stale until a future persist() succeeds.
            log.error(
                    "failed to persist pending daily report file {} -- the in-memory retry queue is unaffected, but"
                            + " this entry is NOT durably recorded until a future persist succeeds (a process"
                            + " restart before then would lose it -- see GitHub issue #75): {}",
                    filePath,
                    e.toString(),
                    e);
        }
    }
}
