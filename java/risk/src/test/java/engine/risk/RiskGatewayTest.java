package engine.risk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.SchemaObjectMapper;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class RiskGatewayTest {

    private final ObjectMapper mapper = SchemaObjectMapper.create();

    private OrderIntent limitIntent(BigDecimal quantity, BigDecimal price) {
        return new OrderIntent(
                UUID.randomUUID(), "BTC-USDT", Side.LONG, OrderType.LIMIT, quantity, price, "15m", Instant.now());
    }

    private OrderIntent guardedMarketIntent(BigDecimal quantity) {
        return new OrderIntent(
                UUID.randomUUID(),
                "BTC-USDT",
                Side.LONG,
                OrderType.GUARDED_MARKET,
                quantity,
                null,
                "15m",
                Instant.now());
    }

    private AccountState healthyAccount(BigDecimal equity) {
        return new AccountState(equity, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
    }

    private void assertRoundTrips(RiskDecision decision) throws Exception {
        String json = mapper.writeValueAsString(decision);
        assertEquals(decision, mapper.readValue(json, RiskDecision.class));
    }

    @Test
    void withinLimitsOrderIsApprovedAtRequestedQuantityAndBaseLeverage() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderIntent intent = limitIntent(new BigDecimal("0.03"), new BigDecimal("60000")); // notional 1800
        AccountState account = healthyAccount(new BigDecimal("100000")); // maxNotional = 2000

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.APPROVED, decision.decision());
        assertEquals(new BigDecimal("0.03"), decision.approvedQuantity());
        assertEquals(RiskLimits.canary().baseLeverage(), decision.approvedLeverage());
        assertRoundTrips(decision);
    }

    @Test
    void notionalOverLimitIsModifiedAndClamped() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderIntent intent = limitIntent(new BigDecimal("0.1"), new BigDecimal("60000")); // notional 6000
        AccountState account = healthyAccount(new BigDecimal("100000")); // maxNotional = 2000

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.MODIFIED, decision.decision());
        assertEquals(new BigDecimal("0.03333333"), decision.approvedQuantity());
        assertTrue(decision.approvedQuantity().multiply(new BigDecimal("60000")).compareTo(new BigDecimal("2000")) <= 0);
        assertNotNull(decision.reason());
        assertRoundTrips(decision);
    }

    @Test
    void dailyLossLimitBreachRejectsOrder() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderIntent intent = limitIntent(new BigDecimal("0.01"), new BigDecimal("60000"));
        AccountState account =
                new AccountState(new BigDecimal("100000"), new BigDecimal("-0.006"), BigDecimal.ZERO, BigDecimal.ZERO);

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.REJECTED, decision.decision());
        assertTrue(decision.reason().toLowerCase().contains("daily"));
        assertNull(decision.approvedQuantity());
        assertNull(decision.approvedLeverage());
        assertRoundTrips(decision);
    }

    @Test
    void weeklyLossLimitBreachRejectsOrder() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderIntent intent = limitIntent(new BigDecimal("0.01"), new BigDecimal("60000"));
        AccountState account =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, new BigDecimal("-0.02"), BigDecimal.ZERO);

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.REJECTED, decision.decision());
        assertTrue(decision.reason().toLowerCase().contains("weekly"));
        assertRoundTrips(decision);
    }

    @Test
    void monthlyLossLimitBreachRejectsOrder() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderIntent intent = limitIntent(new BigDecimal("0.01"), new BigDecimal("60000"));
        AccountState account =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, new BigDecimal("-0.035"));

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.REJECTED, decision.decision());
        assertTrue(decision.reason().toLowerCase().contains("monthly"));
        assertRoundTrips(decision);
    }

    @Test
    void hardStopBreachReportsHardStopNotMonthly() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderIntent intent = limitIntent(new BigDecimal("0.01"), new BigDecimal("60000"));
        // breaches both monthly (-0.03) and hard stop (-0.04) — most severe must win
        AccountState account =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, new BigDecimal("-0.045"));

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.REJECTED, decision.decision());
        assertTrue(decision.reason().toLowerCase().contains("hard stop"));
        assertRoundTrips(decision);
    }

    @Test
    void emergencyStopBreachReportsEmergencyStop() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.stable());
        OrderIntent intent = limitIntent(new BigDecimal("0.01"), new BigDecimal("60000"));
        // breaches monthly, hard stop, and emergency stop — emergency must win
        AccountState account =
                new AccountState(new BigDecimal("100000"), BigDecimal.ZERO, BigDecimal.ZERO, new BigDecimal("-0.11"));

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.REJECTED, decision.decision());
        assertTrue(decision.reason().toLowerCase().contains("emergency stop"));
        assertRoundTrips(decision);
    }

    @Test
    void lossLimitBreachTakesPriorityOverNotionalEvenWhenOrderWouldOtherwiseFit() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        // small order that easily fits notional
        OrderIntent intent = limitIntent(new BigDecimal("0.001"), new BigDecimal("60000"));
        AccountState account =
                new AccountState(new BigDecimal("100000"), new BigDecimal("-0.01"), BigDecimal.ZERO, BigDecimal.ZERO);

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.REJECTED, decision.decision());
    }

    @Test
    void guardedMarketOrderUsesReferencePriceForNotional() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderIntent intent = guardedMarketIntent(new BigDecimal("0.1")); // no limitPrice
        AccountState account = healthyAccount(new BigDecimal("100000")); // maxNotional = 2000

        // referencePrice 60000 -> notional 6000 -> over limit -> MODIFIED
        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.MODIFIED, decision.decision());
        assertRoundTrips(decision);
    }

    @Test
    void zeroOrNegativeReferencePriceIsRejectedNotApproved() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderIntent intent = guardedMarketIntent(new BigDecimal("1000000")); // huge quantity
        AccountState account = healthyAccount(new BigDecimal("100000"));

        // A non-positive price must never let notional = quantity * price
        // clear the check by being <= 0 — that would approve an
        // arbitrarily large quantity regardless of maxOrderNotionalPercent.
        for (BigDecimal badPrice : new BigDecimal[] {BigDecimal.ZERO, new BigDecimal("-60000")}) {
            RiskDecision decision = gateway.evaluate(intent, badPrice, account);
            assertEquals(Decision.REJECTED, decision.decision());
            assertNull(decision.approvedQuantity());
            assertRoundTrips(decision);
        }
    }

    @Test
    void limitOrderUsesOwnLimitPriceIgnoringDifferentReferencePrice() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        // limitPrice 60000 -> notional 0.03 * 60000 = 1800, within 2000 maxNotional
        OrderIntent intent = limitIntent(new BigDecimal("0.03"), new BigDecimal("60000"));
        AccountState account = healthyAccount(new BigDecimal("100000"));

        // a wildly different referencePrice must be ignored for a LIMIT order
        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("1000000"), account);

        assertEquals(Decision.APPROVED, decision.decision());
        assertEquals(new BigDecimal("0.03"), decision.approvedQuantity());
        assertRoundTrips(decision);
    }

    @Test
    void evaluateWithFixedMultiplierCalculatorAppliesMultiplierToNotional() throws Exception {
        // 1 contract at 350 would be notional=350 under plain qty*price
        // (well within any reasonable maxNotional) -- but at a real
        // KOSPI200-style 250000 multiplier the real notional is
        // 87,500,000, which must actually be checked against maxNotional,
        // not silently approved as if the multiplier didn't exist. This
        // is the exact bug this NotionalCalculator plumbing exists to fix.
        RiskGateway gateway =
                new RiskGateway(RiskLimits.canary(), new FixedMultiplierNotionalCalculator(new BigDecimal("250000")));
        OrderIntent intent = limitIntent(new BigDecimal("1"), new BigDecimal("350"));
        AccountState account = healthyAccount(new BigDecimal("100000")); // maxNotional = 2000

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("350"), account);

        assertEquals(Decision.REJECTED, decision.decision());
        assertRoundTrips(decision);
    }

    @Test
    void evaluateWithFixedMultiplierCalculatorApprovesWithinLimit() throws Exception {
        RiskGateway gateway =
                new RiskGateway(RiskLimits.canary(), new FixedMultiplierNotionalCalculator(new BigDecimal("250000")));
        // notional = 1 * 0.01 * 250000 = 2500... use a small enough price so
        // 1 contract fits within maxNotional (2000 at equity=100000).
        OrderIntent intent = limitIntent(BigDecimal.ONE, new BigDecimal("0.007"));
        AccountState account = healthyAccount(new BigDecimal("100000")); // maxNotional = 2000

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("0.007"), account);

        assertEquals(Decision.APPROVED, decision.decision());
        assertEquals(BigDecimal.ONE, decision.approvedQuantity());
        assertRoundTrips(decision);
    }

    @Test
    void evaluateWithFixedMultiplierCalculatorClampsToWholeContractsNotFractional() throws Exception {
        RiskGateway gateway =
                new RiskGateway(RiskLimits.canary(), new FixedMultiplierNotionalCalculator(new BigDecimal("250000")));
        // maxNotional = 0.02 * 16,200,000,000 = 324,000,000; contractValue
        // = 350 * 250000 = 87,500,000; 324,000,000 / 87,500,000 = 3.7028...
        // -- requesting 10 contracts must clamp DOWN to the whole contract
        // count 3, never a fractional one (a fractional KOSPI200 futures
        // contract cannot be submitted) and never UP to 4 (would exceed
        // maxNotional).
        OrderIntent intent = limitIntent(new BigDecimal("10"), new BigDecimal("350"));
        AccountState account = healthyAccount(new BigDecimal("16200000000"));

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("350"), account);

        assertEquals(Decision.MODIFIED, decision.decision());
        assertEquals(0, new BigDecimal("3").compareTo(decision.approvedQuantity()));
        assertEquals(0, decision.approvedQuantity().scale(), "must be a whole contract count, not fractional");
        assertTrue(
                decision.approvedQuantity()
                                .multiply(new BigDecimal("350"))
                                .multiply(new BigDecimal("250000"))
                                .compareTo(new BigDecimal("324000000"))
                        <= 0);
        assertRoundTrips(decision);
    }

    @Test
    void evaluateWithFixedMultiplierCalculatorRejectsFractionalRequestedQuantity() throws Exception {
        RiskGateway gateway =
                new RiskGateway(RiskLimits.canary(), new FixedMultiplierNotionalCalculator(new BigDecimal("250000")));
        OrderIntent intent = limitIntent(new BigDecimal("1.5"), new BigDecimal("0.001"));
        AccountState account = healthyAccount(new BigDecimal("100000"));

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("0.001"), account);

        assertEquals(Decision.REJECTED, decision.decision());
        assertTrue(decision.reason().contains("whole contract count"));
        assertNull(decision.approvedQuantity());
        assertRoundTrips(decision);
    }

    @Test
    void evaluateOneArgConstructorStillUsesSimpleNotionalCalculator() throws Exception {
        // Regression: the plain RiskLimits-only constructor every BTC-USDT
        // loop uses today must remain byte-for-byte the pre-NotionalCalculator
        // behavior -- proven, not just asserted by construction.
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        OrderIntent intent = limitIntent(new BigDecimal("0.03"), new BigDecimal("60000"));
        AccountState account = healthyAccount(new BigDecimal("100000"));

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.APPROVED, decision.decision());
        assertEquals(new BigDecimal("0.03"), decision.approvedQuantity());
    }

    @Test
    void degenerateClampRejectsInsteadOfZeroOrNegativeQuantity() throws Exception {
        RiskGateway gateway = new RiskGateway(RiskLimits.canary());
        // tiny equity relative to price: maxNotional = 0.02 * 0.001 = 0.00002,
        // price 60000 -> 0.00002 / 60000 rounds down to 0 at 8 decimal places
        OrderIntent intent = limitIntent(new BigDecimal("0.01"), new BigDecimal("60000"));
        AccountState account = healthyAccount(new BigDecimal("0.001"));

        RiskDecision decision = gateway.evaluate(intent, new BigDecimal("60000"), account);

        assertEquals(Decision.REJECTED, decision.decision());
        assertNull(decision.approvedQuantity());
        assertRoundTrips(decision);
    }
}
