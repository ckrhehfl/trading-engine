package engine.schemas;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;

class RiskDecisionTest {

    private final ObjectMapper mapper = SchemaObjectMapper.create();

    private RiskDecision approvedDecision() {
        return new RiskDecision(
                UUID.randomUUID(),
                Decision.APPROVED,
                null,
                new BigDecimal("0.5"),
                new BigDecimal("2"),
                Instant.now());
    }

    @Test
    void roundTripPreservesAllFields() throws JsonProcessingException {
        RiskDecision original = approvedDecision();

        String json = mapper.writeValueAsString(original);
        RiskDecision parsed = mapper.readValue(json, RiskDecision.class);

        assertEquals(original, parsed);
    }

    @Test
    void decimalFieldsSerializeAsJsonStringsNotNumbers() throws JsonProcessingException {
        RiskDecision decision = approvedDecision();

        JsonNode raw = mapper.readTree(mapper.writeValueAsString(decision));

        assertTrue(raw.get("approved_quantity").isTextual());
        assertTrue(raw.get("approved_leverage").isTextual());
    }

    @Test
    void schemaVersionDefaultsToOnePointZero() {
        RiskDecision decision = approvedDecision();

        assertEquals("1.0", decision.schemaVersion());
    }

    @ParameterizedTest
    @EnumSource(
            value = Decision.class,
            names = {"REJECTED", "MODIFIED"})
    void rejectedOrModifiedRequiresReason(Decision decision) {
        assertThrows(
                IllegalArgumentException.class,
                () -> new RiskDecision(UUID.randomUUID(), decision, null, null, null, Instant.now()));
    }

    @Test
    void whitespaceOnlyReasonIsRejected() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new RiskDecision(
                                UUID.randomUUID(), Decision.REJECTED, "   ", null, null, Instant.now()));
    }

    @Test
    void rejectedWithReasonIsValid() {
        RiskDecision decision =
                new RiskDecision(
                        UUID.randomUUID(),
                        Decision.REJECTED,
                        "daily loss limit exceeded",
                        null,
                        null,
                        Instant.now());

        assertNull(decision.approvedQuantity());
    }

    @ParameterizedTest
    @EnumSource(
            value = Decision.class,
            names = {"APPROVED", "MODIFIED"})
    void approvedOrModifiedRequiresApprovedFields(Decision decision) {
        String reason = decision == Decision.MODIFIED ? "ok" : null;

        assertThrows(
                IllegalArgumentException.class,
                () -> new RiskDecision(UUID.randomUUID(), decision, reason, null, null, Instant.now()));
    }

    @Test
    void modifiedWithReasonAndApprovedFieldsIsValid() {
        RiskDecision decision =
                new RiskDecision(
                        UUID.randomUUID(),
                        Decision.MODIFIED,
                        "reduced size due to daily loss buffer",
                        new BigDecimal("0.3"),
                        new BigDecimal("1.5"),
                        Instant.now());

        assertEquals(Decision.MODIFIED, decision.decision());
        assertEquals(new BigDecimal("0.3"), decision.approvedQuantity());
    }

    @Test
    void rejectedWithApprovedFieldsSetIsRejected() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new RiskDecision(
                                UUID.randomUUID(),
                                Decision.REJECTED,
                                "daily loss limit exceeded",
                                new BigDecimal("0.5"),
                                new BigDecimal("2"),
                                Instant.now()));
    }

    @Test
    void knownValuesProduceExactJsonFixture() throws JsonProcessingException {
        RiskDecision decision =
                new RiskDecision(
                        UUID.fromString("11111111-1111-1111-1111-111111111111"),
                        Decision.APPROVED,
                        null,
                        new BigDecimal("0.5"),
                        new BigDecimal("2"),
                        Instant.parse("2026-07-19T04:00:00Z"));

        assertEquals(
                "{\"intent_id\":\"11111111-1111-1111-1111-111111111111\",\"decision\":\"APPROVED\","
                        + "\"reason\":null,\"approved_quantity\":\"0.5\",\"approved_leverage\":\"2\","
                        + "\"decided_at\":\"2026-07-19T04:00:00Z\",\"schema_version\":\"1.0\"}",
                mapper.writeValueAsString(decision));
    }
}
