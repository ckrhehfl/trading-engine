package engine.exchange;

import java.math.BigDecimal;

/** A single open position as reported by {@code GET /openApi/swap/v2/user/positions}. */
public record PositionSnapshot(
        String symbol,
        String positionSide,
        BigDecimal positionAmt,
        BigDecimal avgPrice,
        BigDecimal leverage,
        BigDecimal unrealizedProfit,
        BigDecimal liquidationPrice) {}
