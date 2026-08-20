package engine.runtime;

import engine.exchange.BalanceSnapshot;
import engine.exchange.ExchangeAdapter;
import engine.exchange.OrderStatus;
import engine.exchange.PositionMode;
import engine.exchange.PositionSnapshot;
import engine.oms.Order;
import engine.schemas.Side;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Hand-written {@link ExchangeAdapter} test double, local to {@code
 * :runtime}'s own test sources -- mirrors {@code engine.execution}'s own
 * package-private {@code FakeExchangeAdapter} (see {@code
 * ExchangeOrderExecutorTest}), not reused directly across modules because
 * Gradle test source sets aren't shared between modules (confirmed: {@code
 * :runtime} depends on {@code :execution}'s main sources only, never its
 * test sources) -- this codebase's established no-mocking-framework
 * convention means every module keeps its own small, local fakes rather than
 * introduce a shared test-fixtures dependency for one class.
 *
 * <p>{@link #getBalance()}/{@link #getPositions()}/{@link #setLeverage} are
 * scriptable -- the only three {@link ExchangeAdapter} methods {@link
 * VstPreflight} and {@link SubmissionMarkerResolver} ever call ({@code
 * setLeverage} added for {@code VstPreflight}'s own real leverage-
 * enforcement step). Every other method throws {@link
 * UnsupportedOperationException}, asserting by construction that neither of
 * those classes calls anything else.
 */
final class FakeExchangeAdapter implements ExchangeAdapter {

    record LeverageCall(String symbol, Side side, int leverage) {}

    private volatile BalanceSnapshot balance;
    private volatile RuntimeException balanceFailure;
    private volatile List<PositionSnapshot> positions = List.of();
    private volatile RuntimeException positionsFailure;
    private volatile RuntimeException setLeverageFailure;
    private final List<LeverageCall> leverageCalls = new CopyOnWriteArrayList<>();

    /** Clears any previously-scripted {@link #willFailBalanceWith} -- lets a test script a failure, then a later recovery, on the same instance. */
    void willReturnBalance(BalanceSnapshot balance) {
        this.balance = Objects.requireNonNull(balance);
        this.balanceFailure = null;
    }

    void willFailBalanceWith(RuntimeException failure) {
        this.balanceFailure = Objects.requireNonNull(failure);
    }

    /** Clears any previously-scripted {@link #willFailPositionsWith} -- lets a test script a failure, then a later recovery, on the same instance. */
    void willReturnPositions(List<PositionSnapshot> positions) {
        this.positions = Objects.requireNonNull(positions);
        this.positionsFailure = null;
    }

    void willFailPositionsWith(RuntimeException failure) {
        this.positionsFailure = Objects.requireNonNull(failure);
    }

    void willFailSetLeverageWith(RuntimeException failure) {
        this.setLeverageFailure = Objects.requireNonNull(failure);
    }

    List<LeverageCall> leverageCalls() {
        return new ArrayList<>(leverageCalls);
    }

    @Override
    public BalanceSnapshot getBalance() {
        if (balanceFailure != null) {
            throw balanceFailure;
        }
        if (balance == null) {
            throw new IllegalStateException("test bug: willReturnBalance/willFailBalanceWith was never called");
        }
        return balance;
    }

    @Override
    public List<PositionSnapshot> getPositions() {
        if (positionsFailure != null) {
            throw positionsFailure;
        }
        return positions;
    }

    @Override
    public void submitOrder(Order order) {
        throw new UnsupportedOperationException("this fake's submitOrder is not scripted for this test suite");
    }

    @Override
    public void cancelOrder(Order order) {
        throw new UnsupportedOperationException("this fake's cancelOrder is not scripted for this test suite");
    }

    @Override
    public OrderStatus queryOrder(Order order) {
        throw new UnsupportedOperationException("this fake's queryOrder is not scripted for this test suite");
    }

    @Override
    public void setLeverage(String symbol, Side side, int leverage) {
        if (setLeverageFailure != null) {
            throw setLeverageFailure;
        }
        leverageCalls.add(new LeverageCall(symbol, side, leverage));
    }

    @Override
    public void setPositionMode(PositionMode mode) {
        throw new UnsupportedOperationException("this fake's setPositionMode is not scripted for this test suite");
    }
}
