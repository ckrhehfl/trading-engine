package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import engine.exchange.ExchangeException;
import java.io.IOException;
import java.math.BigDecimal;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Exercises {@link BingXPriceFeed} against a real local HTTP server
 * ({@link FakeBingXTradesServer}, JDK's {@code HttpServer}, not a mock)
 * serving canned recent-trades JSON, matching {@code BingXAdapterTest}'s
 * pattern in {@code java/exchange}.
 */
class BingXPriceFeedTest {

    private FakeBingXTradesServer server;
    private BingXPriceFeed priceFeed;

    @BeforeEach
    void setUp() throws IOException {
        server = new FakeBingXTradesServer();
        priceFeed = new BingXPriceFeed(server.baseUrl());
    }

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.close();
        }
    }

    @Test
    void latestPriceReturnsTheMostRecentTradesPriceNotTheFirstElement() {
        // Verified 2026-07-25 against the real, public, unauthenticated
        // endpoint: data is ordered oldest-first (time/fillId strictly
        // increasing), so the latest trade is the *last* element, not the
        // first -- this canned body deliberately differs first vs last to
        // catch a wrong-index regression.
        server.respondWith(
                200,
                "{\"code\":0,\"msg\":\"\",\"data\":["
                        + "{\"time\":1000,\"price\":\"59000.0\",\"qty\":\"0.01\",\"fillId\":\"1\"},"
                        + "{\"time\":2000,\"price\":\"60123.4\",\"qty\":\"0.02\",\"fillId\":\"2\"}"
                        + "]}");

        BigDecimal price = priceFeed.latestPrice("BTC-USDT");

        assertEquals(0, new BigDecimal("60123.4").compareTo(price));
    }

    @Test
    void latestPriceRequestsTheCorrectPathAndSymbolQueryParam() {
        server.respondWithPrice("100");

        priceFeed.latestPrice("BTC-USDT");

        assertEquals("GET", server.lastMethod());
        assertEquals("/openApi/swap/v2/quote/trades", server.lastPath());
        assertEquals("BTC-USDT", server.lastQueryParams().get("symbol"));
    }

    @Test
    void latestPriceThrowsExchangeExceptionOnNon2xxStatus() {
        server.respondWith(500, "internal server error");

        assertThrows(ExchangeException.class, () -> priceFeed.latestPrice("BTC-USDT"));
    }

    @Test
    void latestPriceThrowsExchangeExceptionOnEmptyResponseBody() {
        // Empty body -> ObjectMapper#readTree("") returns a non-null
        // MissingNode (verified against this project's jackson-databind
        // version in BingXAdapterTest), whose has("code") safely returns
        // false -- this must route through the normal "missing code field"
        // path, not NPE.
        server.respondWith(200, "");

        assertThrows(ExchangeException.class, () -> priceFeed.latestPrice("BTC-USDT"));
    }

    @Test
    void latestPriceThrowsExchangeExceptionOnMalformedJson() {
        server.respondWith(200, "{not valid json");

        assertThrows(ExchangeException.class, () -> priceFeed.latestPrice("BTC-USDT"));
    }

    @Test
    void latestPriceThrowsExchangeExceptionOnEmptyDataArray() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":[]}");

        assertThrows(ExchangeException.class, () -> priceFeed.latestPrice("BTC-USDT"));
    }

    @Test
    void latestPriceThrowsExchangeExceptionOnNonZeroCode() {
        server.respondWith(200, "{\"code\":100410,\"msg\":\"symbol not found\",\"data\":[]}");

        assertThrows(ExchangeException.class, () -> priceFeed.latestPrice("BTC-USDT"));
    }

    @Test
    void latestPriceThrowsExchangeExceptionWhenCodeFieldIsMissingEntirely() {
        server.respondWith(200, "{\"msg\":\"\",\"data\":[{\"time\":1,\"price\":\"100\"}]}");

        assertThrows(ExchangeException.class, () -> priceFeed.latestPrice("BTC-USDT"));
    }

    @Test
    void latestPriceThrowsExchangeExceptionWhenLatestTradeIsMissingPriceField() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":[{\"time\":1}]}");

        assertThrows(ExchangeException.class, () -> priceFeed.latestPrice("BTC-USDT"));
    }
}
