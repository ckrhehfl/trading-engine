package engine.runtime;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.io.IOException;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * A cross-<b>process</b> mutex over a single lock file, protecting reads
 * and read-modify-write cycles against the shared {@link AccountLedger}
 * (via {@link AccountLedgerStore}) once multiple independent {@code
 * kis-paper} OS processes share one real KIS account -- see the governing
 * plan's "Shared KIS account risk ledger" section, "3. Cross-process
 * concurrency mechanism" for the full reasoning behind the design below.
 * <b>Standalone and unwired in Task B</b> -- nothing in this codebase
 * calls {@link #acquire} yet; {@code SharedKisAccountLedger} (Task C) is
 * the first real caller.
 *
 * <p><b>Primitive: atomic file creation ({@link Files#createFile}), not
 * {@link java.nio.channels.FileLock}.</b> This decision was made and
 * reasoned through before this class was written (see the governing
 * plan), not re-derived here -- restated briefly because it is the single
 * most load-bearing choice in this file: this repository lives on a
 * Windows-mounted drive inside WSL2 ({@code /mnt/c/...}), a 9p/drvfs
 * mount, not native ext4 (confirmed directly: {@code mount | grep
 * /mnt/c} reports {@code type 9p ... aname=drvfs}) -- {@code FileLock}'s
 * byte-range advisory locking has real, unverified reliability risk on a
 * mount like that. This codebase already trusts the same family of
 * primitive (atomic create/rename) everywhere durable state exists
 * ({@link AccountLedgerStore}, {@code SubmissionMarkerStore}, {@code
 * DailyReportGenerator}, {@code FileSignalSource}) -- {@link
 * #acquire}/{@link #close} extends that proven pattern to mutual
 * exclusion rather than introducing a new primitive family. {@link
 * AccountLedgerLockMultiProcessTest} is the real, second-JVM proof this
 * actually holds on this repository's real filesystem, not merely an
 * assumption -- see that test's own Javadoc.
 *
 * <p><b>Lock file content</b>: JSON {@code {"pid": <long>, "hostname":
 * <string>, "acquiredAt": <ISO-8601 instant>}} -- written immediately
 * after the atomic create succeeds. {@code pid}/{@code hostname} exist
 * only to support the staleness check below (attribution/debugging), not
 * as a correctness input to mutual exclusion itself -- exclusivity comes
 * entirely from {@link Files#createFile}'s own atomicity.
 *
 * <p><b>Contention and staleness</b>: on {@link FileAlreadyExistsException}
 * from {@link Files#createFile}, this class reads the existing lock
 * file's metadata and checks whether it is <i>stale</i> -- either
 * condition is sufficient: (1) {@code
 * ProcessHandle.of(pid).map(ProcessHandle::isAlive).orElse(false)} is
 * {@code false} (the holder is provably dead -- meaningful because every
 * process sharing this lock runs on the same host, per this project's
 * single-VPS/single-laptop deployment model), or (2) {@code
 * acquiredAt} is older than {@code staleThreshold} (a generous backstop
 * against PID reuse producing a false "still alive" reading). A genuine
 * steal is <b>never silent</b> -- logged at {@code ERROR}, matching this
 * module's own established "never silently resolve an ambiguous
 * cross-process situation" convention (see {@code
 * SubmissionMarkerResolver}'s Javadoc for the same principle applied to
 * an unresolved order-submission marker) -- then the file is re-verified
 * to still hold the exact metadata just judged stale (see {@link
 * #tryStealIfStale}'s own Javadoc for a real, measured race this closes,
 * found by this task's own required real-process test) and, only if so,
 * deleted; {@link #acquire} then retries {@link Files#createFile}
 * immediately, without backing off (the staleness is already known, no
 * reason to wait). A non-stale (live, recent) holder instead makes {@link
 * #acquire} back off with short, bounded exponential retry (~25ms
 * &rarr; 250ms per attempt) until {@code totalRetryBudget} is exhausted,
 * at which point it throws {@link IllegalStateException} -- this class
 * never hangs indefinitely.
 *
 * <p><b>Deviation from the governing plan's own literal code sketch</b>:
 * the plan's summary writes {@code Files.createFile(path,
 * StandardOpenOption.CREATE_NEW)} -- {@link Files#createFile} does not
 * actually accept a {@link java.nio.file.StandardOpenOption} argument (it
 * takes {@link java.nio.file.attribute.FileAttribute} varargs instead,
 * and is already atomic/exclusive-create by itself, throwing {@link
 * FileAlreadyExistsException} if the target exists -- no {@code
 * CREATE_NEW} option is needed or accepted for that specific call). This
 * class uses the real, correct API ({@code Files.createFile(lockPath)})
 * to get the identical atomic-create-fails-if-exists semantics the plan's
 * sketch intended.
 */
final class AccountLedgerLock implements AutoCloseable {

    private static final Logger log = LoggerFactory.getLogger(AccountLedgerLock.class);

    /** Own copy, not shared with {@link AccountLedgerStore} -- see that class's Javadoc for why. */
    private static final ObjectMapper MAPPER = new ObjectMapper()
            .registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);

    private static final long INITIAL_BACKOFF_MILLIS = 25;
    private static final long MAX_BACKOFF_MILLIS = 250;

    private final Path lockPath;

    private AccountLedgerLock(Path lockPath) {
        this.lockPath = lockPath;
    }

    /** The lock file's own JSON shape -- see class Javadoc. */
    record LockMetadata(long pid, String hostname, Instant acquiredAt) {}

    /**
     * Blocks (via bounded, backing-off retry -- never a real thread block)
     * until {@code lockPath} is exclusively created by this call, a stale
     * existing lock is stolen, or {@code totalRetryBudget} is exhausted.
     * Returns a real {@link AccountLedgerLock} usable in a try-with-
     * resources block; {@link #close} deletes the lock file.
     *
     * @throws IllegalStateException if {@code totalRetryBudget} elapses
     *     without ever acquiring the lock -- names {@code lockPath} and the
     *     elapsed time, never hangs past the given budget.
     */
    static AccountLedgerLock acquire(Path lockPath, Duration staleThreshold, Duration totalRetryBudget) {
        Objects.requireNonNull(lockPath, "lockPath is required");
        Objects.requireNonNull(staleThreshold, "staleThreshold is required");
        Objects.requireNonNull(totalRetryBudget, "totalRetryBudget is required");

        long startNanos = System.nanoTime();
        long budgetNanos = totalRetryBudget.toNanos();
        long backoffMillis = INITIAL_BACKOFF_MILLIS;

        while (true) {
            long elapsedNanos = System.nanoTime() - startNanos;
            if (elapsedNanos >= budgetNanos) {
                throw new IllegalStateException(
                        "failed to acquire account ledger lock " + lockPath + " within retry budget "
                                + totalRetryBudget + " (elapsed " + Duration.ofNanos(elapsedNanos) + ")");
            }

            try {
                createAndWriteMetadata(lockPath);
                return new AccountLedgerLock(lockPath);
            } catch (FileAlreadyExistsException e) {
                if (tryStealIfStale(lockPath, staleThreshold)) {
                    continue; // steal already known-stale -- retry create immediately, no backoff
                }
                sleepQuietly(backoffMillis);
                backoffMillis = Math.min(MAX_BACKOFF_MILLIS, backoffMillis * 2);
            } catch (IOException e) {
                throw new IllegalStateException("failed to create account ledger lock file " + lockPath, e);
            }
        }
    }

    private static void createAndWriteMetadata(Path lockPath) throws IOException {
        Path parent = lockPath.toAbsolutePath().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        Files.createFile(lockPath); // atomic -- throws FileAlreadyExistsException if it already exists
        LockMetadata metadata = new LockMetadata(ProcessHandle.current().pid(), hostname(), Instant.now());
        Files.writeString(lockPath, MAPPER.writeValueAsString(metadata));
    }

    /**
     * Returns {@code true} if a stale lock was found and deleted (caller
     * should retry {@link Files#createFile} immediately), {@code false} if
     * the lock is live/recent (caller should back off) or its metadata
     * could not be read right now.
     *
     * <p><b>Real TOCTOU finding from this task's own required real-second-
     * JVM test, fixed here rather than merely disclosed</b> (see {@link
     * AccountLedgerLockMultiProcessTest}'s Javadoc for the full account of
     * how this was found): reading this lock's metadata and judging it
     * stale is <b>not instantaneous</b> -- real, repeatable measurements
     * on this project's actual drvfs mount under genuine multi-process
     * contention showed gaps of 500ms or more between a successful {@link
     * Files#readString} and this method's own staleness verdict (traced
     * to real, individual {@link Files#writeString} calls on this
     * filesystem taking that long under concurrent access to the same
     * path -- a real filesystem-latency characteristic of this mount, not
     * a bug in the JDK's process APIs, which a dedicated diagnostic
     * confirmed behave correctly here). A gap that size is easily long
     * enough for the <i>original</i> lock holder to legitimately finish
     * its own critical section, delete the file itself, and for a
     * <b>different</b> process to legitimately acquire a brand new lock
     * generation at the same path -- all before this method gets around
     * to actually deleting anything. Unconditionally deleting {@code
     * lockPath} at that point -- the original implementation's behavior --
     * would delete that new, live, legitimately-held lock out from under
     * its rightful current holder, breaking mutual exclusion. Confirmed
     * as the actual, sole root cause of a real observed lost update (a
     * 4-process, 20-increment run producing a final count of 19, traced
     * via added timestamped diagnostics to exactly this sequence) before
     * this fix -- not a theoretical concern.
     *
     * <p><b>Fix</b>: re-read the file immediately before deleting and only
     * delete if its content is still exactly the {@link LockMetadata} just
     * judged stale ({@code equals()} on a record -- pid, hostname, and
     * {@code acquiredAt} to nanosecond precision all match). This shrinks
     * the race window from "however long staleness evaluation takes" (500ms+,
     * observed) down to the gap between two adjacent file operations
     * (sub-millisecond, and itself further guarded: {@link
     * Files#delete}'s own {@link NoSuchFileException} is tolerated as
     * "someone else already reclaimed it," the same acceptable-small-race
     * convention this codebase already applies elsewhere, e.g. {@code
     * AccountLedgerStore}'s temp-file-then-move pattern). If the re-read
     * shows different content (a new, different generation -- or the file
     * is simply gone), this method does <b>not</b> delete: it returns
     * {@code false} (gone would ordinarily mean "safe to retry
     * immediately," but by the time re-verification finds it gone rather
     * than the NoSuchFileException path above having caught it directly,
     * treating it as ordinary contention and letting the caller's normal
     * backoff-and-recheck loop handle it is simpler and equally safe) so
     * the caller re-evaluates fresh on its next attempt rather than ever
     * acting on stale information about what the file currently holds.
     */
    private static boolean tryStealIfStale(Path lockPath, Duration staleThreshold) {
        LockMetadata metadata = readMetadataOrNull(lockPath);
        if (metadata == null) {
            // Vanished between our failed create and this read -- another
            // waiter already released or stole it. Safe (and correct) to
            // report "stale" here: it tells the caller to retry the
            // create loop immediately rather than back off waiting on a
            // holder that's already gone.
            return true;
        }
        if (metadata == EMPTY_OR_UNPARSEABLE) {
            // Empty or partially-written content -- we most likely raced
            // the real holder's own create-then-write-metadata window
            // (see createAndWriteMetadata). Cannot determine staleness
            // right now; never guess. Reported as "not stale" so the
            // caller backs off briefly and retries -- by then the
            // holder's write will have completed.
            return false;
        }

        boolean holderDead = !ProcessHandle.of(metadata.pid()).map(ProcessHandle::isAlive).orElse(false);
        boolean expired = Duration.between(metadata.acquiredAt(), Instant.now()).compareTo(staleThreshold) > 0;
        if (!holderDead && !expired) {
            return false; // live, recent holder -- not stale
        }

        log.error(
                "stealing account ledger lock {} -- recorded holder pid={} hostname={} acquiredAt={}"
                        + " (holderProvablyDead={}, acquiredAtOlderThanStaleThreshold={}, staleThreshold={})."
                        + " This is a real cross-process safety event, not routine contention -- investigate"
                        + " whether the prior holder crashed or is merely slow.",
                lockPath,
                metadata.pid(),
                metadata.hostname(),
                metadata.acquiredAt(),
                holderDead,
                expired,
                staleThreshold);

        // Re-verify immediately before deleting -- see method Javadoc's
        // "Real TOCTOU finding" for why this re-check is load-bearing, not
        // defensive-programming boilerplate.
        LockMetadata current = readMetadataOrNull(lockPath);
        if (current == null || current == EMPTY_OR_UNPARSEABLE || !current.equals(metadata)) {
            return false;
        }

        try {
            Files.delete(lockPath);
        } catch (NoSuchFileException e) {
            // Another waiter beat us to stealing it -- fine, we'll simply
            // contend for the now-vacant (or newly-recreated) file
            // normally on the next loop iteration.
        } catch (IOException e) {
            throw new IllegalStateException("failed to delete stale account ledger lock file " + lockPath, e);
        }
        return true;
    }

    /**
     * Sentinel distinguishing "file present but empty/unparseable right
     * now" from a genuine {@code null} ("file absent") -- see {@link
     * #readMetadataOrNull}.
     */
    private static final LockMetadata EMPTY_OR_UNPARSEABLE = new LockMetadata(-1, "", Instant.EPOCH);

    /**
     * @return the lock file's parsed metadata; {@code null} if the file
     *     does not exist; {@link #EMPTY_OR_UNPARSEABLE} if it exists but its
     *     content can't be read/parsed right now (see {@link
     *     #tryStealIfStale}'s Javadoc for why this is treated as "cannot
     *     determine, don't guess" rather than either extreme).
     */
    private static LockMetadata readMetadataOrNull(Path lockPath) {
        String raw;
        try {
            raw = Files.readString(lockPath);
        } catch (NoSuchFileException e) {
            return null;
        } catch (IOException e) {
            return EMPTY_OR_UNPARSEABLE;
        }
        try {
            return MAPPER.readValue(raw, LockMetadata.class);
        } catch (IOException e) {
            return EMPTY_OR_UNPARSEABLE;
        }
    }

    private static String hostname() {
        try {
            return InetAddress.getLocalHost().getHostName();
        } catch (UnknownHostException e) {
            // hostname is attribution/debugging only, never a correctness
            // input (see class Javadoc) -- not worth failing acquisition
            // over.
            return "unknown-host";
        }
    }

    private static void sleepQuietly(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted while waiting to acquire account ledger lock", e);
        }
    }

    /**
     * Deletes the lock file, releasing it for the next waiter. Tolerates
     * {@link NoSuchFileException} (shouldn't happen under correct use --
     * this instance is the only thing that should have created it and
     * nothing else should have deleted it out from under a live holder --
     * but a missing file at close time is not itself evidence of a
     * problem worth failing over). Any other deletion failure is logged
     * loudly rather than thrown: {@link AutoCloseable#close()} must not
     * mask whatever happened inside the caller's {@code try} block by
     * throwing here, but a lock file this class fails to delete will
     * block every other waiter until {@code staleThreshold} elapses, so
     * this is a real operational problem, not a silent no-op.
     */
    @Override
    public void close() {
        try {
            Files.delete(lockPath);
        } catch (NoSuchFileException e) {
            // tolerated -- see method Javadoc
        } catch (IOException e) {
            log.error(
                    "failed to delete account ledger lock file {} on close -- it will block other waiters until"
                            + " its staleThreshold elapses: {}",
                    lockPath,
                    e.toString());
        }
    }
}
