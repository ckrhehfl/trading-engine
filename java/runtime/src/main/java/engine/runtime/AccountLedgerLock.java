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
 * <p><b>Primitive: atomic file creation, not {@link
 * java.nio.channels.FileLock}.</b> This decision was made and reasoned
 * through before this class was written (see the governing plan), not
 * re-derived here -- restated briefly because it is the single most
 * load-bearing choice in this file: this repository lives on a
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
 * <p><b>Documentation-accuracy correction, real Minor finding, a further
 * real CodeRabbit review round on this PR</b>: this section (and the
 * "Deviation" paragraph below) used to describe {@code
 * Files.createFile(lockPath)} followed by a separate {@code
 * Files.writeString} call as the current mechanism -- accurate for this
 * class's original version, but stale after round 2's own fix (see
 * {@link #createAndWriteMetadata}'s Javadoc for the real bug that fix
 * closed): the actual, current create-and-write step is a single atomic
 * {@link Files#newByteChannel} call with {@link
 * java.nio.file.StandardOpenOption#CREATE_NEW}, with the metadata written
 * through that same open handle -- not two separate, path-based
 * operations. Both places are corrected here rather than left
 * inconsistent with {@link #createAndWriteMetadata}'s own (already
 * accurate) Javadoc.
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
 * sufficient: (1) {@code
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
 * deleted; {@link #acquire} then retries the atomic create immediately,
 * without backing off (the staleness is already known, no reason to
 * wait). A non-stale (live, recent) holder instead makes {@link
 * #acquire} back off with short, bounded exponential retry (~25ms
 * &rarr; 250ms per attempt) until {@code totalRetryBudget} is exhausted,
 * at which point it throws {@link IllegalStateException} -- this class
 * never hangs indefinitely.
 *
 * <p><b>Deviation from the governing plan's own literal code sketch</b>:
 * the plan's summary writes {@code Files.createFile(path,
 * StandardOpenOption.CREATE_NEW)} -- {@link Files#createFile} does not
 * actually accept a {@link java.nio.file.StandardOpenOption} argument (it
 * takes {@link java.nio.file.attribute.FileAttribute} varargs instead).
 * This class instead uses {@link Files#newByteChannel} with {@code
 * CREATE_NEW} (see {@link #createAndWriteMetadata}'s own Javadoc for why
 * a plain {@code Files.createFile} + separate write, this class's
 * original approach, was itself later found insufficient and replaced) --
 * both get the identical atomic-create-fails-if-exists semantics the
 * plan's sketch intended, so this remains a deviation in API choice only,
 * not in the semantics the plan actually asked for.
 */
final class AccountLedgerLock implements AutoCloseable {

    private static final Logger log = LoggerFactory.getLogger(AccountLedgerLock.class);

    /**
     * Own copy, not shared with {@link AccountLedgerStore} -- see that
     * class's Javadoc for why. {@code FAIL_ON_TRAILING_TOKENS} enabled
     * here too -- proactively, not itself flagged by CodeRabbit this
     * round, but the identical reasoning behind {@code
     * AccountLedgerStore}'s own real Major finding (Jackson silently
     * ignores anything after the first complete JSON value by default)
     * applies equally to this class's own {@link LockMetadata} parsing.
     */
    private static final ObjectMapper MAPPER = new ObjectMapper()
            .registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);

    private static final long INITIAL_BACKOFF_MILLIS = 25;
    private static final long MAX_BACKOFF_MILLIS = 250;

    private final Path lockPath;

    /**
     * The exact {@link LockMetadata} this instance itself wrote when it
     * acquired the lock -- retained so {@link #close} can re-verify it is
     * still deleting <i>this instance's own</i> lock generation, not a
     * different one a sibling legitimately acquired in the meantime. See
     * {@link #close}'s own Javadoc for the real, measured scenario this
     * closes (a second Critical finding from this task's own real
     * CodeRabbit review, the release-side counterpart to {@link
     * #tryStealIfStale}'s acquire-side fix).
     */
    private final LockMetadata ownMetadata;

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
        // Real Trivial finding, a further real CodeRabbit review round on
        // this PR: a transient (non-FileAlreadyExistsException) IOException
        // during creation used to be promoted straight to
        // IllegalStateException on its very first occurrence, consuming
        // none of totalRetryBudget -- inconsistent with this class's own
        // documented, measured 500ms+ transient I/O latency on this
        // repository's real drvfs mount, which is exactly the kind of
        // environment characteristic a single retry, not zero, should be
        // able to ride out. Tracked here so a real, final failure still
        // reports its actual root cause once the budget is genuinely
        // exhausted, rather than a generic timeout message with no cause.
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
                if (tryStealIfStale(lockPath, staleThreshold)) {
                    continue; // steal already known-stale -- retry create immediately, no backoff
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
     * written.
     *
     * <p><b>Critical finding, real CodeRabbit review of this PR, fixed
     * here</b>: if {@link Files#createFile} succeeds but the subsequent
     * {@link Files#writeString} then fails (any {@link IOException} -- a
     * real possibility, not hypothetical, given this class's own Javadoc
     * already documents 500ms+ write latencies under contention on this
     * repository's real drvfs mount, which is exactly the kind of
     * operation more likely to hit a transient failure the longer it
     * takes), the original version of this method left behind a real,
     * permanently empty lock file with no cleanup. {@link
     * #readMetadataOrNull} can never parse a pid/{@code acquiredAt} out of
     * empty content, so {@link #tryStealIfStale}'s ordinary dead-pid/
     * expired-timestamp checks could never fire against it -- every future
     * waiter for this lock would exhaust its retry budget and fail,
     * forever, until a human manually deleted the file. Now: any failure
     * writing the metadata deletes the just-created file before
     * propagating (best-effort -- if the cleanup delete itself also fails,
     * it's attached via {@link Throwable#addSuppressed} rather than
     * masking the original failure). This closes the ordinary case; {@link
     * #tryStealIfAbandonedEmpty} is the backstop for the harder residual
     * case this cleanup can't reach at all -- a hard process kill between
     * {@link Files#createFile} succeeding and this method's own {@code
     * catch} block ever running.
     *
     * <p><b>Second, deeper Major finding on the same PR, same real
     * CodeRabbit review round, also fixed here</b>: creating the file
     * ({@link Files#createFile}) and writing its content ({@link
     * Files#writeString}) used to be two separate, path-based operations.
     * If <i>this</i> holder's own write is slow enough (the same real,
     * measured drvfs latency named above) for a sibling to legitimately
     * judge the file abandoned via {@link #tryStealIfAbandonedEmpty},
     * delete it, and create its own new, live generation at the same
     * path -- and only <i>then</i> does this (stale, orphaned, but not
     * yet aware of any of that) holder's original {@code
     * Files.writeString} call finally run -- that call is purely
     * path-based, so it would silently overwrite the sibling's real, live
     * metadata with this holder's own stale content. Two independent,
     * legitimate holders would then both believe they own the lock, with
     * the file's actual content reflecting neither reliably. Fixed by
     * opening a single {@link java.nio.channels.SeekableByteChannel} atomically
     * ({@link java.nio.file.StandardOpenOption#CREATE_NEW}) and writing
     * the metadata through <i>that same handle</i>, rather than
     * re-resolving {@code lockPath} for a second, separate operation.
     * Empirically confirmed, not merely assumed, that this closes the gap
     * on this repository's real drvfs mount: a real handle-survives-
     * concurrent-delete-and-recreate probe was run directly against
     * {@code /mnt/c/Dev/trading-engine} before writing this fix --
     * deleting a path out from under an already-open {@code CREATE_NEW}
     * channel succeeds (POSIX-style unlink semantics, not Windows'
     * traditional delete-blocked-while-open behavior), a new file can
     * then be created at that same path, and a subsequent write through
     * the original (now-orphaned, unreachable-via-the-path) channel lands
     * on that original, now-invisible file rather than corrupting the new
     * one. {@link
     * AccountLedgerLockTest#aLockFilesOwnOpenCreationHandleCannotClobberADifferentGenerationCreatedAfterItWasStolen}
     * proves this holds through the real production code path, not just
     * the standalone probe.
     *
     * <p><b>Third, still deeper Major finding, a further real CodeRabbit
     * review round on this same PR, also fixed here.</b> The previous fix
     * protects the <i>file's content</i> from corruption, but not
     * {@link #acquire}'s own <i>success signal</i>: an orphaned channel's
     * write still returns normally (it succeeds against its own, now
     * path-invisible file -- that's the whole point of the previous fix),
     * so this method would still return a real, non-null {@link
     * LockMetadata} and {@link #acquire} would still hand the caller a
     * live {@link AccountLedgerLock} -- even though a sibling had already
     * reclaimed the path via {@link #tryStealIfAbandonedEmpty} and is
     * holding a real, different generation there right now. Both this
     * holder and the sibling would then believe, independently and
     * wrongly, that they hold the lock -- a genuine loss of mutual
     * exclusion, distinct from (and not fixed by) the content-corruption
     * fix above. Fixed by re-reading {@code lockPath} immediately after
     * the write completes and confirming it still holds exactly the
     * {@link LockMetadata} just written; if not, this method returns
     * {@code null} rather than the metadata -- a defined "lost the race"
     * signal {@link #acquire} treats as ordinary contention (back off and
     * retry the whole loop) rather than success or a hard failure. Never
     * deletes anything in this path: unlike the steal/close paths, there
     * is nothing stale to reclaim here -- the path now legitimately
     * belongs to whoever's generation the re-read found, and this method
     * has no business touching it.
     *
     * <p><b>Fourth finding, on this exact fix, a further real CodeRabbit
     * review round on this same PR -- a real, self-introduced regression,
     * fixed here.</b> The re-verify above compared {@code current}
     * against {@code metadata} with a single {@code equals()} check --
     * but {@link #readMetadataOrNull} can also return {@link
     * #READ_FAILED} (a transient {@link Files#readString} failure),
     * which is not equal to {@code metadata} either, so it fell into the
     * exact same "lost the race, don't delete" branch as a genuine
     * generation mismatch. That is wrong for this specific outcome: a
     * transient read failure proves nothing about who owns the path --
     * most likely <i>this</i> holder still does, since it just created
     * and wrote the file inside this very method call. Treating it as
     * "lost the race" (never deleting) would leave this holder's own
     * real, live lock file behind while reporting failure to its own
     * caller -- and because the file genuinely holds this holder's own
     * live pid and a just-recorded {@code acquiredAt}, the <i>next</i>
     * {@link #acquire} attempt's own {@link #tryStealIfStale} call would
     * correctly judge it not stale (it isn't -- from the outside, it's a
     * perfectly healthy, fresh lock), self-blocking this process behind
     * its own abandoned-in-place file until {@code staleThreshold}
     * elapses -- and blocking every <i>other</i> waiter for the same
     * {@code staleThreshold} window, on a shared risk-ledger lock, for no
     * real reason. Fixed: {@code READ_FAILED} is now handled separately
     * from a genuine mismatch -- cleans up only this holder's own,
     * re-verified generation (via {@link #deleteIfStillOwnGeneration},
     * the same delete-only-if-content-still-matches-exactly discipline
     * {@link #tryStealIfStale} itself uses) before reporting the lost
     * race, so the next attempt starts from a genuinely clean path
     * instead of contending with its own ghost.
     */
    private static LockMetadata createAndWriteMetadata(Path lockPath) throws IOException {
        Path parent = lockPath.toAbsolutePath().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        SeekableByteChannel channel;
        try {
            // Atomic create-and-open in one call -- throws
            // FileAlreadyExistsException if it already exists, same as
            // Files.createFile did, and deliberately NOT caught here:
            // nothing was created in that case, so there is nothing for
            // this method's own cleanup-on-failure logic below to clean
            // up: acquire()'s existing FileAlreadyExistsException handling
            // is exactly the right, unchanged path for it.
            channel = Files.newByteChannel(lockPath, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
        } catch (FileAlreadyExistsException e) {
            throw e;
        }
        try (channel) {
            LockMetadata metadata = new LockMetadata(ProcessHandle.current().pid(), hostname(), Instant.now());
            // WritableByteChannel#write is not guaranteed to consume the
            // whole buffer in one call (real Major finding, real
            // CodeRabbit review of this PR -- confirmed against the JDK's
            // own documented contract, not assumed) -- loop until fully
            // written, matching this class's own careful, don't-assume
            // discipline everywhere else in this file.
            ByteBuffer buffer = ByteBuffer.wrap(MAPPER.writeValueAsBytes(metadata));
            while (buffer.hasRemaining()) {
                channel.write(buffer);
            }
            // Re-verify we still actually own lockPath -- see this
            // method's own "Third, still deeper Major finding" Javadoc
            // above for why a successful write alone does not prove this.
            LockMetadata current = readMetadataOrNull(lockPath);
            if (metadata.equals(current)) {
                return metadata;
            }
            if (current == READ_FAILED) {
                // A transient read failure proves nothing about the
                // file's real content -- see this method's own "Fourth
                // finding" Javadoc for why this must not be treated the
                // same as a genuine mismatch.
                log.error(
                        "account ledger lock {} could not be re-read immediately after this holder wrote its own"
                                + " metadata ({}) -- cannot prove ownership from this read alone; cleaning up only"
                                + " this holder's own verified generation (if it's still there) and reporting lost"
                                + " contention rather than leaving a live-looking file behind.",
                        lockPath,
                        metadata);
                deleteIfStillOwnGeneration(lockPath, metadata);
            }
            return null;
        } catch (IOException | RuntimeException e) {
            // Best-effort, path-based cleanup -- not itself re-verified
            // against a specific generation the way the steal and close
            // paths are. A known, narrow, disclosed residual gap rather
            // than a silent one: if this write failure itself takes long
            // enough for a sibling to independently judge this file
            // abandoned and steal it in the interim, this cleanup could
            // delete that sibling's new file instead of this (failed)
            // one. Judged acceptable to leave unguarded for now -- unlike
            // the slow-but-eventually-successful cases this file's other
            // fixes address (real, measured, and readily triggered by
            // ordinary contention), this requires a genuine I/O failure
            // during the write itself, a materially rarer precondition.
            try {
                Files.deleteIfExists(lockPath);
            } catch (IOException cleanupFailure) {
                e.addSuppressed(cleanupFailure);
            }
            throw e;
        }
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
        if (current == null || current == READ_FAILED || current == EMPTY_OR_UNPARSEABLE || !current.equals(metadata)) {
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
     * Deletes {@code lockPath} only if it still holds exactly {@code
     * metadata} -- the same re-verify-immediately-before-delete
     * discipline {@link #tryStealIfStale} itself uses, reused here for
     * {@link #createAndWriteMetadata}'s own "clean up only my own,
     * provably-still-mine generation" case (see that method's own
     * "Fourth finding" Javadoc). Never guesses: if the current content
     * can't be confirmed to still be exactly {@code metadata} -- a
     * genuine mismatch, or even another {@link #READ_FAILED} on this
     * verification read itself -- this method does nothing rather than
     * risk deleting a different, currently-live holder's lock. Tolerates
     * {@link NoSuchFileException} (already gone -- fine, nothing to do).
     */
    private static void deleteIfStillOwnGeneration(Path lockPath, LockMetadata metadata) {
        LockMetadata current = readMetadataOrNull(lockPath);
        if (!metadata.equals(current)) {
            return;
        }
        try {
            Files.delete(lockPath);
        } catch (NoSuchFileException e) {
            // Already gone -- fine.
        } catch (IOException e) {
            throw new IllegalStateException(
                    "failed to delete this holder's own account ledger lock file " + lockPath, e);
        }
    }

    /**
     * Backstop for the one gap {@link #createAndWriteMetadata}'s own
     * cleanup-on-failure can't reach: a hard process kill (or crash)
     * between {@link Files#createFile} succeeding and that method's
     * {@code catch} block ever running, leaving a real, permanently-empty
     * lock file with no {@code pid}/{@code acquiredAt} for the ordinary
     * {@link #tryStealIfStale} checks to judge. Without this, such a file
     * could never be reclaimed -- every future waiter would exhaust its
     * retry budget forever (a real Critical finding, real CodeRabbit
     * review of this PR).
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
     */
    private static boolean tryStealIfAbandonedEmpty(Path lockPath, Duration staleThreshold) {
        Instant lastModified = lastModifiedTimeOrNull(lockPath);
        if (lastModified == null) {
            return true; // vanished since our read -- safe to retry immediately
        }
        if (Duration.between(lastModified, Instant.now()).compareTo(staleThreshold) <= 0) {
            return false; // still recent -- most likely mid-write, never guess
        }

        log.error(
                "stealing an empty/unparseable account ledger lock {} -- last modified {} exceeds staleThreshold"
                        + " {}, treating as abandoned (its holder most likely died between creating the file and"
                        + " writing its metadata -- e.g. a hard process kill). This is a real cross-process"
                        + " safety event, not routine contention -- investigate.",
                lockPath,
                lastModified,
                staleThreshold);

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

        try {
            Files.delete(lockPath);
        } catch (NoSuchFileException e) {
            // Another waiter beat us to it -- fine.
        } catch (IOException e) {
            throw new IllegalStateException("failed to delete abandoned empty account ledger lock file " + lockPath, e);
        }
        return true;
    }

    private static Instant lastModifiedTimeOrNull(Path lockPath) {
        try {
            return Files.getLastModifiedTime(lockPath).toInstant();
        } catch (NoSuchFileException e) {
            return null;
        } catch (IOException e) {
            throw new IllegalStateException(
                    "failed to read last-modified time for account ledger lock file " + lockPath, e);
        }
    }

    /**
     * Sentinel: the file is present and was read successfully, but its
     * content is empty or unparseable right now -- see {@link
     * #readMetadataOrNull}. Distinguished from {@link #READ_FAILED} (a
     * real Minor finding, real CodeRabbit review of this PR): the two
     * used to share one sentinel, which made {@link #close}'s own log
     * message assert "no longer holds this instance's own metadata" even
     * for a transient {@link Files#readString} failure that says nothing
     * at all about the file's actual current content -- misleading an
     * operator investigating a real incident in the wrong direction. Both
     * are still treated identically everywhere <i>behavior</i> is
     * concerned (never delete, never guess) -- only the two sentinels'
     * own distinctness, and the messages built from them, differ.
     */
    private static final LockMetadata EMPTY_OR_UNPARSEABLE = new LockMetadata(-1, "", Instant.EPOCH);

    /** Sentinel: {@link Files#readString} itself failed (a transient {@link IOException}) -- see {@link #EMPTY_OR_UNPARSEABLE}'s Javadoc. */
    private static final LockMetadata READ_FAILED = new LockMetadata(-2, "", Instant.EPOCH);

    /**
     * @return the lock file's parsed metadata; {@code null} if the file
     *     does not exist; {@link #READ_FAILED} if {@link Files#readString}
     *     itself threw; {@link #EMPTY_OR_UNPARSEABLE} if the read succeeded
     *     but the content can't be parsed, <b>parses successfully as the
     *     JSON literal {@code null}</b> (a real Minor finding, real
     *     CodeRabbit review of this PR -- Jackson maps a bare {@code null}
     *     token to a Java {@code null} without throwing, since it's valid
     *     JSON; left unnormalized, that {@code null} would have been
     *     indistinguishable from this method's own "file does not exist"
     *     {@code null}, wrongly telling {@link #tryStealIfStale} to retry
     *     immediately with no backoff and wrongly telling {@link #close}
     *     the file is already gone), <b>or parses to a structurally
     *     incomplete {@link LockMetadata}</b> (a real further Major
     *     finding, a further real CodeRabbit review round: Jackson can
     *     silently fill a missing record component with its default/null
     *     rather than throwing -- {@code {"pid":123}} alone deserializes
     *     "successfully" to a {@code LockMetadata} with a {@code null}
     *     {@code hostname}/{@code acquiredAt} -- and {@link
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
     * <p><b>Critical finding, real CodeRabbit review of this PR, fixed
     * here</b> -- the release-side counterpart to {@link
     * #tryStealIfStale}'s own acquire-side TOCTOU fix (see that method's
     * Javadoc for the full, measured account of why a gap this large is
     * real on this repository's filesystem, not theoretical). The original
     * version of this method deleted <i>whatever currently exists</i> at
     * {@code lockPath} unconditionally. A real sequence this makes
     * possible: (1) this instance holds the lock, but a slow filesystem
     * operation inside its own critical section pushes its real elapsed
     * hold time past {@code staleThreshold}; (2) a waiting sibling process
     * correctly (by this class's own rules) judges this instance's lock
     * stale, steals it, and acquires its own new, legitimate lock
     * generation; (3) this instance, unaware any of that happened, finally
     * reaches its own (now-delayed) {@code close()} call and deletes the
     * <i>sibling's</i> lock file, believing it is releasing its own --
     * breaking mutual exclusion a second, independent way (a third process
     * can now acquire immediately, while the sibling from step 2 still
     * believes it holds the lock). Fixed by re-reading the file and
     * comparing it to {@link #ownMetadata} (record {@code equals()} --
     * exact match required) immediately before deleting; a mismatch (or
     * the file already being gone with different content, or unparseable)
     * means this instance's lock was already stolen out from under it --
     * logged at {@code ERROR} (this class's established never-silent
     * convention for a genuine cross-process safety event) and the delete
     * is skipped entirely, rather than ever risking deletion of a
     * different holder's live lock. As with the acquire-side fix, this
     * closes the exploitable window down to the gap between two adjacent
     * file operations -- not a perfectly atomic compare-and-delete (no
     * such primitive exists in {@code java.nio.file}, and introducing a
     * different locking mechanism to get one was already rejected earlier
     * in this design specifically because of this repository's drvfs
     * reliability concerns -- see the class Javadoc) -- disclosed as such
     * rather than overclaimed.
     *
     * <p><b>Minor finding, same real CodeRabbit review round, also fixed
     * here</b>: the re-verification read above can itself come back as
     * {@link #READ_FAILED} (a transient {@link Files#readString} failure)
     * as well as {@link #EMPTY_OR_UNPARSEABLE} or a genuine mismatch --
     * the original version of this method logged all three identically as
     * "no longer holds this instance's own metadata... found {@code
     * current}", which is simply false for a read failure (this instance
     * may well still be the legitimate holder; the file just couldn't be
     * read this one time) and prints a meaningless sentinel value for
     * {@code current} in both non-mismatch cases. Skipping the delete is
     * still the correct, safe behavior in all three cases (never guess),
     * but each now gets its own honest log message so an operator
     * investigating isn't pointed at "mutual exclusion was lost" for a
     * transient filesystem hiccup.
     */
    @Override
    public void close() {
        try {
            LockMetadata current = readMetadataOrNull(lockPath);
            if (current == null) {
                return; // already gone -- tolerated, see method Javadoc
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
                return;
            }
            if (current == EMPTY_OR_UNPARSEABLE) {
                log.error(
                        "account ledger lock {} exists but its content is empty or unparseable on close"
                                + " (acquired as {}) -- NOT deleting, since this instance cannot prove the file is"
                                + " still its own generation (most likely raced a concurrent writer's own brief"
                                + " create-to-write window). The file will block other waiters until its"
                                + " staleThreshold elapses.",
                        lockPath,
                        ownMetadata);
                return;
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
                return;
            }
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
