package engine.runtime;

import java.time.Instant;
import java.util.List;

/**
 * The result of a single {@link Reconciler#check} run: every mismatch found
 * (empty if none) and when the check ran. {@link Reconciler#check} always
 * passes an already-immutable list here — this record does not defensively
 * copy on construction, matching this codebase's existing plain-record
 * convention (see {@code engine.execution.Fill}, {@code
 * engine.schemas.RiskDecision}).
 */
public record ReconciliationReport(List<ReconciliationMismatch> mismatches, Instant checkedAt) {

    /** True if this run found zero mismatches. */
    public boolean isClean() {
        return mismatches.isEmpty();
    }
}
