package engine.schemas;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.annotation.JsonSerialize;
import com.fasterxml.jackson.databind.ser.std.ToStringSerializer;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/** Risk Gateway -> Execution. See schemas/README.md for the contract. */
public record RiskDecision(
        @JsonProperty(required = true) UUID intentId,
        @JsonProperty(required = true) Decision decision,
        String reason,
        @JsonSerialize(using = ToStringSerializer.class) BigDecimal approvedQuantity,
        @JsonSerialize(using = ToStringSerializer.class) BigDecimal approvedLeverage,
        @JsonProperty(required = true) Instant decidedAt,
        String schemaVersion) {

    public static final String SCHEMA_VERSION = "1.0";

    public RiskDecision {
        Objects.requireNonNull(intentId, "intentId is required");
        Objects.requireNonNull(decision, "decision is required");
        Objects.requireNonNull(decidedAt, "decidedAt is required");
        if (schemaVersion == null) {
            schemaVersion = SCHEMA_VERSION;
        }
        if ((decision == Decision.REJECTED || decision == Decision.MODIFIED)
                && (reason == null || reason.isBlank())) {
            throw new IllegalArgumentException(
                    "reason is required when decision is REJECTED or MODIFIED");
        }
    }

    public RiskDecision(
            UUID intentId,
            Decision decision,
            String reason,
            BigDecimal approvedQuantity,
            BigDecimal approvedLeverage,
            Instant decidedAt) {
        this(intentId, decision, reason, approvedQuantity, approvedLeverage, decidedAt, SCHEMA_VERSION);
    }
}
