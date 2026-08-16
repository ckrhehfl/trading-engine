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

    /**
     * Real Trivial finding, a further real CodeRabbit review round on
     * this PR: this method's own Javadoc used to sit directly above
     * {@link #CONTENTION_RETRY_BUDGET}'s field declaration rather than
     * this method itself (two back-to-back Javadoc blocks in a row both
     * end up attached to whichever declaration immediately follows the
     * second one -- the first block is silently discarded by Javadoc
     * tooling, leaving this method with no rendered documentation at
     * all). Moved to sit immediately above this method, matching every
     * other test method's own convention in this file.
     *
     * <p>Many real OS threads race {@link AccountLedgerLock#acquire}
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
                        try (AccountLedgerLock lock =
                                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, CONTENTION_RETRY_BUDGET)) {
                            int current = counter.get();
                            Thread.sleep(2); // widen the race window
                            counter.set(current + 1);
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
            // Strengthened per a further real CodeRabbit review round on
            // this PR: a fabricated lock file already exists at lockPath
            // before acquire() is ever called, so file existence alone
            // does not distinguish a real steal from acquire() simply
            // never running at all. Verifying the content actually
            // changed to this process's own real metadata (and the
            // fabricated stale-host value is gone) proves the steal
            // itself happened, matching the same discipline already
            // applied to acquireStealsAnAbandonedEmptyLockFileOlderThanStaleThreshold.
            String content = Files.readString(lockPath);
            assertFalse(
                    content.contains("stale-host"),
                    "the fabricated stale generation must have been replaced, not merely left in place");
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
     * Real Major finding, a further real CodeRabbit review round on this
     * PR: choosing a {@code staleThreshold} <b>shorter than a legitimate
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
        // Deliberately generous relative to pathologicallySmallStaleThreshold + the
        // 30ms pre-steal wait below (a ~960ms margin) -- this class's own Javadoc
        // documents real, measured 500ms+ transient write latency on this
        // project's actual drvfs mount under contention, so the steal attempt
        // itself (read + stale judgment + delete + create/write + re-verify) needs
        // real headroom to finish before the holder legitimately releases, or this
        // test would be flaky on a slow/loaded machine for a reason that has
        // nothing to do with the real bug it exists to prove.
        long holderRealHoldMillis = 1_000;

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

        holder.join(TimeUnit.SECONDS.toMillis(5));
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
        assertTrue(
                stolenAcquiredAtNanos < holderReleasedAtNanos.get(),
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
    void closeIsIdempotentAndNeverReExaminesTheFileOnARepeatCall(@TempDir Path tempDir) throws IOException {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        AccountLedgerLock lock =
                AccountLedgerLock.acquire(lockPath, GENEROUS_STALE_THRESHOLD, GENEROUS_RETRY_BUDGET);

        lock.close();
        assertFalse(Files.exists(lockPath), "the first close() call must have deleted this instance's own lock file");

        // A sibling legitimately acquires a brand new generation at the
        // same path, entirely after this instance's own close() already
        // completed.
        Files.writeString(
                lockPath,
                "{\"pid\":999999999,\"hostname\":\"someone-else\",\"acquiredAt\":\"" + Instant.now() + "\"}");

        lock.close(); // a second call on the same, already-closed instance

        assertTrue(
                Files.exists(lockPath),
                "a second close() call must be a pure no-op -- it must never touch a different holder's lock file");
        assertTrue(
                Files.readString(lockPath).contains("someone-else"),
                "the sibling's own lock content must be completely untouched by the repeat close() call");
    }

    /**
     * A lock file that exists but is empty (e.g. {@code Files#createFile}
     * succeeded but the metadata write hasn't landed yet, or crashed
     * before completing) must never be treated as stale merely for being
     * unparseable -- confirmed by using a short {@code totalRetryBudget}
     * and asserting the caller genuinely exhausts it (rather than
     * incorrectly, prematurely succeeding).
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
