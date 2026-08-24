package engine.risk;

import engine.schemas.OrderIntent;
import java.math.BigDecimal;
import java.util.Optional;

/**
 * Converts a proposed {@link OrderIntent}'s quantity and effective price
 * into a real notional (exposure) value {@link RiskGateway} checks
 * against {@link RiskLimits#maxOrderNotionalPercent()} -- and the
 * inverse, converting a notional budget back into an approvable quantity
 * when {@link RiskGateway} clamps rather than rejects.
 *
 * <p>Exists because {@code notional = quantity * price} is only correct
 * for an instrument where one quantity unit trades 1:1 with the priced
 * unit (e.g. BTC-USDT: one quantity unit = one BTC). It is not correct
 * for an instrument where one quantity unit represents one
 * exchange-defined contract worth a multiple of the priced unit (e.g.
 * KOSPI200 index futures: one contract = price in index points times
 * KRW250,000 -- see CLAUDE.md's "RiskLimits" section). {@link RiskGateway}
 * stays venue-agnostic itself (matching {@code ExchangeAdapter}'s own
 * "a new venue means a new adapter, never a new risk/execution
 * implementation" precedent) by depending only on this interface; a new
 * instrument shape means a new {@link NotionalCalculator}, never a
 * change to {@link RiskGateway} itself.
 */
public interface NotionalCalculator {

    /**
     * Non-empty iff {@code intent.quantity()} is invalid for this
     * instrument shape (e.g. fractional where only a whole contract
     * count is valid) -- checked before any notional math, so an invalid
     * quantity fails the order closed rather than silently producing a
     * meaningless notional.
     */
    Optional<String> quantityRejectionReason(OrderIntent intent);

    /**
     * {@code intent}'s real notional exposure at {@code price}. Must
     * round in the direction that never understates exposure (up, not
     * down) where rounding is needed at all -- understating exposure
     * could let an over-limit order clear {@link RiskLimits
     * #maxOrderNotionalPercent()}'s check when it should not.
     */
    BigDecimal notionalOf(OrderIntent intent, BigDecimal price);

    /**
     * The largest quantity, in this instrument's own valid quantity
     * shape (e.g. a whole contract count), whose notional at
     * {@code price} does not exceed {@code maxNotional} -- the inverse
     * of {@link #notionalOf}. Must round in the direction that never
     * approves more than {@code maxNotional} actually allows (down, not
     * up).
     */
    BigDecimal maxQuantityFor(BigDecimal maxNotional, BigDecimal price);
}
