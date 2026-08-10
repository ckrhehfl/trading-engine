package engine.runtime;

import engine.execution.SubmissionListener;
import engine.oms.Order;
import java.util.Objects;

/**
 * Real {@link SubmissionListener} implementation backing durable {@code
 * SUBMISSION_UNKNOWN} handling in {@code bingx-vst} mode -- a thin
 * delegate to {@link SubmissionMarkerStore}: {@link #beforeSubmit} records
 * a marker, {@link #afterSubmitSucceeded} clears it. All the real
 * persistence/atomicity/fail-closed-on-corruption logic lives in {@link
 * SubmissionMarkerStore} itself (already tested by {@code
 * SubmissionMarkerStoreTest}) -- this class exists purely to adapt that
 * store to the {@link SubmissionListener} interface {@code
 * engine.execution.ExchangeOrderExecutor} depends on, without {@code
 * ExchangeOrderExecutor} itself (or the {@code :execution} module) ever
 * needing to know about persistence, {@code :runtime}, or this project's
 * specific marker-file format.
 *
 * <p>Replaces the earlier {@code PersistentSubmissionOrderExecutor}
 * decorator design -- see {@code SubmissionListener}'s own Javadoc and
 * {@code .planning/paper-trading-h-vst-integration.md} for the full
 * account of why: that design made {@code PersistentSubmissionOrderExecutor}
 * a third class implementing {@code OrderExecutor}, against that
 * interface's own documented "exactly two implementations" invariant. This
 * class implements no {@code OrderExecutor}-shaped interface at all --
 * it is a plain collaborator, injected into {@code ExchangeOrderExecutor}'s
 * own constructor, not wrapped around it.
 */
final class MarkerRecordingSubmissionListener implements SubmissionListener {

    private final SubmissionMarkerStore store;

    MarkerRecordingSubmissionListener(SubmissionMarkerStore store) {
        this.store = Objects.requireNonNull(store, "store is required");
    }

    @Override
    public void beforeSubmit(Order order) {
        store.record(order.clientOrderId(), order.symbol());
    }

    @Override
    public void afterSubmitSucceeded(Order order) {
        store.clear(order.clientOrderId());
    }
}
