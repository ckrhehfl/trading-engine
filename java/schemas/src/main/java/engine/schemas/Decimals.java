package engine.schemas;

import java.math.BigDecimal;

/** Shared positive-value check for quantity/price/leverage fields across schemas. */
final class Decimals {

    private Decimals() {}

    static void requirePositive(BigDecimal value, String fieldName) {
        if (value != null && value.signum() <= 0) {
            throw new IllegalArgumentException(fieldName + " must be positive");
        }
    }
}
