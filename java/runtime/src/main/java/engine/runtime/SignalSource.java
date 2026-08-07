package engine.runtime;

import engine.schemas.OrderIntent;
import java.util.Optional;

/**
 * A source of trading signals for {@link TradingLoop} to submit through
 * {@link OrderPipeline} on each {@link TradingLoop#tick()}. Extracted from
 * what used to be {@link DummySignalSource}'s own concrete, final-class
 * method signature -- {@code TradingLoop} depends only on this interface
 * now, not on any one implementation, so a real signal transport (see
 * {@link FileSignalSource}) can be swapped in without touching
 * {@code TradingLoop} or anything downstream of it (per
 * {@code .planning/paper-trading-a-signal-source.md}'s bridge-transport
 * decision).
 *
 * <p>Implementations: {@link DummySignalSource} (test/demo scaffolding
 * only -- never a real strategy, see its own Javadoc) and
 * {@link FileSignalSource} (reads a real strategy's {@link OrderIntent}
 * output from a JSON file written by a separate process). Both must return
 * {@link Optional#empty()} rather than throw on any condition that isn't a
 * genuine, fresh signal -- {@code TradingLoop.tick()} calls this once per
 * tick and does not expect it to fail.
 */
public interface SignalSource {

    /**
     * Returns a fresh signal to submit, or {@link Optional#empty()} if
     * there is nothing new to submit this call. Must never throw for a
     * condition an implementation can reasonably anticipate (a missing
     * file, a duplicate signal, etc.) -- {@code TradingLoop} does wrap
     * every {@code tick()} in its own catch-all, but a well-behaved
     * {@code SignalSource} should not rely on that as its primary error
     * handling.
     */
    Optional<OrderIntent> nextSignal();
}
