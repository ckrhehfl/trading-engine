package engine.runtime;

import java.util.UUID;

/**
 * One detected internal-bookkeeping inconsistency between {@link
 * engine.oms.OrderStore} and {@link engine.execution.PaperBroker} — see
 * {@link Reconciler} and {@link ReconciliationMismatchType}.
 */
public record ReconciliationMismatch(ReconciliationMismatchType type, UUID orderId, String detail) {}
