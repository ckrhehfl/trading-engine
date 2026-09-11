package engine.exchange;

import engine.oms.Order;
import engine.schemas.Side;
import java.util.List;

/**
 * Venue-agnostic boundary between OMS and a real exchange. {@link
 * BingXAdapter} is the first implementation; a new venue means a new
 * implementation of this interface, never a change to OMS/Risk/Execution
 * (see CLAUDE.md's Architecture section).
 *
 * <p>{@code submitOrder}/{@code cancelOrder} mutate the passed {@link
 * Order}'s state directly, mirroring {@code engine.execution.PaperBroker}'s
 * idiom so OMS-facing calling code doesn't need to know whether it's
 * talking to a paper or live adapter. {@code queryOrder} is the deliberate
 * exception: it is a pure read and must never mutate the passed {@code
 * Order} — a real exchange fill needs polling or a push to resolve, unlike
 * a paper fill that's determinable synchronously from an injected price,
 * so there is no fill-application logic to run here yet (see
 * Implementation Priority #8).
 *
 * <p>From this priority onward, an {@code ExchangeAdapter} may only ever be
 * invoked from OMS-mediated flows — never called directly with a hand-built
 * {@code Order}, including for testing or demos (see CLAUDE.md's
 * Implementation Priority #7 note).
 */
public interface ExchangeAdapter {

    void submitOrder(Order order);

    void cancelOrder(Order order);

    OrderStatus queryOrder(Order order);

    List<PositionSnapshot> getPositions();

    BalanceSnapshot getBalance();

    void setLeverage(String symbol, Side side, int leverage);

    void setPositionMode(PositionMode mode);

    /**
     * The mode the account is <em>actually</em> in, read from the venue.
     *
     * <p>Added because setting a mode is not the same as knowing one.
     * {@code VstPreflight} skips the set when a pre-existing position is
     * found -- a venue will not change mode while a position is open --
     * which left an adapter built for one-way potentially talking to a
     * hedge account, sending {@code positionSide=BOTH} that the venue
     * would reject or misapply the moment a human reset the kill switch.
     * Raised by CodeRabbit on PR #161; see GitHub issue #157.
     */
    PositionMode getPositionMode();
}
