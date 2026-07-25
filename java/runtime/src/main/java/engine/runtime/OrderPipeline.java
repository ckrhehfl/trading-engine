package engine.runtime;

import engine.oms.Order;
import engine.oms.OrderStore;
import engine.risk.AccountState;
import engine.risk.RiskGateway;
import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.RiskDecision;
import java.math.BigDecimal;
import java.util.Objects;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The only real code path from {@link OrderIntent} to {@link Order}: every
 * intent is evaluated by {@link RiskGateway#evaluate} before an
 * {@link Order} can exist, and the {@link RiskDecision} that decision
 * produces is what gets handed to {@link OrderStore#createOrder} —
 * never a hand-built or reconstructed one.
 *
 * <p>This closes the gap named repeatedly during Implementation Priority
 * #7's review: {@code Order.fromApprovedDecision} only checks that the
 * {@link RiskDecision} it's given says APPROVED/MODIFIED, not that it
 * actually came from a real {@code evaluate()} call. That check can't be
 * enforced inside {@code Order} itself (it has no way to know where a
 * {@code RiskDecision} came from), so the safety property instead lives
 * here, in this class's shape: {@link #submitIntent} is the only method
 * in this module that ever calls {@code Order.fromApprovedDecision}
 * (via {@link OrderStore#createOrder}), and it does so with the exact
 * {@link RiskDecision} instance {@link RiskGateway#evaluate} just
 * returned two lines above — never a copy, never a caller-supplied one.
 * Do not add another path, overload, or shortcut that lets a caller pass
 * in its own {@link RiskDecision}.
 */
public final class OrderPipeline {

    private static final Logger log = LoggerFactory.getLogger(OrderPipeline.class);

    private final RiskGateway riskGateway;
    private final OrderStore orderStore;

    public OrderPipeline(RiskGateway riskGateway, OrderStore orderStore) {
        this.riskGateway = Objects.requireNonNull(riskGateway, "riskGateway is required");
        this.orderStore = Objects.requireNonNull(orderStore, "orderStore is required");
    }

    /**
     * Evaluates {@code intent} against the Risk Gateway and, if approved
     * (possibly modified), registers the resulting {@link Order} in the
     * {@link OrderStore}. Returns empty, with no {@link Order} created, if
     * the Risk Gateway rejects the intent.
     */
    public Optional<Order> submitIntent(OrderIntent intent, BigDecimal referencePrice, AccountState account) {
        Objects.requireNonNull(intent, "intent is required");
        Objects.requireNonNull(referencePrice, "referencePrice is required");
        Objects.requireNonNull(account, "account is required");

        RiskDecision decision = riskGateway.evaluate(intent, referencePrice, account);
        if (decision.decision() == Decision.REJECTED) {
            log.info("intent {} not submitted: rejected by risk gateway: {}", intent.intentId(), decision.reason());
            return Optional.empty();
        }
        return Optional.of(orderStore.createOrder(intent, decision));
    }
}
