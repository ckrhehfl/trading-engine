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
 * <p><b>Idempotency contract, per {@code intentId}</b> (CodeRabbit review
 * finding on the PR that introduced this interface): a real implementation
 * must not let a repeated call for the same {@code intentId} duplicate
 * work. Concretely -- {@link #reserveForIntent} is keyed by {@code
 * intent.intentId()}; a second call for an {@code intentId} that already
 * has a live reservation must replace, not add to, that reservation (a
 * genuine repeat can happen -- see {@code TradingLoop}'s own
 * {@code submittedOrderIdsRecordsARepeatWhenTheSameIntentIsSubmittedOnTwoSeparateTicks}
 * test for a real, if rare, scenario). {@link #confirmReservation} and
 * {@link #releaseReservation} are each expected to be called at most once
 * per {@link #reserveForIntent} call in the normal (non-throwing) path --
 * see {@code TradingLoop.tick()}'s own call site for the exactly-one-of
 * pairing this depends on -- so an implementation does not need to
 * separately guard against being called twice for the same successful
 * reservation under ordinary operation. The default {@code
 * SyntheticAccountStateProvider} satisfies this trivially: {@link
 * #reserveForIntent} ignores {@code intentId} entirely (always returns a
 * fresh read of the loop's own live equity), and {@link
 * #confirmReservation}/{@link #releaseReservation} are no-ops -- there is
 * no reservation state to duplicate. A real stateful implementation (Task
 * C) must design and test its own duplicate-{@code intentId} handling
 * against its actual reservation data structure -- not attempted here,
 * since none exists yet to test against.
 *
 * <p><b>A real, disclosed gap, left open rather than fixed here</b>
 * (second CodeRabbit review finding, same PR): if {@code
 * OrderPipeline#submitIntent} itself throws -- after {@link
 * #reserveForIntent} already succeeded -- {@code TradingLoop.tick()}
 * deliberately does <b>not</b> call {@link #releaseReservation}. See that
 * call site's own code comment for the full reasoning: releasing would
 * risk understating committed exposure if the order actually was
 * registered before the throw, which is the dangerous direction: the
 * reservation is left at its pessimistic (safe, over-, never under-
 * committed) size instead, pending a future reconciliation pass (the
 * governing plan's Task D) that can determine the real outcome. This is a
 * real, known limitation of this interface's current contract, not a
 * silent oversight.
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
