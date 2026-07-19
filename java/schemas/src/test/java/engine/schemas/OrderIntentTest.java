package engine.schemas;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.exc.MismatchedInputException;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class OrderIntentTest {

    private final ObjectMapper mapper = SchemaObjectMapper.create();

    private OrderIntent limitOrder() {
        return new OrderIntent(
                UUID.randomUUID(),
                "BTC-USDT",
                Side.LONG,
                OrderType.LIMIT,
                new BigDecimal("0.5"),
                new BigDecimal("65000.12345678"),
                "15m",
                Instant.now());
    }

    @Test
    void roundTripPreservesAllFields() throws JsonProcessingException {
        OrderIntent original = limitOrder();

        String json = mapper.writeValueAsString(original);
        OrderIntent parsed = mapper.readValue(json, OrderIntent.class);

        assertEquals(original, parsed);
    }

    @Test
    void decimalFieldsSerializeAsJsonStringsNotNumbers() throws JsonProcessingException {
        OrderIntent order = limitOrder();

        JsonNode raw = mapper.readTree(mapper.writeValueAsString(order));

        assertTrue(raw.get("quantity").isTextual());
        assertTrue(raw.get("limit_price").isTextual());
        assertEquals("0.5", raw.get("quantity").asText());
    }

    @Test
    void jsonFieldNamesAreSnakeCase() throws JsonProcessingException {
        OrderIntent order = limitOrder();

        JsonNode raw = mapper.readTree(mapper.writeValueAsString(order));

        assertTrue(raw.has("intent_id"));
        assertTrue(raw.has("order_type"));
        assertTrue(raw.has("signal_timeframe"));
        assertTrue(raw.has("schema_version"));
    }

    @Test
    void schemaVersionDefaultsToOnePointZero() {
        OrderIntent order = limitOrder();

        assertEquals("1.0", order.schemaVersion());
    }

    @Test
    void limitOrderRequiresLimitPrice() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new OrderIntent(
                                UUID.randomUUID(),
                                "BTC-USDT",
                                Side.LONG,
                                OrderType.LIMIT,
                                new BigDecimal("0.5"),
                                null,
                                "15m",
                                Instant.now()));
    }

    @Test
    void guardedMarketOrderRejectsLimitPrice() {
        assertThrows(
                IllegalArgumentException.class,
                () ->
                        new OrderIntent(
                                UUID.randomUUID(),
                                "BTC-USDT",
                                Side.LONG,
                                OrderType.GUARDED_MARKET,
                                new BigDecimal("0.5"),
                                new BigDecimal("65000"),
                                "15m",
                                Instant.now()));
    }

    @Test
    void guardedMarketOrderWithoutLimitPriceIsValid() {
        OrderIntent order =
                new OrderIntent(
                        UUID.randomUUID(),
                        "BTC-USDT",
                        Side.LONG,
                        OrderType.GUARDED_MARKET,
                        new BigDecimal("0.5"),
                        null,
                        "15m",
                        Instant.now());

        assertNull(order.limitPrice());
    }

    @Test
    void missingRequiredFieldIsRejectedOnDeserialize() throws JsonProcessingException {
        OrderIntent order = limitOrder();
        JsonNode raw = mapper.readTree(mapper.writeValueAsString(order));
        ((com.fasterxml.jackson.databind.node.ObjectNode) raw).remove("symbol");
        String jsonMissingField = mapper.writeValueAsString(raw);

        assertThrows(
                MismatchedInputException.class, () -> mapper.readValue(jsonMissingField, OrderIntent.class));
    }
}
