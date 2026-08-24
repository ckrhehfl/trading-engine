package engine.risk;

import engine.schemas.OrderIntent;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Optional;

/**
 * The default {@link NotionalCalculator}: {@code notional = quantity *
 * price}, one quantity unit trades 1:1 with the priced unit (e.g.
 * BTC-USDT). Every quantity shape is valid (any positive value --
 * {@link OrderIntent}'s own schema already requires positive), so
 * {@link #quantityRejectionReason} never rejects.
 *
 * <p>Byte-for-byte the same arithmetic {@link RiskGateway} always used
 * before {@link NotionalCalculator} existed -- extracted here so
 * {@link RiskGateway}'s one-argument constructor (used by every
 * BTC-USDT loop) stays a provable zero-behavior-change delegation to
 * this class, not a rewrite.
 */
public final class SimpleNotionalCalculator implements NotionalCalculator {

    public static final SimpleNotionalCalculator INSTANCE = new SimpleNotionalCalculator();

    private static final int QUANTITY_SCALE = 8;

    private SimpleNotionalCalculator() {}

    @Override
    public Optional<String> quantityRejectionReason(OrderIntent intent) {
        return Optional.empty();
    }

    @Override
    public BigDecimal notionalOf(OrderIntent intent, BigDecimal price) {
        return intent.quantity().multiply(price);
    }

    @Override
    public BigDecimal maxQuantityFor(BigDecimal maxNotional, BigDecimal price) {
        return maxNotional.divide(price, QUANTITY_SCALE, RoundingMode.DOWN);
    }
}
