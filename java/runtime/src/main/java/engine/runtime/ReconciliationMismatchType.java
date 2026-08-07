package engine.runtime;

/**
 * The kinds of internal-bookkeeping inconsistency {@link Reconciler#check}
 * can detect between {@link engine.oms.OrderStore} and {@link
 * engine.execution.PaperBroker} — see {@link Reconciler}'s own Javadoc for
 * exactly what is (and is not) checked, and why. All four are internal-only:
 * none of them compares anything against a real exchange (there is no
 * {@code BingXAdapter} in this loop — see CLAUDE.md's Priority #7/#10).
 */
public enum ReconciliationMismatchType {

    /**
     * {@link engine.oms.OrderStore} has this order in a still-open state
     * (see {@link Reconciler}'s own open-state set), but {@link
     * engine.execution.PaperBroker#pendingOrders()} has no record of it —
     * it will never receive a fill or cancel confirmation from the paper
     * broker. This is the exact scenario {@link TradingLoop}'s own
     * {@code submitToBroker} Javadoc already names as a real, if rare,
     * failure mode: {@code PaperBroker.submit} throwing after the order
     * was already registered in {@code OrderStore}.
     */
    ORPHANED_IN_BROKER,

    /**
     * The same client order id was handed to {@code OrderPipeline
     * .submitIntent} and produced a real, registered {@code Order} more
     * than once. Structurally guarded against elsewhere ({@code
     * FileSignalSource}'s own last-delivered-id dedup, {@code
     * PaperBroker}'s own {@code seenClientOrderIds} defense-in-depth) —
     * this is redundant defense-in-depth for if either of those ever
     * breaks, not a scenario reachable through the normal file-based
     * signal path today.
     */
    DUPLICATE_SUBMISSION_ATTEMPT,

    /**
     * A client order id the caller recorded submitting has no
     * corresponding entry in {@link engine.oms.OrderStore} at all.
     * Structurally should be impossible given {@code OrderPipeline
     * .submitIntent}'s own contract (it always registers the order in
     * {@code OrderStore} before returning it) — included as a cheap
     * sanity check, the same "defense-in-depth against a caller that
     * bypasses OrderStore" spirit {@code PaperBroker}'s own {@code
     * seenClientOrderIds} field already documents for itself.
     */
    MISSING_FROM_ORDER_STORE,

    /**
     * {@link engine.execution.PaperBroker#pendingOrders()} contains a
     * client order id the caller never recorded submitting — i.e.
     * something reached {@code PaperBroker} through a path other than the
     * tracked submission flow (a hand-built {@code Order} submitted
     * directly to {@code PaperBroker}, bypassing {@code OrderPipeline}/
     * {@code RiskGateway} entirely, would show up this way). The
     * symmetric counterpart to {@link #MISSING_FROM_ORDER_STORE}.
     */
    UNTRACKED_IN_BROKER
}
