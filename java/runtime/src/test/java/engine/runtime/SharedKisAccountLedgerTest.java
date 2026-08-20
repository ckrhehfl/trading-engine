package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.execution.PaperBroker;
import engine.oms.OrderStore;
import engine.risk.AccountState;
import engine.risk.RiskGateway;
import engine.risk.RiskLimits;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@link SharedKisAccountLedger} composes {@link AccountLedgerLock} + {@link
 * AccountLedgerStore} into a real {@link AccountStateProvider} -- see the
 * governing plan's "Shared KIS account risk ledger" section, "2. The shared
 * ledger", and that class's own Javadoc. Each of {@link
 * #reserveForIntentOnAFreshLedgerCreatesAReservationSizedToQuantityTimesPrice}
 * through {@link #releaseReservationIsANoOpWhenNoMatchingReservationExists}
 * exercises one method in isolation against a real, temp-dir-backed ledger
 * file (no mocking framework anywhere in this codebase). {@link
 * #concurrentTradingLoopsSharingOneLedgerFileNeverExceedAllocatedVirtualCapital}
 * is the actual safety-property proof this whole design exists for: real
 * concurrent {@link TradingLoop} instances, each with its own {@link
 * SharedKisAccountLedger} pointed at the same file, submitting
 * simultaneously.
 */
class SharedKisAccountLedgerTest {

    private static final String VENUE = "KIS";
    private static final String ACCOUNT_ID = "acct-1";
    private static final Duration LOCK_STALE_THRESHOLD = Duration.ofSeconds(30);
    private static final Duration LOCK_RETRY_BUDGET = Duration.ofSeconds(5);

    // ---- bootstrapOrLoad ----

    @Test
    void bootstrapOrLoadCreatesAFreshPersistedLedgerWhenNoneExists(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        assertFalse(Files.exists(ledgerPath));

        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));

        assertTrue(Files.exists(ledgerPath), "bootstrapOrLoad must persist a real file, not just return an in-memory ledger");
        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertEquals(new BigDecimal("100000"), onDisk.allocatedVirtualCapital());
        assertTrue(onDisk.reservations().isEmpty());
    }

    @Test
    void bootstrapOrLoadReusesAnAlreadyPersistedLedgerWithoutOverwritingIt(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        LedgerReservation existingReservation = new LedgerReservation(
                UUID.randomUUID(), "SOME-SYM", 999L, "host-x", new BigDecimal("12345"), Instant.now());
        AccountLedger preExisting = new AccountLedger(
                VENUE, ACCOUNT_ID, new BigDecimal("500000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of(existingReservation));
        persistWithLock(ledgerPath, preExisting);

        // A second (sibling-process-style) call, today's real balance (larger
        // than what's already persisted) -- must NOT re-bootstrap or
        // overwrite; the already-persisted allocatedVirtualCapital and
        // reservation must be used as-is.
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("999999999"));

        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("999999999"));
        assertEquals(new BigDecimal("500000"), onDisk.allocatedVirtualCapital());
        assertEquals(1, onDisk.reservations().size());
        assertEquals(existingReservation, onDisk.reservations().get(0));
    }

    /**
     * Disclosed, real consequence of reusing {@link AccountLedgerStore
     * #load}'s own existing fail-closed contract as-is (see {@link
     * SharedKisAccountLedger#bootstrapOrLoad}'s own Javadoc): if today's
     * real KIS balance is smaller than an already-persisted {@code
     * allocatedVirtualCapital}, this call refuses rather than silently
     * keeping the larger, stale figure.
     */
    @Test
    void bootstrapOrLoadFailsClosedWhenTodaysRealBalanceIsSmallerThanTheAlreadyPersistedAllocation(
            @TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        AccountLedger preExisting = new AccountLedger(
                VENUE, ACCOUNT_ID, new BigDecimal("1000000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                null, null, null, List.of());
        persistWithLock(ledgerPath, preExisting);

        assertThrows(
                IllegalStateException.class,
                () -> SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("500000")));
    }

    // ---- reserveForIntent ----

    @Test
    void reserveForIntentOnAFreshLedgerCreatesAReservationSizedToQuantityTimesPrice(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        OrderIntent intent = guardedMarketIntent(new BigDecimal("10"));

        AccountState result = ledger.reserveForIntent(intent, new BigDecimal("100"));

        assertEquals(new BigDecimal("99000"), result.equity());
        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertEquals(1, onDisk.reservations().size());
        assertEquals(new BigDecimal("1000"), onDisk.reservations().get(0).notional());
        assertEquals(intent.intentId(), onDisk.reservations().get(0).clientOrderId());
    }

    @Test
    void reserveForIntentUsesLimitPriceOverReferencePriceWhenPresent(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        OrderIntent intent = new OrderIntent(
                UUID.randomUUID(), "SYM", Side.LONG, OrderType.LIMIT, new BigDecimal("10"), new BigDecimal("50"),
                "1d", Instant.now());

        AccountState result = ledger.reserveForIntent(intent, new BigDecimal("999")); // referencePrice ignored

        assertEquals(new BigDecimal("99500"), result.equity()); // 100000 - (10 * 50)
    }

    @Test
    void reserveForIntentDoesNotCreateAReservationWhenEffectivePriceIsNonPositive(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        OrderIntent intent = guardedMarketIntent(new BigDecimal("10"));

        AccountState result = ledger.reserveForIntent(intent, BigDecimal.ZERO);

        assertEquals(new BigDecimal("100000"), result.equity());
        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertTrue(onDisk.reservations().isEmpty());
    }

    @Test
    void reserveForIntentReturnsFlooredNearZeroEquityAndSkipsReservationWhenReconciliationAlarmIsTripped(
            @TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        AccountLedger withAlarm = new AccountLedger(
                VENUE, ACCOUNT_ID, new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO,
                Instant.now(), Instant.now(), "ledger exposure diverged from real account by more than 10%",
                List.of());
        persistWithLock(ledgerPath, withAlarm);
        SharedKisAccountLedger ledger = new SharedKisAccountLedger(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        OrderIntent intent = guardedMarketIntent(new BigDecimal("10"));

        AccountState result = ledger.reserveForIntent(intent, new BigDecimal("100"));

        assertEquals(0, BigDecimal.ONE.compareTo(result.equity()), "equity must be floored to a small positive constant");
        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertTrue(onDisk.reservations().isEmpty(), "no reservation may be created while the alarm is tripped");
    }

    /**
     * {@link AccountStateProvider}'s own documented monotonic idempotency
     * rule: a repeated {@link SharedKisAccountLedger#reserveForIntent} call
     * for the same {@code intentId} may only leave the recorded exposure
     * unchanged or raise it, never reduce it.
     */
    @Test
    void reserveForIntentNeverShrinksAnExistingReservationOnARepeatedIntentId(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        UUID intentId = UUID.randomUUID();
        OrderIntent large = guardedMarketIntentWithId(intentId, new BigDecimal("50")); // 50 * 100 = 5000
        OrderIntent small = guardedMarketIntentWithId(intentId, new BigDecimal("10")); // 10 * 100 = 1000
        OrderIntent larger = guardedMarketIntentWithId(intentId, new BigDecimal("80")); // 80 * 100 = 8000

        ledger.reserveForIntent(large, new BigDecimal("100"));
        AccountLedger afterLarge = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertEquals(new BigDecimal("5000"), afterLarge.reservations().get(0).notional());

        ledger.reserveForIntent(small, new BigDecimal("100")); // a smaller retry must not shrink it
        AccountLedger afterSmall = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertEquals(1, afterSmall.reservations().size());
        assertEquals(new BigDecimal("5000"), afterSmall.reservations().get(0).notional());

        ledger.reserveForIntent(larger, new BigDecimal("100")); // a genuinely larger request may raise it
        AccountLedger afterLarger = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertEquals(1, afterLarger.reservations().size());
        assertEquals(new BigDecimal("8000"), afterLarger.reservations().get(0).notional());
    }

    /**
     * Real CodeRabbit review finding on this PR: the lock alone only
     * prevents a lost update, not a sequence of independently-valid
     * pessimistic reservations from together exceeding {@code
     * allocatedVirtualCapital}. A second reservation whose own pessimistic
     * notional would push the combined total over budget must be declined
     * outright -- not persisted and relied on to self-correct later.
     */
    @Test
    void reserveForIntentDeclinesAReservationThatWouldPushCombinedTotalOverAllocatedCapital(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        OrderIntent first = guardedMarketIntent(new BigDecimal("600")); // 600 * 100 = 60000
        OrderIntent second = guardedMarketIntent(new BigDecimal("600")); // another 60000 -> 120000 combined, over budget

        AccountState firstResult = ledger.reserveForIntent(first, new BigDecimal("100"));
        AccountState secondResult = ledger.reserveForIntent(second, new BigDecimal("100"));

        assertEquals(new BigDecimal("40000"), firstResult.equity());
        assertEquals(0, BigDecimal.ONE.compareTo(secondResult.equity()), "the declined attempt must get a floored snapshot");
        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertEquals(1, onDisk.reservations().size(), "only the first, budget-fitting reservation may be persisted");
        assertEquals(first.intentId(), onDisk.reservations().get(0).clientOrderId());
        BigDecimal total = onDisk.reservations().stream().map(LedgerReservation::notional).reduce(BigDecimal.ZERO, BigDecimal::add);
        assertTrue(total.compareTo(new BigDecimal("100000")) <= 0);
    }

    /** Complement to the decline test above: a candidate that exactly fills the remaining budget must still be accepted (not over, not under-tolerant). */
    @Test
    void reserveForIntentAcceptsAReservationThatExactlyFillsTheRemainingBudget(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        OrderIntent first = guardedMarketIntent(new BigDecimal("600")); // 60000
        OrderIntent second = guardedMarketIntent(new BigDecimal("400")); // exactly the remaining 40000

        ledger.reserveForIntent(first, new BigDecimal("100"));
        AccountState secondResult = ledger.reserveForIntent(second, new BigDecimal("100"));

        assertEquals(0, BigDecimal.ONE.compareTo(secondResult.equity()), "exactly exhausting the budget floors equity, but the reservation is still accepted");
        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertEquals(2, onDisk.reservations().size());
        BigDecimal total = onDisk.reservations().stream().map(LedgerReservation::notional).reduce(BigDecimal.ZERO, BigDecimal::add);
        assertEquals(new BigDecimal("100000"), total);
    }

    /**
     * Direct, {@link SharedKisAccountLedger}-level proof of the same
     * property {@link
     * #concurrentTradingLoopsSharingOneLedgerFileNeverExceedAllocatedVirtualCapital}
     * proves through the full {@link TradingLoop} pipeline -- this one
     * calls {@link SharedKisAccountLedger#reserveForIntent} directly from
     * many real concurrent threads, deliberately bypassing {@code
     * RiskGateway}/{@code confirmReservation}/{@code releaseReservation}
     * entirely, so the over-budget-decline behavior above is what's
     * actually under test, not masked by the full pipeline's own
     * downstream clamp-and-shrink correction. Five threads each attempt to
     * reserve exactly 40% of {@code allocatedVirtualCapital} (40000 of
     * 100000) -- the arithmetic is deterministic regardless of arrival
     * order: exactly two attempts can ever fit (80000 <= 100000), any
     * further attempt (90000 &gt; wait, 3*40000=120000 &gt; 100000) is
     * declined, so exactly 2 of the 5 reservations must succeed no matter
     * which two.
     */
    @Test
    void reserveForIntentNeverPersistsACombinedTotalOverBudgetUnderRealConcurrentAccess(@TempDir Path tempDir)
            throws InterruptedException {
        Path ledgerPath = tempDir.resolve("ledger.json");
        String stressAccount = "cap-acct";
        BigDecimal allocatedVirtualCapital = new BigDecimal("100000");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, stressAccount, allocatedVirtualCapital);

        int threadCount = 5;
        BigDecimal quantityPerAttempt = new BigDecimal("400"); // notional 40000 each at price 100
        BigDecimal price = new BigDecimal("100");

        List<Throwable> failures = new CopyOnWriteArrayList<>();
        CountDownLatch startLatch = new CountDownLatch(1);
        CountDownLatch doneLatch = new CountDownLatch(threadCount);

        for (int t = 0; t < threadCount; t++) {
            Thread thread = new Thread(() -> {
                try {
                    startLatch.await();
                    SharedKisAccountLedger threadLedger = new SharedKisAccountLedger(
                            ledgerPath, VENUE, stressAccount, allocatedVirtualCapital,
                            LOCK_STALE_THRESHOLD, Duration.ofSeconds(60));
                    OrderIntent intent = guardedMarketIntent(quantityPerAttempt);
                    threadLedger.reserveForIntent(intent, price);
                } catch (Throwable e) {
                    failures.add(e);
                } finally {
                    doneLatch.countDown();
                }
            });
            thread.start();
        }

        startLatch.countDown();
        assertTrue(doneLatch.await(60, TimeUnit.SECONDS), "all threads must finish within the test timeout");
        assertTrue(failures.isEmpty(), "no thread should have failed: " + failures);

        AccountLedger finalLedger = loadWithLock(ledgerPath, VENUE, stressAccount, allocatedVirtualCapital);
        BigDecimal total = finalLedger.reservations().stream().map(LedgerReservation::notional).reduce(BigDecimal.ZERO, BigDecimal::add);
        assertTrue(
                total.compareTo(allocatedVirtualCapital) <= 0,
                "combined committed notional " + total + " must never exceed allocatedVirtualCapital "
                        + allocatedVirtualCapital);
        assertEquals(
                2,
                finalLedger.reservations().size(),
                "exactly 2 of 5 concurrent 40000-notional attempts can ever fit within a 100000 budget, regardless"
                        + " of arrival order -- a lower count means a correct decline that shouldn't have happened,"
                        + " a higher count means a real over-budget lost update");
    }

    @Test
    void equityIsFlooredAtAPositiveMinimumForASingleRequestThatAloneExceedsAllocatedCapital(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("1000"));
        OrderIntent hugeIntent = guardedMarketIntent(new BigDecimal("1000")); // 1000 * 100 = 100000 >> 1000 allocated

        // Declined outright by the over-budget check above (a single request already exceeding the whole
        // budget can never fit) -- equity is still floored either way (this class's own contract, regardless
        // of whether the floor is reached via a declined reservation or an accepted one that exhausts the
        // budget -- see reserveForIntentAcceptsAReservationThatExactlyFillsTheRemainingBudget for the latter).
        AccountState result = ledger.reserveForIntent(hugeIntent, new BigDecimal("100"));

        assertTrue(result.equity().signum() > 0, "AccountState requires strictly positive equity");
        assertEquals(0, BigDecimal.ONE.compareTo(result.equity()));
        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("1000"));
        assertTrue(onDisk.reservations().isEmpty(), "a single over-budget request must never be persisted");
    }

    // ---- confirmReservation ----

    @Test
    void confirmReservationShrinksTheReservationToTheRealApprovedNotional(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        OrderIntent intent = guardedMarketIntent(new BigDecimal("50")); // pessimistic: 50 * 100 = 5000
        ledger.reserveForIntent(intent, new BigDecimal("100"));

        // RiskGateway clamped the approved quantity down to 12 at price 100 -> real notional 1200.
        ledger.confirmReservation(intent.intentId(), new BigDecimal("12"), new BigDecimal("100"));

        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertEquals(1, onDisk.reservations().size());
        assertEquals(new BigDecimal("1200"), onDisk.reservations().get(0).notional());
    }

    @Test
    void confirmReservationRemovesTheReservationWhenApprovedNotionalIsNonPositive(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        OrderIntent intent = guardedMarketIntent(new BigDecimal("50"));
        ledger.reserveForIntent(intent, new BigDecimal("100"));

        ledger.confirmReservation(intent.intentId(), BigDecimal.ZERO, new BigDecimal("100"));

        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertTrue(onDisk.reservations().isEmpty());
    }

    @Test
    void confirmReservationIsANoOpWhenNoMatchingReservationExists(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));

        ledger.confirmReservation(UUID.randomUUID(), new BigDecimal("1"), new BigDecimal("1"));

        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertTrue(onDisk.reservations().isEmpty());
        assertEquals(new BigDecimal("100000"), onDisk.allocatedVirtualCapital());
    }

    // ---- releaseReservation ----

    @Test
    void releaseReservationRemovesTheReservationEntirely(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        OrderIntent intent = guardedMarketIntent(new BigDecimal("10"));
        ledger.reserveForIntent(intent, new BigDecimal("100"));

        ledger.releaseReservation(intent.intentId());

        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertTrue(onDisk.reservations().isEmpty());
    }

    @Test
    void releaseReservationIsANoOpWhenNoMatchingReservationExists(@TempDir Path tempDir) {
        Path ledgerPath = tempDir.resolve("ledger.json");
        SharedKisAccountLedger ledger =
                SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));

        ledger.releaseReservation(UUID.randomUUID());

        AccountLedger onDisk = loadWithLock(ledgerPath, VENUE, ACCOUNT_ID, new BigDecimal("100000"));
        assertTrue(onDisk.reservations().isEmpty());
    }

    // ---- the real safety property: concurrent access across multiple real TradingLoop instances ----

    /**
     * The actual safety property this whole 3-task effort (extract {@link
     * AccountStateProvider}, build {@link AccountLedgerLock}/{@link
     * AccountLedgerStore}, compose them here) exists to prove: real
     * concurrent {@link TradingLoop} instances -- standing in for separate
     * {@code kis-paper} OS processes trading different symbols against the
     * same real KIS account -- sharing one ledger file must never let their
     * combined committed notional exceed {@code allocatedVirtualCapital},
     * even under genuine concurrent access.
     *
     * <p><b>Real threads, not a real second JVM (a deliberate scope
     * decision, not an oversight)</b>: Task B's own {@code
     * AccountLedgerLockMultiProcessTest} already proved the underlying
     * atomic-file-creation primitive itself is genuinely safe across real,
     * separate JVM processes on this repository's real drvfs mount -- that
     * is not this test's job to re-prove. What this task adds on top is a
     * new composition layer ({@link SharedKisAccountLedger}'s own read-
     * modify-write logic, and its wiring into {@link TradingLoop}), and a
     * real lost-update bug in that composition layer (e.g. failing to hold
     * the lock across the read-then-write, or a non-monotonic reservation
     * merge) would be caught identically by many real threads racing on
     * the same file as by many real processes -- {@link AccountLedgerLock}
     * itself has no in-process/cross-process distinction in its own mutual-
     * exclusion logic (exclusivity comes entirely from atomic file
     * creation, not from anything JVM-local). Real threads also make this
     * test's own failure mode (a lost update producing a final sum over
     * budget) deterministic to detect and fast to run, without the
     * additional {@code ProcessBuilder}/classpath-wiring machinery Task B's
     * own multi-process test already carries.
     *
     * <p>Each thread mints aggressive orders (pessimistic notional 10% of
     * {@code allocatedVirtualCapital} -- comfortably fits the reservation-
     * stage over-budget check on its own, see {@link
     * #reserveForIntentDeclinesAReservationThatWouldPushCombinedTotalOverAllocatedCapital},
     * while still being 5x {@link RiskGateway}'s own canary-tier 2%
     * per-order cap) so {@code RiskGateway} clamps every approved order
     * down to {@code maxOrderNotionalPercent} of whatever equity the
     * shared ledger currently reports -- this deliberately keeps every
     * successful reservation as large as {@code RiskGateway} will allow at
     * that instant, maximizing real contention on the shared budget rather
     * than trading well within it, without every single pessimistic
     * request being declined outright before {@code RiskGateway} ever sees
     * it (a materially different, narrower property than this test's own
     * subject -- see the dedicated decline tests above for that).
     */
    @Test
    void concurrentTradingLoopsSharingOneLedgerFileNeverExceedAllocatedVirtualCapital(@TempDir Path tempDir)
            throws InterruptedException {
        Path ledgerPath = tempDir.resolve("ledger.json");
        BigDecimal allocatedVirtualCapital = new BigDecimal("1000000");
        SharedKisAccountLedger.bootstrapOrLoad(ledgerPath, VENUE, "stress-acct", allocatedVirtualCapital);

        int threadCount = 5;
        int iterationsPerThread = 6;
        BigDecimal price = new BigDecimal("100");
        RiskLimits limits = RiskLimits.canary();

        List<Throwable> failures = new CopyOnWriteArrayList<>();
        CountDownLatch startLatch = new CountDownLatch(1);
        CountDownLatch doneLatch = new CountDownLatch(threadCount);

        for (int t = 0; t < threadCount; t++) {
            String symbol = "STRESS-SYM-" + t;
            Thread thread = new Thread(() -> {
                try {
                    startLatch.await();
                    RiskGateway riskGateway = new RiskGateway(limits);
                    OrderStore orderStore = new OrderStore();
                    OrderPipeline orderPipeline = new OrderPipeline(riskGateway, orderStore);
                    PaperBroker broker = new PaperBroker(BigDecimal.ZERO, BigDecimal.ZERO);
                    // notional = 1000 * 100 = 100000 -- 10% of allocatedVirtualCapital (fits the
                    // reservation-stage over-budget check on its own), 5x RiskGateway's own 2% cap.
                    DummySignalSource signalSource = new DummySignalSource(
                            symbol, Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("1000"), null, 1);
                    FakePriceFeed priceFeed = new FakePriceFeed(price);
                    KillSwitch killSwitch = new KillSwitch();
                    SharedKisAccountLedger threadLedger = new SharedKisAccountLedger(
                            ledgerPath, VENUE, "stress-acct", allocatedVirtualCapital,
                            LOCK_STALE_THRESHOLD, Duration.ofSeconds(60));
                    TradingLoop loop = new TradingLoop(
                            orderPipeline, broker, signalSource, priceFeed, killSwitch, symbol, threadLedger);

                    for (int i = 0; i < iterationsPerThread; i++) {
                        loop.tick();
                        if (loop.lastError() != null) {
                            failures.add(loop.lastError());
                        }
                    }
                } catch (Throwable e) {
                    failures.add(e);
                } finally {
                    doneLatch.countDown();
                }
            });
            thread.start();
        }

        startLatch.countDown();
        assertTrue(doneLatch.await(120, TimeUnit.SECONDS), "all threads must finish within the test timeout");
        assertTrue(failures.isEmpty(), "no thread/tick should have failed: " + failures);

        AccountLedger finalLedger = loadWithLock(ledgerPath, VENUE, "stress-acct", allocatedVirtualCapital);
        BigDecimal totalCommitted = finalLedger.reservations().stream()
                .map(LedgerReservation::notional)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        assertTrue(
                totalCommitted.compareTo(allocatedVirtualCapital) <= 0,
                "combined committed notional " + totalCommitted + " must never exceed allocatedVirtualCapital "
                        + allocatedVirtualCapital + " -- a lost update in SharedKisAccountLedger's own"
                        + " read-modify-write cycle would show up here as a real overage");
        // A real reservation was actually created by at least one order somewhere -- otherwise this test
        // would trivially pass without ever exercising the property it claims to prove.
        assertTrue(!finalLedger.reservations().isEmpty(), "the stress run must have produced at least one real reservation");
    }

    private static OrderIntent guardedMarketIntent(BigDecimal quantity) {
        return guardedMarketIntentWithId(UUID.randomUUID(), quantity);
    }

    private static OrderIntent guardedMarketIntentWithId(UUID intentId, BigDecimal quantity) {
        return new OrderIntent(
                intentId, "SYM", Side.LONG, OrderType.GUARDED_MARKET, quantity, null, "1d", Instant.now());
    }

    private static AccountLedger loadWithLock(
            Path ledgerPath, String venue, String accountId, BigDecimal defaultAllocatedCapital) {
        try (AccountLedgerLock lock =
                AccountLedgerLock.acquire(AccountLedgerStore.lockPathFor(ledgerPath), LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET)) {
            return AccountLedgerStore.load(ledgerPath, venue, accountId, defaultAllocatedCapital, lock);
        }
    }

    private static void persistWithLock(Path ledgerPath, AccountLedger ledger) {
        try (AccountLedgerLock lock =
                AccountLedgerLock.acquire(AccountLedgerStore.lockPathFor(ledgerPath), LOCK_STALE_THRESHOLD, LOCK_RETRY_BUDGET)) {
            AccountLedgerStore.persist(ledgerPath, ledger, lock);
        }
    }
}
