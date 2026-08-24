package engine.risk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class FixedMultiplierNotionalCalculatorTest {

    private OrderIntent limitIntent(BigDecimal quantity, BigDecimal price) {
        return new OrderIntent(
                UUID.randomUUID(), "A01609", Side.LONG, OrderType.LIMIT, quantity, price, null, Instant.now());
    }

    @Test
    void constructorRejectsNonPositiveMultiplier() {
        assertThrows(
                IllegalArgumentException.class, () -> new FixedMultiplierNotionalCalculator(BigDecimal.ZERO));
        assertThrows(
                IllegalArgumentException.class,
                () -> new FixedMultiplierNotionalCalculator(new BigDecimal("-250000")));
    }

    @Test
    void notionalOfMultipliesQuantityPriceAndMultiplier() {
        FixedMultiplierNotionalCalculator calculator =
                new FixedMultiplierNotionalCalculator(new BigDecimal("250000"));
        OrderIntent intent = limitIntent(new BigDecimal("2"), new BigDecimal("350.25"));

        BigDecimal notional = calculator.notionalOf(intent, new BigDecimal("350.25"));

        // 2 * 350.25 * 250000 = 175,125,000 exactly -- no rounding needed
        assertEquals(0, new BigDecimal("175125000").compareTo(notional));
    }

    @Test
    void notionalOfRoundsUpNotDownWhenResultIsFractional() {
        FixedMultiplierNotionalCalculator calculator = new FixedMultiplierNotionalCalculator(new BigDecimal("100"));
        OrderIntent intent = limitIntent(BigDecimal.ONE, new BigDecimal("10.003"));

        // 1 * 10.003 * 100 = 1000.3 -- rounding UP must give 1001, not 1000
        // (rounding down would understate real exposure).
        BigDecimal notional = calculator.notionalOf(intent, new BigDecimal("10.003"));

        assertEquals(0, new BigDecimal("1001").compareTo(notional));
    }

    @Test
    void maxQuantityForRoundsDownToAWholeContractCount() {
        FixedMultiplierNotionalCalculator calculator =
                new FixedMultiplierNotionalCalculator(new BigDecimal("250000"));
        // contractValue = 350 * 250000 = 87,500,000
        // 200,000,000 / 87,500,000 = 2.2857... -> must floor to 2, never 3
        // (approving 3 would exceed maxNotional).
        BigDecimal maxQuantity = calculator.maxQuantityFor(new BigDecimal("200000000"), new BigDecimal("350"));

        assertEquals(0, new BigDecimal("2").compareTo(maxQuantity));
        assertTrue(maxQuantity.multiply(new BigDecimal("350")).multiply(new BigDecimal("250000"))
                .compareTo(new BigDecimal("200000000")) <= 0);
    }

    @Test
    void quantityRejectionReasonRejectsFractionalQuantity() {
        FixedMultiplierNotionalCalculator calculator = new FixedMultiplierNotionalCalculator(new BigDecimal("250000"));
        OrderIntent intent = limitIntent(new BigDecimal("1.5"), new BigDecimal("350"));

        Optional<String> reason = calculator.quantityRejectionReason(intent);

        assertTrue(reason.isPresent());
        assertTrue(reason.get().contains("whole contract count"));
    }

    @Test
    void quantityRejectionReasonAcceptsWholeQuantityIncludingTrailingZeroForm() {
        FixedMultiplierNotionalCalculator calculator = new FixedMultiplierNotionalCalculator(new BigDecimal("250000"));

        assertFalse(calculator.quantityRejectionReason(limitIntent(new BigDecimal("3"), new BigDecimal("350")))
                .isPresent());
        assertFalse(calculator.quantityRejectionReason(limitIntent(new BigDecimal("3.00"), new BigDecimal("350")))
                .isPresent());
    }
}
