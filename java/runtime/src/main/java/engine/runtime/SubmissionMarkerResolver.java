package engine.runtime;

import engine.exchange.ExchangeAdapter;
import engine.exchange.PositionSnapshot;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Startup-time (or kill-switch-reset-time) resolution step for every {@link
 * SubmissionMarker} a {@link PersistentSubmissionOrderExecutor} has left
 * recorded -- the "query real exchange state before any retry is permitted"
 * half of {@code SUBMISSION_UNKNOWN} handling. Called from {@link
 * PaperTradingApp}'s {@code bingx-vst}-mode construction path, alongside
 * {@link VstPreflight}, before normal tick-driven trading ever begins. Like
 * {@code Reconciler#check}, this class only <b>reports</b> -- it never
 * trips a {@link KillSwitch} itself; the caller decides what a non-empty
 * {@link Resolution#requiresHumanReview()} means for kill-switch state
 * (mirrors {@code Reconciler#check}'s own division of labor from {@code
 * PaperTradingApp#reconcile()}).
 *
 * <p><b>Why this uses {@link ExchangeAdapter#getPositions()}, not {@link
 * ExchangeAdapter#queryOrder}, and why that is a real, disclosed judgment
 * call, not an oversight.</b> {@code queryOrder(Order)} requires a concrete
 * {@link engine.oms.Order} carrying a non-null {@code exchangeOrderId()} --
 * but the entire reason a {@link SubmissionMarker} exists is that {@code
 * adapter.submitOrder} threw <i>before</i> an {@code exchangeOrderId} was
 * ever captured (confirmed directly against {@code BingXAdapter.submitOrder}:
 * every code path that assigns one is unreachable if the call throws). Even
 * setting that aside, the in-memory {@link engine.oms.Order} object itself
 * does not survive a real process restart -- {@code OrderStore} is
 * in-memory only (see its own Javadoc) -- so there is no {@code Order} to
 * query by, by construction, for exactly the restart scenario this class
 * exists to handle. {@code getPositions()} needs neither: it is real
 * ground truth about what the account currently holds, callable with no
 * per-order identifier at all. This is coarser than a per-order query would
 * be (it can only answer "does <i>any</i> position exist for this symbol,"
 * not "did <i>this specific</i> order fill") -- an accepted limitation
 * given this project's current single-symbol, low-frequency, at-most-one-
 * order-in-flight-per-symbol scope (see CLAUDE.md's Current Scope); revisit
 * if multiple concurrent in-flight orders per symbol ever become real.
 * {@code BingXAdapter.queryOrder} accepting a {@code clientOrderID}-based
 * lookup (common on exchanges in this family) would remove this limitation,
 * but is unconfirmed against BingX's real API (see CLAUDE.md's "Exchange
 * API Facts") and not implemented here -- a real, disclosed follow-up, not
 * guessed at.
 */
final class SubmissionMarkerResolver {

    private static final Logger log = LoggerFactory.getLogger(SubmissionMarkerResolver.class);

    private SubmissionMarkerResolver() {}

    /**
     * @param clearedAsSafe markers with no matching non-zero position for
     *     their symbol -- treated as most likely never having reached the
     *     exchange, already cleared from {@code store} by the time this is
     *     returned.
     * @param requiresHumanReview markers with a matching non-zero position
     *     for their symbol -- the ambiguous submission may have gone
     *     through; deliberately left recorded in {@code store} (never
     *     auto-cleared, never a basis for an automatic retry) until a human
     *     confirms what actually happened.
     */
    record Resolution(List<SubmissionMarker> clearedAsSafe, List<SubmissionMarker> requiresHumanReview) {}

    /**
     * Resolves every marker currently in {@code store} against one shared
     * {@code adapter.getPositions()} call (not one call per marker -- cheap
     * and avoids any risk of the account's position set changing between
     * markers within a single resolution pass). Never throws for a
     * marker-level decision; a real {@code getPositions()} failure (e.g. a
     * network error) propagates uncaught, matching {@link VstPreflight
     * #run}'s own "one-shot startup check, not a per-tick call" contract --
     * a caller unable to even ask the exchange what it currently holds has
     * no safe basis for any resolution decision at all.
     */
    static Resolution resolve(SubmissionMarkerStore store, ExchangeAdapter adapter) {
        Objects.requireNonNull(store, "store is required");
        Objects.requireNonNull(adapter, "adapter is required");

        List<SubmissionMarker> markers = store.all();
        if (markers.isEmpty()) {
            return new Resolution(List.of(), List.of());
        }

        List<PositionSnapshot> positions = adapter.getPositions();
        Set<String> symbolsWithOpenPosition = new HashSet<>();
        for (PositionSnapshot position : positions) {
            if (position.positionAmt() != null && position.positionAmt().signum() != 0) {
                symbolsWithOpenPosition.add(position.symbol());
            }
        }

        List<SubmissionMarker> cleared = new ArrayList<>();
        List<SubmissionMarker> needsReview = new ArrayList<>();
        for (SubmissionMarker marker : markers) {
            if (symbolsWithOpenPosition.contains(marker.symbol())) {
                log.error(
                        "SUBMISSION_UNKNOWN marker clientOrderId={} symbol={} recordedAt={} -- a real non-zero"
                                + " position exists for this symbol; the ambiguous submission may have gone"
                                + " through. NOT auto-clearing and NOT permitting a retry -- requires deliberate"
                                + " human review.",
                        marker.clientOrderId(),
                        marker.symbol(),
                        marker.recordedAtIso());
                needsReview.add(marker);
            } else {
                log.warn(
                        "SUBMISSION_UNKNOWN marker clientOrderId={} symbol={} recordedAt={} -- no matching"
                                + " position found; treating as most likely never reached the exchange, clearing.",
                        marker.clientOrderId(),
                        marker.symbol(),
                        marker.recordedAtIso());
                store.clear(marker.clientOrderId());
                cleared.add(marker);
            }
        }
        return new Resolution(List.copyOf(cleared), List.copyOf(needsReview));
    }
}
