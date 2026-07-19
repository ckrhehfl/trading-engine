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
