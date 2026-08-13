package engine.runtime;

import java.math.BigDecimal;
import java.util.Objects;

/**
 * Hand-written {@link PriceFeed} test double -- same no-mocking-framework
 * convention as {@link FakeExchangeAdapter}. Used by {@code
 * PaperTradingAppTest}'s new {@code PriceFeed}-accepting-constructor tests
 * (Paper Trading Bridge Task 4 / KIS wiring) so they don't need a real or
 * fake HTTP server just to supply a price.
 */
final class FakePriceFeed implements PriceFeed {

    private final BigDecimal price;

    FakePriceFeed(BigDecimal price) {
        this.price = Objects.requireNonNull(price, "price is required");
    }

    @Override
    public BigDecimal latestPrice(String symbol) {
        return price;
    }
}
