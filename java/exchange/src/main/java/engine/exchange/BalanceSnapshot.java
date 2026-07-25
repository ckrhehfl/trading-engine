package engine.exchange;

import java.math.BigDecimal;

/** Account balance as reported by {@code GET /openApi/swap/v3/user/balance}. */
public record BalanceSnapshot(
        BigDecimal balance,
        BigDecimal equity,
        BigDecimal availableMargin,
        BigDecimal usedMargin,
        BigDecimal unrealizedProfit) {}
