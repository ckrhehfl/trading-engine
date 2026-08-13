package engine.exchange;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.oms.Order;
import engine.oms.OrderState;
import engine.schemas.Decision;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.RiskDecision;
import engine.schemas.Side;
import java.io.IOException;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Exercises {@link KisAdapter} against a real local HTTP server ({@link
 * FakeKisServer}, JDK built-in, not a mock) serving canned KIS-shaped
 * JSON. Request parameters/paths/tr_ids match KIS's own real source
 * (confirmed, see {@code KisAdapter}'s class Javadoc); response field
 * names used in these canned responses match this adapter's own inferred
 * parsing (also disclosed there as unverified against a real response).
 * Every {@link Order} used here is obtained via {@link
 * Order#fromApprovedDecision}, matching {@code BingXAdapterTest}'s own
 * pattern.
 */
class KisAdapterTest {

    private FakeKisServer server;
    private KisAdapter adapter;

    @BeforeEach
    void setUp() throws IOException {
        server = new FakeKisServer();
        KisTokenProvider tokenProvider = new KisTokenProvider("test-app-key", "test-app-secret", server.baseUrl());
        adapter = new KisAdapter(tokenProvider, "12345678", "03", server.baseUrl());
    }

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.close();
        }
    }

    private Order guardedMarketOrder(Side side, String quantity) {
        UUID id = UUID.randomUUID();
        OrderIntent intent = new OrderIntent(
                id, "101W09", side, OrderType.GUARDED_MARKET, new BigDecimal(quantity), null, null, Instant.now());
        RiskDecision decision = new RiskDecision(
                id, Decision.APPROVED, null, new BigDecimal(quantity), new BigDecimal("1"), Instant.now());
        return Order.fromApprovedDecision(intent, decision);
    }

    @Test
    void submitOrderAcknowledgesOnSuccessfulResponseAndCapturesExchangeOrderId() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"0000123456\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");

        adapter.submitOrder(order);

        assertEquals(OrderState.ACKNOWLEDGED, order.state());
        assertEquals("0000123456", order.exchangeOrderId());
        assertEquals("POST", server.lastMethod());
        assertEquals("/uapi/domestic-futureoption/v1/trading/order", server.lastPath());
        assertEquals("VTTO1101U", server.lastTrIdHeader());
    }

    @Test
    void submitOrderSendsExpectedBodyFields() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"1\"}}");
        Order order = guardedMarketOrder(Side.LONG, "2");

        adapter.submitOrder(order);

        String body = server.lastRequestBody();
        assertTrue(body.contains("\"CANO\":\"12345678\""));
        assertTrue(body.contains("\"ACNT_PRDT_CD\":\"03\""));
        assertTrue(body.contains("\"SLL_BUY_DVSN_CD\":\"02\""), "LONG must map to buy (02): " + body);
        assertTrue(body.contains("\"SHTN_PDNO\":\"101W09\""));
        assertTrue(body.contains("\"ORD_QTY\":\"2\""));
    }

    @Test
    void submitOrderShortSideSendsSellCode() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"1\"}}");
        Order order = guardedMarketOrder(Side.SHORT, "1");

        adapter.submitOrder(order);

        assertTrue(server.lastRequestBody().contains("\"SLL_BUY_DVSN_CD\":\"01\""));
    }

    @Test
    void submitOrderTransitionsToRejectedOnExchangeLevelError() {
        server.respondWith(200, "{\"rt_cd\":\"1\",\"msg_cd\":\"40570000\",\"msg1\":\"insufficient margin\",\"output\":{}}");
        Order order = guardedMarketOrder(Side.LONG, "1");

        adapter.submitOrder(order);

        assertEquals(OrderState.REJECTED, order.state());
    }

    @Test
    void submitOrderThrowsExchangeExceptionOnHttp500AndLeavesOrderInSubmittedState() {
        server.respondWith(500, "internal server error");
        Order order = guardedMarketOrder(Side.LONG, "1");

        assertThrows(ExchangeException.class, () -> adapter.submitOrder(order));
        assertEquals(OrderState.SUBMITTED, order.state());
    }

    @Test
    void cancelOrderConfirmsCancelOnSuccessfulResponse() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{}}");

        adapter.cancelOrder(order);

        assertEquals(OrderState.CANCELLED, order.state());
        assertEquals("/uapi/domestic-futureoption/v1/trading/order-rvsecncl", server.lastPath());
        assertEquals("VTTO1103U", server.lastTrIdHeader());
        assertTrue(server.lastRequestBody().contains("\"ORGN_ODNO\":\"123\""));
    }

    @Test
    void cancelOrderThrowsExchangeExceptionOnErrorAndLeavesOrderCancelPending() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        server.respondWith(200, "{\"rt_cd\":\"1\",\"msg_cd\":\"40570001\",\"msg1\":\"order not found\",\"output\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.cancelOrder(order));
        assertEquals(OrderState.CANCEL_PENDING, order.state());
    }

    @Test
    void queryOrderReturnsNewStatusWhenNoFillYet() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"ODNO\":\"123\",\"ORD_QTY\":\"1\","
                        + "\"TOT_CCLD_QTY\":\"0\",\"AVG_PRC\":\"0\",\"CNCL_YN\":\"N\"}],\"output2\":{}}");

        OrderStatus status = adapter.queryOrder(order);

        assertEquals("123", status.exchangeOrderId());
        assertEquals("NEW", status.status());
        assertEquals("GET", server.lastMethod());
        assertEquals("VTTO5201R", server.lastTrIdHeader());
        assertEquals(OrderState.ACKNOWLEDGED, order.state(), "queryOrder must never mutate Order");
    }

    @Test
    void queryOrderReturnsPartiallyFilledStatus() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "5");
        adapter.submitOrder(order);
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"ODNO\":\"123\",\"ORD_QTY\":\"5\","
                        + "\"TOT_CCLD_QTY\":\"2\",\"AVG_PRC\":\"350.5\",\"CNCL_YN\":\"N\"}],\"output2\":{}}");

        OrderStatus status = adapter.queryOrder(order);

        assertEquals("PARTIALLY_FILLED", status.status());
        assertEquals(0, new BigDecimal("2").compareTo(status.filledQuantity()));
        assertEquals(0, new BigDecimal("350.5").compareTo(status.avgPrice()));
    }

    @Test
    void queryOrderReturnsFilledStatus() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "5");
        adapter.submitOrder(order);
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"ODNO\":\"123\",\"ORD_QTY\":\"5\","
                        + "\"TOT_CCLD_QTY\":\"5\",\"AVG_PRC\":\"351\",\"CNCL_YN\":\"N\"}],\"output2\":{}}");

        OrderStatus status = adapter.queryOrder(order);

        assertEquals("FILLED", status.status());
    }

    @Test
    void queryOrderReturnsCancelledStatusWhenCnclYnIsY() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "5");
        adapter.submitOrder(order);
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"ODNO\":\"123\",\"ORD_QTY\":\"5\","
                        + "\"TOT_CCLD_QTY\":\"0\",\"AVG_PRC\":\"0\",\"CNCL_YN\":\"Y\"}],\"output2\":{}}");

        OrderStatus status = adapter.queryOrder(order);

        assertEquals("CANCELLED", status.status());
    }

    @Test
    void queryOrderThrowsWhenOrderNotFoundInOutput1() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.queryOrder(order));
    }

    @Test
    void queryOrderThrowsExchangeExceptionOnErrorCode() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        server.respondWith(200, "{\"rt_cd\":\"1\",\"msg_cd\":\"1\",\"msg1\":\"boom\",\"output1\":[],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.queryOrder(order));
    }

    @Test
    void getPositionsParsesArrayAndSkipsZeroQuantityRows() {
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":["
                        + "{\"PDNO\":\"101W09\",\"SLL_BUY_DVSN_NAME\":\"매수\",\"CBLC_QTY\":\"3\","
                        + "\"PCHS_UNPR\":\"350\",\"EVAL_PFLS_AMT\":\"1500\"},"
                        + "{\"PDNO\":\"101W12\",\"SLL_BUY_DVSN_NAME\":\"매도\",\"CBLC_QTY\":\"0\","
                        + "\"PCHS_UNPR\":\"0\",\"EVAL_PFLS_AMT\":\"0\"}"
                        + "],\"output2\":{}}");

        List<PositionSnapshot> positions = adapter.getPositions();

        assertEquals(1, positions.size(), "a zero-quantity row is not a real open position");
        assertEquals("101W09", positions.get(0).symbol());
        assertEquals(0, new BigDecimal("3").compareTo(positions.get(0).positionAmt()));
        assertNull(positions.get(0).leverage(), "no user-settable leverage concept for this venue");
        assertEquals("/uapi/domestic-futureoption/v1/trading/inquire-balance", server.lastPath());
        assertEquals("VTFO6118R", server.lastTrIdHeader());
    }

    @Test
    void getBalanceParsesResponse() {
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"DNCA_TOT_AMT\":\"10000000\","
                        + "\"TOT_EVAL_AMT\":\"10050000\",\"ORD_PSBL_CASH\":\"9500000\",\"MGN_USE_AMT\":\"500000\","
                        + "\"EVAL_PFLS_AMT\":\"50000\"}}");

        BalanceSnapshot balance = adapter.getBalance();

        assertEquals(0, new BigDecimal("10000000").compareTo(balance.balance()));
        assertEquals(0, new BigDecimal("10050000").compareTo(balance.equity()));
        assertEquals(0, new BigDecimal("9500000").compareTo(balance.availableMargin()));
        assertEquals(0, new BigDecimal("500000").compareTo(balance.usedMargin()));
        assertEquals(0, new BigDecimal("50000").compareTo(balance.unrealizedProfit()));
        assertEquals("KRW", balance.asset());
        assertEquals("/uapi/domestic-futureoption/v1/trading/inquire-deposit", server.lastPath());
        assertEquals("CTRP6550R", server.lastTrIdHeader());
    }

    @Test
    void setLeverageIsNoOpAndSendsNoRequest() {
        adapter.setLeverage("101W09", Side.LONG, 2);

        assertNull(server.lastPath(), "setLeverage must never send a request for this venue");
    }

    @Test
    void setPositionModeIsNoOpAndSendsNoRequest() {
        adapter.setPositionMode(PositionMode.HEDGE);

        assertNull(server.lastPath(), "setPositionMode must never send a request for this venue");
    }

    @Test
    void requestIncludesBearerTokenAppKeyAppSecretAndTrIdHeaders() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{}}");

        adapter.setLeverage("101W09", Side.LONG, 1); // no-op, doesn't hit the server
        // Use a real request to actually observe the shared header-attaching logic.
        server.respondWith(
                200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"DNCA_TOT_AMT\":\"1\","
                        + "\"TOT_EVAL_AMT\":\"1\",\"ORD_PSBL_CASH\":\"1\",\"MGN_USE_AMT\":\"1\",\"EVAL_PFLS_AMT\":\"1\"}}");

        adapter.getBalance();

        assertEquals("Bearer fake-token", server.lastAuthorizationHeader());
        assertEquals("test-app-key", server.lastAppKeyHeader());
        assertEquals("test-app-secret", server.lastAppSecretHeader());
        assertEquals("CTRP6550R", server.lastTrIdHeader());
    }

    @Test
    void accountNoAndAccountProductCodeAreStripped() {
        KisTokenProvider tokenProvider = new KisTokenProvider("k", "s", server.baseUrl());
        KisAdapter strippingAdapter = new KisAdapter(tokenProvider, " 12345678\r\n", "\t03 ", server.baseUrl());
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"1\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");

        strippingAdapter.submitOrder(order);

        assertTrue(server.lastRequestBody().contains("\"CANO\":\"12345678\""));
        assertTrue(server.lastRequestBody().contains("\"ACNT_PRDT_CD\":\"03\""));
    }

    @Test
    void orderHasNoPublicConstructorOtherThanFromApprovedDecisionFactory() {
        assertEquals(
                0,
                Order.class.getConstructors().length,
                "Order must be constructible only via Order.fromApprovedDecision(...) -- a public "
                        + "constructor would let ExchangeAdapter callers bypass OMS-mediated flows");
    }
}
