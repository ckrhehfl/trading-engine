package engine.schemas;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.annotation.JsonSerialize;
import com.fasterxml.jackson.databind.ser.std.ToStringSerializer;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/** Research Plane -> Risk Gateway. See schemas/README.md for the contract. */
public record OrderIntent(
        @JsonProperty(required = true) UUID intentId,
        @JsonProperty(required = true) String symbol,
        @JsonProperty(required = true) Side side,
        @JsonProperty(required = true) OrderType orderType,
        @JsonProperty(required = true) @JsonSerialize(using = ToStringSerializer.class) BigDecimal quantity,
        @JsonSerialize(using = ToStringSerializer.class) BigDecimal limitPrice,
        String signalTimeframe,
        @JsonProperty(required = true) Instant createdAt,
        String schemaVersion) {

    public static final String SCHEMA_VERSION = "1.0";

    public OrderIntent {
        Objects.requireNonNull(intentId, "intentId is required");
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(side, "side is required");
        Objects.requireNonNull(orderType, "orderType is required");
        Objects.requireNonNull(quantity, "quantity is required");
        Objects.requireNonNull(createdAt, "createdAt is required");
        if (schemaVersion == null) {
            schemaVersion = SCHEMA_VERSION;
        }
        if (orderType == OrderType.LIMIT && limitPrice == null) {
            throw new IllegalArgumentException("limitPrice is required when orderType is LIMIT");
        }
        if (orderType == OrderType.GUARDED_MARKET && limitPrice != null) {
            throw new IllegalArgumentException(
                    "limitPrice must be null when orderType is GUARDED_MARKET");
        }
    }

    public OrderIntent(
            UUID intentId,
            String symbol,
            Side side,
            OrderType orderType,
            BigDecimal quantity,
            BigDecimal limitPrice,
            String signalTimeframe,
            Instant createdAt) {
        this(
                intentId,
                symbol,
                side,
                orderType,
                quantity,
                limitPrice,
                signalTimeframe,
                createdAt,
                SCHEMA_VERSION);
    }
}
