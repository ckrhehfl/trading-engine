package engine.runtime;

import java.math.BigDecimal;

/**
 * A venue-agnostic source of the latest traded price for a symbol, for
 * {@link TradingLoop} to poll once per {@link TradingLoop#tick()}.
 * Extracted from what used to be {@link BingXPriceFeed}'s own concrete
 * type -- {@code TradingLoop} depends only on this interface now, not on
 * any one implementation, mirroring the same extraction already done once
 * for {@code engine.execution.OrderExecutor} (see that interface's own
 * Javadoc for the precedent this follows). A new venue's price feed is a
 * new implementation of this interface, never a change to
 * {@code TradingLoop}.
 *
 * <p>Implementations: {@link BingXPriceFeed} (polls BingX's public,
 * unauthenticated recent-trades endpoint).
 */
public interface PriceFeed {

    /**
     * Returns the latest traded price for {@code symbol}. May throw (see
     * {@link BingXPriceFeed}'s own Javadoc for its specific error
     * conditions) -- {@code TradingLoop.tick()} wraps its own call to this
     * in a catch-all, so a thrown exception here is treated the same as
     * any other failed tick, not specially.
     */
    BigDecimal latestPrice(String symbol);
}
