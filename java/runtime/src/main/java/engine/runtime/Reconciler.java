package engine.runtime;

import engine.execution.PaperBroker;
import engine.oms.Order;
import engine.oms.OrderState;
import engine.oms.OrderStore;
import java.time.Instant;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * A minimal <b>internal</b> consistency check between {@link OrderStore}'s
 * own order bookkeeping and {@link PaperBroker}'s own simulated-fill
 * bookkeeping — catching bugs in this project's own two independent
 * in-process data structures, not real-exchange reconciliation (there is no
 * {@code BingXAdapter} anywhere in the paper-trading loop — see CLAUDE.md's
 * Priority #7/#10). This is Task E of the 5-task paper-trading bridge plan
 * (see {@code .planning/paper-trading-e-reconciliation.md} for the full
 * design writeup and judgment calls, and CLAUDE.md's Paper Trading Pass
 * Criteria for what "zero duplicate orders, zero position mismatches" can
 * concretely mean before a real exchange is in the loop at all).
 *
 * <p><b>Why these four checks, and not others.</b> {@link OrderStore} and
 * {@link PaperBroker} are read here strictly through their existing public
 * API ({@link OrderStore#findByClientOrderId}, {@link
 * PaperBroker#pendingOrders()}) — neither was modified to add new query
 * surface for this check (see {@code .planning/paper-trading-e-
 * reconciliation.md}'s "what's actually queryable" section). A significant,
 * easy-to-miss finding from reading both classes closely: {@code
 * OrderPipeline.submitIntent} hands {@link PaperBroker#submit} the exact
 * same {@link Order} object it just registered in {@link OrderStore} — they
 * are not two independent copies of order state, they share one mutable
 * object by reference. That makes a naive "does OrderStore's state string
 * match PaperBroker's state string" check trivially, uselessly true by
 * construction. The four checks below are instead built around the two
 * failure modes that really are independent given that shared-reference
 * design: (1) an order that exists in one structure's bookkeeping but never
 * reached the other at all ({@link ReconciliationMismatchType
 * #ORPHANED_IN_BROKER}, {@link ReconciliationMismatchType
 * #UNTRACKED_IN_BROKER}, {@link ReconciliationMismatchType
 * #MISSING_FROM_ORDER_STORE}), and (2) the same client order id being
 * submitted through the pipeline more than once ({@link
 * ReconciliationMismatchType#DUPLICATE_SUBMISSION_ATTEMPT}). A
 * quantity/price cross-check between {@code Order.filledQuantity()} and a
 * {@code Fill}'s own quantity/price was considered and rejected for the
 * same reason: {@link PaperBroker#submit}'s {@code tryFill} computes a
 * {@code Fill} using the exact same {@code BigDecimal} it passes to {@code
 * order.fill(...)}, so with today's fill-or-nothing (no partial fill)
 * logic, that comparison is also structurally guaranteed to match — it
 * would test nothing.
 *
 * <p><b>What "position mismatch" means here.</b> No {@code Position} class
 * exists anywhere in this codebase yet (see the governing plan). {@link
 * PaperBroker} itself has no independent net-position or quantity ledger to
 * compare an {@link OrderStore}-derived position against — so a real
 * position-level reconciliation is not buildable from what currently
 * exists, only an order-bookkeeping-level one is. This check operationalizes
 * CLAUDE.md's "zero position mismatches" Paper Trading Pass Criterion as
 * order-state consistency between the two structures that actually exist
 * today, which is what that criterion can concretely mean before a real
 * exchange (and a real {@code Position} concept) is in the loop.
 *
 * <p><b>Where the "known submitted order ids" come from.</b> Neither {@link
 * OrderStore} nor {@link PaperBroker} exposes a full enumeration of
 * everything it has ever tracked ({@code OrderStore.findByClientOrderId}
 * requires an id you already know; {@code PaperBroker.pendingOrders()} only
 * ever shows currently-open orders). {@link TradingLoop#submittedOrderIds()}
 * is the source of truth this check is built against — see that method's
 * own Javadoc.
 */
public final class Reconciler {

    private static final Logger log = LoggerFactory.getLogger(Reconciler.class);

    /**
     * Order states in which an order should still be "live" at the broker
     * — i.e. either sitting in {@link PaperBroker#pendingOrders()} or in
     * the process of getting there. Every state NOT in this set ({@link
     * OrderState#isTerminal()}'s own four: FILLED, CANCELLED, REJECTED,
     * EXPIRED) is expected to be absent from {@code pendingOrders()} by
     * {@link PaperBroker}'s own design (a fill or cancel removes the order
     * from that map) — so its absence there is never flagged.
     */
    private static final Set<OrderState> OPEN_STATES =
            EnumSet.of(
                    OrderState.NEW,
                    OrderState.SUBMITTED,
                    OrderState.ACKNOWLEDGED,
                    OrderState.PARTIALLY_FILLED,
                    OrderState.CANCEL_PENDING);

    private Reconciler() {}

    /**
     * Compares {@code orderStore} and {@code paperBroker}'s own bookkeeping
     * for every id in {@code submittedOrderIds} (see {@link
     * TradingLoop#submittedOrderIds()}), plus every id {@code paperBroker}
     * itself currently has pending, and returns every mismatch found. Never
     * throws for a detected mismatch — mismatches are data, reported in the
     * returned {@link ReconciliationReport}, and also logged at ERROR
     * (never silently) as they're found. Deciding what to do about a
     * mismatch (e.g. tripping a {@link KillSwitch}) is the caller's job,
     * deliberately kept out of this method — see {@code
     * .planning/paper-trading-e-reconciliation.md} for why.
     */
    public static ReconciliationReport check(
            List<UUID> submittedOrderIds, OrderStore orderStore, PaperBroker paperBroker) {
        Objects.requireNonNull(submittedOrderIds, "submittedOrderIds is required");
        Objects.requireNonNull(orderStore, "orderStore is required");
        Objects.requireNonNull(paperBroker, "paperBroker is required");

        List<ReconciliationMismatch> mismatches = new ArrayList<>();
        Map<UUID, Order> pending = paperBroker.pendingOrders();

        Map<UUID, Long> submissionCounts = new HashMap<>();
        for (UUID id : submittedOrderIds) {
            submissionCounts.merge(id, 1L, Long::sum);
        }

        for (Map.Entry<UUID, Long> entry : submissionCounts.entrySet()) {
            if (entry.getValue() > 1) {
                mismatches.add(
                        new ReconciliationMismatch(
                                ReconciliationMismatchType.DUPLICATE_SUBMISSION_ATTEMPT,
                                entry.getKey(),
                                "order id was submitted through OrderPipeline "
                                        + entry.getValue()
                                        + " separate times -- each order id must be submitted at most once"));
            }
        }

        for (UUID id : submissionCounts.keySet()) {
            Optional<Order> maybeOrder = orderStore.findByClientOrderId(id);
            if (maybeOrder.isEmpty()) {
                mismatches.add(
                        new ReconciliationMismatch(
                                ReconciliationMismatchType.MISSING_FROM_ORDER_STORE,
                                id,
                                "this order id was recorded as submitted, but OrderStore has no record of it at"
                                        + " all"));
                continue;
            }
            OrderState state = maybeOrder.get().state();
            if (OPEN_STATES.contains(state) && !pending.containsKey(id)) {
                mismatches.add(
                        new ReconciliationMismatch(
                                ReconciliationMismatchType.ORPHANED_IN_BROKER,
                                id,
                                "OrderStore has this order in state "
                                        + state
                                        + " (still open), but PaperBroker has no pending record of it -- it will"
                                        + " never receive a fill or cancel confirmation"));
            }
        }

        for (UUID id : pending.keySet()) {
            if (!submissionCounts.containsKey(id)) {
                mismatches.add(
                        new ReconciliationMismatch(
                                ReconciliationMismatchType.UNTRACKED_IN_BROKER,
                                id,
                                "PaperBroker has a pending order for this id that was never recorded as"
                                        + " submitted -- it may have reached PaperBroker through a path other"
                                        + " than the tracked submission flow"));
            }
        }

        for (ReconciliationMismatch mismatch : mismatches) {
            log.error(
                    "internal consistency mismatch detected: type={} orderId={} detail={}",
                    mismatch.type(),
                    mismatch.orderId(),
                    mismatch.detail());
        }
        if (mismatches.isEmpty()) {
            log.debug("reconciliation check: clean, {} known order id(s) checked", submissionCounts.size());
        }

        return new ReconciliationReport(List.copyOf(mismatches), Instant.now());
    }
}
