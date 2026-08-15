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
        for (int i = 0; i < processCount; i++) {
            processes.add(launchContender(
                    lockPath, counterPath, iterationsPerProcess, staleThresholdMillis, totalRetryBudgetMillis,
                    holdMillis));
        }

        for (Process process : processes) {
            boolean finished = process.waitFor(60, TimeUnit.SECONDS);
            if (!finished) {
                process.destroyForcibly();
                fail("a real contender process did not finish within the test timeout");
            }
            if (process.exitValue() != 0) {
                String stderr = new String(process.getErrorStream().readAllBytes());
                fail("a real contender process exited with code " + process.exitValue() + ": " + stderr);
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

    private Process launchContender(
            Path lockPath,
            Path counterPath,
            int iterations,
            long staleThresholdMillis,
            long totalRetryBudgetMillis,
            long holdMillis)
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
        return builder.start();
    }
}
