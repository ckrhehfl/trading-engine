package engine.oms;

import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.EnumSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Order lifecycle state machine. The only way to obtain an instance is
 * {@link #fromApprovedDecision}, which requires an APPROVED/MODIFIED
 * {@link RiskDecision} — there is no path to an Order that didn't go
 * through risk assessment. See schemas/README.md for OrderIntent/
 * RiskDecision and CLAUDE.md for the state-transition table this
 * implements.
 */
public final class Order {

    private static final Logger log = LoggerFactory.getLogger(Order.class);

    private static final Set<OrderState> CAN_REQUEST_CANCEL =
            EnumSet.of(OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED);
    private static final Set<OrderState> CAN_FILL =
            EnumSet.of(OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED, OrderState.CANCEL_PENDING);

    private final UUID clientOrderId;
    private final String symbol;
    private final Side side;
    private final OrderType orderType;
    private final BigDecimal limitPrice;
    private final BigDecimal approvedQuantity;
    private final BigDecimal approvedLeverage;

    private final List<StateTransition> history = new ArrayList<>();

    private OrderState state;
    private String exchangeOrderId;
    private BigDecimal filledQuantity = BigDecimal.ZERO;

    private Order(
            UUID clientOrderId,
            String symbol,
            Side side,
            OrderType orderType,
            BigDecimal limitPrice,
            BigDecimal approvedQuantity,
            BigDecimal approvedLeverage) {
        this.clientOrderId = clientOrderId;
        this.symbol = symbol;
        this.side = side;
        this.orderType = orderType;
        this.limitPrice = limitPrice;
        this.approvedQuantity = approvedQuantity;
        this.approvedLeverage = approvedLeverage;
        transitionTo(OrderState.NEW);
    }

    public static Order fromApprovedDecision(OrderIntent intent, RiskDecision decision) {
        Objects.requireNonNull(intent, "intent is required");
        Objects.requireNonNull(decision, "decision is required");
        if (!intent.intentId().equals(decision.intentId())) {
            throw new IllegalArgumentException(
                    "intent.intentId() and decision.intentId() must match");
        }
        if (decision.decision() != Decision.APPROVED && decision.decision() != Decision.MODIFIED) {
            throw new IllegalArgumentException(
                    "cannot create an Order from a RiskDecision that is not APPROVED or MODIFIED: "
                            + decision.decision());
        }
        return new Order(
                intent.intentId(),
                intent.symbol(),
                intent.side(),
                intent.orderType(),
                intent.limitPrice(),
                decision.approvedQuantity(),
                decision.approvedLeverage());
    }

    public UUID clientOrderId() {
        return clientOrderId;
    }

    public String symbol() {
        return symbol;
    }

    public Side side() {
        return side;
    }

    public OrderType orderType() {
        return orderType;
    }

    public BigDecimal limitPrice() {
        return limitPrice;
    }

    public BigDecimal approvedQuantity() {
        return approvedQuantity;
    }

    public BigDecimal approvedLeverage() {
        return approvedLeverage;
    }

    /**
     * True if the given intent/decision pair describes exactly the order
     * this instance was created from — used by {@link OrderStore} to
     * reject a retry that reuses the same client order id but carries
     * different or conflicting order details (including a decision that
     * is no longer APPROVED/MODIFIED), rather than silently returning the
     * original order as if the retry were identical.
     */
    boolean matches(OrderIntent intent, RiskDecision decision) {
        return clientOrderId.equals(intent.intentId())
                && symbol.equals(intent.symbol())
                && side == intent.side()
                && orderType == intent.orderType()
                && Objects.equals(limitPrice, intent.limitPrice())
                && clientOrderId.equals(decision.intentId())
                && Objects.equals(approvedQuantity, decision.approvedQuantity())
                && Objects.equals(approvedLeverage, decision.approvedLeverage());
    }

    public synchronized OrderState state() {
        return state;
    }

    public synchronized String exchangeOrderId() {
        return exchangeOrderId;
    }

    public synchronized BigDecimal filledQuantity() {
        return filledQuantity;
    }

    public synchronized List<StateTransition> history() {
        return Collections.unmodifiableList(new ArrayList<>(history));
    }

    public synchronized void submit() {
        requireState(OrderState.NEW);
        transitionTo(OrderState.SUBMITTED);
    }

    public synchronized void acknowledge(String exchangeOrderId) {
        requireState(OrderState.SUBMITTED);
        this.exchangeOrderId = Objects.requireNonNull(exchangeOrderId, "exchangeOrderId is required");
        transitionTo(OrderState.ACKNOWLEDGED);
    }

    public synchronized void reject(String reason) {
        requireState(OrderState.SUBMITTED);
        log.info("order {} rejected: {}", clientOrderId, reason);
        transitionTo(OrderState.REJECTED);
    }

    public synchronized void expire() {
        requireState(OrderState.ACKNOWLEDGED);
        transitionTo(OrderState.EXPIRED);
    }

    public synchronized void requestCancel() {
        requireOneOf(CAN_REQUEST_CANCEL);
        transitionTo(OrderState.CANCEL_PENDING);
    }

    public synchronized void confirmCancel() {
        requireState(OrderState.CANCEL_PENDING);
        transitionTo(OrderState.CANCELLED);
    }

    public synchronized void fill(BigDecimal quantity) {
        requireOneOf(CAN_FILL);
        if (quantity == null || quantity.signum() <= 0) {
            throw new IllegalArgumentException("fill quantity must be positive");
        }
        BigDecimal newFilled = filledQuantity.add(quantity);
        if (newFilled.compareTo(approvedQuantity) > 0) {
            throw new IllegalArgumentException(
                    "fill would overfill order: filled=" + filledQuantity
                            + " + " + quantity + " > approvedQuantity=" + approvedQuantity);
        }
        filledQuantity = newFilled;
        if (newFilled.compareTo(approvedQuantity) == 0) {
            transitionTo(OrderState.FILLED);
        } else if (state != OrderState.CANCEL_PENDING) {
            transitionTo(OrderState.PARTIALLY_FILLED);
        }
        // else: partial fill while a cancel is in flight — stay in
        // CANCEL_PENDING so confirmCancel() still has a legal path;
        // moving to PARTIALLY_FILLED here would strand the order, since
        // the exchange's eventual cancel confirmation would have nowhere
        // valid to land.
    }

    private void requireState(OrderState required) {
        if (state != required) {
            throw new IllegalStateException(
                    "expected state " + required + " but order " + clientOrderId + " is " + state);
        }
    }

    private void requireOneOf(Set<OrderState> allowed) {
        if (!allowed.contains(state)) {
            throw new IllegalStateException(
                    "expected one of " + allowed + " but order " + clientOrderId + " is " + state);
        }
    }

    private void transitionTo(OrderState newState) {
        state = newState;
        history.add(new StateTransition(newState, Instant.now()));
        log.info("order {} -> {}", clientOrderId, newState);
    }
}
