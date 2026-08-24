package engine.risk;

import engine.schemas.OrderIntent;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;
import java.util.Objects;
import java.util.Optional;

/**
 * A {@link NotionalCalculator} for instruments where one quantity unit
 * represents one exchange-defined contract worth {@code multiplier}
 * times the priced unit, not one unit of the priced unit itself --
 * e.g. KOSPI200 index futures, where one contract = price (index
 * points) times KRW250,000, KRX's own official contract specification
 * (see CLAUDE.md's "RiskLimits" section, and {@code PaperTradingApp}'s
 * own {@code KIS_KOSPI200_INDEX_FUTURES_MULTIPLIER} for where this
 * class is actually instantiated for KIS).
 *
 * <p>Deliberately generic (no KOSPI/KIS-specific name or import) --
 * {@code engine.risk} must not need to know exchange-specific facts
 * (CLAUDE.md's Architecture section: "a new venue or asset class means
 * writing a new ExchangeAdapter implementation, not modifying OMS/Risk/
 * Execution"); the venue-specific multiplier value is the caller's own
 * knowledge, supplied at construction.
 *
 * <p>Two behaviors this class exists specifically to enforce, both from
 * CLAUDE.md's own already-approved contract-multiplier-conversion rules:
 * quantity must be a positive integer contract count (rejected
 * otherwise, not silently truncated or accepted); notional is always
 * rounded <b>up</b>, never down (rounding down could understate a
 * position's real exposure and let an over-limit order clear {@link
 * RiskLimits#maxOrderNotionalPercent()}'s check). The inverse operation,
 * {@link #maxQuantityFor}, rounds the opposite direction (down) for the
 * same reason applied to the opposite risk: never approve more quantity
 * than {@code maxNotional} actually allows.
 */
public final class FixedMultiplierNotionalCalculator implements NotionalCalculator {

    private final BigDecimal multiplier;

    public FixedMultiplierNotionalCalculator(BigDecimal multiplier) {
        this.multiplier = Objects.requireNonNull(multiplier, "multiplier is required");
        if (multiplier.signum() <= 0) {
            throw new IllegalArgumentException("multiplier must be positive, was " + multiplier);
        }
    }

    @Override
    public Optional<String> quantityRejectionReason(OrderIntent intent) {
        BigDecimal quantity = intent.quantity();
        BigInteger wholeContracts;
        try {
            wholeContracts = quantity.stripTrailingZeros().toBigIntegerExact();
        } catch (ArithmeticException e) {
            return Optional.of(
                    "quantity must be a whole contract count, got " + quantity.toPlainString());
        }
        if (wholeContracts.signum() <= 0) {
            return Optional.of("quantity must be a positive contract count, got " + quantity.toPlainString());
        }
        return Optional.empty();
    }

    @Override
    public BigDecimal notionalOf(OrderIntent intent, BigDecimal price) {
        return intent.quantity().multiply(price).multiply(multiplier).setScale(0, RoundingMode.UP);
    }

    @Override
    public BigDecimal maxQuantityFor(BigDecimal maxNotional, BigDecimal price) {
        BigDecimal contractValue = price.multiply(multiplier);
        return maxNotional.divide(contractValue, 0, RoundingMode.DOWN);
    }

    /** The exact multiplier this instance was constructed with -- lets a caller (e.g. a test) verify which value is actually wired in, not just the calculator's type. */
    public BigDecimal multiplier() {
        return multiplier;
    }
}
