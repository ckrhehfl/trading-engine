package engine.oms;

import java.time.Instant;

public record StateTransition(OrderState state, Instant at) {}
