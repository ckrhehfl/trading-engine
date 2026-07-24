package engine.exchange;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import engine.oms.Order;
import engine.oms.OrderState;
import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.io.IOException;
import java.io.OutputStream;
import java.math.BigDecimal;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Exercises {@link BingXAdapter} against a real local HTTP server (JDK's
 * built-in {@link HttpServer}, not a mock) serving canned BingX-shaped JSON,
 * per CLAUDE.md's "Exchange API Facts — BingX" (Documented, not yet
 * empirically verified) endpoint shapes. Every {@link Order} used here is
 * obtained via {@link Order#fromApprovedDecision}, matching
 * {@code PaperBrokerTest}'s pattern — proving this suite itself never takes
 * the OMS-mediated-flows-only shortcut it exists to guard against.
 */
class BingXAdapterTest {

    private FakeExchangeServer server;
    private BingXAdapter adapter;

    @BeforeEach
    void setUp() throws IOException {
        server = new FakeExchangeServer();
        adapter = new BingXAdapter("test-api-key", "test-api-secret", server.baseUrl());
    }

    @AfterEach
    void tearDown() {
        server.close();
    }

    private Order guardedMarketOrder(Side side, String quantity) {
        UUID id = UUID.randomUUID();
        OrderIntent intent = new OrderIntent(
                id, "BTC-USDT", side, OrderType.GUARDED_MARKET, new BigDecimal(quantity), null, null, Instant.now());
        RiskDecision decision = new RiskDecision(
                id, Decision.APPROVED, null, new BigDecimal(quantity), new BigDecimal("2"), Instant.now());
        return Order.fromApprovedDecision(intent, decision);
    }

    @Test
    void submitOrderAcknowledgesOnSuccessfulResponseAndCapturesExchangeOrderId() {
        server.respondWith(
                200,
                "{\"code\":0,\"msg\":\"\",\"data\":{\"order\":{\"orderId\":123456789,"
                        + "\"symbol\":\"BTC-USDT\",\"status\":\"NEW\"}}}");
        Order order = guardedMarketOrder(Side.LONG, "1");

        adapter.submitOrder(order);

        assertEquals(OrderState.ACKNOWLEDGED, order.state());
        assertEquals("123456789", order.exchangeOrderId());
        assertEquals("POST", server.lastMethod());
        assertEquals("/openApi/swap/v2/trade/order", server.lastPath());
    }

    @Test
    void submitOrderRejectsOnExchangeLevelErrorCodeAndCapturesReason() {
        server.respondWith(200, "{\"code\":80001,\"msg\":\"insufficient margin\",\"data\":{}}");
        Order order = guardedMarketOrder(Side.LONG, "1");

        adapter.submitOrder(order);

        assertEquals(OrderState.REJECTED, order.state());
    }

    @Test
    void submitOrderThrowsExchangeExceptionOnHttp500AndLeavesOrderInSubmittedState() {
        server.respondWith(500, "internal server error");
        Order order = guardedMarketOrder(Side.LONG, "1");

        assertThrows(ExchangeException.class, () -> adapter.submitOrder(order));
        // submit() already ran before the HTTP call (matching PaperBroker's
        // idiom); the failed call must not additionally mutate state.
        assertEquals(OrderState.SUBMITTED, order.state());
    }

    @Test
    void cancelOrderConfirmsCancelOnSuccessfulResponse() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":{\"order\":{\"orderId\":123456789}}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":{}}");

        adapter.cancelOrder(order);

        assertEquals(OrderState.CANCELLED, order.state());
        assertEquals("DELETE", server.lastMethod());
        assertEquals("/openApi/swap/v2/trade/order", server.lastPath());
    }

    @Test
    void cancelOrderThrowsExchangeExceptionOnErrorCodeAndLeavesOrderCancelPending() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":{\"order\":{\"orderId\":123456789}}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        server.respondWith(200, "{\"code\":80016,\"msg\":\"order does not exist\",\"data\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.cancelOrder(order));
        // requestCancel() already ran; no "cancel failed, revert" transition
        // exists on Order, so it correctly stays CANCEL_PENDING here.
        assertEquals(OrderState.CANCEL_PENDING, order.state());
    }

    @Test
    void queryOrderReturnsParsedStatusWithoutMutatingOrder() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":{\"order\":{\"orderId\":123456789}}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        OrderState stateBefore = order.state();
        server.respondWith(
                200,
                "{\"code\":0,\"msg\":\"\",\"data\":{\"order\":{\"orderId\":123456789,"
                        + "\"symbol\":\"BTC-USDT\",\"status\":\"PARTIALLY_FILLED\","
                        + "\"executedQty\":\"0.4\",\"avgPrice\":\"65000.5\"}}}");

        OrderStatus status = adapter.queryOrder(order);

        assertEquals("123456789", status.exchangeOrderId());
        assertEquals("PARTIALLY_FILLED", status.status());
        assertEquals(0, new BigDecimal("0.4").compareTo(status.filledQuantity()));
        assertEquals(0, new BigDecimal("65000.5").compareTo(status.avgPrice()));
        assertEquals("GET", server.lastMethod());
        // The whole point of this test: queryOrder is a pure read.
        assertEquals(stateBefore, order.state());
        assertEquals(BigDecimal.ZERO.compareTo(order.filledQuantity()), 0);
    }

    @Test
    void queryOrderThrowsExchangeExceptionOnErrorCodeWithoutMutatingOrder() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":{\"order\":{\"orderId\":123456789}}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        OrderState stateBefore = order.state();
        server.respondWith(200, "{\"code\":80016,\"msg\":\"order does not exist\",\"data\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.queryOrder(order));
        assertEquals(stateBefore, order.state());
    }

    @Test
    void setLeverageSendsCorrectEndpointAndParams() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":{}}");

        adapter.setLeverage("BTC-USDT", Side.LONG, 2);

        assertEquals("POST", server.lastMethod());
        assertEquals("/openApi/swap/v2/trade/leverage", server.lastPath());
        Map<String, String> params = server.lastQueryParams();
        assertEquals("BTC-USDT", params.get("symbol"));
        assertEquals("LONG", params.get("side"));
        assertEquals("2", params.get("leverage"));
        assertTrue(params.containsKey("timestamp"));
        assertTrue(params.containsKey("signature"));
    }

    @Test
    void setPositionModeSendsCorrectEndpointAndParams() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":{}}");

        adapter.setPositionMode(PositionMode.HEDGE);

        assertEquals("POST", server.lastMethod());
        assertEquals("/openApi/swap/v1/positionSide/dual", server.lastPath());
        assertEquals("true", server.lastQueryParams().get("dualSidePosition"));
    }

    @Test
    void setPositionModeOneWaySendsFalse() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":{}}");

        adapter.setPositionMode(PositionMode.ONE_WAY);

        assertEquals("false", server.lastQueryParams().get("dualSidePosition"));
    }

    @Test
    void getPositionsParsesArrayResponse() {
        server.respondWith(
                200,
                "{\"code\":0,\"msg\":\"\",\"data\":[{\"symbol\":\"BTC-USDT\",\"positionSide\":\"LONG\","
                        + "\"positionAmt\":\"0.5\",\"avgPrice\":\"64000\",\"leverage\":\"2\","
                        + "\"unrealizedProfit\":\"10.5\",\"liquidationPrice\":\"40000\"}]}");

        List<PositionSnapshot> positions = adapter.getPositions();

        assertEquals(1, positions.size());
        PositionSnapshot p = positions.get(0);
        assertEquals("BTC-USDT", p.symbol());
        assertEquals("LONG", p.positionSide());
        assertEquals(0, new BigDecimal("0.5").compareTo(p.positionAmt()));
        assertEquals(0, new BigDecimal("40000").compareTo(p.liquidationPrice()));
        assertEquals("GET", server.lastMethod());
        assertEquals("/openApi/swap/v2/user/positions", server.lastPath());
    }

    @Test
    void getBalanceParsesResponseAndHitsV3Endpoint() {
        // Response shape (array of per-asset balance objects, not a nested
        // "balance" object) confirmed against a live VST call -- see
        // .planning/07-exchange-adapter.md; catching this here is exactly
        // what caught the original bug. userId/shortUid below are synthetic
        // placeholders, not real account identifiers -- unused by parsing,
        // included only to match the field set the real response has.
        server.respondWith(
                200,
                "{\"code\":0,\"msg\":\"\",\"data\":[{\"userId\":\"000000000000000000\",\"asset\":\"VST\","
                        + "\"balance\":\"1000\",\"equity\":\"1050\",\"unrealizedProfit\":\"50\","
                        + "\"realizedProfit\":\"0\",\"availableMargin\":\"900\",\"usedMargin\":\"150\","
                        + "\"frozenMargin\":\"0\",\"shortUid\":\"00000000\"}]}");

        BalanceSnapshot balance = adapter.getBalance();

        assertEquals(0, new BigDecimal("1000").compareTo(balance.balance()));
        assertEquals(0, new BigDecimal("1050").compareTo(balance.equity()));
        assertEquals(0, new BigDecimal("900").compareTo(balance.availableMargin()));
        assertEquals(0, new BigDecimal("150").compareTo(balance.usedMargin()));
        assertEquals(0, new BigDecimal("50").compareTo(balance.unrealizedProfit()));
        assertEquals("/openApi/swap/v3/user/balance", server.lastPath());
    }

    @Test
    void getBalanceTakesFirstEntryWhenMultipleAssetsPresent() {
        server.respondWith(
                200,
                "{\"code\":0,\"msg\":\"\",\"data\":["
                        + "{\"asset\":\"VST\",\"balance\":\"1000\",\"equity\":\"1000\","
                        + "\"availableMargin\":\"1000\",\"usedMargin\":\"0\",\"unrealizedProfit\":\"0\"},"
                        + "{\"asset\":\"OTHER\",\"balance\":\"9999\",\"equity\":\"9999\","
                        + "\"availableMargin\":\"9999\",\"usedMargin\":\"0\",\"unrealizedProfit\":\"0\"}"
                        + "]}");

        BalanceSnapshot balance = adapter.getBalance();

        assertEquals(0, new BigDecimal("1000").compareTo(balance.balance()));
    }

    @Test
    void getBalanceThrowsExchangeExceptionOnEmptyDataArray() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":[]}");

        assertThrows(ExchangeException.class, () -> adapter.getBalance());
    }

    @Test
    void getBalanceThrowsExchangeExceptionWhenCodeFieldIsMissingEntirely() {
        // JsonNode#path("code").asInt() silently returns 0 for a missing
        // field -- without an explicit has("code") check, this malformed
        // response would be misread as a successful, empty balance rather
        // than surfaced as the shape mismatch it actually is. `data` here
        // is deliberately a valid, non-empty balance array -- otherwise
        // this test can't tell requireCode's check apart from
        // selectBalanceNode's separate "empty array" check.
        server.respondWith(
                200,
                "{\"msg\":\"\",\"data\":[{\"asset\":\"VST\",\"balance\":\"1000\",\"equity\":\"1000\","
                        + "\"availableMargin\":\"1000\",\"usedMargin\":\"0\",\"unrealizedProfit\":\"0\"}]}");

        ExchangeException exception = assertThrows(ExchangeException.class, () -> adapter.getBalance());
        assertTrue(exception.getMessage().contains("code"), "exception message should mention the missing field");
    }

    @Test
    void getPositionsThrowsExchangeExceptionOnNonNumericDecimalField() {
        server.respondWith(
                200,
                "{\"code\":0,\"msg\":\"\",\"data\":[{\"symbol\":\"BTC-USDT\",\"positionSide\":\"LONG\","
                        + "\"positionAmt\":\"not-a-number\",\"avgPrice\":\"64000\",\"leverage\":\"2\","
                        + "\"unrealizedProfit\":\"10.5\",\"liquidationPrice\":\"40000\"}]}");

        assertThrows(ExchangeException.class, () -> adapter.getPositions());
    }

    @Test
    void everyRequestIncludesApiKeyHeaderAndSignatureParam() {
        server.respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":{}}");

        adapter.setPositionMode(PositionMode.ONE_WAY);

        assertEquals("test-api-key", server.lastApiKeyHeader());
        assertTrue(server.lastQueryParams().containsKey("signature"));
        assertTrue(server.lastQueryParams().containsKey("timestamp"));
    }

    @Test
    void orderHasNoPublicConstructorOtherThanFromApprovedDecisionFactory() {
        // Proves ExchangeAdapter methods can only ever be called with an
        // Order that went through risk assessment -- there is no other way
        // to obtain one, in this test suite or any other caller.
        assertEquals(
                0,
                Order.class.getConstructors().length,
                "Order must be constructible only via Order.fromApprovedDecision(...) -- a public "
                        + "constructor would let ExchangeAdapter callers bypass OMS-mediated flows");
    }

    /** Minimal fake BingX server: records the last request, serves a canned response. */
    private static final class FakeExchangeServer implements AutoCloseable {
        private final HttpServer server;
        private volatile String lastMethod;
        private volatile String lastPath;
        private volatile Map<String, String> lastQueryParams = Map.of();
        private volatile String lastApiKeyHeader;
        private volatile int responseStatus = 200;
        private volatile String responseBody = "{}";

        FakeExchangeServer() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
        }

        void respondWith(int status, String body) {
            this.responseStatus = status;
            this.responseBody = body;
        }

        String baseUrl() {
            return "http://127.0.0.1:" + server.getAddress().getPort();
        }

        String lastMethod() {
            return lastMethod;
        }

        String lastPath() {
            return lastPath;
        }

        Map<String, String> lastQueryParams() {
            return lastQueryParams;
        }

        String lastApiKeyHeader() {
            return lastApiKeyHeader;
        }

        private void handle(HttpExchange exchange) throws IOException {
            lastMethod = exchange.getRequestMethod();
            lastPath = exchange.getRequestURI().getPath();
            lastQueryParams = parseQuery(exchange.getRequestURI().getRawQuery());
            lastApiKeyHeader = exchange.getRequestHeaders().getFirst("X-BX-APIKEY");

            byte[] body = responseBody.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(responseStatus, body.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(body);
            }
        }

        private static Map<String, String> parseQuery(String rawQuery) {
            Map<String, String> params = new LinkedHashMap<>();
            if (rawQuery == null || rawQuery.isEmpty()) {
                return params;
            }
            for (String pair : rawQuery.split("&")) {
                int eq = pair.indexOf('=');
                if (eq < 0) {
                    params.put(pair, "");
                } else {
                    params.put(pair.substring(0, eq), pair.substring(eq + 1));
                }
            }
            return params;
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }
}
