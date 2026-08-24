package engine.risk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class SimpleNotionalCalculatorTest {

    private OrderIntent limitIntent(BigDecimal quantity, BigDecimal price) {
        return new OrderIntent(
                UUID.randomUUID(), "BTC-USDT", Side.LONG, OrderType.LIMIT, quantity, price, null, Instant.now());
    }

    @Test
    void quantityRejectionReasonNeverRejects() {
        assertFalse(SimpleNotionalCalculator.INSTANCE
                .quantityRejectionReason(limitIntent(new BigDecimal("0.00000001"), new BigDecimal("60000")))
                .isPresent());
    }

    @Test
    void notionalOfIsQuantityTimesPrice() {
        BigDecimal notional = SimpleNotionalCalculator.INSTANCE.notionalOf(
                limitIntent(new BigDecimal("0.03"), new BigDecimal("60000")), new BigDecimal("60000"));

        assertEquals(0, new BigDecimal("1800").compareTo(notional));
    }

    @Test
    void maxQuantityForDividesToEightDecimalPlacesRoundingDown() {
        BigDecimal maxQuantity = SimpleNotionalCalculator.INSTANCE.maxQuantityFor(
                new BigDecimal("2000"), new BigDecimal("60000"));

        assertEquals(new BigDecimal("0.03333333"), maxQuantity);
    }
}
