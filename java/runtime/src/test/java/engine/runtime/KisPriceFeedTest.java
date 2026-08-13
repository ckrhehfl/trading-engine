package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import engine.exchange.ExchangeException;
import engine.exchange.KisTokenProvider;
import java.io.IOException;
import java.math.BigDecimal;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class KisPriceFeedTest {

    private FakeKisQuoteServer server;
    private KisPriceFeed priceFeed;

    @BeforeEach
    void setUp() throws IOException {
        server = new FakeKisQuoteServer();
        KisTokenProvider tokenProvider = new KisTokenProvider("test-app-key", "test-app-secret", server.baseUrl());
        priceFeed = new KisPriceFeed(tokenProvider, server.baseUrl());
    }

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.close();
        }
    }

    @Test
    void latestPriceReturnsParsedPrice() {
        server.respondWithPrice("352.75");

        BigDecimal price = priceFeed.latestPrice("101W09");

        assertEquals(0, new BigDecimal("352.75").compareTo(price));
        assertEquals("/uapi/domestic-futureoption/v1/quotations/inquire-price", server.lastPath());
        assertEquals("F", server.lastQueryParams().get("FID_COND_MRKT_DIV_CODE"));
        assertEquals("101W09", server.lastQueryParams().get("FID_INPUT_ISCD"));
    }

    @Test
    void latestPriceThrowsExchangeExceptionOnErrorCode() {
        server.respondWith(200, "{\"rt_cd\":\"1\",\"msg_cd\":\"1\",\"msg1\":\"boom\",\"output1\":{}}");

        assertThrows(ExchangeException.class, () -> priceFeed.latestPrice("101W09"));
    }

    @Test
    void latestPriceThrowsExchangeExceptionOnMissingPriceField() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":{}}");

        assertThrows(ExchangeException.class, () -> priceFeed.latestPrice("101W09"));
    }

    @Test
    void latestPriceThrowsExchangeExceptionOnHttpError() {
        server.respondWith(500, "internal server error");

        assertThrows(ExchangeException.class, () -> priceFeed.latestPrice("101W09"));
    }
}
