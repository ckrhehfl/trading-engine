package engine.runtime;

import engine.risk.AccountState;
import engine.schemas.OrderIntent;
import java.math.BigDecimal;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Hand-written {@link AccountStateProvider} test double -- same no-mocking-
 * framework convention as {@link FakeExchangeAdapter}/{@link FakePriceFeed}/
 * {@link FakeTradingCalendar}. Returns a fixed {@link AccountState} (set at
 * construction) from every {@link #reserveForIntent} call, and records each
 * method's arguments and call count so a test can assert {@link
 * TradingLoop#tick()} actually calls this seam the way {@link
 * AccountStateProvider}'s own Javadoc says it must -- one {@link
 * #reserveForIntent} per new signal, paired with exactly one of {@link
 * #confirmReservation} (approved/modified) or {@link #releaseReservation}
 * (rejected) <b>when {@code OrderPipeline#submitIntent} returns normally</b>.
 * If that call instead throws, neither fires and the reservation is
 * deliberately left unresolved -- {@code
 * TradingLoopTest#tickLeavesTheReservationUnresolvedWhenSubmitIntentThrowsAfterAReservationWasMade}
 * asserts exactly this against this fake's own recorded call counts, not
 * just against the class-level contract description.
 */
final class FakeAccountStateProvider implements AccountStateProvider {

    private final AccountState accountState;

    private volatile OrderIntent lastReservedIntent;
    private volatile BigDecimal lastReservedReferencePrice;
    private final AtomicInteger reserveCallCount = new AtomicInteger();

    private volatile UUID lastConfirmedIntentId;
    private volatile BigDecimal lastConfirmedQuantity;
    private volatile BigDecimal lastConfirmedPrice;
    private final AtomicInteger confirmCallCount = new AtomicInteger();

    private volatile UUID lastReleasedIntentId;
    private final AtomicInteger releaseCallCount = new AtomicInteger();

    FakeAccountStateProvider(AccountState accountState) {
        this.accountState = Objects.requireNonNull(accountState, "accountState is required");
    }

    @Override
    public AccountState reserveForIntent(OrderIntent intent, BigDecimal referencePrice) {
        lastReservedIntent = intent;
        lastReservedReferencePrice = referencePrice;
        reserveCallCount.incrementAndGet();
        return accountState;
    }

    @Override
    public void confirmReservation(UUID intentId, BigDecimal approvedQuantity, BigDecimal price) {
        lastConfirmedIntentId = intentId;
        lastConfirmedQuantity = approvedQuantity;
        lastConfirmedPrice = price;
        confirmCallCount.incrementAndGet();
    }

    @Override
    public void releaseReservation(UUID intentId) {
        lastReleasedIntentId = intentId;
        releaseCallCount.incrementAndGet();
    }

    OrderIntent lastReservedIntent() {
        return lastReservedIntent;
    }

    BigDecimal lastReservedReferencePrice() {
        return lastReservedReferencePrice;
    }

    int reserveCallCount() {
        return reserveCallCount.get();
    }

    UUID lastConfirmedIntentId() {
        return lastConfirmedIntentId;
    }

    BigDecimal lastConfirmedQuantity() {
        return lastConfirmedQuantity;
    }

    BigDecimal lastConfirmedPrice() {
        return lastConfirmedPrice;
    }

    int confirmCallCount() {
        return confirmCallCount.get();
    }

    UUID lastReleasedIntentId() {
        return lastReleasedIntentId;
    }

    int releaseCallCount() {
        return releaseCallCount.get();
    }
}
