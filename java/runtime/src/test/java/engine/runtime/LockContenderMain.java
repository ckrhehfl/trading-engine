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

    public static void main(String[] args) throws Exception {
        Path lockPath = Path.of(args[0]);
        Path counterPath = Path.of(args[1]);
        int iterations = Integer.parseInt(args[2]);
        Duration staleThreshold = Duration.ofMillis(Long.parseLong(args[3]));
        Duration totalRetryBudget = Duration.ofMillis(Long.parseLong(args[4]));
        long holdMillis = Long.parseLong(args[5]);

        for (int i = 0; i < iterations; i++) {
            try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, staleThreshold, totalRetryBudget)) {
                int current = readCounter(counterPath);
                Thread.sleep(holdMillis);
                Files.writeString(counterPath, Integer.toString(current + 1));
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
}
