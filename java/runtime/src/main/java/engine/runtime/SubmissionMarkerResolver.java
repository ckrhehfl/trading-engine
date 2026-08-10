package engine.runtime;

import engine.exchange.ExchangeAdapter;
import engine.exchange.PositionSnapshot;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Startup-time (or kill-switch-reset-time) resolution step for every {@link
 * SubmissionMarker} a {@link PersistentSubmissionOrderExecutor} has left
 * recorded. Called from {@link PaperTradingApp}'s {@code bingx-vst}-mode
 * construction path, alongside {@link VstPreflight}, before normal
 * tick-driven trading ever begins. Like {@code Reconciler#check}, this class
 * only <b>reports</b> -- it never trips a {@link KillSwitch} itself; the
 * caller decides what a non-empty {@link Resolution#unresolvedMarkers()}
 * means for kill-switch state.
 *
 * <p><b>Every persisted marker is always reported unresolved. This class
 * never auto-clears a marker.</b> This is a deliberate, safety-first design
 * -- a real, correctly-identified CodeRabbit review finding on this PR
 * (marked Critical) found that the original design's own reasoning was
 * wrong: it cleared a marker whenever {@link ExchangeAdapter#getPositions()}
 * showed no matching non-zero position for that marker's symbol, on the
 * theory that this meant the submission "most likely never reached the
 * exchange." That is false in a real, reachable case: an order that
 * <i>was</i> accepted but is still open and unfilled (e.g. a real
 * {@code LIMIT} order resting away from the market) also produces zero
 * matching position -- a position only exists once quantity is actually
 * filled. Auto-clearing on "no position" would therefore have permitted a
 * fresh resubmit of an order that is, in fact, already live at the exchange
 * -- a real duplicate-order risk, the exact failure mode
 * {@code SUBMISSION_UNKNOWN} handling exists to prevent. There is no
 * currently-available signal that can positively rule out "the order is
 * open and unfilled" (see below) -- so the only safe default is to never
 * auto-clear at all. Resolving a marker for real requires a human directly
 * confirming the order's real fate (e.g. via the BingX UI/API) and then
 * deliberately clearing it -- not something this class attempts to automate.
 *
 * <p><b>Why {@link ExchangeAdapter#getPositions()} is still called, purely
 * as diagnostic context, not a decision input.</b> {@code queryOrder(Order)}
 * requires a concrete {@link engine.oms.Order} carrying a non-null {@code
 * exchangeOrderId()} -- but the entire reason a {@link SubmissionMarker}
 * exists is that {@code adapter.submitOrder} threw <i>before</i> an {@code
 * exchangeOrderId} was ever captured (confirmed directly against
 * {@code BingXAdapter.submitOrder}: every code path that assigns one is
 * unreachable if the call throws). Even setting that aside, the in-memory
 * {@link engine.oms.Order} object itself does not survive a real process
 * restart -- {@code OrderStore} is in-memory only -- so there is no
 * {@code Order} to query by, by construction, for exactly the restart
 * scenario this class exists to handle. {@code getPositions()} is still
 * fetched and logged alongside each unresolved marker purely to give a
 * human investigating a faster starting point (e.g. "was there a matching
 * position at the moment of this restart or not") -- it no longer decides
 * anything. {@code BingXAdapter.queryOrder} accepting a {@code
 * clientOrderID}-based lookup (common on exchanges in this family) would
 * let a future version resolve markers for real, but is unconfirmed against
 * BingX's real API (see CLAUDE.md's "Exchange API Facts") and not
 * implemented here -- a real, disclosed follow-up, not guessed at.
 */
final class SubmissionMarkerResolver {

    private static final Logger log = LoggerFactory.getLogger(SubmissionMarkerResolver.class);

    private SubmissionMarkerResolver() {}

    /** @param unresolvedMarkers every marker currently in the store -- see class Javadoc for why none is ever auto-cleared. */
    record Resolution(List<SubmissionMarker> unresolvedMarkers) {}

    /**
     * Reports every marker currently in {@code store} as unresolved, logging
     * each alongside whether a matching non-zero position currently exists
     * for its symbol (diagnostic only -- see class Javadoc). Never throws:
     * a {@code getPositions()} failure (e.g. a network error) is caught and
     * logged rather than propagated, since it is purely diagnostic and must
     * not prevent this process from reaching a safe (tripped) startup state
     * -- unlike {@link VstPreflight#run}, whose own {@code getPositions()}
     * call result is load-bearing for its own decision and does propagate.
     */
    static Resolution resolve(SubmissionMarkerStore store, ExchangeAdapter adapter) {
        Objects.requireNonNull(store, "store is required");
        Objects.requireNonNull(adapter, "adapter is required");

        List<SubmissionMarker> markers = store.all();
        if (markers.isEmpty()) {
            return new Resolution(List.of());
        }

        Set<String> symbolsWithOpenPosition = fetchSymbolsWithOpenPositionForDiagnosticsOnly(adapter);

        for (SubmissionMarker marker : markers) {
            boolean matchingPosition = symbolsWithOpenPosition.contains(marker.symbol());
            log.error(
                    "SUBMISSION_UNKNOWN marker clientOrderId={} symbol={} recordedAt={} -- unresolved; requires"
                            + " deliberate human review before any new signal is submitted for this symbol"
                            + " (diagnostic only, does not by itself confirm or rule out the order: a matching"
                            + " non-zero position currently exists for this symbol = {}).",
                    marker.clientOrderId(),
                    marker.symbol(),
                    marker.recordedAtIso(),
                    matchingPosition);
        }
        return new Resolution(markers);
    }

    private static Set<String> fetchSymbolsWithOpenPositionForDiagnosticsOnly(ExchangeAdapter adapter) {
        List<PositionSnapshot> positions;
        try {
            positions = adapter.getPositions();
        } catch (RuntimeException e) {
            log.warn(
                    "getPositions() failed while gathering diagnostic context for unresolved SUBMISSION_UNKNOWN"
                            + " marker(s) -- proceeding without it, every marker is still reported unresolved: {}",
                    e.toString());
            return Set.of();
        }
        Set<String> symbolsWithOpenPosition = new HashSet<>();
        for (PositionSnapshot position : positions) {
            if (position.positionAmt() != null && position.positionAmt().signum() != 0) {
                symbolsWithOpenPosition.add(position.symbol());
            }
        }
        return symbolsWithOpenPosition;
    }
}
