package engine.execution;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

public record Fill(
        UUID clientOrderId,
        String symbol,
        BigDecimal price,
        BigDecimal quantity,
        BigDecimal notional,
        BigDecimal fee,
        Instant filledAt) {}
