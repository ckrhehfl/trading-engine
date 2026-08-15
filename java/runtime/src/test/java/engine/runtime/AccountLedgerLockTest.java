package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * In-process tests for {@link AccountLedgerLock}: real-OS-thread mutual
 * exclusion, fabricated-stale-lock stealing (dead PID and expired
 * timestamp, separately), and retry-budget exhaustion. The complementary,
 * real-<b>process</b> proof (the actual point of Task B -- see the
 * governing plan) lives in {@link AccountLedgerLockMultiProcessTest},
 * kept in its own file since it launches genuine second JVMs and is
 * meaningfully slower than everything here.
 */
class AccountLedgerLockTest {

    private static final Duration GENEROUS_STALE_THRESHOLD = Duration.ofSeconds(30);
    private static final Duration GENEROUS_RETRY_BUDGET = Duration.ofSeconds(5);

    /**
     * Many real OS threads race {@link AccountLedgerLock#acquire} against
     * the same lock path. Mutual exclusion is proven the same way the
     * governing plan itself suggests: a critical section that performs a
     * deliberately non-atomic read-sleep-increment-write on an unguarded
     * shared counter. If two threads were ever inside the lock at once, at
     * least one increment is lost and the final count falls short of the
     * expected total -- a real data race would be caught, not just
     * asserted away. All threads are also proven to have eventually
     * succeeded (no thread swallowed an exception).
     */
    @Test
    void acquireProvidesRealMutualExclusionAcrossManyThreads(@TempDir Path tempDir) throws InterruptedException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        int threadCount = 12;
        int iterationsPerThread = 10;
        int[] counter = {0}; // unguarded on purpose -- see method Javadoc
        List<Throwable> failures = new CopyOnWriteArrayList<>();
        CountDownLatch done = new CountDownLatch(threadCount);

        for (int t = 0; t < threadCount; t++) {
            Thread thread = new Thread(() -> {
                try {
                    for (int i = 0; i < iterationsPerThread; i++) {
                        try (AccountLedgerLock lock =
                                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET)) {
                            int current = counter[0];
                            Thread.sleep(2); // widen the race window
                            counter[0] = current + 1;
                        }
                    }
                } catch (Throwable e) {
                    failures.add(e);
                } finally {
                    done.countDown();
                }
            });
            thread.start();
        }

        assertTrue(done.await(30, TimeUnit.SECONDS), "all threads must finish within the test timeout");
        assertTrue(failures.isEmpty(), "no thread should have failed to acquire: " + failures);
        assertEquals(
                threadCount * iterationsPerThread,
                counter[0],
                "a lower-than-expected count means two threads were inside the lock-protected section at once");
        assertFalse(Files.exists(lockPath), "the lock file must not be left behind once every holder has released it");
    }

    /**
     * Hand-writes a lock file whose recorded {@code pid} does not
     * correspond to any real running process (verified via {@link
     * ProcessHandle#of} before relying on it, per the governing task
     * brief -- not merely assumed absent) and a fresh {@code acquiredAt}
     * well inside {@code staleThreshold}. {@link AccountLedgerLock#acquire}
     * must steal it via the dead-PID path alone and succeed.
     */
    @Test
    void acquireStealsAFabricatedLockWithADeadPid(@TempDir Path tempDir) throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        long deadPid = findAPidThatIsNotRunning();

        Files.writeString(
                lockPath,
                "{\"pid\":" + deadPid + ",\"hostname\":\"stale-host\",\"acquiredAt\":\"" + Instant.now() + "\"}");

        try (AccountLedgerLock lock =
                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET)) {
            assertTrue(Files.exists(lockPath), "a real lock must have been created after the steal");
        }
    }

    /**
     * Hand-writes a lock file whose recorded {@code pid} <b>is</b> this
     * test's own real, live process (so the dead-PID path cannot fire) but
     * whose {@code acquiredAt} is far older than {@code staleThreshold}.
     * {@link AccountLedgerLock#acquire} must steal it via the expired-
     * timestamp path alone and succeed -- proven separately from the
     * dead-PID case above, per the governing task brief's explicit
     * requirement.
     */
    @Test
    void acquireStealsAFabricatedLockWithAnExpiredTimestampEvenIfTheHolderIsAlive(@TempDir Path tempDir)
            throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        long livePid = ProcessHandle.current().pid();
        Duration staleThreshold = Duration.ofMillis(50);
        Instant longAgo = Instant.now().minus(Duration.ofSeconds(60));

        Files.writeString(
                lockPath,
                "{\"pid\":" + livePid + ",\"hostname\":\"stale-host\",\"acquiredAt\":\"" + longAgo + "\"}");

        try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, staleThreshold, GENEROUS_RETRY_BUDGET)) {
            assertTrue(Files.exists(lockPath), "a real lock must have been created after the steal");
        }
    }

    /**
     * A real, live thread holds the lock for longer than {@code
     * totalRetryBudget}. A second {@code acquire} call must throw {@link
     * IllegalStateException} rather than hang, and must do so close to
     * (not wildly beyond) the configured budget.
     */
    @Test
    void acquireThrowsRatherThanHangingWhenTheRetryBudgetIsExhausted(@TempDir Path tempDir) throws Exception {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        Duration retryBudget = Duration.ofMillis(500);
        CountDownLatch holderReady = new CountDownLatch(1);
        CountDownLatch releaseHolder = new CountDownLatch(1);
        List<Throwable> holderFailures = new ArrayList<>();

        Thread holder = new Thread(() -> {
            try (AccountLedgerLock lock =
                    AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET)) {
                holderReady.countDown();
                releaseHolder.await(10, TimeUnit.SECONDS);
            } catch (Throwable e) {
                holderFailures.add(e);
            }
        });
        holder.start();
        assertTrue(holderReady.await(5, TimeUnit.SECONDS), "holder thread must acquire the lock first");

        try {
            long start = System.nanoTime();
            IllegalStateException thrown = assertThrows(
                    IllegalStateException.class,
                    () -> AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, retryBudget));
            long elapsed = System.nanoTime() - start;

            assertTrue(thrown.getMessage().contains(lockPath.toString()), "exception must name the lock path");
            long elapsedMillis = Duration.ofNanos(elapsed).toMillis();
            assertTrue(
                    elapsedMillis < retryBudget.toMillis() + 1000,
                    "must fail close to the configured budget (" + retryBudget + "), not wildly exceed it; took "
                            + elapsedMillis + "ms");
            assertTrue(
                    elapsedMillis >= retryBudget.toMillis() - 50,
                    "must not give up meaningfully before the configured budget elapsed; took " + elapsedMillis
                            + "ms");
        } finally {
            releaseHolder.countDown();
            holder.join(TimeUnit.SECONDS.toMillis(10));
        }
        assertTrue(holderFailures.isEmpty(), "holder thread must not have failed: " + holderFailures);
    }

    /** Finds a pid with no corresponding real process, verified rather than merely guessed. */
    private static long findAPidThatIsNotRunning() {
        // Real PIDs are typically small positive numbers on Linux; a huge,
        // implausible value is exceedingly unlikely to collide with a real
        // process, but this is verified below rather than trusted blindly.
        long candidate = 999_999_999L;
        if (ProcessHandle.of(candidate).map(ProcessHandle::isAlive).orElse(false)) {
            fail("candidate pid " + candidate + " unexpectedly corresponds to a real running process;"
                    + " pick a different fabricated value");
        }
        return candidate;
    }
}
