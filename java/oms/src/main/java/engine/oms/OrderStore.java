package engine.oms;

import engine.schemas.OrderIntent;
import engine.schemas.RiskDecision;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * In-memory, idempotent order store keyed by client order id
 * ({@code OrderIntent.intentId()}). Not durable — persistence is out of
 * scope for this skeleton.
 */
public final class OrderStore {

    private final ConcurrentHashMap<UUID, Order> orders = new ConcurrentHashMap<>();

    /**
     * Returns the existing Order for this client order id if one was
     * already created, otherwise creates exactly one — concurrent calls
     * with the same id race safely and never produce two Orders.
     *
     * <p>A retry that reuses a client order id must describe the same
     * order; if it doesn't (including a decision that is no longer
     * APPROVED/MODIFIED), that's a conflicting request, not an idempotent
     * replay, and is rejected rather than silently returning the original
     * order as if nothing were wrong.
     */
    public Order createOrder(OrderIntent intent, RiskDecision decision) {
        Order order =
                orders.computeIfAbsent(
                        intent.intentId(), id -> Order.fromApprovedDecision(intent, decision));
        if (!order.matches(intent, decision)) {
            throw new IllegalStateException(
                    "conflicting retry for client order id "
                            + intent.intentId()
                            + ": request does not match the already-created order");
        }
        return order;
    }

    public Optional<Order> findByClientOrderId(UUID clientOrderId) {
        return Optional.ofNullable(orders.get(clientOrderId));
    }
}
