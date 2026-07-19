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

    @Test
    void rejectedRequiresReason() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new RiskDecision(
                                UUID.randomUUID(), Decision.REJECTED, null, null, null, Instant.now()));
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
}
