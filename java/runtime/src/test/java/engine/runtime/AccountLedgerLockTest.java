package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import java.io.IOException;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
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
     * Much larger than {@link #GENEROUS_RETRY_BUDGET}: 12 threads x 10
     * acquisitions, each waiter backing off up to 250ms per failed
     * attempt, can plausibly accumulate close to (or past) a 5s budget
     * under ordinary queuing alone -- not a mutual-exclusion bug, just
     * real contention -- especially combined with this class's own
     * documented 500ms+ single-operation latency under load on this
     * repository's real drvfs mount. This test gets its own, dedicated
     * budget instead of sharing the smaller one other tests use
     * deliberately (to keep {@code
     * acquireThrowsRatherThanHangingWhenTheRetryBudgetIsExhausted} fast).
     */
    private static final Duration CONTENTION_RETRY_BUDGET = Duration.ofSeconds(60);

    /**
     * Many real OS threads race {@link AccountLedgerLock#acquire}
     * against the same lock path. Mutual exclusion is proven the same
     * way the governing plan itself suggests: a critical section that
     * performs a deliberately non-atomic read-sleep-increment-write on an
     * unguarded shared counter. If two threads were ever inside the lock
     * at once, at least one increment is lost and the final count falls
     * short of the expected total -- a real data race would be caught,
     * not just asserted away. All threads are also proven to have
     * eventually succeeded (no thread swallowed an exception).
     */
    @Test
    void acquireProvidesRealMutualExclusionAcrossManyThreads(@TempDir Path tempDir) throws InterruptedException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        int threadCount = 12;
        int iterationsPerThread = 10;
        // Real Minor finding, real CodeRabbit review of this PR: a plain
        // int[] gives no cross-thread visibility guarantee at all -- this
        // class's own mutual exclusion is enforced entirely through file
        // I/O (AccountLedgerLock.acquire/close), and file I/O establishes
        // no happens-before edge in the Java Memory Model between threads.
        // Even with perfect mutual exclusion, one thread could fail to
        // observe another's latest increment, and the final assertion
        // below would then fail for a reason unrelated to the lock itself.
        // AtomicInteger's plain get()/set() (deliberately NOT
        // incrementAndGet(), which would make the increment itself atomic
        // and defeat this test's whole point) fixes the visibility gap
        // while keeping the exact same non-atomic read-sleep-increment-
        // write race this test exists to detect.
        AtomicInteger counter = new AtomicInteger();
        List<Throwable> failures = new CopyOnWriteArrayList<>();
        CountDownLatch done = new CountDownLatch(threadCount);

        for (int t = 0; t < threadCount; t++) {
            Thread thread = new Thread(() -> {
                try {
                    for (int i = 0; i < iterationsPerThread; i++) {
                        AccountLedgerLock lock =
                                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, CONTENTION_RETRY_BUDGET);
                        try {
                            int current = counter.get();
                            Thread.sleep(2); // widen the race window
                            counter.set(current + 1);
                        } finally {
                            closeUntilReleased(lock, lockPath);
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
                counter.get(),
                "a lower-than-expected count means two threads were inside the lock-protected section at once");
        assertFalse(Files.exists(lockPath), "the lock file must not be left behind once every holder has released it");
    }

    /**
     * Retries {@link AccountLedgerLock#close()} until {@code lockPath} is
     * confirmed gone: {@code close()} does not itself signal success or
     * failure (see {@link AccountLedgerLock}'s own class Javadoc, fourth
     * caller-contract point) and can take a retryable, non-final path on
     * this project's own drvfs mount, so a single call is not sufficient
     * proof of release under this test's own real, sustained contention
     * (12 threads, 10 iterations each).
     *
     * <p>Real Minor finding, a further real CodeRabbit review round on
     * this PR: the retry budget here previously totaled only 250ms (5
     * attempts x 50ms) -- shorter than this project's own real, measured
     * single-file-operation latency on this repository's drvfs mount
     * (500ms+ under contention, per {@link AccountLedgerLock}'s own class
     * Javadoc). A budget smaller than the delay it exists to absorb
     * cannot do its job: the deletion-visibility assertion could then
     * fail for a reason unrelated to a real lock defect, polluting this
     * task's own sole empirical signal. Now 40 attempts x 50ms = ~2s,
     * applied identically here, in {@code
     * AccountLedgerLockMultiProcessTest#waitForLockFileAbsence}, and in
     * {@code LockContenderMain#closeUntilReleased} (round 55's own three
     * sibling fixes).
     */
    private static void closeUntilReleased(AccountLedgerLock lock, Path lockPath) throws InterruptedException {
        for (int attempt = 0; attempt < 40; attempt++) {
            lock.close();
            if (Files.notExists(lockPath)) {
                return;
            }
            Thread.sleep(50);
        }
        // Not treated as a hard failure here -- if the lock file is
        // genuinely still present after this many attempts, this test's
        // own final assertFalse(Files.exists(lockPath)) below is what
        // actually judges it, with a real, meaningful signal rather than
        // a single-shot race against a known transient condition.
    }

    /**
     * {@link AccountLedgerLock#requireHeld()} is a pure no-op (no
     * exception) while this instance has not yet been closed -- proven
     * here against a real, successfully-acquired lock, not merely
     * asserted from reading the source.
     */
    @Test
    void requireHeldSucceedsWhileTheLockIsStillHeld(@TempDir Path tempDir) {
        Path lockPath = tempDir.resolve("ledger.json.lock");

        try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET)) {
            lock.requireHeld();
        }
    }

    /**
     * {@link AccountLedgerLock#requireHeld()} throws {@link
     * IllegalStateException} once this instance has genuinely reached a
     * final, closed state -- the real backing check {@link
     * AccountLedgerStore#load}/{@link AccountLedgerStore#persist} rely on
     * to reject a caller passing an already-released lock as if it were
     * still proof of holding one.
     */
    @Test
    void requireHeldThrowsAfterTheLockIsGenuinelyClosed(@TempDir Path tempDir) throws InterruptedException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET);
        closeUntilReleased(lock, lockPath);
        assertTrue(Files.notExists(lockPath), "test setup: the lock must be genuinely, fully released before"
                + " this test's own real subject (requireHeld on a closed instance) is exercised");

        IllegalStateException thrown = assertThrows(IllegalStateException.class, lock::requireHeld);

        assertTrue(
                thrown.getMessage().contains(lockPath.toString()),
                "exception must name the lock path; was: " + thrown.getMessage());
    }

    /**
     * Hand-writes a lock file whose recorded {@code pid} does not
     * correspond to any real running process (verified via {@link
     * ProcessHandle#of} before relying on it, per the governing task
     * brief -- not merely assumed absent) and a fresh {@code acquiredAt}
     * well inside {@code staleThreshold}. {@link AccountLedgerLock#acquire}
     * must steal it via the dead-PID path alone and succeed.
     *
     * <p>Uses this test's own real, current hostname in the fabricated
     * content (not an arbitrary fake value) -- a further real CodeRabbit
     * review round on this PR found the dead-PID path itself is only
     * meaningful when the recorded {@code hostname} matches the current
     * host (see
     * {@link #acquireDoesNotStealAFabricatedDeadPidLockFromADifferentHostnameWhileFresh}
     * for the different-host case this distinction protects against);
     * this test's own subject is the
     * same-host dead-PID path specifically, so it must use a real,
     * matching hostname to keep testing that path once that distinction
     * exists.
     */
    @Test
    void acquireStealsAFabricatedLockWithADeadPid(@TempDir Path tempDir) throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        long deadPid = findAPidThatIsNotRunning();

        Files.writeString(
                lockPath,
                "{\"pid\":" + deadPid + ",\"hostname\":\"" + realHostname() + "\",\"acquiredAt\":\""
                        + Instant.now() + "\"}");

        try (AccountLedgerLock lock =
                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET)) {
            assertTrue(Files.exists(lockPath), "a real lock must have been created after the steal");
            // Strengthened per a further real CodeRabbit review round on
            // this PR: a fabricated lock file already exists at lockPath
            // before acquire() is ever called, so file existence alone
            // does not distinguish a real steal from acquire() simply
            // never running at all. Verifying the content actually
            // changed to this process's own real metadata (and the
            // fabricated dead pid is gone) proves the steal itself
            // happened, matching the same discipline already applied to
            // acquireStealsAnAbandonedEmptyLockFileOlderThanStaleThreshold.
            String content = Files.readString(lockPath);
            assertFalse(
                    content.contains("\"pid\":" + deadPid),
                    "the fabricated stale generation must have been replaced, not merely left in place");
            assertTrue(
                    content.contains("\"pid\":" + ProcessHandle.current().pid()),
                    "the reclaimed file must hold this process's own real metadata now");
        }
    }

    /**
     * {@code tryStealIfStale}'s dead-PID check ({@code
     * ProcessHandle.of(metadata.pid())}) only ever consults <i>this</i>
     * host's own process table -- meaningful only because this project's
     * own documented deployment model has every process sharing a given
     * lock running on the same host (see {@link AccountLedgerLock}'s own
     * class Javadoc). {@code holderDead} requires {@code
     * metadata.hostname()} to match this host's own current hostname
     * before trusting the PID-liveness result; a hostname mismatch
     * treats the PID as not provably dead (fails toward not stealing via
     * this path, not toward stealing) rather than guessing. The
     * independent {@code expired} check (a lock's own {@code
     * acquiredAt} older than {@code staleThreshold}) is deliberately
     * unaffected -- see {@link
     * #acquireStealsAFabricatedDeadPidLockFromADifferentHostnameOnceItsTimestampExpires}
     * immediately below for why a foreign-host lock must still be
     * reclaimable via that independent path.
     *
     * <p>Uses a short, non-generous retry budget so this test (which
     * proves acquisition genuinely does NOT happen) doesn't need to wait
     * out {@link #GENEROUS_RETRY_BUDGET}'s own multi-second budget to
     * observe the expected failure.
     */
    @Test
    void acquireDoesNotStealAFabricatedDeadPidLockFromADifferentHostnameWhileFresh(@TempDir Path tempDir)
            throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        long deadPid = findAPidThatIsNotRunning();
        Duration shortRetryBudget = Duration.ofMillis(300);

        Files.writeString(
                lockPath,
                "{\"pid\":" + deadPid + ",\"hostname\":\"definitely-a-different-host\",\"acquiredAt\":\""
                        + Instant.now() + "\"}");

        IllegalStateException thrown = assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, shortRetryBudget));

        assertTrue(
                thrown.getMessage().contains("within retry budget"),
                "must genuinely exhaust the retry budget (never steal a foreign-host lock via the PID-liveness"
                        + " path alone, and never fail for some other reason); was: " + thrown.getMessage());
        assertTrue(
                Files.readString(lockPath).contains("definitely-a-different-host"),
                "the foreign-host lock must be completely untouched -- never stolen via the PID-liveness path"
                        + " when the hostname doesn't match this host");
    }

    /**
     * The direct counterpart to {@link
     * #acquireDoesNotStealAFabricatedDeadPidLockFromADifferentHostnameWhileFresh}
     * immediately above, proving the {@code expired} check remains a
     * fully independent, working backstop for a foreign-host lock -- the
     * same {@code hostname} mismatch that suppresses the PID-liveness
     * path must not also suppress this one.
     */
    @Test
    void acquireStealsAFabricatedDeadPidLockFromADifferentHostnameOnceItsTimestampExpires(@TempDir Path tempDir)
            throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        long deadPid = findAPidThatIsNotRunning();
        Duration staleThreshold = Duration.ofMillis(50);
        Instant longAgo = Instant.now().minus(Duration.ofSeconds(60));

        Files.writeString(
                lockPath,
                "{\"pid\":" + deadPid + ",\"hostname\":\"definitely-a-different-host\",\"acquiredAt\":\""
                        + longAgo + "\"}");

        try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, staleThreshold, GENEROUS_RETRY_BUDGET)) {
            assertTrue(Files.exists(lockPath), "a real lock must have been created after the steal");
            String content = Files.readString(lockPath);
            assertFalse(
                    content.contains("definitely-a-different-host"),
                    "the fabricated foreign-host generation must have been replaced, not merely left in place");
            assertTrue(
                    content.contains("\"pid\":" + ProcessHandle.current().pid()),
                    "the reclaimed file must hold this process's own real metadata now");
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
            // Strengthened per a further real CodeRabbit review round on
            // this PR, same reasoning as
            // acquireStealsAFabricatedLockWithADeadPid's own identical
            // fix above -- file existence alone doesn't prove a steal
            // happened here either. The fabricated pid is deliberately
            // this process's own real pid (so the dead-pid path cannot
            // fire and only the expired-timestamp path is exercised), so
            // checking pid again would not distinguish a real steal from
            // a no-op; checking that the fabricated stale-host value is
            // gone is the real differentiator here.
            String content = Files.readString(lockPath);
            assertFalse(
                    content.contains("stale-host"),
                    "the fabricated stale generation must have been replaced, not merely left in place");
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
        // CopyOnWriteArrayList, not a plain ArrayList -- real Trivial finding,
        // real CodeRabbit review of this PR: the holder thread writes to this
        // list while the main thread reads it after only a bounded join()
        // wait (not a guaranteed-finished join), an unsynchronized data race
        // if the holder thread hasn't actually finished yet. Matches the same
        // type already used for this exact purpose elsewhere in this file.
        List<Throwable> holderFailures = new CopyOnWriteArrayList<>();

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
     * Choosing a {@code staleThreshold} <b>shorter than a legitimate
     * holder's own real critical-section duration</b> causes a genuine
     * mutual-exclusion violation. This is not a bug in {@link
     * AccountLedgerLock#acquire}'s own steal logic -- it does exactly
     * what its documented contract says: steal a lock whose {@code
     * acquiredAt} exceeds {@code staleThreshold}, regardless of whether
     * the holder is provably dead or merely still legitimately working.
     * It is a real, demonstrated consequence of the staleness-by-elapsed-
     * time design that every caller of {@code acquire} must respect when
     * choosing a real {@code staleThreshold} value.
     *
     * <p><b>First observed empirically, not just reasoned about</b>: a raw
     * multi-process stress run (this task's own {@code LockContenderMain}
     * harness, 6 processes × 8 iterations, {@code staleThreshold=1ms}
     * against a real ~15ms hold time) reliably lost real increments every
     * time it was run (well below the expected 48, some individual
     * contender processes even exiting with real errors) -- direct
     * evidence, not a hypothetical, that this failure mode is real on
     * this repository's actual filesystem. This test reproduces the same
     * underlying phenomenon deterministically, with two real threads and
     * controlled timing (`Thread.sleep`, not real multi-process
     * scheduling variance), rather than the inherently timing-variable
     * raw harness used for that original discovery -- proving this is a
     * real, general property of the design itself, not an artifact
     * specific to that harness's own timing.
     *
     * <p><b>Deliberately not "fixed" with an enforced minimum {@code
     * staleThreshold}</b>: the real minimum any given deployment actually
     * needs depends entirely on how long <i>that deployment's own real
     * critical sections</i> can legitimately run -- a property only a
     * caller (this class's own Task C, not yet built) can know, not
     * something this primitive can validate in advance without inventing
     * an arbitrary constant unmoored from any real, justified number.
     * This is documented as a real, disclosed caller-contract requirement
     * instead (see the class Javadoc): {@code staleThreshold} must be
     * chosen comfortably larger than the longest legitimate critical
     * section this lock will ever protect -- this project's own proposed
     * real default (~30s, per the governing plan) already has enormous
     * headroom over any critical section this lock is actually expected
     * to protect.
     */
    @Test
    void aStaleThresholdShorterThanARealHoldersOwnCriticalSectionCausesARealMutualExclusionViolation(
            @TempDir Path tempDir) throws Exception {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        Duration pathologicallySmallStaleThreshold = Duration.ofMillis(10);
        // 5s, not the previous 1s -- this class's own Javadoc documents
        // real, measured 500ms+ transient I/O latency PER OPERATION on
        // this project's actual drvfs mount under contention, and the
        // steal attempt itself is several real operations back to back
        // (read + stale judgment + delete + create/write + re-verify) --
        // at 500ms+ each, that can plausibly approach or exceed a 1s
        // hold time, making it a coin flip whether the steal finishes
        // before the holder legitimately releases, rather than the real
        // headroom this test needs to reliably prove the mutual-exclusion
        // violation it targets, not a rare race with the steal itself.
        long holderRealHoldMillis = 5_000;

        CountDownLatch holderAcquired = new CountDownLatch(1);
        AtomicLong holderReleasedAtNanos = new AtomicLong(-1);
        List<Throwable> holderFailures = new CopyOnWriteArrayList<>();

        Thread holder = new Thread(() -> {
            try (AccountLedgerLock lock =
                    AccountLedgerLock.acquire(lockPath, pathologicallySmallStaleThreshold, GENEROUS_RETRY_BUDGET)) {
                holderAcquired.countDown();
                Thread.sleep(holderRealHoldMillis); // its own real, legitimate critical-section work
                // Recorded here, INSIDE the critical section, not in a
                // finally block below -- a finally block runs only after
                // try-with-resources has already called close() on the way
                // out, and by the time this test's own steal has happened,
                // close() is not the fast path: it re-reads the (by-then-
                // stolen) lock file, finds a generation mismatch, and logs
                // ERROR, all subject to this repository's own documented
                // 500ms+ single-operation drvfs latency. Recording the
                // "released" timestamp after that would let this test pass
                // even when the steal actually completed after the holder's
                // own real work had already finished -- weakening the exact
                // claim this test exists to prove (real CodeRabbit review of
                // this PR).
                holderReleasedAtNanos.set(System.nanoTime());
            } catch (Throwable e) {
                holderFailures.add(e);
                // Fallback only -- does not overwrite a timestamp the try
                // block above already recorded; covers the case where the
                // exception happened before reaching that line.
                holderReleasedAtNanos.compareAndSet(-1, System.nanoTime());
            }
        });
        holder.start();
        assertTrue(holderAcquired.await(5, TimeUnit.SECONDS), "holder must acquire first");

        // Wait long enough for the holder's own acquiredAt to exceed the
        // pathologically small staleThreshold while it is still
        // genuinely, actively sleeping inside its own critical section.
        Thread.sleep(pathologicallySmallStaleThreshold.toMillis() + 30);

        long stolenAcquiredAtNanos;
        try (AccountLedgerLock stolen =
                AccountLedgerLock.acquire(lockPath, pathologicallySmallStaleThreshold, GENEROUS_RETRY_BUDGET)) {
            stolenAcquiredAtNanos = System.nanoTime();
        }

        // 15s, not the previous 5s -- a necessary companion to
        // holderRealHoldMillis's own increase above (not itself cited by
        // the review round that raised this): the holder thread cannot
        // finish before its own holderRealHoldMillis sleep completes,
        // plus whatever real time its own close() then takes re-reading
        // a by-then-stolen lock file under this same drvfs contention --
        // a 5s join budget against a 5s hold time alone would leave zero
        // real margin, reintroducing exactly the flakiness risk this
        // whole fix exists to remove.
        holder.join(TimeUnit.SECONDS.toMillis(15));
        assertTrue(holderFailures.isEmpty(), "holder thread must not have failed: " + holderFailures);
        // Real Minor finding, a further real CodeRabbit review round on
        // this PR: System.nanoTime()'s own documented contract is only
        // "monotonic, arbitrary origin" -- unlike epoch millis, a real
        // return value is not guaranteed positive, so a "> 0" check could
        // spuriously fail on an otherwise legitimate, correctly-recorded
        // timestamp. -1 is this test's own real sentinel (initial value
        // above); checking against it specifically is the semantically
        // correct completion check, not merely a coincidentally-similar
        // positivity check.
        assertTrue(holderReleasedAtNanos.get() != -1, "holder must have finished by now");

        // The proof: the second acquire() succeeded WHILE the original
        // holder was still genuinely, legitimately active -- both were
        // concurrently "inside the lock" from their own perspective, a
        // real mutual-exclusion violation directly caused by
        // staleThreshold being shorter than the holder's own real
        // critical-section duration.
        // Compared via subtraction, not a direct < -- System.nanoTime()'s
        // own documented contract only guarantees correctness for
        // differences, since the underlying value can wrap around; a
        // direct comparison would misjudge the two timestamps' real order
        // if a wraparound happened to fall between them (real Minor
        // finding, real CodeRabbit review of this PR).
        assertTrue(
                stolenAcquiredAtNanos - holderReleasedAtNanos.get() < 0,
                "expected the steal to succeed WHILE the original holder was still genuinely active -- proving a"
                        + " real mutual-exclusion violation when staleThreshold is shorter than a legitimate"
                        + " holder's own real critical-section duration");
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
     * Real Major finding, a further real CodeRabbit review round on this
     * PR: {@code close()} must be idempotent. Without that, a second call
     * on an already-closed instance would re-examine {@code lockPath} --
     * and if a sibling had since legitimately acquired a brand new
     * generation there (a real, plausible sequence, not contrived: this
     * instance's own first {@code close()} already ran and deleted its
     * own file, vacating the path for anyone), the second call would log
     * a real {@code ERROR} that reads exactly like a genuine cross-process
     * safety event ("no longer holds this instance's own metadata"),
     * purely as an artifact of being called twice -- even though nothing
     * was ever actually wrong and the first call already completed
     * correctly.
     */
    @Test
    void closeIsIdempotentAndNeverReExaminesTheFileOnARepeatCall(@TempDir Path tempDir)
            throws IOException, InterruptedException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        AccountLedgerLock lock =
                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET);

        // close() does not itself signal success/failure and can take a
        // retryable, non-final path on this project's own drvfs mount (see
        // AccountLedgerLock's own class Javadoc, fourth caller-contract
        // point) -- a single call is not sufficient proof of release, so
        // this test's own real subject (idempotency on a REPEAT call)
        // relies on the same closeUntilReleased helper every other real
        // caller in this file uses to get there first.
        closeUntilReleased(lock, lockPath);
        assertFalse(Files.exists(lockPath), "the first close() call must have deleted this instance's own lock file");

        // A sibling legitimately acquires a brand new generation at the
        // same path, entirely after this instance's own close() already
        // completed.
        Files.writeString(
                lockPath,
                "{\"pid\":999999999,\"hostname\":\"someone-else\",\"acquiredAt\":\"" + Instant.now() + "\"}");

        lock.close(); // a second call on the same, already-closed instance -- closeUntilReleased above already
        // confirmed closed is true, so this is a genuine, pure no-op (early return, zero I/O) -- unlike the first
        // close() above, there is no retryable outcome here for a bounded retry to guard against.

        assertTrue(
                Files.exists(lockPath),
                "a second close() call must be a pure no-op -- it must never touch a different holder's lock file");
        assertTrue(
                Files.readString(lockPath).contains("someone-else"),
                "the sibling's own lock content must be completely untouched by the repeat close() call");
    }

    /**
     * Real Trivial finding, a further real CodeRabbit review round on this
     * PR: {@code close()}'s own {@code EMPTY_OR_UNPARSEABLE} branch used to
     * be treated as a final, unretryable outcome -- the same treatment as a
     * genuine different-holder mismatch. On this specific project's real
     * drvfs mount (previously measured sub-3ms mtime precision, 500ms+
     * transient I/O latency under contention -- see {@link
     * AccountLedgerLock}'s own class Javadoc), that conflated two different
     * situations: a genuine new holder mid-write (nothing to gain from
     * retrying, correctly final) and a transient filesystem read/visibility
     * gap on this instance's <i>own</i>, still-valid content (where a later
     * {@code close()} call might well observe the real content and
     * successfully delete it). Now treated the same retryable way {@code
     * READ_FAILED} already is. Proven here, not just asserted: a first
     * {@code close()} call observes fabricated empty content and must
     * neither delete nor permanently mark this instance closed; a second
     * call, after the real content is restored (simulating the transient
     * condition resolving), must still be able to complete the delete.
     */
    @Test
    void closeRetriesAfterObservingEmptyOrUnparseableContentOnAnEarlierAttempt(@TempDir Path tempDir)
            throws IOException, InterruptedException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        AccountLedgerLock lock =
                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET);
        String ownContent = Files.readString(lockPath);
        Files.writeString(lockPath, ""); // fabricates the EMPTY_OR_UNPARSEABLE state on this instance's own path

        lock.close(); // first attempt: must not delete, must not permanently mark this instance closed

        assertTrue(
                Files.exists(lockPath),
                "close() must not delete when it cannot confirm the file is still its own generation");
        assertEquals(
                "",
                Files.readString(lockPath),
                "the fabricated empty content must be untouched by the non-deleting first close() attempt");

        Files.writeString(lockPath, ownContent); // the transient condition resolves -- this instance's own real
        // content is confirmable again

        // A single second close() call is not itself guaranteed to
        // observe the now-resolved content and delete on its own first
        // try -- close() does not signal success/failure (class Javadoc,
        // fourth caller-contract point), and this repository's own real
        // drvfs mount can still interpose a further transient gap here.
        // This test's own subject is "not permanently marked closed,"
        // which closeUntilReleased's own bounded retry proves without
        // conflating it with an unrelated, single-attempt timing risk.
        closeUntilReleased(lock, lockPath);

        assertFalse(
                Files.exists(lockPath),
                "a later close() call must still be able to delete this instance's own lock once its content is"
                        + " confirmable again -- EMPTY_OR_UNPARSEABLE must be retryable, not final");
    }

    /**
     * {@link AccountLedgerLock#requireHeld()} must reject this instance
     * once {@link AccountLedgerLock#close()} has been called at all --
     * even when that call took a retryable, non-final {@code doClose()}
     * path (here, {@code EMPTY_OR_UNPARSEABLE}, the same fixture {@link
     * #closeRetriesAfterObservingEmptyOrUnparseableContentOnAnEarlierAttempt}
     * uses) and this instance's own {@code closed} field is therefore
     * still {@code false}. A caller that called {@code close()} once,
     * hit exactly this transient outcome, and then (incorrectly) treated
     * the instance as still valid proof of holding the lock is exactly
     * the mistake {@code requireHeld()} exists to reject -- and the
     * {@code EMPTY_OR_UNPARSEABLE} case specifically means this instance
     * cannot prove it is still the sole holder, so a sibling may already
     * hold a live, different generation underneath it.
     */
    @Test
    void requireHeldThrowsAfterCloseIsCalledEvenWhenDoCloseTakesARetryableNonFinalPath(@TempDir Path tempDir)
            throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        AccountLedgerLock lock =
                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET);
        Files.writeString(lockPath, ""); // fabricates the EMPTY_OR_UNPARSEABLE state on this instance's own path

        lock.close(); // takes the retryable, non-final path -- closed stays false

        assertTrue(
                Files.exists(lockPath),
                "test setup: close() must not have deleted the file on this retryable outcome");
        IllegalStateException thrown = assertThrows(IllegalStateException.class, lock::requireHeld);
        assertTrue(
                thrown.getMessage().contains(lockPath.toString()),
                "exception must name the lock path; was: " + thrown.getMessage());
    }

    /**
     * {@link AccountLedgerLock#requireHeld()} must reject this instance
     * once its own generation has been legitimately stolen by a real
     * sibling -- a genuinely reachable state whenever {@code
     * staleThreshold} elapses on a real, still-live, still-working
     * holder (this class's own documented caller contract already
     * requires {@code staleThreshold} be chosen comfortably larger than
     * any real critical section, but a caller that never observed the
     * steal -- never called {@code close()}, so {@code releaseRequested}
     * alone would not catch this -- must still be blocked from using
     * this now-superseded instance as proof of holding the lock).
     * Proven through the real production path: a genuine second {@link
     * AccountLedgerLock#acquire} call, not fabricated content, performs
     * the steal.
     */
    @Test
    void requireHeldThrowsOnceThisInstancesGenerationHasBeenLegitimatelyStolen(@TempDir Path tempDir)
            throws InterruptedException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        Duration tinyStaleThreshold = Duration.ofMillis(50);
        // staleThreshold is not a property of the acquired instance -- it is
        // only ever consulted by a LATER caller judging an existing lock it
        // finds already present. original's own staleThreshold here is
        // irrelevant to whether it can later be stolen; it is the sibling's
        // own staleThreshold below that determines that.
        AccountLedgerLock original =
                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET);
        // original is deliberately never close()d in this test -- it covers
        // exactly the caller that never observed its own lock being stolen,
        // and such a caller would never call close() either. Closing it
        // here would exercise doClose()'s own generation-mismatch branch
        // against sibling's real, live generation and log a real ERROR,
        // polluting this test's own signal without deleting anything (a
        // genuine mismatch is never deleted) -- sibling's own lock file is
        // released below regardless.
        Thread.sleep(150); // exceed tinyStaleThreshold so the sibling below can legitimately judge it stale
        AccountLedgerLock sibling = AccountLedgerLock.acquire(lockPath, tinyStaleThreshold, GENEROUS_RETRY_BUDGET);

        try {
            IllegalStateException thrown = assertThrows(IllegalStateException.class, original::requireHeld);
            assertTrue(
                    thrown.getMessage().contains(lockPath.toString()),
                    "exception must name the lock path; was: " + thrown.getMessage());
        } finally {
            closeUntilReleased(sibling, lockPath);
        }
    }

    /**
     * The other half of {@link AccountLedgerLock#stillHoldsCurrentGeneration()}'s
     * own retry loop -- the retry loop itself lives in that production
     * method, not in either test: a <i>persistent</i> {@code
     * EMPTY_OR_UNPARSEABLE} condition on this instance's own {@code
     * lockPath} (not a genuine steal) must still fail closed once {@link
     * AccountLedgerLock#requireHeld()}'s own bounded retry budget is
     * exhausted -- matching {@code requireHeld()}'s own documented
     * "doubt must reject, not accept" principle. Does not independently
     * prove the retry loop's own "recovers if the condition resolves
     * mid-window" half -- the identical, already-disclosed structural
     * reason applies here as it does for {@code
     * deleteIfStillOwnGeneration}'s own analogous retry (see that
     * method's own Javadoc): reproducing the condition resolving at a
     * specific point inside this method's own internal retry loop would
     * need either accepted timing-dependent flakiness or an unrequested
     * production-only test hook.
     */
    @Test
    void requireHeldFailsClosedOnAPersistentEmptyOrUnparseableCondition(@TempDir Path tempDir) throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        AccountLedgerLock lock =
                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET);
        Files.writeString(lockPath, ""); // fabricates a persistent EMPTY_OR_UNPARSEABLE state, never restored

        IllegalStateException thrown = assertThrows(IllegalStateException.class, lock::requireHeld);

        assertTrue(
                thrown.getMessage().contains(lockPath.toString()),
                "exception must name the lock path; was: " + thrown.getMessage());
    }

    /**
     * A lock file that exists but is empty (e.g. the atomic {@link
     * Files#newByteChannel} create with {@code CREATE_NEW} succeeded but
     * the metadata write hasn't landed yet, or crashed before completing)
     * must never be treated as stale merely for being unparseable --
     * confirmed by using a short {@code totalRetryBudget} and asserting
     * the caller genuinely exhausts it (rather than incorrectly,
     * prematurely succeeding).
     *
     * <p>Real Trivial finding, a further real CodeRabbit review round on
     * this PR: this test used to assert only the exception's <i>type</i>
     * ({@code IllegalStateException}), which -- given {@code
     * AccountLedgerLock}'s own internal helper methods, until this same
     * review round, could throw {@code IllegalStateException} from several
     * different real failure paths (a genuine delete/read failure, not
     * just ordinary retry-budget exhaustion) -- did not actually prove
     * this test's own named premise (that this scenario correctly runs
     * out its retry budget without ever stealing). Now asserts the
     * thrown exception's message specifically matches the real retry-
     * budget-exhaustion wording, the same message-validation pattern
     * {@code acquireThrowsRatherThanHangingWhenTheRetryBudgetIsExhausted}
     * already uses.
     */
    @Test
    void acquireDoesNotStealAFreshEmptyLockFile(@TempDir Path tempDir) throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        Files.writeString(lockPath, ""); // present, but empty -- simulates the create-then-write race window
        Duration retryBudget = Duration.ofMillis(300);

        IllegalStateException thrown = assertThrows(
                IllegalStateException.class,
                () -> AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, retryBudget));

        assertTrue(
                thrown.getMessage().contains("within retry budget"),
                "must genuinely exhaust the retry budget (not steal, and not fail for some other reason); was: "
                        + thrown.getMessage());
        assertTrue(thrown.getMessage().contains(lockPath.toString()), "exception must name the lock path");
    }

    /**
     * Real Major finding, a further real CodeRabbit review round on this
     * PR: Jackson can silently fill a missing record component with its
     * default/null rather than throwing -- {@code {"pid":123}} alone
     * would otherwise deserialize "successfully" to a real, trusted-
     * looking {@link AccountLedgerLock.LockMetadata} whose {@code
     * hostname()}/{@code acquiredAt()} are {@code null}, and {@code
     * tryStealIfStale}'s own {@code Duration.between(metadata
     * .acquiredAt(), ...)} call would then throw a raw {@code
     * NullPointerException} that never flows back through {@code
     * acquire}'s own retry loop the way every other failure mode here
     * does. {@code readMetadataOrNull} now validates completeness
     * immediately after parsing, routing an incomplete result through the
     * exact same mtime-based {@code tryStealIfAbandonedEmpty} path a
     * genuinely empty file already uses -- proven here by fabricating a
     * structurally-incomplete-but-parseable, aged lock file and confirming
     * it's still reclaimed (not that it throws -- the whole point of this
     * fix is that it must not).
     */
    @Test
    void acquireStealsAnAgedLockFileWithIncompleteMetadataRatherThanThrowingAnNpe(@TempDir Path tempDir)
            throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        Files.writeString(lockPath, "{\"pid\":123}"); // valid JSON, but missing hostname/acquiredAt entirely
        Duration staleThreshold = Duration.ofMillis(50);
        Files.setLastModifiedTime(
                lockPath, java.nio.file.attribute.FileTime.from(Instant.now().minus(Duration.ofSeconds(60))));

        try (AccountLedgerLock lock = AccountLedgerLock.acquire(lockPath, staleThreshold, GENEROUS_RETRY_BUDGET)) {
            assertTrue(Files.exists(lockPath), "a real lock must have been created after reclaiming the incomplete file");
            assertTrue(
                    Files.readString(lockPath).contains("hostname"),
                    "the reclaimed file must hold this instance's own real, complete metadata now");
        }
    }

    /**
     * Backstop path for a Critical finding from this task's own real
     * CodeRabbit review: an empty lock file whose last-modified time is
     * older than {@code staleThreshold} (simulating a holder that died
     * between the atomic {@link Files#newByteChannel} create with {@code
     * CREATE_NEW} succeeding and ever writing its metadata -- e.g. a hard
     * process kill) must still be reclaimable, not block every future
     * waiter forever.
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
     * The original {@code createAndWriteMetadata} created the lock file
     * and wrote its content as two separate, <b>path</b>-based
     * operations. If a holder's own write was slow enough for a sibling
     * to legitimately judge the file abandoned, steal it, and acquire
     * its own new generation, the original slow writer's <i>own,
     * now-orphaned</i> write would still land -- since {@code
     * Files.writeString} re-resolves the path fresh each time -- and
     * silently clobber the sibling's real, live metadata with the
     * original holder's stale content.
     *
     * <p>This test exercises the fixed mechanism directly: the real
     * question is whether a write through the <i>original, already-open
     * creation handle</i> survives a concurrent delete-and-recreate at
     * the same path, which is exactly what {@code
     * createAndWriteMetadata} now relies on. Opens a {@code CREATE_NEW}
     * channel exactly the way {@code createAndWriteMetadata} itself
     * does, without yet writing through it -- reproducing the real
     * slow-write window -- then has a sibling steal and reacquire
     * through the real production path, and only then completes the
     * original, now-orphaned write through the old handle.
     *
     * <p><b>Linux-only</b>: this test's own premise -- an open file
     * surviving a concurrent delete-and-recreate at the same path -- is
     * real, observed, POSIX-style behavior (confirmed by a standalone
     * probe against this repository's real filesystem, see {@code
     * createAndWriteMetadata}'s own Javadoc) but is platform-dependent
     * in general (traditional Windows semantics differ). Annotated
     * {@code @EnabledOnOs(OS.LINUX)} accordingly -- true both in this
     * project's real dev environment (WSL2, a real Linux kernel under
     * the drvfs mount) and its CI ({@code ubuntu-latest}).
     *
     * <p><b>What this test does NOT cover, by design, not oversight</b>:
     * it proves data is never corrupted, but bypasses {@code
     * createAndWriteMetadata} itself (opening its own raw channel rather
     * than calling that method), so it does not exercise that method's
     * own {@code null}-return statement, its {@code
     * READ_FAILED}/{@code EMPTY_OR_UNPARSEABLE} cleanup call, or {@code
     * acquire}'s handling of losing either race through the real
     * production call path. See {@code createAndWriteMetadata}'s own
     * Javadoc, "Known, permanent test-coverage gap," for why this is
     * accepted as a permanent limitation rather than closed.
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

    /**
     * Mirrors {@code AccountLedgerLock}'s own private {@code hostname()}
     * exactly (same {@link InetAddress#getLocalHost()} call, same
     * fallback) -- this test class has no access to that private method,
     * so it needs its own copy to fabricate a lock file whose recorded
     * {@code hostname} genuinely matches this host, not a value merely
     * assumed to match.
     */
    private static String realHostname() {
        try {
            return InetAddress.getLocalHost().getHostName();
        } catch (UnknownHostException e) {
            return "unknown-host";
        }
    }
}
