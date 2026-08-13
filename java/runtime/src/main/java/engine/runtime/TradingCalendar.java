package engine.runtime;

import java.time.Instant;

/**
 * Whether a trading session is open right now. {@link PaperTradingApp
 * #runTick()} gates its call to {@link TradingLoop#tick()} on this --
 * introduced for the KIS/KOSPI200 venue (see CLAUDE.md's "KIS/KOSPI200
 * venue integration, Phase 1" section), whose real market has fixed
 * hours, unlike BTC's 24/7 market.
 *
 * <p>Implementations: {@link AlwaysOpenTradingCalendar} (used by the
 * existing {@code simulated}/{@code bingx-vst} modes -- unconditionally
 * {@code true}, so those modes are provably unaffected by this
 * interface's introduction) and {@link KrxMarketCalendar} (KOSPI200
 * futures' real KST session).
 */
public interface TradingCalendar {

    /**
     * @param now must not be {@code null} -- every implementation rejects a
     *     {@code null} argument (never silently treats it as open or
     *     closed). Callers must provide a non-null {@code Instant}.
     */
    boolean isOpen(Instant now);
}
