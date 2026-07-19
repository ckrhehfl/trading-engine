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
     */
    public Order createOrder(OrderIntent intent, RiskDecision decision) {
        return orders.computeIfAbsent(
                intent.intentId(), id -> Order.fromApprovedDecision(intent, decision));
    }

    public Optional<Order> findByClientOrderId(UUID clientOrderId) {
        return Optional.ofNullable(orders.get(clientOrderId));
    }
}
