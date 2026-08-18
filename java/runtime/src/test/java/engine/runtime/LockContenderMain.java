package engine.runtime;

import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
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
                int current = readCounter(counterPath);
                Thread.sleep(holdMillis);
                Files.writeString(counterPath, Integer.toString(current + 1));
            } finally {
                closeUntilReleased(lock, lockPath);
            }
        }
    }

    private static int readCounter(Path counterPath) throws Exception {
        try {
            return Integer.parseInt(Files.readString(counterPath).trim());
        } catch (NoSuchFileException e) {
            return 0;
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
     */
    private static void closeUntilReleased(AccountLedgerLock lock, Path lockPath) throws InterruptedException {
        for (int attempt = 0; attempt < 5; attempt++) {
            lock.close();
            if (Files.notExists(lockPath)) {
                return;
            }
            Thread.sleep(50);
        }
        // Not treated as a hard failure here -- if the lock file is
        // genuinely still present after this many attempts, the launching
        // test's own final assertions (the counter total, and its own
        // lock-file-absence check) are what actually judge this run, not
        // this launcher itself.
    }
}
