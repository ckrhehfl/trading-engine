package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * <b>The actual point of Task B</b> (see the governing plan's "Shared KIS
 * account risk ledger" section and its "3. Cross-process concurrency
 * mechanism" design): a real, concrete resolution of whether {@link
 * AccountLedgerLock}'s atomic-file-creation mutex genuinely provides
 * mutual exclusion across separate OS processes on this repository's real
 * filesystem -- {@code /mnt/c/Dev/trading-engine}, a Windows-mounted
 * drvfs volume under WSL2 (confirmed directly: {@code mount | grep
 * /mnt/c} reports {@code type 9p ... aname=drvfs}), not native ext4. This
 * is a real, disclosed unknown the governing plan calls out explicitly --
 * this test is the resolution of it, not an assumption standing in for
 * one.
 *
 * <p>Launches genuine second (and third, fourth) JVMs via {@link
 * ProcessBuilder} running {@link LockContenderMain} -- not a fake, not a
 * mock, not a second thread. Each contender process independently
 * acquires the <i>same</i> lock file this JVM's own {@code @TempDir}
 * created, and races a non-atomic read-sleep-increment-write against a
 * shared counter file while holding it. If mutual exclusion held across
 * every real process for the whole run, the final counter exactly equals
 * the total number of increments attempted; any shortfall is a genuine,
 * observed correctness failure of the underlying primitive on this real
 * filesystem, not a false negative from an imperfect test technique.
 *
 * <p><b>Real, disclosed classpath-portability judgment call</b>: {@link
 * ProcessBuilder} launches each contender using {@code
 * System.getProperty("java.home")} to find a real {@code java} binary and
 * {@code System.getProperty("java.class.path")} to reproduce this JVM's
 * own classpath in the child. Verified empirically first, not assumed:
 * under Gradle's default {@code Test} task configuration on this real
 * repository (Gradle 8.12, Linux/WSL2), {@code java.class.path} is a real,
 * literal, colon-separated list of this module's build output directories
 * and dependency jars -- confirmed by a throwaway diagnostic test run
 * before this file was written, printing every entry. This is
 * <b>not</b> universally guaranteed: Gradle is documented to sometimes
 * substitute a single "pathing jar" (a small jar whose manifest {@code
 * Class-Path} attribute holds the real list) to work around OS command-
 * line length limits, primarily a Windows concern. This test does not
 * attempt to handle that case defensively -- if it is ever hit on a future
 * CI/dev environment, this test would fail with a real, diagnosable
 * {@code ClassNotFoundException} from the child process (surfaced via
 * this test's own stderr-on-failure assertion below), not silently pass
 * while proving nothing.
 */
class AccountLedgerLockMultiProcessTest {

    @Test
    void acquireProvidesRealMutualExclusionAcrossGenuinelySeparateOsProcesses(@TempDir Path tempDir)
            throws Exception {
        Path lockPath = tempDir.resolve("ledger.json.lock");
        Path counterPath = tempDir.resolve("counter.txt");
        int processCount = 4;
        int iterationsPerProcess = 5;
        int expectedTotal = processCount * iterationsPerProcess;
        long staleThresholdMillis = 30_000;
        long totalRetryBudgetMillis = 20_000;
        long holdMillis = 20;

        List<Process> processes = new ArrayList<>();
        List<Path> outputs = new ArrayList<>();
        try {
            for (int i = 0; i < processCount; i++) {
                Path outputPath = tempDir.resolve("contender-" + i + "-output.log");
                outputs.add(outputPath);
                processes.add(launchContender(
                        lockPath, counterPath, iterationsPerProcess, staleThresholdMillis, totalRetryBudgetMillis,
                        holdMillis, outputPath));
            }

            for (int i = 0; i < processes.size(); i++) {
                Process process = processes.get(i);
                boolean finished = process.waitFor(60, TimeUnit.SECONDS);
                if (!finished) {
                    fail("a real contender process did not finish within the test timeout. Output so far: "
                            + readOutputOrPlaceholder(outputs.get(i)));
                }
                if (process.exitValue() != 0) {
                    fail("a real contender process exited with code " + process.exitValue() + ": "
                            + readOutputOrPlaceholder(outputs.get(i)));
                }
            }
        } finally {
            // Real Major finding, real CodeRabbit review of this PR: an
            // early fail(...) above must not leave any still-running
            // contender behind. An orphaned contender would keep creating
            // and deleting the real, shared lock file (and appending to
            // the shared counter file) completely unsupervised, corrupting
            // both this test's own cleanup assertion below AND any later
            // test that reuses a lock/counter path, well after this test
            // method has already returned.
            //
            // destroyForcibly() itself is asynchronous -- a further real
            // finding on a later review round of this same PR: it only
            // requests termination and returns immediately, without
            // confirming the process actually exited. If a still-alive
            // child recreates lockPath/counterPath after this finally
            // block returns, it races @TempDir's own directory cleanup --
            // a resulting directory-not-empty failure would mask this
            // test's real result (its own assertions above, or a genuine
            // mutual-exclusion failure already reported) behind an
            // unrelated secondary exception. Waiting briefly for each
            // process's actual exit closes that window without weakening
            // the original cleanup -- every process is still destroyed and
            // waited on regardless of whether an earlier fail(...) already
            // fired.
            for (Process process : processes) {
                process.destroyForcibly().waitFor(10, TimeUnit.SECONDS);
            }
        }

        int finalCount = Integer.parseInt(Files.readString(counterPath).trim());
        assertEquals(
                expectedTotal,
                finalCount,
                "final counter must equal the total attempted increments -- a shortfall means two real,"
                        + " separate OS processes were inside the AccountLedgerLock-protected critical section at"
                        + " the same time, i.e. the atomic-file-creation mutex does not hold on this filesystem");
        assertTrue(Files.notExists(lockPath), "the lock file must not be left behind once every contender is done");
    }

    /**
     * Real Major finding, real CodeRabbit review of this PR: {@link
     * ProcessBuilder} pipes a child's stdout/stderr by default, and this
     * test used to read the child's stderr only <i>after</i> {@code
     * waitFor} returned. {@link AccountLedgerLock} logs at {@code ERROR}
     * on every steal (routine under this test's own deliberately heavy
     * contention) -- if a child fills the OS pipe buffer (commonly 64KB)
     * before this test ever reads it, the child blocks forever on its own
     * write, and {@code waitFor(60, TimeUnit.SECONDS)} times out for a
     * reason that has nothing to do with the lock primitive itself,
     * turning a real correctness signal into a misleading, undiagnosable
     * test-plumbing failure. Fixed by redirecting each child's combined
     * output straight to its own file on disk from the start -- nothing is
     * ever left sitting in an in-memory pipe for this process to drain.
     */
    private Process launchContender(
            Path lockPath,
            Path counterPath,
            int iterations,
            long staleThresholdMillis,
            long totalRetryBudgetMillis,
            long holdMillis,
            Path outputPath)
            throws IOException {
        String javaBin = System.getProperty("java.home") + File.separator + "bin" + File.separator + "java";
        String classpath = System.getProperty("java.class.path");
        ProcessBuilder builder = new ProcessBuilder(
                javaBin,
                "-cp",
                classpath,
                LockContenderMain.class.getName(),
                lockPath.toString(),
                counterPath.toString(),
                Integer.toString(iterations),
                Long.toString(staleThresholdMillis),
                Long.toString(totalRetryBudgetMillis),
                Long.toString(holdMillis));
        builder.redirectErrorStream(true);
        builder.redirectOutput(outputPath.toFile());
        return builder.start();
    }

    private static String readOutputOrPlaceholder(Path outputPath) {
        try {
            return Files.readString(outputPath);
        } catch (IOException e) {
            return "<no output captured: " + e + ">";
        }
    }
}
