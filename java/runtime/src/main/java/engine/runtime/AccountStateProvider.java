package engine.runtime;

import engine.risk.AccountState;
import engine.risk.RiskGateway;
import engine.schemas.OrderIntent;
import java.math.BigDecimal;
import java.util.UUID;

/**
 * Produces the {@link AccountState} {@link TradingLoop} hands to {@link
 * OrderPipeline#submitIntent} for each new signal, and tracks the
 * reservation that {@code AccountState} represents across that intent's
 * eventual outcome. Extracted so a future shared, cross-process capital
 * ledger (the "Shared KIS account risk ledger" design in the governing
 * plan -- Task C, not built here) can be plugged into {@link TradingLoop}
 * without ever touching this class's control-flow logic again -- the same
 * extraction precedent this codebase already used once for {@link
 * PriceFeed} (and once before that for {@code engine.execution
 * .OrderExecutor}).
 *
 * <p><b>Why three methods, not just "give me an AccountState"</b>: {@link
 * RiskGateway#evaluate} can only ever clamp a requested quantity
 * <i>down</i> (MODIFIED) or reject it outright (REJECTED) -- it can never
 * approve more than what {@link OrderIntent#quantity()} asked for. That
 * means the only way to make "read committed exposure -&gt; decide -&gt;
 * record the new commitment" safe under concurrent access -- without
 * requiring a lock to span the entire {@code RiskGateway.evaluate()} call
 * itself, whose signature is frozen -- is to reserve <b>pessimistically</b>,
 * up front, against the full pre-clamp {@code intent.quantity()}, then
 * correct the reservation down afterward once the real, possibly-smaller
 * approved size is known:
 *
 * <ul>
 *   <li>{@link #reserveForIntent} is called once per new signal, before
 *       {@code RiskGateway.evaluate()} runs, and must size the reservation
 *       to {@code intent.quantity()} -- the full requested quantity, not
 *       any anticipated clamp.
 *   <li>Exactly one of the two calls below must follow, for every call to
 *       {@link #reserveForIntent}:
 *       <ul>
 *         <li>{@link #confirmReservation} -- called when {@code
 *             OrderPipeline#submitIntent} returns a real {@link
 *             engine.oms.Order} (Risk Gateway APPROVED or MODIFIED the
 *             intent). Shrinks the reservation from the pessimistic
 *             pre-clamp size down to the real approved size (the
 *             approved quantity at the given price) -- without this,
 *             every MODIFIED (clamped) order would leave its reservation
 *             permanently oversized, needlessly throttling later orders
 *             as clamped orders accumulate.
 *         <li>{@link #releaseReservation} -- called when {@code
 *             OrderPipeline#submitIntent} returns empty (Risk Gateway
 *             REJECTED the intent) -- releases the reservation entirely,
 *             since no {@code Order} was ever produced.
 *       </ul>
 * </ul>
 *
 * <p><b>Default implementation</b>: {@link TradingLoop}'s own private,
 * synchronous, process-local {@code SyntheticAccountStateProvider} -- a
 * no-op on {@link #confirmReservation}/{@link #releaseReservation}, and
 * on {@link #reserveForIntent} does exactly what {@code TradingLoop}
 * always did inline before this extraction: return the loop's own
 * private, synthetic, in-memory equity figure. This is what every
 * constructor overload except the new 7-arg one still gets, so {@code
 * simulated}/{@code bingx-vst} remain zero-behavior-change callers.
 *
 * <p>A real, shared/durable, cross-process implementation -- a file-backed
 * ledger multiple {@code kis-paper} OS processes trading against the same
 * real KIS account can all read/write safely -- is future work, not built
 * here; see the governing plan's Task B/C.
 */
public interface AccountStateProvider {

    /**
     * Returns an {@link AccountState} reflecting a reservation sized
     * pessimistically to {@code intent.quantity()} (see class Javadoc) --
     * called once per new signal, before {@link RiskGateway#evaluate}
     * runs. Must be paired with exactly one of {@link #confirmReservation}
     * or {@link #releaseReservation}.
     */
    AccountState reserveForIntent(OrderIntent intent, BigDecimal referencePrice);

    /**
     * Corrects the reservation {@code intentId} made in {@link
     * #reserveForIntent} down from its pessimistic pre-clamp size to the
     * real approved size ({@code approvedQuantity} at {@code price}) --
     * called when Risk Gateway APPROVED or MODIFIED the intent and a real
     * {@link engine.oms.Order} was produced.
     */
    void confirmReservation(UUID intentId, BigDecimal approvedQuantity, BigDecimal price);

    /**
     * Releases the reservation {@code intentId} made in {@link
     * #reserveForIntent} entirely -- called when Risk Gateway REJECTED the
     * intent and no {@link engine.oms.Order} was ever produced.
     */
    void releaseReservation(UUID intentId);
}
