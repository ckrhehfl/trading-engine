package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledOnOs;
import org.junit.jupiter.api.condition.OS;
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
    /**
     * Real Minor finding, real CodeRabbit review of this PR: 12 threads x
     * 10 acquisitions, each waiter backing off up to 250ms per failed
     * attempt, can plausibly accumulate close to (or past)
     * {@link #GENEROUS_RETRY_BUDGET}'s shared 5s budget under ordinary
     * queuing alone -- not a mutual-exclusion bug, just real contention --
     * especially combined with this class's own documented 500ms+ single-
     * operation latency under load on this repository's real drvfs mount.
     * A budget exhaustion there would fail this test for the wrong reason
     * (test-harness impatience, not a lock defect), misleading whoever
     * reads the failure. This test gets its own, much more generous,
     * dedicated budget instead of sharing the smaller one other tests use
     * deliberately (to keep {@code acquireThrowsRatherThanHangingWhenTheRetryBudgetIsExhausted}
     * fast).
     */
    private static final Duration CONTENTION_RETRY_BUDGET = Duration.ofSeconds(60);

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
                                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, CONTENTION_RETRY_BUDGET)) {
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

        // Comfortably exceeds CONTENTION_RETRY_BUDGET itself -- must never
        // be the thing that times this test out before a real per-thread
        // budget exhaustion would.
        assertTrue(done.await(90, TimeUnit.SECONDS), "all threads must finish within the test timeout");
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
                    // Slack must absorb the last backoff sleep (<=250ms)
                    // plus one real createFile/readString round trip --
                    // AccountLedgerLock's own Javadoc documents measured
                    // 500ms+ single-operation latency under contention on
                    // this repository's real drvfs mount (see "The real
                    // finding" in .planning/kis-ledger-b-*.md). A tighter
                    // bound here risks a real, environment-driven flake,
                    // not just a theoretical one (Minor finding, real
                    // CodeRabbit review of this PR).
                    elapsedMillis < retryBudget.toMillis() + 3000,
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

    /**
     * Deterministic regression test for a Critical finding from this
     * task's own real CodeRabbit review (see {@link AccountLedgerLock
     * #close}'s own Javadoc for the full real scenario this closes):
     * simulates a sibling process legitimately stealing this exact lock
     * (e.g. because a real {@code staleThreshold} elapsed while this
     * holder was stalled by a slow filesystem operation) and acquiring
     * its own new generation, all before this instance ever gets around
     * to closing. {@code close()} must never delete that sibling's lock.
     */
    @Test
    void closeDoesNotDeleteADifferentLockGenerationThatHasSinceReplacedThisOnesOwnFile(@TempDir Path tempDir)
            throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        AccountLedgerLock lock =
                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET);

        // Simulate a sibling stealing this lock and acquiring its own,
        // different generation -- without ever going through this
        // instance's own close().
        Files.delete(lockPath);
        Files.writeString(
                lockPath,
                "{\"pid\":999999999,\"hostname\":\"someone-else\",\"acquiredAt\":\"" + Instant.now() + "\"}");

        lock.close();

        assertTrue(Files.exists(lockPath), "close() must not delete a different holder's lock");
        assertTrue(
                Files.readString(lockPath).contains("someone-else"),
                "the sibling's own lock content must be completely untouched");
    }

    /**
     * A lock file that exists but is empty (e.g. {@code Files#createFile}
     * succeeded but the metadata write hasn't landed yet, or crashed
     * before completing) must never be treated as stale merely for being
     * unparseable -- confirmed by using a short {@code totalRetryBudget}
     * and asserting the caller genuinely exhausts it (rather than
     * incorrectly, prematurely succeeding).
     */
    @Test
    void acquireDoesNotStealAFreshEmptyLockFile(@TempDir Path tempDir) throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        Files.writeString(lockPath, ""); // present, but empty -- simulates the create-then-write race window

        assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, Duration.ofMillis(300)));
    }

    /**
     * Backstop path for a Critical finding from this task's own real
     * CodeRabbit review: an empty lock file whose last-modified time is
     * older than {@code staleThreshold} (simulating a holder that died
     * between {@link Files#createFile} succeeding and ever writing its
     * metadata -- e.g. a hard process kill) must still be reclaimable,
     * not block every future waiter forever.
     */
    @Test
    void acquireStealsAnAbandonedEmptyLockFileOlderThanStaleThreshold(@TempDir Path tempDir) throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        Files.writeString(lockPath, "");
        Duration staleThreshold = Duration.ofMillis(50);
        Files.setLastModifiedTime(
                lockPath, java.nio.file.attribute.FileTime.from(Instant.now().minus(Duration.ofSeconds(60))));

        try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, staleThreshold, GENEROUS_RETRY_BUDGET)) {
            assertTrue(Files.exists(lockPath), "a real lock must have been created after reclaiming the abandoned file");
            assertTrue(Files.readString(lockPath).contains("\"pid\""), "the reclaimed file must hold this instance's real metadata now");
        }
    }

    /**
     * Real Major finding, real CodeRabbit review of this PR, on top of the
     * two Critical findings above: the original {@code createAndWriteMetadata}
     * created the lock file and wrote its content as two separate,
     * <b>path</b>-based operations. If a holder's own write was slow
     * enough for a sibling to legitimately judge the file abandoned,
     * steal it, and acquire its own new generation, the original slow
     * writer's <i>own, now-orphaned</i> write would still land -- since
     * {@code Files.writeString} re-resolves the path fresh each time -- and
     * silently clobber the sibling's real, live metadata with the
     * original holder's stale content.
     *
     * <p>This test exercises the fixed mechanism directly, more precisely
     * than the reviewer's own suggested pseudocode (a plain, unguarded
     * {@code Files.writeString} "simulating" the delayed write -- which
     * would not actually prove anything, since an ordinary path-based
     * write was never the vulnerable operation once created via an open
     * handle; the real question is whether a write through the
     * <i>original, already-open creation handle</i> survives a concurrent
     * delete-and-recreate at the same path, which is exactly what {@code
     * createAndWriteMetadata} now relies on). Opens a {@code CREATE_NEW}
     * channel exactly the way {@code createAndWriteMetadata} itself does,
     * without yet writing through it -- reproducing the real slow-write
     * window -- then has a sibling steal and reacquire through the real
     * production path, and only then completes the original, now-orphaned
     * write through the old handle.
     *
     * <p><b>Two real, Trivial/Minor findings from a further real
     * CodeRabbit review round, both addressed</b>: (1) this test's own
     * premise -- an open file surviving a concurrent delete-and-recreate
     * at the same path -- is real, observed, POSIX-style behavior
     * (confirmed by this task's own standalone probe against this
     * repository's real filesystem, see {@code createAndWriteMetadata}'s
     * own Javadoc), but is documented to be platform-dependent in
     * general (traditional Windows semantics differ). Annotated {@code
     * @EnabledOnOs(OS.LINUX)} accordingly -- true both in this project's
     * real dev environment (WSL2, a real Linux kernel under the drvfs
     * mount) and its CI (`ubuntu-latest`), so this is a precise
     * statement of the tested premise, not a behavior change. (2) {@code
     * staleWritersChannel} is now opened inside its own try-with-resources
     * so it cannot leak if {@code Files.delete} or {@code
     * AccountLedgerLock.acquire} throws before this test's own explicit,
     * mid-body {@code close()} call -- that explicit close (needed to
     * make the delayed write actually happen at the right point in the
     * sequence) stays; a redundant second close from the try-with-
     * resources block once that already ran is a documented no-op per
     * {@link SeekableByteChannel#close()}'s own contract, not a bug.
     */
    @Test
    @EnabledOnOs(OS.LINUX)
    void aLockFilesOwnOpenCreationHandleCannotClobberADifferentGenerationCreatedAfterItWasStolen(
            @TempDir Path tempDir) throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");

        // Mirrors createAndWriteMetadata's own atomic create-and-open step
        // directly, without completing the write -- reproducing the real
        // slow-write window that Critical finding above is about.
        try (SeekableByteChannel staleWritersChannel =
                Files.newByteChannel(lockPath, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)) {

            // A sibling judges this (from its perspective, abandoned) lock
            // stale and acquires its own real, new generation through the
            // real production path.
            Files.delete(lockPath);
            try (AccountLedgerLock sibling =
                    AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET)) {
                String siblingContent = Files.readString(lockPath);

                // The original, now-orphaned writer finally completes its
                // own delayed write through its OLD, already-open handle.
                staleWritersChannel.write(ByteBuffer.wrap("STALE-CONTENT-FROM-ORIGINAL-CREATOR".getBytes()));
                staleWritersChannel.close();

                assertEquals(
                        siblingContent,
                        Files.readString(lockPath),
                        "a delayed write through an old, already-orphaned creation handle must never corrupt a"
                                + " different, currently-live lock generation at the same path");
            }
        }
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
