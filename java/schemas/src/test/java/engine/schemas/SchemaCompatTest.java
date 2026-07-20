package engine.schemas;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/**
 * Validates engine.schemas against the shared golden fixtures in
 * schemas/fixtures/ — the Python side validates the same files in
 * python/tests/test_schema_compat.py. If both languages reproduce the
 * same fixture, they agree with each other by transitivity, without
 * needing a cross-process test harness.
 */
class SchemaCompatTest {

    private final ObjectMapper mapper = SchemaObjectMapper.create();
    private final Path fixturesDir = Path.of(System.getProperty("schemaFixturesDir"));

    private String readFixture(String name) throws IOException {
        return Files.readString(fixturesDir.resolve(name));
    }

    /** Compare parsed JSON structure, not raw text — see Python's twin test. */
    private void assertSemanticallyRoundTrips(String raw, String reserialized) throws IOException {
        JsonNode rawTree = mapper.readTree(raw);
        JsonNode reserializedTree = mapper.readTree(reserialized);
        assertEquals(rawTree, reserializedTree);
    }

    @Test
    void orderIntentLimitFixture() throws IOException {
        String raw = readFixture("order_intent_limit.json");

        OrderIntent order = mapper.readValue(raw, OrderIntent.class);

        assertEquals(UUID.fromString("a1a1a1a1-1111-4111-8111-111111111111"), order.intentId());
        assertEquals("BTC-USDT", order.symbol());
        assertEquals(Side.LONG, order.side());
        assertEquals(OrderType.LIMIT, order.orderType());
        assertEquals(new BigDecimal("0.5"), order.quantity());
        assertEquals(new BigDecimal("65000.12345678"), order.limitPrice());
        assertEquals("15m", order.signalTimeframe());
        assertSemanticallyRoundTrips(raw, mapper.writeValueAsString(order));
    }

    @Test
    void orderIntentGuardedMarketFixture() throws IOException {
        String raw = readFixture("order_intent_guarded_market.json");

        OrderIntent order = mapper.readValue(raw, OrderIntent.class);

        assertEquals(Side.SHORT, order.side());
        assertEquals(OrderType.GUARDED_MARKET, order.orderType());
        assertEquals(new BigDecimal("0.25"), order.quantity());
        assertNull(order.limitPrice());
        assertNull(order.signalTimeframe());
        assertSemanticallyRoundTrips(raw, mapper.writeValueAsString(order));
    }

    @Test
    void riskDecisionApprovedFixture() throws IOException {
        String raw = readFixture("risk_decision_approved.json");

        RiskDecision decision = mapper.readValue(raw, RiskDecision.class);

        assertEquals(Decision.APPROVED, decision.decision());
        assertNull(decision.reason());
        assertEquals(new BigDecimal("0.5"), decision.approvedQuantity());
        assertEquals(new BigDecimal("2"), decision.approvedLeverage());
        assertSemanticallyRoundTrips(raw, mapper.writeValueAsString(decision));
    }

    @Test
    void riskDecisionRejectedFixture() throws IOException {
        String raw = readFixture("risk_decision_rejected.json");

        RiskDecision decision = mapper.readValue(raw, RiskDecision.class);

        assertEquals(Decision.REJECTED, decision.decision());
        assertNotNull(decision.reason());
        assertNull(decision.approvedQuantity());
        assertNull(decision.approvedLeverage());
        assertSemanticallyRoundTrips(raw, mapper.writeValueAsString(decision));
    }

    @Test
    void riskDecisionModifiedFixture() throws IOException {
        String raw = readFixture("risk_decision_modified.json");

        RiskDecision decision = mapper.readValue(raw, RiskDecision.class);

        assertEquals(Decision.MODIFIED, decision.decision());
        assertNotNull(decision.reason());
        assertEquals(new BigDecimal("0.1"), decision.approvedQuantity());
        assertEquals(new BigDecimal("1"), decision.approvedLeverage());
        assertSemanticallyRoundTrips(raw, mapper.writeValueAsString(decision));
    }
}
