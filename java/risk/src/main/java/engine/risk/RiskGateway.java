package engine.risk;

import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.RiskDecision;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Applies one {@link RiskLimits} tier to a proposed {@link OrderIntent}
 * and produces the {@link RiskDecision} that is the only way
 * {@code engine.oms.Order} can be constructed — see CLAUDE.md
 * "Risk Parameters" and Non-negotiable Rules.
 *
 * <p><b>Simplification, revisit once real drawdown tracking exists</b>
 * (not before Implementation Priority #6+): monthly, hard-stop, and
 * emergency-stop are all checked against {@code AccountState
 * .monthlyPnlPercent()} as escalating thresholds on the same measure,
 * rather than a separate peak-to-trough drawdown figure. Functionally
 * this only affects the reason text Risk Gateway reports; the outcome
 * (reject the order) is the same either way, and a true kill switch —
 * halting an already-running process, not just blocking new orders — is
 * explicitly out of scope for this component.
 */
public final class RiskGateway {

    private static final Logger log = LoggerFactory.getLogger(RiskGateway.class);
    private static final int QUANTITY_SCALE = 8;

    private final RiskLimits limits;

    public RiskGateway(RiskLimits limits) {
        this.limits = Objects.requireNonNull(limits, "limits is required");
    }

    public RiskDecision evaluate(OrderIntent intent, BigDecimal referencePrice, AccountState account) {
        Objects.requireNonNull(intent, "intent is required");
        Objects.requireNonNull(referencePrice, "referencePrice is required");
        Objects.requireNonNull(account, "account is required");

        String lossBreachReason = checkLossLimits(account);
        if (lossBreachReason != null) {
            return reject(intent, lossBreachReason);
        }

        BigDecimal price = intent.limitPrice() != null ? intent.limitPrice() : referencePrice;
        if (price.signum() <= 0) {
            // OrderIntent.limitPrice() is already positive-validated by the
            // schema when present; a non-positive price can only reach here
            // via referencePrice (e.g. a bad market-data tick during an
            // outage). A non-positive price makes notional <= 0, which would
            // trivially clear any positive maxNotional and approve the full
            // requested quantity regardless of size — reject outright instead.
            return reject(intent, "cannot evaluate order: effective price " + price + " is not positive");
        }
        BigDecimal notional = intent.quantity().multiply(price);
        BigDecimal maxNotional = limits.maxOrderNotionalPercent().multiply(account.equity());

        if (notional.compareTo(maxNotional) <= 0) {
            return approve(intent, intent.quantity());
        }

        BigDecimal clampedQuantity = maxNotional.divide(price, QUANTITY_SCALE, RoundingMode.DOWN);
        if (clampedQuantity.signum() <= 0) {
            return reject(
                    intent,
                    "cannot approve any quantity: max order notional " + maxNotional
                            + " at price " + price + " rounds to zero");
        }
        return modify(
                intent,
                clampedQuantity,
                "requested notional " + notional + " exceeds max order notional " + maxNotional
                        + " (" + limits.maxOrderNotionalPercent() + " of equity); quantity clamped from "
                        + intent.quantity() + " to " + clampedQuantity);
    }

    /** Returns a breach reason, most severe first, or null if all clear. */
    private String checkLossLimits(AccountState account) {
        if (limits.emergencyStopLossPercent() != null
                && account.monthlyPnlPercent().compareTo(limits.emergencyStopLossPercent()) <= 0) {
            return "emergency stop breached: monthly PnL " + account.monthlyPnlPercent()
                    + " <= " + limits.emergencyStopLossPercent();
        }
        if (account.monthlyPnlPercent().compareTo(limits.hardStopLossPercent()) <= 0) {
            return "hard stop breached: monthly PnL " + account.monthlyPnlPercent()
                    + " <= " + limits.hardStopLossPercent();
        }
        if (account.monthlyPnlPercent().compareTo(limits.monthlyLossLimitPercent()) <= 0) {
            return "monthly loss limit breached: monthly PnL " + account.monthlyPnlPercent()
                    + " <= " + limits.monthlyLossLimitPercent();
        }
        if (account.weeklyPnlPercent().compareTo(limits.weeklyLossLimitPercent()) <= 0) {
            return "weekly loss limit breached: weekly PnL " + account.weeklyPnlPercent()
                    + " <= " + limits.weeklyLossLimitPercent();
        }
        if (account.dailyPnlPercent().compareTo(limits.dailyLossLimitPercent()) <= 0) {
            return "daily loss limit breached: daily PnL " + account.dailyPnlPercent()
                    + " <= " + limits.dailyLossLimitPercent();
        }
        return null;
    }

    private RiskDecision approve(OrderIntent intent, BigDecimal quantity) {
        log.info("order {} approved: quantity={}", intent.intentId(), quantity);
        return new RiskDecision(
                intent.intentId(), Decision.APPROVED, null, quantity, limits.baseLeverage(), Instant.now());
    }

    private RiskDecision modify(OrderIntent intent, BigDecimal quantity, String reason) {
        log.info("order {} modified: {}", intent.intentId(), reason);
        return new RiskDecision(
                intent.intentId(), Decision.MODIFIED, reason, quantity, limits.baseLeverage(), Instant.now());
    }

    private RiskDecision reject(OrderIntent intent, String reason) {
        log.info("order {} rejected: {}", intent.intentId(), reason);
        return new RiskDecision(intent.intentId(), Decision.REJECTED, reason, null, null, Instant.now());
    }
}
