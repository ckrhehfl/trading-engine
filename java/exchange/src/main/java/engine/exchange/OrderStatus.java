package engine.exchange;

import java.math.BigDecimal;

/**
 * A point-in-time read of an order's exchange-side status. Pure DTO,
 * deliberately never used to mutate an {@link engine.oms.Order} — see
 * {@link ExchangeAdapter#queryOrder}.
 *
 * <p>{@code status} is BingX's raw status string, intentionally not
 * normalized: CLAUDE.md's BingX research notes casing differs between REST
 * ({@code CANCELLED}) and WebSocket ({@code CANCELED}) samples in BingX's
 * own docs, so normalizing here would be guessing at a convention nobody
 * has verified yet.
 */
public record OrderStatus(String exchangeOrderId, String status, BigDecimal filledQuantity, BigDecimal avgPrice) {}
