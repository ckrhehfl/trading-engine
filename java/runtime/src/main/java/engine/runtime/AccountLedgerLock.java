package engine.runtime;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.io.IOException;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
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
 * <p>Full design history, every empirical finding, and the real
 * multi-process/CodeRabbit-review record behind the decisions below live
 * in {@code .planning/kis-ledger-b-account-ledger-store-lock.md}, not
 * here -- this Javadoc documents this class's current behavioral
 * contract only.
 *
 * <p><b>Primitive: atomic file creation, not {@link
 * java.nio.channels.FileLock}.</b> This repository lives on a
 * Windows-mounted drive inside WSL2 ({@code /mnt/c/...}), a 9p/drvfs
 * mount, not native ext4 (confirmed directly: {@code mount | grep
 * /mnt/c} reports {@code type 9p ... aname=drvfs}) -- {@code FileLock}'s
 * byte-range advisory locking has real, unverified reliability risk on a
 * mount like that. This codebase already trusts the same family of
 * primitive (atomic create/rename) everywhere durable state exists
 * ({@link AccountLedgerStore}, {@code SubmissionMarkerStore}, {@code
 * DailyReportGenerator}, {@code FileSignalSource}) -- {@link
 * #acquire}/{@link #close} extends that proven pattern to mutual
 * exclusion rather than introducing a new primitive family. The atomic
 * create-and-write step is a single {@link Files#newByteChannel} call
 * with {@link java.nio.file.StandardOpenOption#CREATE_NEW}, with the
 * metadata written through that same open handle -- not two separate,
 * path-based operations (see {@link #createAndWriteMetadata}'s own
 * Javadoc for why that distinction matters). {@code
 * AccountLedgerLockMultiProcessTest} is the real, second-JVM proof this
 * actually holds on this repository's real filesystem, not merely an
 * assumption -- see that test's own Javadoc.
 *
 * <p><b>Lock file content</b>: JSON {@code {"pid": <long>, "hostname":
 * <string>, "acquiredAt": <ISO-8601 instant>}} -- written through the
 * same open handle the atomic create itself returns. {@code pid}/{@code
 * hostname} exist only to support the staleness check below
 * (attribution/debugging), not as a correctness input to mutual exclusion
 * itself -- exclusivity comes entirely from the atomic create's own
 * exclusivity ({@link FileAlreadyExistsException} if the target already
 * exists).
 *
 * <p><b>Contention and staleness</b>: on {@link FileAlreadyExistsException}
 * from the atomic create, this class reads the existing lock file's
 * metadata and checks whether it is <i>stale</i> -- either condition is
 * sufficient: (1) the recorded {@code hostname} matches this host's own
 * <i>and</i> {@code
 * ProcessHandle.of(pid).map(ProcessHandle::isAlive).orElse(false)} is
 * {@code false} (the holder is provably dead -- meaningful because every
 * process sharing this lock runs on the same host, per this project's
 * single-VPS/single-laptop deployment model; a {@code hostname} mismatch
 * -- a future multi-host deployment, or a misconfiguration -- leaves the
 * pid-liveness result untrusted rather than guessed, since a genuinely
 * foreign pid would almost always read as "not found" on this host
 * regardless of whether its real holder is alive), or (2) {@code
 * acquiredAt} is older than {@code staleThreshold} (a generous backstop
 * against PID reuse producing a false "still alive" reading, and the
 * only path that can reclaim a stale lock recorded by a different host).
 * A genuine
 * steal is <b>never silent</b> -- logged at {@code ERROR}, matching this
 * module's own established "never silently resolve an ambiguous
 * cross-process situation" convention (see {@code
 * SubmissionMarkerResolver}'s Javadoc for the same principle applied to
 * an unresolved order-submission marker) -- then the file is re-verified
 * to still hold the exact metadata just judged stale (see {@link
 * #tryStealIfStale}'s own Javadoc for the race this closes) and, only if
 * so, deleted; {@link #acquire} then retries the atomic create
 * immediately, without backing off (the staleness is already known, no
 * reason to wait). A non-stale (live, recent) holder instead makes
 * {@link #acquire} back off with short, bounded exponential retry (~25ms
 * &rarr; 250ms per attempt) until {@code totalRetryBudget} is exhausted,
 * at which point it throws {@link IllegalStateException} -- this class
 * never hangs indefinitely.
 *
 * <p><b>Caller contract: {@code staleThreshold} must be chosen
 * comfortably larger than the longest legitimate critical section this
 * lock will ever protect.</b> {@link #acquire}'s own steal logic steals a
 * lock whose {@code acquiredAt} exceeds {@code staleThreshold}, regardless
 * of whether the holder is provably dead or merely still legitimately
 * working. If a real holder's own critical section legitimately takes
 * longer than {@code staleThreshold}, a waiter will steal its still-live
 * lock out from under it, and both processes end up concurrently "inside
 * the lock" from their own perspective -- a real mutual-exclusion
 * violation, not a bug in this class's own logic (empirically
 * demonstrated and reproduced deterministically by {@code
 * AccountLedgerLockTest#aStaleThresholdShorterThanARealHoldersOwnCriticalSectionCausesARealMutualExclusionViolation}).
 * Deliberately <b>not</b> guarded by an enforced minimum {@code
 * staleThreshold} inside this class: the real minimum any given
 * deployment needs depends entirely on how long <i>that deployment's own
 * real critical sections</i> can legitimately run, a property only a
 * caller (this class's own Task C, not yet built) can know -- inventing
 * an arbitrary constant here, unmoored from any real caller's actual
 * usage, would be worse than stating the real requirement plainly. This
 * project's own proposed real default (~30s, per the governing plan)
 * already has enormous headroom over any critical section this lock is
 * actually expected to protect.
 *
 * <p><b>A second, separate part of the same caller contract: {@code
 * staleThreshold} must also be chosen comfortably larger than the real,
 * measured mtime precision of the mount hosting the lock file.</b>
 * {@link #tryStealIfAbandonedEmpty}'s own re-verify-immediately-before-
 * delete check relies on a stolen generation's file having a
 * <i>different</i> last-modified time than the abandoned one it replaced
 * -- if the mount's mtime precision is coarser than {@code
 * staleThreshold}, a deleted-then-recreated file could resolve to the
 * same mtime as the generation it replaced, defeating that
 * re-verification. This is a genuinely separate condition from the
 * critical-section-duration contract above -- satisfying one does not
 * automatically satisfy the other (e.g. a 100ms critical section and a
 * 500ms {@code staleThreshold} clears the first contract comfortably
 * while still being smaller than a coarse, e.g. 1-second, mtime
 * precision). Measured directly against this repository's own real
 * drvfs mount before this class was written (200 real writes in a tight
 * loop, 200/200 distinct mtimes, sub-3ms precision observed) -- not a
 * real risk at this project's own realistic {@code staleThreshold}
 * values, but a real, general requirement worth stating explicitly for
 * any future deployment on a different, coarser-mtime-precision mount.
 * This project's own proposed real default (~30s) already satisfies
 * this requirement by a wide margin on any filesystem with second-level
 * or finer mtime precision.
 *
 * <p><b>A third, separate part of the same caller contract: a single
 * instance must only ever be used and closed by the thread that acquired
 * it.</b> {@link #closed} is a plain, non-volatile field, mutated only in
 * {@link #close}. If a single instance were shared across threads (this
 * codebase has no such caller today -- confirmed, not merely assumed),
 * one thread could fail to observe another thread's already-{@code true}
 * {@link #closed} value under the Java Memory Model, letting {@link
 * #close}'s own idempotency guard run twice for real. A second,
 * genuinely-executed {@code doClose} could then observe a sibling
 * process's own, legitimately-since-acquired generation at {@code
 * lockPath} and log a false "mutual exclusion was lost" {@code ERROR} --
 * precisely the misleading-log-noise problem {@link #closed}'s own
 * idempotency guard exists to prevent in the first place. Not a defect in
 * this class as currently used; stated explicitly so a future caller
 * (Task C) never introduces cross-thread sharing without knowing this
 * contract exists.
 *
 * <p><b>A fourth, separate part of the same caller contract: {@link
 * #close} does not itself signal success or failure, so a caller wanting
 * the lock file genuinely released must call {@code close()} again when
 * that has not yet happened.</b> {@code close()} returns {@code void};
 * whether a given call actually deleted {@code lockPath} or instead took
 * one of {@link #doClose}'s retryable paths (a {@link #READ_FAILED}
 * re-verification read, {@link #EMPTY_OR_UNPARSEABLE} content, or any
 * {@link IOException} other than {@link NoSuchFileException} from the
 * delete itself -- see {@link #close}'s own Javadoc for the full
 * final-vs-retryable distinction) is currently observable only via this
 * class's own {@code ERROR}-level logging, not any return value or
 * exception. A caller with a real reason to confirm release (rather than
 * simply moving on, which is safe -- an un-released lock only delays a
 * future waiter until {@code staleThreshold}, it never causes incorrect
 * behavior) must call {@code close()} again rather than assume the first
 * call was final.
 *
 * <p><b>Deviation from the governing plan's own literal code sketch</b>:
 * the plan's summary writes {@code Files.createFile(path,
 * StandardOpenOption.CREATE_NEW)} -- {@link Files#createFile} does not
 * actually accept a {@link java.nio.file.StandardOpenOption} argument (it
 * takes {@link java.nio.file.attribute.FileAttribute} varargs instead).
 * This class instead uses {@link Files#newByteChannel} with {@code
 * CREATE_NEW}, which gets the identical atomic-create-fails-if-exists
 * semantics the plan's sketch intended, so this remains a deviation in
 * API choice only, not in the semantics the plan actually asked for.
 */
final class AccountLedgerLock implements AutoCloseable {

    private static final Logger log = LoggerFactory.getLogger(AccountLedgerLock.class);

    /**
     * Own copy, not shared with {@link AccountLedgerStore} -- see that
     * class's Javadoc for why. {@code FAIL_ON_TRAILING_TOKENS} enabled
     * here too, proactively, applying the identical reasoning {@code
     * AccountLedgerStore} already applies to its own JSON parsing
     * (Jackson silently ignores anything after the first complete JSON
     * value by default) to this class's own {@link LockMetadata} parsing.
     */
    private static final ObjectMapper MAPPER = new ObjectMapper()
            .registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);

    private static final long INITIAL_BACKOFF_MILLIS = 25;
    private static final long MAX_BACKOFF_MILLIS = 250;

    /**
     * How many times {@link #deleteIfStillOwnGeneration} re-reads {@code
     * lockPath} before giving up on a transient ({@link #READ_FAILED}/
     * {@link #EMPTY_OR_UNPARSEABLE}) verification result -- see that
     * method's own Javadoc for why a single immediate read is not enough.
     * Deliberately small and fixed (not scaled to {@code
     * totalRetryBudget}): this runs synchronously inside {@link
     * #createAndWriteMetadata}, itself already inside {@link #acquire}'s
     * own retry loop, so it must add only a small, bounded amount of
     * latency, not consume a meaningful share of the caller's own budget.
     */
    private static final int OWN_GENERATION_DELETE_RETRY_ATTEMPTS = 3;

    /** Delay between {@link #deleteIfStillOwnGeneration}'s own retry attempts -- see that constant's Javadoc. */
    private static final long OWN_GENERATION_DELETE_RETRY_DELAY_MILLIS = 50;

    private final Path lockPath;

    /**
     * The exact {@link LockMetadata} this instance itself wrote when it
     * acquired the lock -- retained so {@link #close} can re-verify it is
     * still deleting <i>this instance's own</i> lock generation, not a
     * different one a sibling legitimately acquired in the meantime. See
     * {@link #close}'s own Javadoc for the real, measured scenario this
     * closes -- the release-side counterpart to {@link
     * #tryStealIfStale}'s acquire-side fix.
     */
    private final LockMetadata ownMetadata;

    /**
     * {@link #close} must be idempotent -- a second call on an
     * already-closed instance (this project's own convention is
     * try-with-resources, which never double-closes on its own, but
     * nothing prevents a caller from doing so directly, and {@link
     * AutoCloseable#close()}'s own contract does not forbid it the way
     * {@link java.io.Closeable#close()}'s stricter one does) must not
     * re-read {@code lockPath} and, if some other process has since
     * legitimately acquired a brand new generation there, log a real
     * {@code ERROR} ("no longer holds this instance's own metadata...")
     * that would read as a genuine cross-process safety event even though
     * nothing wrong actually happened -- a harmless double-close would
     * never delete the sibling's lock (the existing generation check
     * already prevents that), but the misleading log noise alone is a
     * real problem for anyone investigating it. Guarded here rather than
     * left to each future caller to avoid double-closing on its own.
     */
    private boolean closed;

    private AccountLedgerLock(Path lockPath, LockMetadata ownMetadata) {
        this.lockPath = lockPath;
        this.ownMetadata = ownMetadata;
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
        // A transient (non-FileAlreadyExistsException) IOException during
        // creation must consume retry budget like ordinary contention, not
        // be promoted straight to IllegalStateException on its very first
        // occurrence -- this class's own documented, measured 500ms+
        // transient I/O latency on this repository's real drvfs mount is
        // exactly the kind of environment characteristic a single retry,
        // not zero, should be able to ride out. Tracked here so a real,
        // final failure still reports its actual root cause once the
        // budget is genuinely exhausted, rather than a generic timeout
        // message with no cause.
        IOException lastTransientFailure = null;

        while (true) {
            long elapsedNanos = System.nanoTime() - startNanos;
            if (elapsedNanos >= budgetNanos) {
                IllegalStateException budgetExhausted = new IllegalStateException(
                        "failed to acquire account ledger lock " + lockPath + " within retry budget "
                                + totalRetryBudget + " (elapsed " + Duration.ofNanos(elapsedNanos) + ")");
                if (lastTransientFailure != null) {
                    budgetExhausted.initCause(lastTransientFailure);
                }
                throw budgetExhausted;
            }

            try {
                LockMetadata ownMetadata = createAndWriteMetadata(lockPath);
                if (ownMetadata == null) {
                    // Our own write landed on an orphaned file -- a
                    // sibling reclaimed lockPath (via
                    // tryStealIfAbandonedEmpty) while we were still
                    // writing to it, and is the real, live holder now.
                    // We do not hold the lock. Treat exactly like
                    // ordinary lost contention: back off and retry the
                    // whole loop, never returning a lock we don't
                    // actually have and never touching the sibling's
                    // real file.
                    sleepQuietly(backoffMillis);
                    backoffMillis = Math.min(MAX_BACKOFF_MILLIS, backoffMillis * 2);
                    continue;
                }
                return new AccountLedgerLock(lockPath, ownMetadata);
            } catch (FileAlreadyExistsException e) {
                // tryStealIfStale can itself throw IOException -- its own
                // delete/read-path failures must not be wrapped in
                // IllegalStateException and thrown directly, which would
                // bypass this loop's retry budget entirely (this catch
                // block is a sibling of, not nested inside, the catch
                // (IOException e) block below, so it would never be caught
                // there either). Handled inline instead, folded into the
                // same lastTransientFailure-tracking backoff-and-retry
                // path.
                try {
                    if (tryStealIfStale(lockPath, staleThreshold)) {
                        continue; // steal already known-stale -- retry create immediately, no backoff
                    }
                } catch (IOException stealFailure) {
                    lastTransientFailure = stealFailure;
                }
                sleepQuietly(backoffMillis);
                backoffMillis = Math.min(MAX_BACKOFF_MILLIS, backoffMillis * 2);
            } catch (IOException e) {
                // Transient (e.g. a real, measured slow/flaky drvfs I/O
                // operation, not "the file already exists") -- consume
                // retry budget like ordinary contention rather than
                // failing on the very first occurrence; see this loop's
                // own lastTransientFailure comment above for why the
                // budget-exhausted branch still reports this as the cause.
                lastTransientFailure = e;
                sleepQuietly(backoffMillis);
                backoffMillis = Math.min(MAX_BACKOFF_MILLIS, backoffMillis * 2);
            }
        }
    }

    /**
     * Creates {@code lockPath} (atomically -- see class Javadoc) and writes
     * this holder's {@link LockMetadata} into it, returning the exact value
     * written, or {@code null} if this holder lost a race for the path (see
     * below) -- {@link #acquire} treats a {@code null} return as ordinary
     * contention (back off and retry), not success or a hard failure.
     *
     * <p><b>Single atomic handle, not two separate operations.</b> The file
     * is created and its content written through one {@link
     * java.nio.channels.SeekableByteChannel}, opened atomically ({@link
     * java.nio.file.StandardOpenOption#CREATE_NEW}), rather than a
     * path-based create followed by a separate path-based write. This
     * repository's drvfs mount exhibits real, measured 500ms+ write
     * latency under contention, and a two-step approach leaves a real
     * window in which a sibling could legitimately judge the
     * still-being-written file abandoned (via {@link
     * #tryStealIfAbandonedEmpty}), delete it, and create its own new, live
     * generation at the same path -- after which this holder's own
     * still-pending write, being purely path-based, would silently
     * overwrite the sibling's real, live metadata with this holder's own
     * stale content, leaving two holders both believing they own the lock.
     * Writing through the single open handle instead means a concurrent
     * delete-and-recreate at the same path cannot corrupt this holder's
     * write: this repository's drvfs mount exhibits POSIX-style unlink
     * semantics (confirmed directly against {@code
     * /mnt/c/Dev/trading-engine} -- deleting a path out from under an
     * already-open {@code CREATE_NEW} channel succeeds, a new file can
     * then be created at that same path, and a subsequent write through
     * the original, now-path-invisible channel lands on that original file
     * rather than the new one), not Windows' traditional
     * delete-blocked-while-open behavior. {@code
     * AccountLedgerLockTest#aLockFilesOwnOpenCreationHandleCannotClobberADifferentGenerationCreatedAfterItWasStolen}
     * proves this holds through the real production code path.
     *
     * <p><b>Cleanup on write failure.</b> If writing the metadata fails
     * (any {@link IOException} after the file was successfully created),
     * the just-created file is deleted before the failure propagates --
     * but only if it still holds exactly this holder's own metadata (via
     * {@link #deleteIfStillOwnGeneration}, so a sibling's newly acquired
     * generation at the same path is never destroyed). A partial write is
     * the most common real shape this failure takes, and a partially
     * written file's content is truncated JSON -- {@link
     * #readMetadataOrNull} returns {@link #EMPTY_OR_UNPARSEABLE} for it,
     * which does not {@code equals()} this holder's own metadata, so this
     * cleanup does <b>not</b> delete it. Best-effort where it does apply --
     * if the cleanup delete itself also fails, it is attached via {@link
     * Throwable#addSuppressed} rather than masking the original failure.
     * Without any cleanup at all, a permanently empty (or partially
     * written) lock file would be left behind: {@link #readMetadataOrNull}
     * can never parse a pid/{@code acquiredAt} out of empty or truncated
     * content, so {@link #tryStealIfStale}'s ordinary dead-pid/expired-
     * timestamp checks could never fire against it, and every future
     * waiter would exhaust its retry budget and fail until a human
     * manually deleted the file (or, in the always-real partial-write
     * case above, until {@code staleThreshold} elapses). {@link
     * #tryStealIfAbandonedEmpty} is the actual backstop for that residual
     * window -- not merely the narrower hard-crash case originally
     * documented here, but the ordinary partial-write case too, since this
     * method's own cleanup cannot reach either.
     *
     * <p><b>Re-verification after write, and why a lost race is not an
     * error.</b> A successful write through the single open handle above
     * only protects the file's <i>content</i> -- it does not, by itself,
     * guarantee this holder's own {@link #acquire} call should report
     * success, because an orphaned channel's write still returns normally
     * against its own now-path-invisible file even when a sibling has
     * already reclaimed the path via {@link #tryStealIfAbandonedEmpty} and
     * holds a real, different generation there. To close that gap, this
     * method re-reads {@code lockPath} immediately after the write
     * completes and confirms it still holds exactly the {@link
     * LockMetadata} just written; if not, it returns {@code null} rather
     * than the metadata. This method never deletes anything on that path
     * for a genuine generation mismatch (real, different metadata read
     * back): the path now legitimately belongs to whoever's generation
     * the re-read found, and this method has no business touching it. The
     * comparison treats two <i>transient</i> outcomes differently from
     * that genuine mismatch -- a read failure ({@link #readMetadataOrNull}
     * returning {@link #READ_FAILED}) <b>and</b> empty or unparseable
     * content ({@link #EMPTY_OR_UNPARSEABLE}) as well, grouped together
     * for the identical
     * reason {@link #close}'s own {@code doClose} already groups them:
     * neither proves ownership was actually lost, since this holder just
     * created and wrote the file in this same call, and this project's
     * own drvfs mount has a real, previously-measured susceptibility to
     * transient read/visibility gaps on a process's own still-valid
     * content. Both transient cases attempt to clean up only this
     * holder's own, re-verified generation (via {@link
     * #deleteIfStillOwnGeneration}, the same delete-only-if-content-
     * still-matches-exactly discipline {@link #tryStealIfStale} itself
     * uses, with its own brief internal retry against exactly this kind
     * of transient condition -- see that method's own Javadoc) before
     * reporting the lost race. This is a real, meaningfully-improved
     * best effort, not an absolute guarantee: if the transient condition
     * genuinely outlasts {@link #deleteIfStillOwnGeneration}'s own
     * bounded retry budget, this holder's own live lock file is left
     * behind after all, self-blocking it (and every other waiter) until
     * {@code staleThreshold} elapses and {@link #tryStealIfAbandonedEmpty}
     * reclaims it -- the same real backstop this design has always relied
     * on, now reached less often rather than relied on exclusively.
     *
     * <p><b>Known, permanent test-coverage gap on this method's own
     * post-write re-verification branches</b> -- both the genuine-
     * generation-mismatch {@code null}-return path above, and the {@link
     * #READ_FAILED}/{@link #EMPTY_OR_UNPARSEABLE} cleanup-then-{@code
     * null} path immediately above that. Proving either directly requires
     * a real, second thread or process to interfere with {@code
     * lockPath} in the exact, unobservable window between this method's
     * own write completing and its immediately-following re-read -- two
     * sequential lines inside one method call, with no yield point either
     * line could be paused at from outside. Both ways to close this gap
     * for real are rejected: (1) accepting genuine timing-dependent test
     * flakiness by racing real threads against this window, against this
     * codebase's own no-flaky-tests discipline; or (2) adding a
     * production-code, test-only synchronization hook purely so a test
     * could pause execution there, which is unrequested production
     * surface area added solely to serve one test. The {@code
     * EMPTY_OR_UNPARSEABLE} grouping itself is still trusted despite this
     * gap: it matches {@code doClose}'s own already-independently-
     * validated identical grouping, and relies on {@link
     * #deleteIfStillOwnGeneration}'s own already-proven safety property,
     * not on a fresh, untested claim of its own. Coverage today is
     * indirect only: the multi-thread ({@code
     * AccountLedgerLockTest#acquireProvidesRealMutualExclusionAcrossManyThreads})
     * and multi-process ({@code AccountLedgerLockMultiProcessTest}) tests
     * exercise {@code acquire} under enough real contention that these
     * exact races are structurally possible on every run, without ever
     * being provable to have actually occurred on any given run.
     */
    private static LockMetadata createAndWriteMetadata(Path lockPath) throws IOException {
        Path parent = lockPath.toAbsolutePath().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        // Atomic create-and-open in one call -- throws
        // FileAlreadyExistsException if it already exists, same as
        // Files.createFile does, and deliberately left to propagate
        // straight to acquire() (a try/catch here that only rethrows the
        // exact same exception would have no behavioral effect, so none
        // exists here) -- nothing was created in that case, so there is
        // nothing for this method's own cleanup-on-failure logic below to
        // clean up; acquire()'s existing FileAlreadyExistsException
        // handling is exactly the right path for it.
        SeekableByteChannel channel =
                Files.newByteChannel(lockPath, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
        LockMetadata metadata = null;
        try (channel) {
            metadata = new LockMetadata(ProcessHandle.current().pid(), hostname(), Instant.now());
            // WritableByteChannel#write is not guaranteed to consume the
            // whole buffer in one call (confirmed against the JDK's own
            // documented contract, not assumed) -- loop until fully
            // written, matching this class's own careful, don't-assume
            // discipline everywhere else in this file.
            ByteBuffer buffer = ByteBuffer.wrap(MAPPER.writeValueAsBytes(metadata));
            while (buffer.hasRemaining()) {
                channel.write(buffer);
            }
            // Re-verify we still actually own lockPath -- see this
            // method's own "Re-verification after write, and why a lost
            // race is not an error" Javadoc above for why a successful
            // write alone does not prove this.
            LockMetadata current = readMetadataOrNull(lockPath);
            if (metadata.equals(current)) {
                return metadata;
            }
            if (current == READ_FAILED || current == EMPTY_OR_UNPARSEABLE) {
                // A transient read failure proves nothing about the
                // file's real content -- see this method's own
                // "Re-verification after write" Javadoc for why this must
                // not be treated the same as a genuine mismatch.
                // EMPTY_OR_UNPARSEABLE must be grouped here too, not left
                // to fall through to the genuine-mismatch branch below (no
                // cleanup) -- consistent with this same sentinel's own
                // treatment in doClose(), which already treats it as a
                // transient read/visibility gap on this instance's own
                // still-valid content (a real, previously-measured
                // characteristic of this project's own drvfs mount), not
                // proof of a different holder. Left ungrouped, a holder
                // whose own re-verification read hit
                // this sentinel would return null without deleting its
                // own real, just-written, live-looking file -- acquire()
                // would then retry, find that same file via
                // FileAlreadyExistsException, and tryStealIfStale would
                // correctly judge it not stale (its own live pid, its own
                // fresh acquiredAt), exhausting the retry budget and
                // self-blocking on its own lock file until staleThreshold
                // elapses, blocking every other waiter too.
                // deleteIfStillOwnGeneration only ever deletes when the
                // content still exactly matches this holder's own
                // metadata, so a sibling's genuine new generation is
                // never at risk from this grouping.
                log.error(
                        "account ledger lock {} could not be confirmed immediately after this holder wrote its own"
                                + " metadata ({}) -- read failed, or the content read back empty/unparseable."
                                + " Cannot prove ownership from this read alone; cleaning up only this holder's own"
                                + " verified generation (if it's still there) and reporting lost contention rather"
                                + " than leaving a live-looking file behind.",
                        lockPath,
                        metadata);
                deleteIfStillOwnGeneration(lockPath, metadata);
            }
            return null;
        } catch (IOException | RuntimeException e) {
            // Generation-safe cleanup: this catch must verify the delete
            // target is still this holder's own generation before
            // deleting, not unconditionally call
            // Files.deleteIfExists(lockPath). The real sequence an
            // unconditional delete would make possible: (1) this holder
            // creates the file via CREATE_NEW; (2) the write fails slowly
            // enough for (3) a sibling to legitimately reclaim the
            // still-empty file via tryStealIfAbandonedEmpty and create its
            // own real generation there; (4) this catch then deletes the
            // sibling's real, live lock file; (5) a third process acquires
            // immediately, and two processes are now inside the critical
            // section at once -- a real lost update on the shared risk
            // ledger. {@link #deleteIfStillOwnGeneration} (built for the
            // READ_FAILED case elsewhere in this class) already provides
            // exactly the needed generation-safe delete, reused here at no
            // extra cost. metadata may still be null here (e.g. if
            // constructing the LockMetadata record itself somehow threw,
            // though nothing in its own construction can) -- guarded
            // rather than assumed. Catches RuntimeException here too, not
            // only IOException: deleteIfStillOwnGeneration's own bounded
            // retry (see that method's Javadoc) calls sleepQuietly, which
            // throws IllegalStateException -- a RuntimeException, not an
            // IOException -- if this thread is interrupted while waiting.
            // Left uncaught, that would propagate straight out of this
            // whole catch block, skipping "throw e" entirely and losing
            // the real root cause this block exists to report, on top of
            // leaving this holder's own lock file behind to self-block
            // every waiter until staleThreshold elapses.
            if (metadata != null) {
                try {
                    deleteIfStillOwnGeneration(lockPath, metadata);
                } catch (IOException | RuntimeException cleanupFailure) {
                    e.addSuppressed(cleanupFailure);
                }
            }
            throw e;
        }
    }

    /**
     * Returns {@code true} if a stale lock was found and deleted (caller
     * should retry {@link #createAndWriteMetadata} immediately), {@code
     * false} if the lock is live/recent (caller should back off) or its
     * metadata could not be read right now.
     *
     * <p><b>A real TOCTOU window this method's own re-verification step
     * exists to close</b> (see {@code AccountLedgerLockMultiProcessTest}'s
     * Javadoc for the real multi-JVM test that found it): reading this
     * lock's metadata and judging it stale is <b>not instantaneous</b> --
     * real, repeatable measurements
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
     *
     * <p><b>Declares {@code throws IOException} rather than wrapping a
     * genuine delete/read failure in {@link IllegalStateException} and
     * throwing it directly.</b> This method (and the delete-path helpers
     * it calls, {@link #tryStealIfAbandonedEmpty}/{@link
     * #lastModifiedTimeOrNull}) is called from {@link #acquire}'s {@code
     * catch (FileAlreadyExistsException e)} block, a sibling of {@code
     * acquire}'s own {@code catch (IOException e)} handler, not nested
     * inside it -- so an {@code IllegalStateException} thrown directly
     * here would propagate straight out of {@code acquire()} entirely,
     * consuming none of {@code totalRetryBudget}, inconsistent with this
     * class's own established pattern of giving every other transient-
     * I/O-failure path a bounded retry instead of an immediate hard
     * failure. Propagating the real {@link IOException} instead lets
     * {@code acquire()} catch it at the steal call site and fold it into
     * the same {@code lastTransientFailure}-tracking backoff-and-retry
     * path its own direct {@link #createAndWriteMetadata} failures already
     * use.
     */
    private static boolean tryStealIfStale(Path lockPath, Duration staleThreshold) throws IOException {
        LockMetadata metadata = readMetadataOrNull(lockPath);
        if (metadata == null) {
            // Vanished between our failed create and this read -- another
            // waiter already released or stole it. Safe (and correct) to
            // report "stale" here: it tells the caller to retry the
            // create loop immediately rather than back off waiting on a
            // holder that's already gone.
            return true;
        }
        if (metadata == READ_FAILED) {
            // A transient read failure says nothing about the file's real
            // content or age -- never route this into the mtime-based
            // abandonment check below, which needs a confirmed-empty read
            // to mean anything. Never guess; back off and retry.
            return false;
        }
        if (metadata == EMPTY_OR_UNPARSEABLE) {
            return tryStealIfAbandonedEmpty(lockPath, staleThreshold);
        }

        // ProcessHandle.of(pid) only ever consults THIS host's own process
        // table -- meaningful only because this project's own documented
        // deployment model has every process sharing a given lock running
        // on the same host (see class Javadoc). A lock recorded by a
        // genuinely different host would almost certainly show a
        // "not found" result for that pid here regardless of whether the
        // real, foreign holder is still alive -- so the pid-liveness
        // result is only trusted when the recorded hostname matches this
        // host's own; a mismatch is treated as "not provably dead" (fails
        // toward not stealing via this path) rather than guessed. The
        // expired check below is deliberately independent of this gate --
        // a foreign-host lock must still be reclaimable once its own
        // acquiredAt exceeds staleThreshold.
        boolean holderDead = hostname().equals(metadata.hostname())
                && !ProcessHandle.of(metadata.pid()).map(ProcessHandle::isAlive).orElse(false);
        boolean expired = Duration.between(metadata.acquiredAt(), Instant.now()).compareTo(staleThreshold) > 0;
        if (!holderDead && !expired) {
            return false; // live, recent holder -- not stale
        }

        // Re-verify immediately before deleting -- see method Javadoc's
        // "Real TOCTOU finding" for why this re-check is load-bearing, not
        // defensive-programming boilerplate.
        LockMetadata current = readMetadataOrNull(lockPath);
        if (current == null || current == READ_FAILED || current == EMPTY_OR_UNPARSEABLE || !current.equals(metadata)) {
            return false;
        }

        // Logged only now, after re-verification confirms a delete is
        // actually about to happen -- logging any earlier (e.g. right
        // after the initial staleness judgment, before re-verification)
        // would produce a false-positive "stealing" ERROR even on the
        // common, benign path where re-verification finds the lock no
        // longer matches (a legitimate new holder already took it, or a
        // sibling already stole it) and this method returns false without
        // deleting anything.
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

        try {
            Files.delete(lockPath);
        } catch (NoSuchFileException e) {
            // Another waiter beat us to stealing it -- fine, we'll simply
            // contend for the now-vacant (or newly-recreated) file
            // normally on the next loop iteration.
        }
        // Any other IOException propagates -- must reach acquire()'s
        // retry-budget handling, not be wrapped into an
        // immediately-propagating IllegalStateException.
        return true;
    }

    /**
     * Deletes {@code lockPath} only if it still holds exactly {@code
     * metadata} -- the same re-verify-immediately-before-delete
     * discipline {@link #tryStealIfStale} itself uses, reused here for
     * {@link #createAndWriteMetadata}'s own "clean up only my own,
     * provably-still-mine generation" case (see that method's own
     * "Re-verification after write" Javadoc). Never guesses: if the
     * current content can be confirmed to be a genuine, different
     * generation -- this method does nothing rather than risk deleting a
     * different, currently-live holder's lock. Tolerates {@link
     * NoSuchFileException} (already gone -- fine, nothing to do).
     *
     * <p><b>Retries briefly (up to {@link #OWN_GENERATION_DELETE_RETRY_ATTEMPTS}
     * reads, {@link #OWN_GENERATION_DELETE_RETRY_DELAY_MILLIS} apart) when
     * the verification read itself comes back as {@link #READ_FAILED} or
     * {@link #EMPTY_OR_UNPARSEABLE}, rather than giving up on the very
     * first such read.</b> A single immediate read is not enough to give
     * this cleanup a real chance of catching a resolving transient
     * condition: this project's own drvfs mount has real, measured 500ms+
     * transient I/O latency under contention (see class Javadoc), which
     * can easily outlast the gap between two back-to-back reads. Without
     * this retry, a transient condition sustained across even one extra
     * read would make this cleanup call a pure no-op, contradicting both
     * of this method's callers' own stated intent ("cleaning up only this
     * holder's own verified generation") -- a real, disclosed availability
     * gap, not theoretical. Safe to retry: this file's own mtime is only
     * ever a few milliseconds old at either of this method's call sites,
     * far short of any realistic {@code staleThreshold}, so {@link
     * #tryStealIfAbandonedEmpty} could not have legitimately reclaimed it
     * during this short window -- retrying here never risks racing (or
     * masking) a real steal. A genuine, different generation (a real
     * {@link LockMetadata} that isn't {@code metadata} and isn't one of
     * the two transient sentinels) ends the retry loop immediately without
     * deleting, on the very first read that observes it -- retrying
     * further would serve no purpose once ownership is genuinely settled.
     * This retry does not, and cannot, guarantee success if the real
     * condition outlasts this method's own bounded retry budget; {@link
     * #tryStealIfAbandonedEmpty}, once {@code staleThreshold} elapses,
     * remains the real backstop for that case, as it always has been.
     *
     * <p><b>Known, permanent test-coverage gap, the identical shape (and
     * for the identical reason) as the one already disclosed on {@link
     * #createAndWriteMetadata}'s own Javadoc.</b> Both of this method's
     * real call sites are reachable only through that method's own
     * internal, unobservable re-verification window -- so neither this
     * retry's own "succeeds on a later attempt" case, nor even this
     * method's pre-existing, unconditional safety property (never
     * deletes a genuine different generation), has ever had a direct,
     * dedicated test; both are covered only indirectly, by the same
     * multi-thread/multi-process contention tests already cited there.
     * This retry's own correctness rests on the same reasoning already
     * applied to {@code createAndWriteMetadata}'s analogous {@code
     * EMPTY_OR_UNPARSEABLE} grouping fix: it is a small, mechanically
     * simple change (a bounded read-and-compare loop, no new I/O
     * primitive), it strictly narrows an existing no-op window rather
     * than introducing new risk, and it can never delete anything the
     * unconditional exact-match check below wouldn't already have
     * allowed on a single, unretried read.
     *
     * <p>Declares {@code throws IOException} for any other delete failure
     * rather than wrapping it -- must propagate as a real {@link
     * IOException} so it can reach {@link #acquire}'s own retry-budget
     * handling, whichever of this method's two call sites it originates
     * from.
     */
    private static void deleteIfStillOwnGeneration(Path lockPath, LockMetadata metadata) throws IOException {
        for (int attempt = 0; attempt < OWN_GENERATION_DELETE_RETRY_ATTEMPTS; attempt++) {
            LockMetadata current = readMetadataOrNull(lockPath);
            if (metadata.equals(current)) {
                try {
                    Files.delete(lockPath);
                } catch (NoSuchFileException e) {
                    // Already gone -- fine.
                }
                return;
            }
            if (current != READ_FAILED && current != EMPTY_OR_UNPARSEABLE) {
                // A genuine, different generation (or a confirmed-absent
                // file) -- not a transient condition, so no point
                // retrying further; ownership is already settled one way
                // or the other.
                return;
            }
            if (attempt < OWN_GENERATION_DELETE_RETRY_ATTEMPTS - 1) {
                sleepQuietly(OWN_GENERATION_DELETE_RETRY_DELAY_MILLIS);
            }
        }
    }

    /**
     * Backstop for the one gap {@link #createAndWriteMetadata}'s own
     * cleanup-on-failure can't reach: a hard process kill (or crash)
     * between the atomic {@link Files#newByteChannel} create with {@link
     * java.nio.file.StandardOpenOption#CREATE_NEW} succeeding and that
     * method's {@code catch} block ever running, leaving a real, permanently-empty
     * lock file with no {@code pid}/{@code acquiredAt} for the ordinary
     * {@link #tryStealIfStale} checks to judge. Without this, such a file
     * could never be reclaimed -- every future waiter would exhaust its
     * retry budget forever.
     *
     * <p>Falls back to the lock <b>file's own last-modified time</b>
     * (rather than any in-memory state, keeping this class's stateless
     * design intact) as the staleness signal: a fresh empty file (younger
     * than {@code staleThreshold}) is presumed mid-write and left alone,
     * matching {@link #readMetadataOrNull}'s own "never guess" principle;
     * one older than {@code staleThreshold} is presumed abandoned and
     * stolen, with the same re-verify-immediately-before-delete discipline
     * {@link #tryStealIfStale} itself uses (checking the file is still
     * exactly as old, not merely still empty, before deleting) for the
     * same TOCTOU reason documented there.
     *
     * <p>Declares {@code throws IOException} -- a genuine delete/read
     * failure here must reach {@link #acquire}'s retry-budget handling,
     * not be wrapped into an immediately-propagating {@link
     * IllegalStateException}.
     */
    private static boolean tryStealIfAbandonedEmpty(Path lockPath, Duration staleThreshold) throws IOException {
        Instant lastModified = lastModifiedTimeOrNull(lockPath);
        if (lastModified == null) {
            return true; // vanished since our read -- safe to retry immediately
        }
        if (Duration.between(lastModified, Instant.now()).compareTo(staleThreshold) <= 0) {
            return false; // still recent -- most likely mid-write, never guess
        }

        Instant currentLastModified = lastModifiedTimeOrNull(lockPath);
        if (currentLastModified == null) {
            return true; // already gone -- another waiter beat us to it
        }
        if (!currentLastModified.equals(lastModified)) {
            // Modified since our read -- someone is actively writing (or
            // has since written real metadata) to it right now. Not
            // provably the same abandoned file anymore; never delete on
            // stale information (same TOCTOU reasoning as tryStealIfStale).
            return false;
        }

        // Logged only now, after re-verification confirms a delete is
        // actually about to happen -- same reasoning as tryStealIfStale's
        // own log placement: logging any earlier would produce a
        // false-positive "stealing" ERROR on the benign path above where
        // the file was modified since our first read and this method
        // returns false
        // without deleting anything.
        log.error(
                "stealing an empty/unparseable account ledger lock {} -- last modified {} exceeds staleThreshold"
                        + " {}, treating as abandoned (its holder most likely died between creating the file and"
                        + " writing its metadata -- e.g. a hard process kill). This is a real cross-process"
                        + " safety event, not routine contention -- investigate.",
                lockPath,
                lastModified,
                staleThreshold);

        try {
            Files.delete(lockPath);
        } catch (NoSuchFileException e) {
            // Another waiter beat us to it -- fine.
        }
        // Any other IOException propagates -- see this method's own Javadoc.
        return true;
    }

    /**
     * Declares {@code throws IOException} rather than wrapping a real
     * failure -- see {@link #tryStealIfStale}'s own Javadoc for why.
     */
    private static Instant lastModifiedTimeOrNull(Path lockPath) throws IOException {
        try {
            return Files.getLastModifiedTime(lockPath).toInstant();
        } catch (NoSuchFileException e) {
            return null;
        }
    }

    /**
     * Sentinel: the file is present and was read successfully, but its
     * content is empty or unparseable right now -- see {@link
     * #readMetadataOrNull}. Distinguished from {@link #READ_FAILED}: the
     * two are never merged into one sentinel, so {@link #close}'s own log
     * message never asserts "no longer holds this instance's own
     * metadata" for a transient {@link Files#readString} failure that says
     * nothing at all about the file's actual current content -- which
     * would mislead an operator investigating a real incident in the
     * wrong direction. Both are still treated identically everywhere
     * <i>behavior</i> is concerned (never delete, never guess) -- only the
     * two sentinels' own distinctness, and the messages built from them,
     * differ.
     */
    private static final LockMetadata EMPTY_OR_UNPARSEABLE = new LockMetadata(-1, "", Instant.EPOCH);

    /** Sentinel: {@link Files#readString} itself failed (a transient {@link IOException}) -- see {@link #EMPTY_OR_UNPARSEABLE}'s Javadoc. */
    private static final LockMetadata READ_FAILED = new LockMetadata(-2, "", Instant.EPOCH);

    /**
     * @return the lock file's parsed metadata; {@code null} if the file
     *     does not exist; {@link #READ_FAILED} if {@link Files#readString}
     *     itself threw; {@link #EMPTY_OR_UNPARSEABLE} if the read succeeded
     *     but the content can't be parsed, <b>parses successfully as the
     *     JSON literal {@code null}</b> (Jackson maps a bare {@code null}
     *     token to a Java {@code null} without throwing, since it's valid
     *     JSON; left unnormalized, that {@code null} would be
     *     indistinguishable from this method's own "file does not exist"
     *     {@code null}, wrongly telling {@link #tryStealIfStale} to retry
     *     immediately with no backoff and wrongly telling {@link #close}
     *     the file is already gone), <b>or parses to a structurally
     *     incomplete {@link LockMetadata}</b> (Jackson can silently fill a
     *     missing record component with its default/null rather than
     *     throwing -- {@code {"pid":123}} alone deserializes "successfully"
     *     to a {@code LockMetadata} with a {@code null} {@code
     *     hostname}/{@code acquiredAt} -- and {@link
     *     #tryStealIfStale}'s own {@code Duration.between(metadata
     *     .acquiredAt(), ...)} call would then throw a raw {@code
     *     NullPointerException} that does not flow back through {@link
     *     #acquire}'s retry loop the way every other failure mode here
     *     does. Checked immediately after parsing, before this method
     *     ever returns a value the rest of this class treats as trustworthy
     *     metadata) (see {@link #tryStealIfStale}'s Javadoc for why all of
     *     these non-null/non-metadata outcomes are treated as "cannot
     *     determine, don't guess" rather than either extreme).
     */
    private static LockMetadata readMetadataOrNull(Path lockPath) {
        String raw;
        try {
            raw = Files.readString(lockPath);
        } catch (NoSuchFileException e) {
            return null;
        } catch (IOException e) {
            return READ_FAILED;
        }
        try {
            LockMetadata parsed = MAPPER.readValue(raw, LockMetadata.class);
            if (parsed == null || parsed.hostname() == null || parsed.acquiredAt() == null || parsed.pid() <= 0) {
                return EMPTY_OR_UNPARSEABLE;
            }
            return parsed;
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
     * Deletes the lock file, releasing it for the next waiter -- but
     * <b>only if it still holds exactly this instance's own {@link
     * #ownMetadata}</b>. Tolerates the file already being gone (via {@link
     * #readMetadataOrNull} returning {@code null}, or a race on the final
     * {@link Files#delete} call itself throwing {@link
     * NoSuchFileException} -- shouldn't happen under correct use, but not
     * itself evidence of a problem worth failing over). Any other
     * deletion failure is logged loudly rather than thrown: {@link
     * AutoCloseable#close()} must not mask whatever happened inside the
     * caller's {@code try} block by throwing here, but a lock file this
     * class fails to delete will block every other waiter until {@code
     * staleThreshold} elapses, so this is a real operational problem, not
     * a silent no-op.
     *
     * <p><b>The release-side counterpart to {@link #tryStealIfStale}'s own
     * acquire-side TOCTOU fix</b> (see that method's Javadoc for the full,
     * measured account of why a gap this large is real on this
     * repository's filesystem, not theoretical). This method must not
     * delete <i>whatever currently exists</i> at {@code lockPath}
     * unconditionally -- a real sequence that would make possible: (1)
     * this instance holds the lock, but a slow filesystem
     * operation inside its own critical section pushes its real elapsed
     * hold time past {@code staleThreshold}; (2) a waiting sibling process
     * correctly (by this class's own rules) judges this instance's lock
     * stale, steals it, and acquires its own new, legitimate lock
     * generation; (3) this instance, unaware any of that happened, finally
     * reaches its own (now-delayed) {@code close()} call and deletes the
     * <i>sibling's</i> lock file, believing it is releasing its own --
     * breaking mutual exclusion a second, independent way (a third process
     * can now acquire immediately, while the sibling from step 2 still
     * believes it holds the lock). Guarded against by re-reading the file
     * and comparing it to {@link #ownMetadata} (record {@code equals()} --
     * exact match required) immediately before deleting; a mismatch (or
     * the file already being gone with different content, or unparseable)
     * means this instance's lock was already stolen out from under it --
     * logged at {@code ERROR} (this class's established never-silent
     * convention for a genuine cross-process safety event) and the delete
     * is skipped entirely, rather than ever risking deletion of a
     * different holder's live lock. As with the acquire-side check, this
     * closes the exploitable window down to the gap between two adjacent
     * file operations -- not a perfectly atomic compare-and-delete (no
     * such primitive exists in {@code java.nio.file}, and introducing a
     * different locking mechanism to get one was already rejected
     * elsewhere in this design specifically because of this repository's
     * drvfs reliability concerns -- see the class Javadoc) -- disclosed as
     * such rather than overclaimed.
     *
     * <p><b>The re-verification read above can itself come back as three
     * distinct outcomes, each needing its own honest log message.</b>
     * {@link #READ_FAILED} (a transient {@link Files#readString} failure),
     * {@link #EMPTY_OR_UNPARSEABLE}, and a genuine mismatch must never be
     * logged identically as "no longer holds this instance's own
     * metadata... found {@code current}" -- that would be simply false for
     * a read failure (this instance may well still be the legitimate
     * holder; the file just couldn't be read this one time) and would
     * print a meaningless sentinel value for {@code current} in both
     * non-mismatch cases. Skipping the delete is still the correct, safe
     * behavior in all three cases (never guess), but each gets its own
     * honest log message so an operator investigating isn't pointed at
     * "mutual exclusion was lost" for a transient filesystem hiccup.
     *
     * <p><b>Idempotent</b> -- a second call on an already-closed instance
     * is a pure no-op (see this class's own {@link #closed} field
     * Javadoc for the real, misleading-log-noise problem this prevents).
     *
     * <p><b>{@link #closed} must only ever be set on a genuinely final
     * outcome, not merely whenever {@link #doClose} returns without
     * throwing.</b> {@link #doClose} returns {@code boolean}: {@code true}
     * for an outcome no future retry could ever improve on (the file is
     * genuinely gone or already deleted by this call, or the
     * re-verification found a genuine generation mismatch -- a real, live
     * different holder provably owns the path now, so no retry could ever
     * change that), {@code false} for a transient, recoverable outcome a
     * later {@code close()} call might still resolve better (a {@link
     * #READ_FAILED} re-verification read, {@link #EMPTY_OR_UNPARSEABLE}
     * content -- unlike a genuine different-holder mismatch, this state
     * cannot actually be distinguished from a transient filesystem
     * read/visibility gap on this instance's own still-valid content, a
     * real, previously-measured characteristic of this project's own
     * drvfs mount (see this class's own class Javadoc), so it is treated
     * the same retryable way {@code READ_FAILED} already is -- or any
     * {@link IOException} other than {@link NoSuchFileException} from the
     * delete itself). Getting this distinction wrong would be a real
     * availability bug, not merely untidy: setting {@link #closed}
     * unconditionally whenever {@link #doClose} returns without an
     * exception would be wrong, because {@code doClose} also returns
     * normally (no exception) on its own transient-failure paths -- a
     * single transient {@link #READ_FAILED} on close would then
     * permanently mark this instance closed with no way to ever retry
     * releasing the still-live lock file, blocking every other waiter for
     * a shared KIS account risk ledger lock until {@code staleThreshold}
     * elapses regardless of how many times a caller called {@code
     * close()} again. If some genuinely unanticipated exception ever
     * escapes {@link #doClose} uncaught (everything this class's own
     * logic can throw is already handled inside it), {@link #closed} is
     * correctly never set at all, for the same retry-ability reason.
     */
    @Override
    public void close() {
        if (closed) {
            return;
        }
        closed = doClose();
    }

    /**
     * Throws {@link IllegalStateException} if this instance has already
     * reached a final, closed state (see {@link #closed}'s own Javadoc for
     * exactly when that happens -- a retryable, non-final {@link #close}
     * outcome does <b>not</b> trip this check). {@link
     * AccountLedgerStore#load}/{@link AccountLedgerStore#persist} call
     * this as part of enforcing their own documented caller contract: an
     * already-released lock is worse to accept as proof of holding one
     * than accepting none at all, since it would read as protected when
     * it is not. Package-private -- this is a structural check on this
     * project's own {@code AccountLedgerStore} callers, not a public API.
     */
    void requireHeld() {
        if (closed) {
            throw new IllegalStateException(
                    "AccountLedgerLock for " + lockPath + " has already been released -- it cannot be used as"
                            + " proof of currently holding it");
        }
    }

    /**
     * Returns {@code true} if this call reached a final outcome (deleted,
     * confirmed already gone, or confirmed a state no retry could improve
     * on) -- see {@link #close}'s own Javadoc for why the caller must not
     * treat every normal return the same way.
     */
    private boolean doClose() {
        try {
            LockMetadata current = readMetadataOrNull(lockPath);
            if (current == null) {
                return true; // already gone -- tolerated, see method Javadoc
            }
            if (current == READ_FAILED) {
                log.error(
                        "account ledger lock {} could not be read on close (transient failure, acquired as {}) --"
                                + " NOT deleting, since this instance cannot prove the file is still its own"
                                + " generation. The file will block other waiters until its staleThreshold"
                                + " elapses. This is most likely a transient filesystem read failure, not proof"
                                + " that mutual exclusion was lost.",
                        lockPath,
                        ownMetadata);
                return false; // transient -- a later close() call may still resolve this
            }
            if (current == EMPTY_OR_UNPARSEABLE) {
                log.error(
                        "account ledger lock {} exists but its content is empty or unparseable on close"
                                + " (acquired as {}) -- NOT deleting, since this instance cannot prove the file is"
                                + " still its own generation (most likely raced a concurrent writer's own brief"
                                + " create-to-write window, or a transient filesystem read/visibility gap on this"
                                + " instance's own still-valid content). The file will block other waiters until"
                                + " its staleThreshold elapses.",
                        lockPath,
                        ownMetadata);
                // Retryable, not final. Unlike the genuine different-holder
                // mismatch below (where a real, live new holder provably
                // owns the path now, so no retry could ever help), this
                // branch cannot actually distinguish that same situation
                // from a transient read/visibility gap on this instance's
                // OWN still-valid content -- a real, previously-measured
                // characteristic of this project's own drvfs mount (see
                // this class's own class Javadoc: sub-3ms mtime precision,
                // 500ms+ transient I/O latency under contention). Treating
                // this the same retryable way READ_FAILED already is means
                // a later close() call gets a real chance to observe this
                // instance's own genuine content again and complete the
                // delete, rather than permanently stranding a lock this
                // instance still legitimately owns.
                return false;
            }
            if (!current.equals(ownMetadata)) {
                log.error(
                        "account ledger lock {} no longer holds this instance's own metadata (acquired as {},"
                                + " found {}) -- NOT deleting, since doing so could destroy a different, currently"
                                + " live holder's lock. This instance's own critical section ran for at least"
                                + " part of its duration without real mutual exclusion -- investigate (a slow"
                                + " filesystem operation or GC pause pushing past staleThreshold is the most"
                                + " likely cause, not a bug in this class's own steal logic).",
                        lockPath,
                        ownMetadata,
                        current);
                return true; // a different, live holder genuinely owns this path now -- final
            }
            Files.delete(lockPath);
            return true;
        } catch (NoSuchFileException e) {
            // tolerated -- see method Javadoc
            return true;
        } catch (IOException e) {
            log.error(
                    "failed to delete account ledger lock file {} on close -- it will block other waiters until"
                            + " its staleThreshold elapses: {}",
                    lockPath,
                    e.toString());
            return false; // transient -- a later close() call may still succeed
        }
    }
}
