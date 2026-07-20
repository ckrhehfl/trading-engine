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
 *
 * <p>The {@code ABSOLUTE_*} constants are hard ceilings/floors equal to
 * CLAUDE.md's most permissive currently-documented tier (stable) — not
 * tighter values. They exist so the public constructor can't be used to
 * construct a tier exceeding what's currently approved (e.g. by future
 * config-loading code), regardless of caller; raising any of them
 * requires editing this file, which is itself a CODEOWNERS-gated,
 * human-reviewed change. Confirmed with @ckrhehfl: CLAUDE.md's separate
 * "Live Entry Criteria: leverage hard max 2x" describes the gate for the
 * *initial* paper-to-live transition (which happens under the canary
 * tier, itself capped at 2x) — not a ceiling stable() must also respect.
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

    public static final BigDecimal ABSOLUTE_MAX_LEVERAGE = new BigDecimal("3");
    public static final BigDecimal ABSOLUTE_MAX_NOTIONAL_PERCENT = new BigDecimal("0.05");
    public static final BigDecimal ABSOLUTE_MIN_DAILY_LOSS_LIMIT_PERCENT = new BigDecimal("-0.01");
    public static final BigDecimal ABSOLUTE_MIN_WEEKLY_LOSS_LIMIT_PERCENT = new BigDecimal("-0.03");
    public static final BigDecimal ABSOLUTE_MIN_MONTHLY_LOSS_LIMIT_PERCENT = new BigDecimal("-0.06");
    public static final BigDecimal ABSOLUTE_MIN_HARD_STOP_LOSS_PERCENT = new BigDecimal("-0.08");
    public static final BigDecimal ABSOLUTE_MIN_EMERGENCY_STOP_LOSS_PERCENT = new BigDecimal("-0.10");

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
        if (maxLeverage.compareTo(ABSOLUTE_MAX_LEVERAGE) > 0) {
            throw new IllegalArgumentException(
                    "maxLeverage must not exceed the policy ceiling of " + ABSOLUTE_MAX_LEVERAGE
                            + "x (CLAUDE.md's stable tier) without a CLAUDE.md + code change");
        }
        if (maxOrderNotionalPercent.signum() <= 0 || maxOrderNotionalPercent.compareTo(BigDecimal.ONE) > 0) {
            throw new IllegalArgumentException("maxOrderNotionalPercent must be in (0, 1]");
        }
        if (maxOrderNotionalPercent.compareTo(ABSOLUTE_MAX_NOTIONAL_PERCENT) > 0) {
            throw new IllegalArgumentException(
                    "maxOrderNotionalPercent must not exceed the policy ceiling of "
                            + ABSOLUTE_MAX_NOTIONAL_PERCENT + " (CLAUDE.md's stable tier)");
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
        if (dailyLossLimitPercent.compareTo(ABSOLUTE_MIN_DAILY_LOSS_LIMIT_PERCENT) < 0) {
            throw new IllegalArgumentException(
                    "dailyLossLimitPercent must not be more lenient than the policy floor of "
                            + ABSOLUTE_MIN_DAILY_LOSS_LIMIT_PERCENT + " (CLAUDE.md's stable tier)");
        }
        if (weeklyLossLimitPercent.compareTo(ABSOLUTE_MIN_WEEKLY_LOSS_LIMIT_PERCENT) < 0) {
            throw new IllegalArgumentException(
                    "weeklyLossLimitPercent must not be more lenient than the policy floor of "
                            + ABSOLUTE_MIN_WEEKLY_LOSS_LIMIT_PERCENT + " (CLAUDE.md's stable tier)");
        }
        if (monthlyLossLimitPercent.compareTo(ABSOLUTE_MIN_MONTHLY_LOSS_LIMIT_PERCENT) < 0) {
            throw new IllegalArgumentException(
                    "monthlyLossLimitPercent must not be more lenient than the policy floor of "
                            + ABSOLUTE_MIN_MONTHLY_LOSS_LIMIT_PERCENT + " (CLAUDE.md's stable tier)");
        }
        if (hardStopLossPercent.compareTo(ABSOLUTE_MIN_HARD_STOP_LOSS_PERCENT) < 0) {
            throw new IllegalArgumentException(
                    "hardStopLossPercent must not be more lenient than the policy floor of "
                            + ABSOLUTE_MIN_HARD_STOP_LOSS_PERCENT + " (CLAUDE.md's stable tier)");
        }
        if (emergencyStopLossPercent != null
                && emergencyStopLossPercent.compareTo(ABSOLUTE_MIN_EMERGENCY_STOP_LOSS_PERCENT) < 0) {
            throw new IllegalArgumentException(
                    "emergencyStopLossPercent must not be more lenient than the policy floor of "
                            + ABSOLUTE_MIN_EMERGENCY_STOP_LOSS_PERCENT + " (CLAUDE.md's stable tier)");
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
