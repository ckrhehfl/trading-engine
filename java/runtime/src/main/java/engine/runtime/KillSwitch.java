package engine.runtime;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * In-process kill switch: a single flag {@link TradingLoop#tick()} checks
 * every call. Tripping it stops {@link TradingLoop} from generating new
 * signals; it does not stop {@link TradingLoop} from continuing to apply
 * price updates to already-pending orders (see {@link TradingLoop}'s
 * Javadoc for why that's deliberate).
 *
 * <p>This is deliberately minimal, not a complete kill-switch story: there
 * is no external trigger wiring here (no file watch, no HTTP endpoint, no
 * OS signal handler) -- there is no ops surface for one to connect to yet.
 * That's a later priority's job (see CLAUDE.md's Future Tooling Watchlist,
 * "Monitoring/alerting"). {@link #trip()} only affects the single
 * in-process {@code TradingLoop} instance it was handed to; it does not
 * persist across restarts and reaches no other process.
 */
public final class KillSwitch {

    private final AtomicBoolean tripped = new AtomicBoolean(false);

    public void trip() {
        tripped.set(true);
    }

    public void reset() {
        tripped.set(false);
    }

    public boolean isTripped() {
        return tripped.get();
    }
}
