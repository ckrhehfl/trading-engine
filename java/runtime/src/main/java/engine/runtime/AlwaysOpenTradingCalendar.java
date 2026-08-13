package engine.runtime;

import java.time.Instant;

/**
 * Unconditionally open -- used by the {@code simulated}/{@code bingx-vst}
 * execution modes, whose real markets (an internal simulator, and BTC
 * perpetual futures) have no fixed trading hours. Kept trivial and
 * covered by its own test rather than trusted by inspection, so the "zero
 * behavior change for existing modes" claim made when {@link
 * TradingCalendar} was introduced is provable, not just asserted -- same
 * discipline this codebase already applies to other provably-inert
 * retrofits (see {@code OrderExecutor}'s own extraction history).
 */
public final class AlwaysOpenTradingCalendar implements TradingCalendar {

    @Override
    public boolean isOpen(Instant now) {
        return true;
    }
}
