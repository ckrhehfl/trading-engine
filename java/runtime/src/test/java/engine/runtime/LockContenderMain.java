package engine.runtime;

import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;

/**
 * Standalone entry point launched as a genuine second (or third, ...) OS
 * process by {@link AccountLedgerLockMultiProcessTest}, via {@link
 * ProcessBuilder} -- the real proof that {@link AccountLedgerLock}'s
 * atomic-file-creation mutex actually provides mutual exclusion across
 * separate JVMs on this repository's real filesystem (a Windows-mounted
 * drvfs volume under WSL2, {@code /mnt/c/...}), not merely across threads
 * in one JVM (see {@link AccountLedgerLockTest} for that separate,
 * cheaper, complementary proof).
 *
 * <p>Not a test itself -- has no assertions and is never run directly by
 * the JUnit platform (no {@code @Test}, and this class is intentionally
 * not named {@code *Test}). It repeatedly acquires the shared lock at
 * {@code args[0]} and, while holding it, performs a deliberately
 * non-atomic read-sleep-increment-write cycle against a shared counter
 * file at {@code args[1]}: if two real OS processes were ever inside the
 * lock-protected section at the same time, at least one increment would
 * be lost, and the launching test's own final-count assertion catches it.
 *
 * <p>Exits {@code 0} on success; an uncaught exception propagates out of
 * {@code main}, which the JVM reports to stderr and exits non-zero for --
 * the launching test checks both.
 */
final class LockContenderMain {

    private LockContenderMain() {}

    private static final String USAGE = "usage: LockContenderMain <lockPath> <counterPath> <iterations>"
            + " <staleThresholdMillis> <totalRetryBudgetMillis> <holdMillis>";

    public static void main(String[] args) throws Exception {
        // Real Trivial finding, a further real CodeRabbit review round on
        // this PR: this class runs in a genuinely separate JVM (see class
        // Javadoc), so an unvalidated ArrayIndexOutOfBoundsException/
        // NumberFormatException from a real launch-configuration mistake
        // would only ever surface as a raw stack trace in the launching
        // test's own captured stderr file -- a clear usage message here
        // lets that be told apart immediately from a genuine mutual-
        // exclusion defect, rather than requiring the stderr file to be
        // opened and read first.
        if (args.length != 6) {
            throw new IllegalArgumentException(USAGE + "; got " + args.length + " argument(s)");
        }
        Path lockPath = Path.of(args[0]);
        Path counterPath = Path.of(args[1]);
        int iterations;
        long staleThresholdMillis;
        long totalRetryBudgetMillis;
        long holdMillis;
        try {
            iterations = Integer.parseInt(args[2]);
            staleThresholdMillis = Long.parseLong(args[3]);
            totalRetryBudgetMillis = Long.parseLong(args[4]);
            holdMillis = Long.parseLong(args[5]);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException(
                    USAGE + " -- one or more numeric arguments could not be parsed: " + e.getMessage(), e);
        }
        Duration staleThreshold = Duration.ofMillis(staleThresholdMillis);
        Duration totalRetryBudget = Duration.ofMillis(totalRetryBudgetMillis);

        for (int i = 0; i < iterations; i++) {
            AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, staleThreshold, totalRetryBudget);
            try {
                int current = readCounter(counterPath, i == 0);
                Thread.sleep(holdMillis);
                writeCounterAtomically(counterPath, current + 1);
            } finally {
                closeUntilReleased(lock);
            }
        }
    }

    /**
     * Real Major finding, a further real CodeRabbit review round on this
     * PR: {@code counterPath} being absent is only ever a normal state on
     * this process's own very first iteration, before any contender has
     * ever written it. On any later iteration, an observed absence means
     * the file this process (or a sibling) already created has vanished
     * again -- possibly via {@link #writeCounterAtomically}'s own
     * non-atomic fallback move path, or this project's own documented
     * drvfs visibility gap -- and silently treating that as "0" would
     * rewind the counter, losing every increment recorded so far. The
     * launching {@link AccountLedgerLockMultiProcessTest} would then
     * misattribute the resulting final-count shortfall to a genuine
     * mutual-exclusion defect (two processes inside the lock-protected
     * section at once) when the real cause is this harness losing its own
     * bookkeeping. This harness is Task B's own sole empirical proof of
     * real cross-process mutual exclusion (see class Javadoc); a failure
     * mode that pollutes its signal is removed here, not merely
     * tolerated: {@code absenceIsExpected} is {@code true} only for
     * {@code i == 0}; any later absence propagates as a real, explicit
     * {@link IllegalStateException} instead.
     */
    private static int readCounter(Path counterPath, boolean absenceIsExpected) throws Exception {
        try {
            return Integer.parseInt(Files.readString(counterPath).trim());
        } catch (NoSuchFileException e) {
            if (!absenceIsExpected) {
                throw new IllegalStateException(
                        "counter file " + counterPath + " vanished after it had already been written --"
                                + " this is a harness/filesystem fault, not a mutual-exclusion failure",
                        e);
            }
            return 0;
        }
    }

    /**
     * Writes via a temp file plus {@link StandardCopyOption#ATOMIC_MOVE}
     * -- the same pattern {@code AccountLedgerStore#persist} already
     * establishes, reused here for the identical underlying reason. A
     * direct {@code Files.writeString(counterPath, ...)} truncates then
     * writes as two separate steps; even though {@code counterPath} is
     * only ever written while this process holds the real, shared {@link
     * AccountLedgerLock}, this project's own class Javadoc documents
     * real, measured transient I/O latency and read/visibility gaps on
     * this repository's real drvfs mount -- a real, if narrow, risk that
     * the <i>next</i> holder's own {@link #readCounter} call could
     * observe the post-truncate, pre-write empty state and throw {@code
     * NumberFormatException}, exiting this whole process non-zero for a
     * reason with nothing to do with mutual exclusion. This test harness
     * is Task B's own sole empirical proof of real cross-process mutual
     * exclusion (see class Javadoc); a failure mode that pollutes its
     * signal is removed here, not merely tolerated. The temp file name
     * includes this process's own pid so genuinely concurrent contenders
     * (each writing only while holding the lock, but still real,
     * separate processes) can never collide on it even outside the
     * lock-protected window.
     */
    private static void writeCounterAtomically(Path counterPath, int value) throws IOException {
        Path tmp = counterPath.resolveSibling(
                counterPath.getFileName() + ".tmp." + ProcessHandle.current().pid());
        Files.writeString(tmp, Integer.toString(value));
        try {
            Files.move(tmp, counterPath, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (AtomicMoveNotSupportedException | FileAlreadyExistsException e) {
            // Real Trivial finding, a further real CodeRabbit review round
            // on this PR: entering this fallback means real atomicity was
            // just lost for this write -- silently proceeding hides that
            // fact from the launching test's own stderr-file diagnostics
            // (see class Javadoc), so a later counter shortfall caused by
            // this exact fallback path would be indistinguishable from a
            // genuine mutual-exclusion defect. Logged before the fallback
            // move itself, not after, so the log line exists even if the
            // fallback move also fails.
            System.err.println("WARNING: ATOMIC_MOVE unavailable for " + counterPath
                    + " -- falling back to a non-atomic replace. A later counter shortfall may be a harness"
                    + " artifact rather than a real mutual-exclusion failure. Cause: " + e);
            // Same fallback this project's own durable-store classes
            // already establish for the identical class of failure (some
            // filesystems/cross-volume setups don't support ATOMIC_MOVE
            // at all, or implementation-specifically reject an existing
            // target rather than replace it) -- not atomic, but strictly
            // better than leaving counterPath in its pre-truncate state
            // forever on a real move failure.
            Files.move(tmp, counterPath, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    /**
     * Retries {@link AccountLedgerLock#close()} until {@code lockPath} is
     * confirmed gone: {@code close()} does not itself signal success or
     * failure (see {@link AccountLedgerLock}'s own class Javadoc, fourth
     * caller-contract point) and can take a retryable, non-final path on
     * this project's own drvfs mount, so a single call is not sufficient
     * proof of release under this launcher's own real, sustained
     * multi-process contention (the whole point of {@link
     * AccountLedgerLockMultiProcessTest}).
     *
     * <p>Real Minor finding, a further real CodeRabbit review round on
     * this PR: the retry budget here previously totaled only 250ms (5
     * attempts x 50ms) -- shorter than this project's own real, measured
     * single-file-operation latency on this repository's drvfs mount
     * (500ms+ under contention, per {@link AccountLedgerLock}'s own class
     * Javadoc). A budget too small to absorb that delay defeats this
     * method's own purpose: a contender could exit having left the lock
     * file behind for a reason unrelated to a real defect, blocking the
     * next holder until {@code staleThreshold} elapses. Now 40 attempts x
     * 50ms = ~2s, matching the identical fix applied to {@link
     * AccountLedgerLockTest#closeUntilReleased} and {@code
     * AccountLedgerLockMultiProcessTest#waitForLockFileAbsence} (round
     * 56's own three sibling fixes).
     *
     * <p>Real Minor finding, a further real CodeRabbit review round on
     * this PR: {@code Files.notExists(lockPath)} alone is not proof this
     * process's own release genuinely completed -- under real
     * contention, a sibling contender (or the launching test's own
     * generation-stealing) can legitimately acquire the very next
     * generation at the same path immediately after this process's own
     * release completes, so the file can still exist (now under a
     * different, live generation) even though this process is genuinely,
     * fully released. Polling mere existence in that case wastes the
     * whole retry budget above on a wait that was already over -- exactly
     * the extra cost {@code holdMillis}-driven contention in {@link
     * AccountLedgerLockMultiProcessTest} is likely to trigger. Now polls
     * {@link AccountLedgerLock#stillHoldsCurrentGeneration()} instead,
     * matching the identical fix applied to {@link
     * AccountLedgerLockTest#closeUntilReleased} -- see that method's own
     * Javadoc for why {@link AccountLedgerLock#requireHeld()} cannot
     * substitute for this. The {@code lockPath} parameter this method
     * used to take is no longer needed -- {@code lock} alone now fully
     * determines the loop's termination condition.
     */
    private static void closeUntilReleased(AccountLedgerLock lock) throws InterruptedException {
        for (int attempt = 0; attempt < 40; attempt++) {
            lock.close();
            if (!lock.stillHoldsCurrentGeneration()) {
                return;
            }
            Thread.sleep(50);
        }
        // Not treated as a hard failure here -- if this process's own
        // generation is genuinely still present after this many attempts,
        // the launching test's own final assertions (the counter total,
        // and its own lock-file-absence check) are what actually judge
        // this run, not this launcher itself.
    }
}
