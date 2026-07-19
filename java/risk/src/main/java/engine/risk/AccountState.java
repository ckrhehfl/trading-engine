package engine.risk;

import java.math.BigDecimal;
import java.util.Objects;

/**
 * Snapshot of account state Risk Gateway needs to evaluate an order. The
 * caller supplies this — real position/PnL tracking doesn't exist yet
 * (no ExchangeAdapter until Implementation Priority #7); test fixtures
 * stand in until then.
 */
public record AccountState(
        BigDecimal equity,
        BigDecimal dailyPnlPercent,
        BigDecimal weeklyPnlPercent,
        BigDecimal monthlyPnlPercent) {

    public AccountState {
        Objects.requireNonNull(equity, "equity is required");
        Objects.requireNonNull(dailyPnlPercent, "dailyPnlPercent is required");
        Objects.requireNonNull(weeklyPnlPercent, "weeklyPnlPercent is required");
        Objects.requireNonNull(monthlyPnlPercent, "monthlyPnlPercent is required");
        if (equity.signum() <= 0) {
            throw new IllegalArgumentException("equity must be positive");
        }
    }
}
