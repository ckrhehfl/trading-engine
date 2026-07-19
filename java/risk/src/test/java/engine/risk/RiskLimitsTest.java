package engine.risk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class RiskLimitsTest {

    @Test
    void constructsWithValidValues() {
        RiskLimits limits =
                new RiskLimits(
                        new BigDecimal("1"),
                        new BigDecimal("2"),
                        new BigDecimal("0.02"),
                        new BigDecimal("-0.005"),
                        new BigDecimal("-0.015"),
                        new BigDecimal("-0.03"),
                        new BigDecimal("-0.04"),
                        null);

        assertEquals(new BigDecimal("1"), limits.baseLeverage());
    }

    @Test
    void maxLeverageBelowBaseLeverageIsRejected() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new RiskLimits(
                                new BigDecimal("2"),
                                new BigDecimal("1"),
                                new BigDecimal("0.02"),
                                new BigDecimal("-0.005"),
                                new BigDecimal("-0.015"),
                                new BigDecimal("-0.03"),
                                new BigDecimal("-0.04"),
                                null));
    }

    @Test
    void zeroOrNegativeBaseLeverageIsRejected() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new RiskLimits(
                                BigDecimal.ZERO,
                                new BigDecimal("2"),
                                new BigDecimal("0.02"),
                                new BigDecimal("-0.005"),
                                new BigDecimal("-0.015"),
                                new BigDecimal("-0.03"),
                                new BigDecimal("-0.04"),
                                null));
    }

    @Test
    void notionalPercentOutOfRangeIsRejected() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new RiskLimits(
                                new BigDecimal("1"),
                                new BigDecimal("2"),
                                BigDecimal.ZERO,
                                new BigDecimal("-0.005"),
                                new BigDecimal("-0.015"),
                                new BigDecimal("-0.03"),
                                new BigDecimal("-0.04"),
                                null));
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new RiskLimits(
                                new BigDecimal("1"),
                                new BigDecimal("2"),
                                new BigDecimal("1.5"),
                                new BigDecimal("-0.005"),
                                new BigDecimal("-0.015"),
                                new BigDecimal("-0.03"),
                                new BigDecimal("-0.04"),
                                null));
    }

    @Test
    void misorderedLossLimitsAreRejected() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new RiskLimits(
                                new BigDecimal("1"),
                                new BigDecimal("2"),
                                new BigDecimal("0.02"),
                                new BigDecimal("-0.02"), // daily more severe than weekly: wrong
                                new BigDecimal("-0.015"),
                                new BigDecimal("-0.03"),
                                new BigDecimal("-0.04"),
                                null));
    }

    @Test
    void emergencyStopLessSevereThanHardStopIsRejected() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new RiskLimits(
                                new BigDecimal("2"),
                                new BigDecimal("3"),
                                new BigDecimal("0.05"),
                                new BigDecimal("-0.01"),
                                new BigDecimal("-0.03"),
                                new BigDecimal("-0.06"),
                                new BigDecimal("-0.08"),
                                new BigDecimal("-0.07"))); // less severe than hard stop -0.08
    }

    @Test
    void canaryMatchesClaudeMdDocumentedValues() {
        RiskLimits canary = RiskLimits.canary();

        assertEquals(new BigDecimal("1"), canary.baseLeverage());
        assertEquals(new BigDecimal("2"), canary.maxLeverage());
        assertEquals(new BigDecimal("0.02"), canary.maxOrderNotionalPercent());
        assertEquals(new BigDecimal("-0.005"), canary.dailyLossLimitPercent());
        assertEquals(new BigDecimal("-0.015"), canary.weeklyLossLimitPercent());
        assertEquals(new BigDecimal("-0.03"), canary.monthlyLossLimitPercent());
        assertEquals(new BigDecimal("-0.04"), canary.hardStopLossPercent());
        assertNull(canary.emergencyStopLossPercent());
    }

    @Test
    void stableMatchesClaudeMdDocumentedValues() {
        RiskLimits stable = RiskLimits.stable();

        assertEquals(new BigDecimal("2"), stable.baseLeverage());
        assertEquals(new BigDecimal("3"), stable.maxLeverage());
        assertEquals(new BigDecimal("0.05"), stable.maxOrderNotionalPercent());
        assertEquals(new BigDecimal("-0.01"), stable.dailyLossLimitPercent());
        assertEquals(new BigDecimal("-0.03"), stable.weeklyLossLimitPercent());
        assertEquals(new BigDecimal("-0.06"), stable.monthlyLossLimitPercent());
        assertEquals(new BigDecimal("-0.08"), stable.hardStopLossPercent());
        assertEquals(new BigDecimal("-0.10"), stable.emergencyStopLossPercent());
    }
}
