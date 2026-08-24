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
 * names/casing used in these canned responses match KIS's own real,
 * empirically-verified response shape (2026-08-21 -- see {@code
 * KisAdapter}'s class Javadoc "Real verification..." disclosure), not
 * merely an inference anymore. Every {@link Order} used here is obtained via {@link
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

    private Order limitOrder(Side side, String quantity, String limitPrice) {
        UUID id = UUID.randomUUID();
        OrderIntent intent = new OrderIntent(
                id,
                "101W09",
                side,
                OrderType.LIMIT,
                new BigDecimal(quantity),
                new BigDecimal(limitPrice),
                null,
                Instant.now());
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
    void submitOrderGuardedMarketSendsMarketTypeAndZeroPrice() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"1\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");

        adapter.submitOrder(order);

        String body = server.lastRequestBody();
        assertTrue(body.contains("\"UNIT_PRICE\":\"0\""), body);
        assertTrue(body.contains("\"NMPR_TYPE_CD\":\"02\""), body);
        assertTrue(body.contains("\"ORD_DVSN_CD\":\"02\""), body);
    }

    @Test
    void submitOrderLimitSendsLimitTypeAndRealPrice() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"1\"}}");
        Order order = limitOrder(Side.LONG, "1", "350.5");

        adapter.submitOrder(order);

        String body = server.lastRequestBody();
        assertTrue(body.contains("\"UNIT_PRICE\":\"350.5\""), body);
        assertTrue(body.contains("\"NMPR_TYPE_CD\":\"01\""), body);
        assertTrue(body.contains("\"ORD_DVSN_CD\":\"01\""), body);
    }

    @Test
    void submitOrderRejectsFractionalQuantityRatherThanTruncating() {
        Order order = guardedMarketOrder(Side.LONG, "1.7");

        assertThrows(ExchangeException.class, () -> adapter.submitOrder(order));
        assertNull(server.lastPath(), "a fractional quantity must be rejected before any request is sent");
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
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\",\"ord_qty\":\"1\","
                        + "\"tot_ccld_qty\":\"0\",\"avg_idx\":\"0\",\"qty\":\"1\"}],\"output2\":{}}");

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
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\",\"ord_qty\":\"5\","
                        + "\"tot_ccld_qty\":\"2\",\"avg_idx\":\"350.5\",\"qty\":\"3\"}],\"output2\":{}}");

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
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\",\"ord_qty\":\"5\","
                        + "\"tot_ccld_qty\":\"5\",\"avg_idx\":\"351\",\"qty\":\"0\"}],\"output2\":{}}");

        OrderStatus status = adapter.queryOrder(order);

        assertEquals("FILLED", status.status());
    }

    @Test
    void queryOrderReturnsCancelledStatusWhenRemainingQuantityIsZeroWithoutFullFill() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "5");
        adapter.submitOrder(order);
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\",\"ord_qty\":\"5\","
                        + "\"tot_ccld_qty\":\"0\",\"avg_idx\":\"0\",\"qty\":\"0\"}],\"output2\":{}}");

        OrderStatus status = adapter.queryOrder(order);

        assertEquals("CANCELLED", status.status());
    }

    @Test
    void queryOrderThrowsWhenOrdQtyMissingRatherThanMisclassifyingStatus() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\","
                        + "\"tot_ccld_qty\":\"0\",\"avg_idx\":\"0\",\"qty\":\"1\"}],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.queryOrder(order));
    }

    @Test
    void queryOrderThrowsWhenTotCcldQtyMissingRatherThanMisclassifyingStatus() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\",\"ord_qty\":\"1\","
                        + "\"avg_idx\":\"0\",\"qty\":\"1\"}],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.queryOrder(order));
    }

    @Test
    void queryOrderThrowsWhenQtyMissingRatherThanMisreportingAsCancelled() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "5");
        adapter.submitOrder(order);
        // A still-live order (no "qty" field, ordered=5, filled=2) must
        // throw, not fall through to noRemainder=true and be misreported
        // as CANCELLED -- the exact bug CodeRabbit flagged.
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\",\"ord_qty\":\"5\","
                        + "\"tot_ccld_qty\":\"2\",\"avg_idx\":\"350\"}],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.queryOrder(order));
    }

    @Test
    void queryOrderThrowsWhenTotCcldQtyExceedsOrdQty() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "5");
        adapter.submitOrder(order);
        // ord_qty=5, tot_ccld_qty=6, qty=0 -- a fill exceeding what was
        // ever ordered must never be reported as a valid FILLED status.
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\",\"ord_qty\":\"5\","
                        + "\"tot_ccld_qty\":\"6\",\"avg_idx\":\"350\",\"qty\":\"0\"}],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.queryOrder(order));
    }

    @Test
    void queryOrderThrowsWhenQtyExceedsRemainingCapacity() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "5");
        adapter.submitOrder(order);
        // ord_qty=5, tot_ccld_qty=2, qty=4 -- remaining (4) exceeds
        // ord_qty-tot_ccld_qty (3), an internally inconsistent response.
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\",\"ord_qty\":\"5\","
                        + "\"tot_ccld_qty\":\"2\",\"avg_idx\":\"350\",\"qty\":\"4\"}],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.queryOrder(order));
    }

    @Test
    void queryOrderThrowsWhenOrdQtyIsZero() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "5");
        adapter.submitOrder(order);
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\",\"ord_qty\":\"0\","
                        + "\"tot_ccld_qty\":\"0\",\"avg_idx\":\"0\",\"qty\":\"0\"}],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.queryOrder(order));
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
    void queryOrderFollowsPaginationToFindOrderOnASecondPage() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"999\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);

        // First page: doesn't contain the target order, but tr_cont="M"
        // response header says more pages exist -- queryOrder must follow
        // ctx_area_nk200 into a second request rather than giving up.
        server.queueResponse(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"111\",\"ord_qty\":\"1\","
                        + "\"tot_ccld_qty\":\"0\",\"avg_idx\":\"0\",\"qty\":\"1\"}],\"ctx_area_nk200\":\"PAGE2KEY\"}",
                "M");
        // Second (final) page: contains the target order, tr_cont="F".
        server.queueResponse(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"999\",\"ord_qty\":\"1\","
                        + "\"tot_ccld_qty\":\"1\",\"avg_idx\":\"351\",\"qty\":\"0\"}],\"ctx_area_nk200\":\"\"}",
                "F");

        OrderStatus status = adapter.queryOrder(order);

        assertEquals("999", status.exchangeOrderId());
        assertEquals("FILLED", status.status());
    }

    @Test
    void queryOrderThrowsWhenTrContMWithBlankContinuationKey() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        // tr_cont="M" (more pages) but ctx_area_nk200 is blank -- a real
        // anomaly this method can't resolve into an actual next page.
        server.queueResponse(
                200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[],\"ctx_area_nk200\":\"\"}", "M");

        assertThrows(ExchangeException.class, () -> adapter.queryOrder(order));
    }

    @Test
    void queryOrderSearchesFromYesterdayThroughToday() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{\"ODNO\":\"123\"}}");
        Order order = guardedMarketOrder(Side.LONG, "1");
        adapter.submitOrder(order);
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[{\"odno\":\"123\",\"ord_qty\":\"1\","
                        + "\"tot_ccld_qty\":\"0\",\"avg_idx\":\"0\",\"qty\":\"1\"}],\"output2\":{}}");

        adapter.queryOrder(order);

        String startDate = server.lastQueryParams().get("STRT_ORD_DT");
        String endDate = server.lastQueryParams().get("END_ORD_DT");
        assertTrue(startDate.compareTo(endDate) < 0, "STRT_ORD_DT must be strictly before END_ORD_DT: " + startDate + " vs " + endDate);
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
    void getPositionsThrowsWhenCblcQtyMissingRatherThanTreatingRowAsFlat() {
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":["
                        + "{\"pdno\":\"101W09\",\"sll_buy_dvsn_name\":\"매수\","
                        + "\"ccld_avg_unpr1\":\"350\",\"evlu_pfls_amt\":\"1500\"}"
                        + "],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.getPositions());
    }

    @Test
    void getPositionsThrowsWhenOutput1MissingRatherThanReturningEmptyList() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.getPositions());
    }

    @Test
    void getPositionsThrowsWhenPdnoMissing() {
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":["
                        + "{\"sll_buy_dvsn_name\":\"매수\",\"cblc_qty\":\"3\","
                        + "\"ccld_avg_unpr1\":\"350\",\"evlu_pfls_amt\":\"1500\"}"
                        + "],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.getPositions());
    }

    @Test
    void getPositionsThrowsWhenPdnoBlank() {
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":["
                        + "{\"pdno\":\"   \",\"sll_buy_dvsn_name\":\"매수\",\"cblc_qty\":\"3\","
                        + "\"ccld_avg_unpr1\":\"350\",\"evlu_pfls_amt\":\"1500\"}"
                        + "],\"output2\":{}}");

        assertThrows(ExchangeException.class, () -> adapter.getPositions());
    }

    @Test
    void getPositionsFollowsPaginationToFindAPositionOnASecondPage() {
        // First page: only a flat (zero-quantity) row, but tr_cont="M"
        // response header says more pages exist -- getPositions must
        // follow ctx_area_nk200 into a second request rather than
        // concluding the account has no open positions.
        server.queueResponse(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":["
                        + "{\"pdno\":\"101W12\",\"sll_buy_dvsn_name\":\"매도\",\"cblc_qty\":\"0\","
                        + "\"ccld_avg_unpr1\":\"0\",\"evlu_pfls_amt\":\"0\"}"
                        + "],\"ctx_area_nk200\":\"PAGE2KEY\"}",
                "M");
        // Second (final) page: contains a real open position, tr_cont="F".
        server.queueResponse(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":["
                        + "{\"pdno\":\"101W09\",\"sll_buy_dvsn_name\":\"매수\",\"cblc_qty\":\"3\","
                        + "\"ccld_avg_unpr1\":\"350\",\"evlu_pfls_amt\":\"1500\"}"
                        + "],\"ctx_area_nk200\":\"\"}",
                "F");

        List<PositionSnapshot> positions = adapter.getPositions();

        assertEquals(1, positions.size());
        assertEquals("101W09", positions.get(0).symbol());
    }

    @Test
    void getPositionsThrowsWhenTrContMWithBlankContinuationKey() {
        // tr_cont="M" (more pages) but ctx_area_nk200 is blank -- a real
        // anomaly this method can't resolve into an actual next page.
        server.queueResponse(
                200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[],\"ctx_area_nk200\":\"\"}", "M");

        assertThrows(ExchangeException.class, () -> adapter.getPositions());
    }

    @Test
    void getPositionsThrowsWhenPageLimitReachedWithMorePagesRemaining() {
        // Every page (up to the bound) reports tr_cont="M" with a real
        // continuation key -- KIS genuinely has more data than this
        // method's bounded loop will ever fetch. Must fail closed rather
        // than silently return an incomplete position list.
        for (int i = 0; i < 15; i++) {
            server.queueResponse(
                    200,
                    "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[],\"ctx_area_nk200\":\"KEY" + i + "\"}",
                    "M");
        }

        assertThrows(ExchangeException.class, () -> adapter.getPositions());
    }

    @Test
    void getPositionsParsesArrayAndSkipsZeroQuantityRows() {
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":["
                        + "{\"pdno\":\"101W09\",\"sll_buy_dvsn_name\":\"매수\",\"cblc_qty\":\"3\","
                        + "\"ccld_avg_unpr1\":\"350\",\"evlu_pfls_amt\":\"1500\"},"
                        + "{\"pdno\":\"101W12\",\"sll_buy_dvsn_name\":\"매도\",\"cblc_qty\":\"0\","
                        + "\"ccld_avg_unpr1\":\"0\",\"evlu_pfls_amt\":\"0\"}"
                        + "],\"output2\":{}}");

        List<PositionSnapshot> positions = adapter.getPositions();

        assertEquals(1, positions.size(), "a zero-quantity row is not a real open position");
        assertEquals("101W09", positions.get(0).symbol());
        assertEquals(0, new BigDecimal("3").compareTo(positions.get(0).positionAmt()));
        assertEquals(0, new BigDecimal("350").compareTo(positions.get(0).avgPrice()));
        assertNull(positions.get(0).leverage(), "no user-settable leverage concept for this venue");
        assertEquals("/uapi/domestic-futureoption/v1/trading/inquire-balance", server.lastPath());
        assertEquals("VTFO6118R", server.lastTrIdHeader());
    }

    @Test
    void getBalanceParsesResponse() {
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[],\"output2\":{\"tot_dncl_amt\":\"10000000\","
                        + "\"prsm_dpast_amt\":\"10050000\",\"ord_psbl_cash\":\"9500000\",\"mgna_tota\":\"500000\","
                        + "\"evlu_pfls_amt_smtl\":\"50000\"}}");

        BalanceSnapshot balance = adapter.getBalance();

        assertEquals(0, new BigDecimal("10000000").compareTo(balance.balance()));
        assertEquals(0, new BigDecimal("10050000").compareTo(balance.equity()));
        assertEquals(0, new BigDecimal("9500000").compareTo(balance.availableMargin()));
        assertEquals(0, new BigDecimal("500000").compareTo(balance.usedMargin()));
        assertEquals(0, new BigDecimal("50000").compareTo(balance.unrealizedProfit()));
        assertEquals("KRW", balance.asset());
        assertEquals("/uapi/domestic-futureoption/v1/trading/inquire-balance", server.lastPath());
        assertEquals("VTFO6118R", server.lastTrIdHeader());
    }

    @Test
    void getBalanceThrowsWhenOutput2MissingRatherThanReturningNullAmounts() {
        server.respondWith(200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[]}");

        assertThrows(ExchangeException.class, () -> adapter.getBalance());
    }

    @Test
    void getBalanceThrowsWhenOutput2MissingAField() {
        server.respondWith(
                200,
                "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[],\"output2\":{\"tot_dncl_amt\":\"10000000\","
                        + "\"prsm_dpast_amt\":\"10050000\",\"ord_psbl_cash\":\"9500000\",\"mgna_tota\":\"500000\"}}");

        assertThrows(ExchangeException.class, () -> adapter.getBalance());
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
                200, "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":[],\"output2\":{\"tot_dncl_amt\":\"1\","
                        + "\"prsm_dpast_amt\":\"1\",\"ord_psbl_cash\":\"1\",\"mgna_tota\":\"1\",\"evlu_pfls_amt_smtl\":\"1\"}}");

        adapter.getBalance();

        assertEquals("Bearer fake-token", server.lastAuthorizationHeader());
        assertEquals("test-app-key", server.lastAppKeyHeader());
        assertEquals("test-app-secret", server.lastAppSecretHeader());
        assertEquals("VTFO6118R", server.lastTrIdHeader());
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
