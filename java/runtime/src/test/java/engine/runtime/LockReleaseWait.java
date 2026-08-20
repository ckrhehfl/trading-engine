package engine.runtime;

/**
 * Shared retry budget and helper for waiting until a just-{@link
 * AccountLedgerLock#close() close}d lock's own release has genuinely
 * concluded -- extracted here per a further real CodeRabbit review round
 * on this PR (the one immediately after round 57, which itself introduced
 * this exact logic independently in two places).
 *
 * <p><b>Real, disclosed drift risk this class exists to close</b>:
 * {@link AccountLedgerLockTest} and {@code LockContenderMain} each
 * independently carried their own {@code closeUntilReleased} method with
 * the identical "40 attempts x 50ms" retry budget, and {@link
 * AccountLedgerLockMultiProcessTest}'s own {@code waitForLockFileAbsence}
 * carried the same two numbers a third time. Round 56 needed to widen
 * this exact budget (250ms -> 2s) and had to touch all three by hand to
 * keep them in sync -- each of the three round-56 Javadoc blocks even
 * says so explicitly ("round 56's own three sibling fixes"). Three
 * independently-maintained copies of the same real number is a real risk
 * a future change could silently widen (or narrow) only one or two of
 * them. Consolidated to a single source of truth: {@link #MAX_ATTEMPTS}/
 * {@link #DELAY_MILLIS} for the constants themselves, and {@link
 * #closeUntilReleased} for the two call sites ({@link
 * AccountLedgerLockTest}, {@code LockContenderMain}) whose own
 * termination condition is identical -- poll {@link AccountLedgerLock
 * #stillHoldsCurrentGeneration()}, established in round 57 as the real,
 * correct completion signal (see that method's own Javadoc for why
 * {@code Files.notExists(lockPath)} alone is not proof of release under
 * real contention, and why {@link AccountLedgerLock#requireHeld()}
 * cannot substitute for it either).
 *
 * <p><b>Deliberately NOT reused by {@code AccountLedgerLockMultiProcessTest
 * #waitForLockFileAbsence}</b> beyond the two constants above -- that
 * method runs in the <i>launching</i> test process, which never itself
 * held an {@link AccountLedgerLock} for the four real child-process
 * contenders it is waiting on, so it has no generation of its own to
 * compare against; file absence really is the only signal genuinely
 * available to it, a real structural difference from the two {@link
 * #closeUntilReleased} call sites, not an oversight.
 */
final class LockReleaseWait {

    private LockReleaseWait() {}

    /** Shared with {@code AccountLedgerLockMultiProcessTest#waitForLockFileAbsence} -- see class Javadoc. */
    static final int MAX_ATTEMPTS = 40;

    /** Shared with {@code AccountLedgerLockMultiProcessTest#waitForLockFileAbsence} -- see class Javadoc. */
    static final long DELAY_MILLIS = 50;

    /**
     * Retries {@link AccountLedgerLock#close()} until {@code lock} no
     * longer holds its own current generation at its own {@code
     * lockPath} (see {@link AccountLedgerLock#stillHoldsCurrentGeneration()}'s
     * own Javadoc) -- {@code close()} does not itself signal success or
     * failure and can take a retryable, non-final path on this project's
     * own drvfs mount (see {@link AccountLedgerLock}'s own class Javadoc,
     * fourth caller-contract point), so a single call is not sufficient
     * proof of release under real, sustained contention. Not treated as
     * a hard failure if the budget is exhausted here -- each caller's own
     * subsequent assertions are what actually judge a genuine failure,
     * with a real, meaningful signal rather than a single-shot race
     * against a known transient condition.
     */
    static void closeUntilReleased(AccountLedgerLock lock) throws InterruptedException {
        for (int attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
            lock.close();
            if (!lock.stillHoldsCurrentGeneration()) {
                return;
            }
            Thread.sleep(DELAY_MILLIS);
        }
    }
}
