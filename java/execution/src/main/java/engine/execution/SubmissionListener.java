package engine.execution;

import engine.oms.Order;

/**
 * Optional hook {@link ExchangeOrderExecutor#submit} calls immediately
 * before and after an ambiguous submission attempt -- lets a caller
 * observe (and durably record) submission-outcome ambiguity without
 * {@code ExchangeOrderExecutor} needing to know anything about
 * persistence, or about any specific venue, itself.
 *
 * <p>Added on real CodeRabbit review (Paper Trading Bridge Task H,
 * `.planning/paper-trading-h-vst-integration.md`) as the replacement for
 * an earlier design, {@code engine.runtime.PersistentSubmissionOrderExecutor},
 * which wrapped {@link OrderExecutor} as a third implementation of that
 * interface -- against {@code OrderExecutor}'s own documented "exactly
 * two implementations" invariant. This interface is the corrected design:
 * {@code ExchangeOrderExecutor} remains the *only* {@code OrderExecutor}
 * that talks to a real exchange, and a cross-cutting concern like durable
 * {@code SUBMISSION_UNKNOWN} marking is composed into it via dependency
 * injection (this interface), not via a second, competing {@code
 * OrderExecutor} implementation wrapping it.
 *
 * <p><b>Contract, precisely.</b> {@link #beforeSubmit} is called
 * immediately before {@link ExchangeAdapter#submitOrder}, unconditionally,
 * every time {@link ExchangeOrderExecutor#submit} runs. {@link
 * #afterSubmitSucceeded} is called immediately after {@code
 * adapter.submitOrder} returns -- <b>only if it returns normally</b>; if
 * it throws, {@code afterSubmitSucceeded} is <b>not</b> called (the
 * submission's outcome remains genuinely ambiguous, which is exactly the
 * state a caller recording a marker in {@link #beforeSubmit} needs to
 * leave recorded). Neither method receives a return value; an
 * implementation that wants to observe or persist state must do so via
 * its own side effects.
 *
 * <p><b>Throwing, precisely</b> (a real, durable-persistence-backed
 * implementation -- e.g. {@code engine.runtime.MarkerRecordingSubmissionListener}
 * -- can genuinely throw on an I/O failure, so this is not a hypothetical):
 * a thrown {@link #beforeSubmit} propagates uncaught out of
 * {@code submit} exactly like a thrown {@code adapter.submitOrder} would --
 * {@code adapter.submitOrder} is never even reached, so there is no
 * ambiguity to preserve, this is simply an early submission failure. A
 * thrown {@link #afterSubmitSucceeded} is different: by the time it runs,
 * {@code adapter.submitOrder} has already returned normally and the order
 * is definitively known-live (already registered for poll/fill/cancel
 * tracking) -- {@code ExchangeOrderExecutor#submit} catches and logs that
 * failure rather than propagating it, since rethrowing would incorrectly
 * mark a known-live, already-tracked order as orphaned. See {@code
 * ExchangeOrderExecutor#submit}'s own Javadoc/implementation for exactly
 * where.
 */
public interface SubmissionListener {

    /** Called before every submission attempt, unconditionally. */
    void beforeSubmit(Order order);

    /**
     * Called only if the submission attempt returned normally (did not
     * throw) -- the resulting {@link Order} state ({@code ACKNOWLEDGED}
     * or {@code REJECTED}) is by this point definitively known, so any
     * ambiguity a {@link #beforeSubmit} call recorded is resolved.
     */
    void afterSubmitSucceeded(Order order);

    /**
     * A listener that does nothing -- the default when no real listener
     * is supplied (e.g. {@code simulated}-mode-equivalent construction,
     * or any caller with no durable-marking concern).
     */
    SubmissionListener NO_OP = new SubmissionListener() {
        @Override
        public void beforeSubmit(Order order) {}

        @Override
        public void afterSubmitSucceeded(Order order) {}
    };
}
