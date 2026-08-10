package engine.runtime;

import java.util.UUID;

/**
 * A durable, {@code clientOrderId}-keyed record that an ambiguous {@code
 * OrderExecutor.submit} call is in flight -- see {@link
 * PersistentSubmissionOrderExecutor} and {@link SubmissionMarkerStore}.
 * {@code recordedAtIso} is an ISO-8601 string (via {@code Instant#toString()})
 * rather than a {@code java.time.Instant} field directly -- plain
 * jackson-databind (no {@code jackson-datatype-jsr310} module is a
 * dependency anywhere in this repo) has no built-in support for {@code
 * Instant}, and adding that module for one timestamp field in one
 * internal-only file format is not worth it.
 */
record SubmissionMarker(UUID clientOrderId, String symbol, String recordedAtIso) {}
