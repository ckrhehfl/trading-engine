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

/**
 * Every test here is anchored to the 2026-09-08 incident rather than to
 * invented values -- see {@code .planning/quantity-precision-discuss.md}
 * and GitHub issue #151.
 */
class SteppedNotionalCalculatorTest {

    /** BingX's real published step for BTC-USDT: {@code size = 0.0001}. */
    private static final BigDecimal BTC_STEP = new BigDecimal("0.0001");

    /**
     * The exact quantity {@code daily-tsmom-ensemble} emitted on
     * 2026-09-08, copied from the real signal file on the VPS. 29
     * significant digits, from a {@code Decimal} division under Python's
     * default 28-digit context.
     */
    private static final BigDecimal INCIDENT_QUANTITY =
            new BigDecimal("0.02318401746487214694970992456");

    private OrderIntent intent(BigDecimal quantity) {
        return new OrderIntent(
                UUID.randomUUID(),
                "BTC-USDT",
                Side.SHORT,
                OrderType.GUARDED_MARKET,
                quantity,
                null,
                "1d",
                Instant.now());
    }

    private SteppedNotionalCalculator btc() {
        return new SteppedNotionalCalculator(BTC_STEP);
    }

    // ---------------------------------------------------------- the incident

    @Test
    void rejectsTheRealIncidentQuantity() {
        Optional<String> reason = btc().quantityRejectionReason(intent(INCIDENT_QUANTITY));

        assertTrue(reason.isPresent(), "a 29-digit quantity must not reach a venue with a 0.0001 step");
        assertTrue(reason.get().contains("0.0001"), "the reason must name the step: " + reason.get());
    }

    @Test
    void acceptsThatQuantityOnceItIsTruncatedToTheStep() {
        // 0.0231 is exactly what BingX filled -- the incident quantity
        // truncated to 4dp. The fix is only useful if the corrected value
        // passes.
        assertFalse(btc().quantityRejectionReason(intent(new BigDecimal("0.0231"))).isPresent());
    }

    // ------------------------------------------------------- shape acceptance

    @Test
    void acceptsAnyExactMultipleOfTheStep() {
        SteppedNotionalCalculator calculator = btc();
        for (String q : new String[] {"0.0001", "0.001", "0.5", "1", "12.3456"}) {
            assertFalse(
                    calculator.quantityRejectionReason(intent(new BigDecimal(q))).isPresent(),
                    q + " is a multiple of 0.0001 and must be accepted");
        }
    }

    @Test
    void acceptsTrailingZeroFormsOfTheSameNumber() {
        // BigDecimal("0.02310") and BigDecimal("0.0231") differ in scale but
        // not in value. Rejecting on scale would refuse a perfectly valid
        // quantity -- the same trap FixedMultiplierNotionalCalculator has a
        // test for.
        SteppedNotionalCalculator calculator = btc();
        assertFalse(calculator.quantityRejectionReason(intent(new BigDecimal("0.02310"))).isPresent());
        assertFalse(calculator.quantityRejectionReason(intent(new BigDecimal("1.0000000"))).isPresent());
    }

    @Test
    void rejectsAQuantityFinerThanTheStepEvenWhenItLooksTidy() {
        // 0.00015 is not a multiple of 0.0001. It looks harmless and is
        // exactly the kind of value a naive "round to 5 decimals" would
        // produce.
        assertTrue(btc().quantityRejectionReason(intent(new BigDecimal("0.00015"))).isPresent());
    }

    @Test
    void theSchemaIsWhatRejectsANonPositiveQuantity() {
        // Written after a first draft of the calculator carried its own
        // positivity check and this test failed -- because OrderIntent's
        // compact constructor throws first, making that branch unreachable.
        //
        // The branch was removed rather than the test weakened: an
        // unreachable guard is one nobody can prove works, which is the
        // inert-guard shape this repository keeps hitting. This test pins
        // the assumption the calculator now relies on, so relaxing
        // OrderIntent breaks here loudly instead of silently opening a hole.
        assertThrows(IllegalArgumentException.class, () -> intent(BigDecimal.ZERO));
        assertThrows(IllegalArgumentException.class, () -> intent(new BigDecimal("-0.0231")));
    }

    // ------------------------------------------------------------ constructor

    @Test
    void constructorRejectsNonPositiveStep() {
        assertThrows(IllegalArgumentException.class, () -> new SteppedNotionalCalculator(BigDecimal.ZERO));
        assertThrows(
                IllegalArgumentException.class, () -> new SteppedNotionalCalculator(new BigDecimal("-0.0001")));
    }

    // --------------------------------------------------------------- notional

    @Test
    void notionalOfIsQuantityTimesPrice() {
        BigDecimal notional = btc().notionalOf(intent(new BigDecimal("0.0231")), new BigDecimal("79020.3"));
        assertEquals(0, new BigDecimal("1825.36893").compareTo(notional), "got " + notional);
    }

    // ------------------------------------------------- the clamp path's own bug

    @Test
    void maxQuantityForRoundsDownToTheStepNotToEightDecimals() {
        // The second defect found while reading this: SimpleNotionalCalculator
        // clamps to QUANTITY_SCALE = 8, which BingX cannot accept either. The
        // clamp path is taken by orders that were too large -- i.e. exactly
        // where being wrong matters most.
        BigDecimal clamped = btc().maxQuantityFor(new BigDecimal("1825.36893"), new BigDecimal("79020.3"));

        assertEquals(0, new BigDecimal("0.0231").compareTo(clamped), "got " + clamped);
        assertFalse(
                btc().quantityRejectionReason(intent(clamped)).isPresent(),
                "a clamped quantity must itself be submittable -- otherwise the clamp"
                        + " produces an order the very next check rejects");
    }

    @Test
    void maxQuantityForNeverRoundsUpPastTheBudget() {
        // Rounding up would approve more exposure than RiskGateway's own
        // limit allows, which is the one direction this must never fail in.
        BigDecimal price = new BigDecimal("79020.3");
        BigDecimal budget = new BigDecimal("1900");

        BigDecimal clamped = btc().maxQuantityFor(budget, price);

        assertTrue(
                clamped.multiply(price).compareTo(budget) <= 0,
                "clamped notional " + clamped.multiply(price) + " exceeds budget " + budget);
    }

    @Test
    void maxQuantityForReturnsZeroWhenTheBudgetCannotAffordOneStep() {
        // RiskGateway rejects on signum() <= 0, so returning zero here is
        // what turns "too small to express" into a rejection rather than a
        // dust order the venue would refuse.
        BigDecimal clamped = btc().maxQuantityFor(new BigDecimal("1"), new BigDecimal("79020.3"));

        assertEquals(0, BigDecimal.ZERO.compareTo(clamped), "got " + clamped);
    }
}
