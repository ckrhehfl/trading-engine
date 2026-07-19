package engine.risk;

import java.math.BigDecimal;
import java.util.Objects;

/**
 * One risk tier's configuration — see CLAUDE.md "Risk Parameters".
 * Changing these values needs explicit human approval; this class only
 * hardcodes the two currently-documented tiers ({@link #canary()},
 * {@link #stable()}), it does not itself decide what the numbers are.
 *
 * <p>Loss-limit percents keep CLAUDE.md's own negative sign convention
 * (e.g. {@code -0.005} for "-0.5%"); a breach is {@code accountPnlPercent
 * <= limitPercent}. {@code emergencyStopLossPercent} is nullable — the
 * canary tier has no emergency stop.
 */
public record RiskLimits(
        BigDecimal baseLeverage,
        BigDecimal maxLeverage,
        BigDecimal maxOrderNotionalPercent,
        BigDecimal dailyLossLimitPercent,
        BigDecimal weeklyLossLimitPercent,
        BigDecimal monthlyLossLimitPercent,
        BigDecimal hardStopLossPercent,
        BigDecimal emergencyStopLossPercent) {

    public RiskLimits {
        Objects.requireNonNull(baseLeverage, "baseLeverage is required");
        Objects.requireNonNull(maxLeverage, "maxLeverage is required");
        Objects.requireNonNull(maxOrderNotionalPercent, "maxOrderNotionalPercent is required");
        Objects.requireNonNull(dailyLossLimitPercent, "dailyLossLimitPercent is required");
        Objects.requireNonNull(weeklyLossLimitPercent, "weeklyLossLimitPercent is required");
        Objects.requireNonNull(monthlyLossLimitPercent, "monthlyLossLimitPercent is required");
        Objects.requireNonNull(hardStopLossPercent, "hardStopLossPercent is required");

        if (baseLeverage.signum() <= 0) {
            throw new IllegalArgumentException("baseLeverage must be positive");
        }
        if (maxLeverage.compareTo(baseLeverage) < 0) {
            throw new IllegalArgumentException("maxLeverage must be >= baseLeverage");
        }
        if (maxOrderNotionalPercent.signum() <= 0 || maxOrderNotionalPercent.compareTo(BigDecimal.ONE) > 0) {
            throw new IllegalArgumentException("maxOrderNotionalPercent must be in (0, 1]");
        }
        if (dailyLossLimitPercent.compareTo(weeklyLossLimitPercent) < 0) {
            throw new IllegalArgumentException("dailyLossLimitPercent must be >= weeklyLossLimitPercent");
        }
        if (weeklyLossLimitPercent.compareTo(monthlyLossLimitPercent) < 0) {
            throw new IllegalArgumentException("weeklyLossLimitPercent must be >= monthlyLossLimitPercent");
        }
        if (monthlyLossLimitPercent.compareTo(hardStopLossPercent) < 0) {
            throw new IllegalArgumentException("monthlyLossLimitPercent must be >= hardStopLossPercent");
        }
        if (emergencyStopLossPercent != null
                && hardStopLossPercent.compareTo(emergencyStopLossPercent) < 0) {
            throw new IllegalArgumentException("hardStopLossPercent must be >= emergencyStopLossPercent");
        }
    }

    public static RiskLimits canary() {
        return new RiskLimits(
                new BigDecimal("1"),
                new BigDecimal("2"),
                new BigDecimal("0.02"),
                new BigDecimal("-0.005"),
                new BigDecimal("-0.015"),
                new BigDecimal("-0.03"),
                new BigDecimal("-0.04"),
                null);
    }

    public static RiskLimits stable() {
        return new RiskLimits(
                new BigDecimal("2"),
                new BigDecimal("3"),
                new BigDecimal("0.05"),
                new BigDecimal("-0.01"),
                new BigDecimal("-0.03"),
                new BigDecimal("-0.06"),
                new BigDecimal("-0.08"),
                new BigDecimal("-0.10"));
    }
}
