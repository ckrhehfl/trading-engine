package engine.exchange;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import engine.oms.Order;
import engine.schemas.OrderType;
import engine.schemas.Side;
import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.format.DateTimeFormatter;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * {@link ExchangeAdapter} implementation for 한국투자증권(Korea Investment
 * & Securities, "KIS")'s domestic futures/options REST API -- originally
 * built for KOSPI200 index futures specifically (see CLAUDE.md's
 * "KIS/KOSPI200 venue integration, Phase 1" section for the full design),
 * <b>confirmed venue-generic across KIS's own domestic futures/options
 * product line, not KOSPI200-specific</b>: the order/cancel/query/balance/
 * positions endpoints below are the exact same endpoints, same {@code
 * tr_id}s, for any domestic futures/options symbol KIS lists (index
 * futures, individual-stock futures, ...), distinguished only by the
 * {@code SHTN_PDNO}/{@code PDNO} symbol value itself -- confirmed directly
 * against KIS's own official example source (their {@code order}
 * function's own docstring uses the identical parameter regardless of
 * underlying). This class needed no change to support that extension; see
 * CLAUDE.md's own "Scope extension beyond KOSPI200 index futures" for what
 * did need one ({@code engine.runtime.KisPriceFeed}'s quote lookup, a
 * different class). Endpoint paths,
 * request parameters, and the OAuth2 auth flow are taken directly from
 * KIS's own official {@code koreainvestment/open-trading-api} GitHub
 * repository (verified 2026-08 against the real, current source there,
 * not assumed from secondary docs) -- but this project has no real KIS
 * credentials yet as of this writing, so (unlike request parameters,
 * confirmed against real source) <b>response field names below are this
 * class's own best-effort inference from KIS's documented naming
 * convention, not yet empirically verified against a real response</b>.
 * Treat this class's parsing behavior with the same reduced confidence
 * CLAUDE.md already applies to BingX facts marked "documented, not yet
 * empirically verified," until exercised against a real KIS paper
 * account (Task 4's own real-verification step).
 *
 * <p><b>Real verification against KIS's live paper-trading API (2026-08-21)
 * found response field names above were wrong in two distinct ways, both
 * corrected here rather than left as the original inference</b>: (1)
 * response field-name <b>casing is genuinely per-endpoint, not a single
 * project-wide convention</b> -- confirmed directly against KIS's own
 * official {@code koreainvestment/open-trading-api} example source
 * (each endpoint's own {@code COLUMN_MAPPING}, not assumed): {@code order}
 * (submit) responds with UPPERCASE fields ({@code ODNO}, matching this
 * class's original guess), while {@code inquire-balance} and {@code
 * inquire-ccnl} both respond with lowercase fields ({@code pdno}, {@code
 * cblc_qty}, {@code odno}, {@code tot_ccld_qty}, ...) -- the original
 * uppercase guess for those two was wrong and would have silently parsed
 * every field as {@code null} ({@link com.fasterxml.jackson.databind.JsonNode}
 * field lookup is case-sensitive) rather than throwing, since {@link
 * #parseBigDecimal} treats a missing field as legitimately absent, not an
 * error. Request-parameter casing (always UPPERCASE, e.g. {@code CANO},
 * {@code ACNT_PRDT_CD}) is unaffected -- this was a response-parsing-only
 * bug. (2) {@code getBalance()} originally called {@code inquire-deposit}
 * (TR {@code CTRP6550R}) under the (now-disproven) assumption that its
 * {@code tr_id} was shared between real and paper trading -- a real call
 * against the paper host returned {@code HTTP 500}/{@code EGW00205}
 * ("credentials_type이 유효하지 않습니다.(Bearer)"), and a manually
 * {@code V}-prefixed variant ({@code VTRP6550R}) returned {@code
 * OPSQ0002} ("없는 서비스 코드 입니다") -- i.e. {@code inquire-deposit} has
 * no working paper-trading counterpart at all. {@code getBalance()} now
 * reuses the same {@code inquire-balance}/{@code VTFO6118R} call {@link
 * #getPositions()} already made successfully, reading its {@code output2}
 * (account-level summary) object instead.
 *

 * <p><b>Paper-only by design, not by convention</b>: every {@code tr_id}
 * constant below is hardcoded to its KIS-assigned demo/paper-trading
 * value (the {@code V}-prefixed forms KIS's own real/demo TR-id
 * convention uses) -- there is no branch, flag, or parameter anywhere in
 * this class that could select a real-trading {@code tr_id} instead.
 * This project has no live KIS trading path planned (see CLAUDE.md's own
 * framing of this whole phase as paper-only infrastructure) — closing
 * off that path structurally, the same "eliminate the configuration
 * surface, don't validate it" principle CLAUDE.md's BingX VST design
 * already applies to the VST host itself, applied here one layer earlier
 * (the class itself, not just whatever factory constructs it). If a live
 * KIS path is ever genuinely planned, extending this class with a real
 * {@code tr_id} branch is a deliberate, separately-reviewed future
 * change -- not a config value anyone can flip today.
 *
 * <p>Auth is KIS's OAuth2 bearer-token scheme via the shared {@link
 * KisTokenProvider} (see that class's own Javadoc for why it's a
 * separate, shared collaborator rather than folded into this class) --
 * materially different from {@link BingXAdapter}'s stateless per-request
 * HMAC signing. Every request also carries {@code appkey}/{@code
 * appsecret} headers (from the same {@code KisTokenProvider}) alongside
 * the bearer token, {@code custtype: P} (individual, not corporate,
 * customer), and an endpoint-specific {@code tr_id} header.
 *
 * <p>{@code setLeverage}/{@code setPositionMode}: KRX futures margin is
 * exchange-mandated, not a user-settable multiplier the way BingX's
 * leverage API is, and KRX has no BingX-style hedge/one-way position-mode
 * concept at all (one net position per contract). Both methods here
 * explicitly log at WARN and return without making any request --
 * deliberately not a network no-op that could be mistaken for "nothing
 * needed doing," and deliberately not {@link UnsupportedOperationException}
 * either, so this stays a same-shaped {@code ExchangeAdapter} as {@link
 * BingXAdapter} rather than one that throws where the other one succeeds.
 * Real risk enforcement for this venue is <b>not</b> these two methods --
 * it's the notional-value conversion {@code RiskGateway} must apply using
 * KOSPI200's real ₩250,000-per-index-point contract multiplier (see
 * CLAUDE.md's own {@code RiskLimits} section for this design) -- callers
 * (the KIS factory, {@code KisPreflight}) must not treat a normal return
 * from either method here as "leverage/position-mode protection applied."
 */
public final class KisAdapter implements ExchangeAdapter {

    private static final Logger log = LoggerFactory.getLogger(KisAdapter.class);

    private static final String ORDER_PATH = "/uapi/domestic-futureoption/v1/trading/order";
    private static final String ORDER_CANCEL_PATH = "/uapi/domestic-futureoption/v1/trading/order-rvsecncl";
    private static final String INQUIRE_CCNL_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-ccnl";
    private static final String INQUIRE_BALANCE_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-balance";

    // Demo/paper-only tr_id values -- see class Javadoc, "Paper-only by
    // design". Real-trading equivalents (TTTO1101U, TTTO1103U, TTTO5201R,
    // CTFO6118R) deliberately do not appear anywhere in this class.
    private static final String TR_ID_ORDER = "VTTO1101U";
    private static final String TR_ID_ORDER_CANCEL = "VTTO1103U";
    private static final String TR_ID_INQUIRE_CCNL = "VTTO5201R";
    private static final String TR_ID_INQUIRE_BALANCE = "VTFO6118R";

    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(10);
    private static final ZoneId KST = ZoneId.of("Asia/Seoul");
    private static final DateTimeFormatter ORDER_DATE_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd");
    // Bounded pagination for queryOrder's inquire-ccnl loop -- see that
    // method's own comment for why this mirrors KIS's own official Python
    // sample's max_depth=10 guard.
    private static final int MAX_INQUIRE_CCNL_PAGES = 10;

    private final KisTokenProvider tokenProvider;
    private final String accountNo;
    private final String accountProductCode;
    private final String baseUrl;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    /**
     * {@code accountNo} is KIS's 8-digit {@code CANO}, {@code
     * accountProductCode} its 2-digit {@code ACNT_PRDT_CD} (KIS's real
     * source uses {@code "03"} for a futures/options account) -- unlike
     * {@link BingXAdapter}, KIS ties every trading request to an explicit
     * account, not the API key alone.
     */
    public KisAdapter(KisTokenProvider tokenProvider, String accountNo, String accountProductCode, String baseUrl) {
        this.tokenProvider = Objects.requireNonNull(tokenProvider, "tokenProvider is required");
        this.accountNo = Objects.requireNonNull(accountNo, "accountNo is required").strip();
        this.accountProductCode = Objects.requireNonNull(accountProductCode, "accountProductCode is required").strip();
        this.baseUrl = Objects.requireNonNull(baseUrl, "baseUrl is required");
        this.httpClient = HttpClient.newBuilder().connectTimeout(REQUEST_TIMEOUT).build();
    }

    /**
     * <b>Ambiguous-submission recovery is a real, disclosed gap, not yet
     * closed for this adapter</b> (raised on real CodeRabbit review; deferred
     * to Task 4, not fixed here, since Task 2 has zero live wiring and
     * cannot itself exercise this path). KIS's real order request (below)
     * has <b>no client-supplied idempotency key</b> -- unlike BingX's own
     * {@code clientOrderID} field (confirmed, via this project's own real
     * VST verification, to give BingX real server-side duplicate-submission
     * rejection), nothing in KIS's official request parameters lets a
     * resubmission be recognized as "the same order" by KIS itself. If a
     * network failure happens after KIS has genuinely accepted an order but
     * before this method observes the response, the resulting {@code Order}
     * is left {@code SUBMITTED} with no {@code exchangeOrderId} -- and
     * {@link #queryOrder} cannot resolve it (it searches {@code
     * inquire-ccnl} by {@code exchangeOrderId}, which doesn't exist yet in
     * this scenario). Before KIS is ever wired into a live-submitting
     * {@code ExchangeOrderExecutor} (Task 4), a real resolution path must
     * exist -- e.g. matching a pending {@code Order} against {@code
     * inquire-ccnl}'s full result set by symbol/side/quantity/time rather
     * than by ID, or an explicit manual-confirmation step -- and must never
     * be "just resubmit and hope."
     */
    @Override
    public void submitOrder(Order order) {
        Objects.requireNonNull(order, "order is required");
        order.submit();

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("ORD_PRCS_DVSN_CD", "02");
        body.put("CANO", accountNo);
        body.put("ACNT_PRDT_CD", accountProductCode);
        body.put("SLL_BUY_DVSN_CD", kisSide(order.side()));
        body.put("SHTN_PDNO", order.symbol());
        body.put("ORD_QTY", wholeContractCount(order.approvedQuantity()).toString());
        // GUARDED_MARKET is sent as a real, wire-level UNPROTECTED market
        // order (UNIT_PRICE="0") when limitPrice() is null -- flagged
        // prominently on real CodeRabbit review as a Critical concern, not
        // silently accepted: neither KIS's own order API nor this codebase's
        // RiskGateway/ExchangeOrderExecutor enforce a fill-price bound at
        // the wire level for this order type. This mirrors BingXAdapter's
        // own already-shipped "MARKET" mapping exactly (see that class's own
        // "no distinct guarded market order type" comment) -- so this is a
        // pre-existing characteristic of this project's order-guard design
        // as a whole, not something Task 2 introduces fresh for KIS
        // specifically. It is exactly what CLAUDE.md's own Live Entry
        // Criteria "market-order guard enabled" line exists to gate before
        // any live (non-paper) order flow -- that verification has not
        // happened for either adapter yet and must, as its own dedicated
        // Discuss, before GUARDED_MARKET is ever used against a real account
        // through this adapter.
        body.put("UNIT_PRICE", order.limitPrice() != null ? order.limitPrice().toPlainString() : "0");
        body.put("NMPR_TYPE_CD", kisQuoteTypeCode(order.orderType()));
        body.put("KRX_NMPR_CNDT_CD", "0");
        body.put("ORD_DVSN_CD", kisOrderDivisionCode(order.orderType()));
        body.put("CTAC_TLNO", "");
        body.put("FUOP_ITEM_DVSN_CD", "");

        // A network/HTTP-level failure here leaves the order exactly where
        // submit() already put it (SUBMITTED) -- matches BingXAdapter's own
        // reasoning: we genuinely don't know whether KIS received the
        // order. See this method's own top-level Javadoc for why that
        // ambiguity is harder to resolve for KIS than it is for BingX.
        JsonNode root = request("POST", ORDER_PATH, TR_ID_ORDER, body, Map.of());

        if (!isSuccess(root)) {
            String reason = errorMessage(root);
            log.info("KIS rejected order {}: {}", order.clientOrderId(), reason);
            order.reject(reason);
            return;
        }
        String exchangeOrderId = extractOrderId(root.path("output"));
        log.info("KIS acknowledged order {} as {}", order.clientOrderId(), exchangeOrderId);
        order.acknowledge(exchangeOrderId);
    }

    @Override
    public void cancelOrder(Order order) {
        Objects.requireNonNull(order, "order is required");
        order.requestCancel();

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("ORD_PRCS_DVSN_CD", "02");
        body.put("CANO", accountNo);
        body.put("ACNT_PRDT_CD", accountProductCode);
        body.put("RVSE_CNCL_DVSN_CD", "02"); // 02: cancel (01 would be modify -- not used by this codebase)
        body.put("ORGN_ODNO", order.exchangeOrderId());
        body.put("ORD_QTY", "0"); // 0: cancel the full remaining quantity
        body.put("UNIT_PRICE", "0");
        body.put("NMPR_TYPE_CD", "02");
        body.put("KRX_NMPR_CNDT_CD", "0");
        body.put("RMN_QTY_YN", "Y");
        body.put("ORD_DVSN_CD", "02");
        body.put("FUOP_ITEM_DVSN_CD", "");

        JsonNode root = request("POST", ORDER_CANCEL_PATH, TR_ID_ORDER_CANCEL, body, Map.of());

        if (!isSuccess(root)) {
            // Matches BingXAdapter's own reasoning: Order has no "cancel
            // failed, revert to previous state" transition, so this must
            // throw rather than attempt a state change that doesn't exist.
            throw new ExchangeException(
                    "KIS rejected cancel for order " + order.clientOrderId() + ": " + errorMessage(root));
        }
        log.info("KIS confirmed cancel for order {}", order.clientOrderId());
        order.confirmCancel();
    }

    @Override
    public OrderStatus queryOrder(Order order) {
        Objects.requireNonNull(order, "order is required");

        // Looks back to yesterday, not just today (tightened on real
        // CodeRabbit review): a bare "today in KST" window misses an order
        // placed shortly before local midnight when queried shortly after
        // it -- a real, not hypothetical, boundary given this loop's tick
        // cadence and KIS's own KST-local date semantics for this endpoint.
        String startDate = java.time.LocalDate.now(KST).minusDays(1).format(ORDER_DATE_FORMAT);
        String endDate = java.time.LocalDate.now(KST).format(ORDER_DATE_FORMAT);

        JsonNode matched = null;
        String continuationKey = "";
        boolean firstPage = true;
        // Bounded, matching KIS's own official Python sample's own
        // max_depth=10 recursive-continuation guard -- real pagination
        // never legitimately runs this long for one account/day-range, so
        // hitting the bound means stopping rather than looping unbounded.
        for (int page = 0; page < MAX_INQUIRE_CCNL_PAGES && matched == null; page++) {
            Map<String, String> query = new LinkedHashMap<>();
            query.put("CANO", accountNo);
            query.put("ACNT_PRDT_CD", accountProductCode);
            query.put("STRT_ORD_DT", startDate);
            query.put("END_ORD_DT", endDate);
            query.put("SLL_BUY_DVSN_CD", "00");
            query.put("CCLD_NCCS_DVSN", "00");
            query.put("SORT_SQN", "DS");
            query.put("PDNO", "");
            query.put("STRT_ODNO", "");
            query.put("MKET_ID_CD", "");
            query.put("CTX_AREA_FK200", "");
            query.put("CTX_AREA_NK200", continuationKey);

            // Pure read: order is never touched below, regardless of outcome.
            HttpResponse<String> response =
                    sendRequest("GET", INQUIRE_CCNL_PATH, TR_ID_INQUIRE_CCNL, Map.of(), query, firstPage ? "" : "N");
            JsonNode root = parseBody(INQUIRE_CCNL_PATH, response);

            if (!isSuccess(root)) {
                throw new ExchangeException(
                        "KIS queryOrder failed for order " + order.clientOrderId() + ": " + errorMessage(root));
            }
            matched = findOrderInOutput1(root.path("output1"), order.exchangeOrderId());
            String trCont = response.headers().firstValue("tr_cont").orElse("");
            if (!"M".equals(trCont) && !"F".equals(trCont)) {
                break; // no further pages
            }
            continuationKey = root.path("ctx_area_nk200").asText("");
            firstPage = false;
        }
        if (matched == null) {
            throw new ExchangeException(
                    "KIS queryOrder: order " + order.exchangeOrderId() + " not found in inquire-ccnl output1 for "
                            + startDate + "-" + endDate);
        }
        return toOrderStatus(matched);
    }

    @Override
    public List<PositionSnapshot> getPositions() {
        Map<String, String> query = new LinkedHashMap<>();
        query.put("CANO", accountNo);
        query.put("ACNT_PRDT_CD", accountProductCode);
        query.put("MGNA_DVSN", "01");
        query.put("EXCC_STAT_CD", "1");

        JsonNode root = request("GET", INQUIRE_BALANCE_PATH, TR_ID_INQUIRE_BALANCE, Map.of(), query);
        if (!isSuccess(root)) {
            throw new ExchangeException("KIS getPositions failed: " + errorMessage(root));
        }
        List<PositionSnapshot> positions = new ArrayList<>();
        for (JsonNode node : root.path("output1")) {
            // output1 field names are lowercase -- confirmed against KIS's
            // own real response and official column mapping, see class
            // Javadoc's "Real verification..." disclosure.
            BigDecimal quantity = parseBigDecimal(node, "cblc_qty");
            // A zero-quantity row (no open position for that product) isn't
            // a real open position -- skipping it matches PositionSnapshot's
            // own implicit contract elsewhere in this codebase (BingXAdapter
            // never emits a snapshot for a flat symbol either).
            if (quantity == null || quantity.signum() == 0) {
                continue;
            }
            positions.add(new PositionSnapshot(
                    node.path("pdno").asText(),
                    node.path("sll_buy_dvsn_name").asText(),
                    quantity,
                    // ccld_avg_unpr1 ("체결평균단가1", average execution
                    // price) -- the original PCHS_UNPR guess doesn't appear
                    // in KIS's own official output1 column mapping at all.
                    parseBigDecimal(node, "ccld_avg_unpr1"),
                    // No BingX-style user-settable leverage multiplier for
                    // this venue -- see class Javadoc. Left null (matching
                    // parseBigDecimal's own missing-field convention) rather
                    // than a fabricated value.
                    null,
                    parseBigDecimal(node, "evlu_pfls_amt"),
                    null));
        }
        return positions;
    }

    /**
     * Reuses the same {@code inquire-balance}/{@code VTFO6118R} call {@link
     * #getPositions()} already makes successfully, reading its {@code
     * output2} (account-level cash/margin/P&amp;L summary) object instead
     * of {@code output1} -- see class Javadoc's "Real verification..."
     * disclosure for why {@code inquire-deposit} (this method's original
     * data source) doesn't work here at all. {@code output2} field ->
     * {@link BalanceSnapshot} field mapping (KIS's own official column
     * names/descriptions, human-confirmed, no exact 1:1 semantic match
     * exists for every field so this is a best reasonable correspondence,
     * not a guaranteed-identical concept):
     * {@code tot_dncl_amt} (총예수금액/total deposit amount) -> {@code
     * balance}; {@code prsm_dpast_amt} (추정예탁자산금액/estimated total
     * account assets, i.e. deposit + P&amp;L) -> {@code equity}; {@code
     * ord_psbl_cash} (주문가능현금/order-available cash) -> {@code
     * availableMargin}; {@code mgna_tota} (증거금총액/total margin) ->
     * {@code usedMargin}; {@code evlu_pfls_amt_smtl} (평가손익금액합계/total
     * evaluation P&amp;L) -> {@code unrealizedProfit}.
     */
    @Override
    public BalanceSnapshot getBalance() {
        Map<String, String> query = new LinkedHashMap<>();
        query.put("CANO", accountNo);
        query.put("ACNT_PRDT_CD", accountProductCode);
        query.put("MGNA_DVSN", "01");
        query.put("EXCC_STAT_CD", "1");

        JsonNode root = request("GET", INQUIRE_BALANCE_PATH, TR_ID_INQUIRE_BALANCE, Map.of(), query);
        if (!isSuccess(root)) {
            throw new ExchangeException("KIS getBalance failed: " + errorMessage(root));
        }
        JsonNode output2 = root.path("output2");
        return new BalanceSnapshot(
                parseBigDecimal(output2, "tot_dncl_amt"),
                parseBigDecimal(output2, "prsm_dpast_amt"),
                parseBigDecimal(output2, "ord_psbl_cash"),
                parseBigDecimal(output2, "mgna_tota"),
                parseBigDecimal(output2, "evlu_pfls_amt_smtl"),
                "KRW");
    }

    /**
     * No BingX-style user-settable leverage multiplier exists for KRX
     * futures -- see class Javadoc. Deliberately never sends a request.
     */
    @Override
    public void setLeverage(String symbol, Side side, int leverage) {
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(side, "side is required");
        log.warn(
                "KisAdapter.setLeverage is a no-op for {}: KRX futures margin is exchange-mandated, not a"
                        + " user-settable multiplier -- see KisAdapter's own class Javadoc. Callers must not treat"
                        + " this as leverage protection having been applied.",
                symbol);
    }

    /**
     * No BingX-style hedge/one-way position-mode concept exists for KRX
     * futures (one net position per contract) -- see class Javadoc.
     * Deliberately never sends a request.
     */
    @Override
    public void setPositionMode(PositionMode mode) {
        Objects.requireNonNull(mode, "mode is required");
        log.warn(
                "KisAdapter.setPositionMode is a no-op: KRX futures has no hedge/one-way position-mode concept --"
                        + " see KisAdapter's own class Javadoc. Callers must not treat this as position-mode"
                        + " protection having been applied.");
    }

    /**
     * Signs (via the shared {@link KisTokenProvider}) and sends one
     * request, returning the parsed JSON body. {@code jsonBody} is used for
     * POST requests (sent as the request body); {@code queryParams} for GET
     * requests (sent as the query string) -- a request uses exactly one of
     * the two, matching the shape every simple (non-paginated) caller above
     * has. Convenience wrapper over {@link #sendRequest}/{@link #parseBody}
     * for callers that don't need the raw {@link HttpResponse} itself (see
     * {@link #queryOrder}, which does, for pagination's {@code tr_cont}
     * response header).
     */
    private JsonNode request(
            String httpMethod, String path, String trId, Map<String, Object> jsonBody, Map<String, String> queryParams) {
        return parseBody(path, sendRequest(httpMethod, path, trId, jsonBody, queryParams, ""));
    }

    /**
     * Lower-level primitive: attaches every shared KIS header (bearer token,
     * appkey/appsecret, tr_id, custtype, and tr_cont for pagination
     * continuation) and sends the request, returning the raw {@link
     * HttpResponse} so a caller that needs a response header (only {@link
     * #queryOrder}, for {@code tr_cont}) can read it -- {@link #request}
     * itself only ever needs the parsed body.
     */
    private HttpResponse<String> sendRequest(
            String httpMethod,
            String path,
            String trId,
            Map<String, Object> jsonBody,
            Map<String, String> queryParams,
            String trCont) {
        String token = tokenProvider.currentToken();

        HttpRequest.Builder builder = HttpRequest.newBuilder()
                .timeout(REQUEST_TIMEOUT)
                .header("Content-Type", "application/json")
                .header("Accept", "text/plain")
                .header("authorization", "Bearer " + token)
                .header("appkey", tokenProvider.appKey())
                .header("appsecret", tokenProvider.appSecret())
                .header("tr_id", trId)
                .header("custtype", "P")
                .header("tr_cont", trCont);

        HttpRequest httpRequest =
                switch (httpMethod) {
                    case "GET" -> builder.uri(URI.create(baseUrl + path + "?" + queryString(queryParams)))
                            .GET()
                            .build();
                    case "POST" -> builder.uri(URI.create(baseUrl + path))
                            .POST(HttpRequest.BodyPublishers.ofString(toJson(jsonBody)))
                            .build();
                    default -> throw new IllegalArgumentException("unsupported HTTP method: " + httpMethod);
                };

        try {
            return httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new ExchangeException("KIS request failed: I/O error calling " + path, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ExchangeException("KIS request interrupted calling " + path, e);
        }
    }

    /**
     * Validates the HTTP status and parses the body -- deliberately does
     * NOT log/embed {@code response.body()} anywhere (tightened on real
     * CodeRabbit review): unlike BingX's own equivalent log line
     * ({@code BingXAdapter}'s own Javadoc explains why it's safe there --
     * BingX's response envelope never echoes request credentials or
     * account-identifying data back), a real KIS response body can carry
     * account numbers and balance/deposit amounts ({@code CANO}, {@code
     * DNCA_TOT_AMT}, etc.) -- CLAUDE.md's own rule against logging account
     * identifiers applies here even though this is a transient log line,
     * not a committed file. {@code rt_cd}/{@code msg_cd} alone are enough
     * to diagnose a failure without carrying that risk.
     */
    private JsonNode parseBody(String path, HttpResponse<String> response) {
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new ExchangeException("KIS request to " + path + " returned HTTP " + response.statusCode());
        }
        JsonNode root;
        try {
            root = objectMapper.readTree(response.body());
        } catch (IOException e) {
            throw new ExchangeException("KIS response body for " + path + " is not valid JSON", e);
        }
        log.debug("KIS response for {}: HTTP {} rt_cd={}", path, response.statusCode(), root.path("rt_cd").asText("?"));
        return root;
    }

    private String toJson(Map<String, Object> body) {
        try {
            return objectMapper.writeValueAsString(body);
        } catch (IOException e) {
            throw new ExchangeException("failed to serialize KIS request body: " + body, e);
        }
    }

    private static String queryString(Map<String, String> params) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> entry : params.entrySet()) {
            if (sb.length() > 0) {
                sb.append('&');
            }
            sb.append(java.net.URLEncoder.encode(entry.getKey(), java.nio.charset.StandardCharsets.UTF_8))
                    .append('=')
                    .append(java.net.URLEncoder.encode(entry.getValue(), java.nio.charset.StandardCharsets.UTF_8));
        }
        return sb.toString();
    }

    /**
     * KOSPI200 futures quantity is a contract count, never fractional.
     * {@code RiskGateway}'s own contract-multiplier conversion (CLAUDE.md's
     * {@code RiskLimits} section) is what's actually responsible for
     * enforcing this upstream -- this is a defensive last check at the wire
     * boundary, not a substitute for that. <b>Rejects, does not truncate</b>
     * (tightened on real CodeRabbit review): {@link BigDecimal#toBigInteger()}
     * silently discards any fractional part -- {@code 1.7} would have
     * silently become {@code "1"} on the wire, a real quantity mismatch
     * between what {@code RiskGateway} approved and what KIS actually
     * receives, not merely a formatting nicety.
     */
    private static java.math.BigInteger wholeContractCount(BigDecimal approvedQuantity) {
        java.math.BigInteger contracts;
        try {
            contracts = approvedQuantity.stripTrailingZeros().toBigIntegerExact();
        } catch (ArithmeticException e) {
            throw new ExchangeException(
                    "KIS order quantity must be a whole contract count, got " + approvedQuantity.toPlainString(), e);
        }
        if (contracts.signum() <= 0) {
            throw new ExchangeException(
                    "KIS order quantity must be positive, got " + approvedQuantity.toPlainString());
        }
        return contracts;
    }

    /** {@code "01"} sell / {@code "02"} buy -- opening a SHORT is a sell, opening a LONG is a buy; KRX futures has no separate hedge-mode position-side field to also set (see class Javadoc). */
    private static String kisSide(Side side) {
        return side == Side.LONG ? "02" : "01";
    }

    /** {@code "01"} limit / {@code "02"} market -- KIS has no distinct "guarded market" type, same as BingX. */
    private static String kisQuoteTypeCode(OrderType orderType) {
        return orderType == OrderType.LIMIT ? "01" : "02";
    }

    private static String kisOrderDivisionCode(OrderType orderType) {
        return orderType == OrderType.LIMIT ? "01" : "02";
    }

    /** {@code rt_cd == "0"} is KIS's own real success convention (confirmed against real source, see class Javadoc). */
    private static boolean isSuccess(JsonNode root) {
        return "0".equals(root.path("rt_cd").asText(null));
    }

    private static String errorMessage(JsonNode root) {
        String msg = root.path("msg1").asText("");
        String code = root.path("msg_cd").asText("");
        return msg.isBlank() ? ("KIS error, msg_cd=" + code) : (msg + " (msg_cd=" + code + ")");
    }

    /**
     * {@code output}'s order-number field -- inferred as {@code ODNO} from
     * KIS's own naming convention (the cancel request's {@code ORGN_ODNO},
     * "original order number", confirmed against real source, implies the
     * order-response field it refers back to is named {@code ODNO}) but
     * <b>not yet confirmed against a real response body</b> -- see class
     * Javadoc's top-level disclosure.
     */
    private static String extractOrderId(JsonNode outputNode) {
        JsonNode odno = outputNode.get("ODNO");
        if (odno == null || odno.isNull()) {
            throw new ExchangeException("KIS order response missing 'ODNO' field: " + outputNode);
        }
        return odno.asText();
    }

    // output1 field names are lowercase for inquire-ccnl -- confirmed
    // against KIS's own official column mapping, see class Javadoc's "Real
    // verification..." disclosure.
    private static JsonNode findOrderInOutput1(JsonNode output1, String exchangeOrderId) {
        for (JsonNode node : output1) {
            if (exchangeOrderId.equals(node.path("odno").asText(null))) {
                return node;
            }
        }
        return null;
    }

    /**
     * Derives a normalized status string matching {@code BingXAdapter}'s own
     * status-value convention ({@code NEW}/{@code PARTIALLY_FILLED}/{@code
     * FILLED}/{@code CANCELLED}) from a quantity comparison, rather than
     * trusting a raw KIS status field -- deliberately, so {@code
     * ExchangeOrderExecutor}'s existing status-mapping table (built once,
     * for BingX) can be reused unmodified for this adapter too, without
     * needing its own KIS-specific branch.
     *
     * <p><b>Cancellation detection was corrected on real verification</b>:
     * the original {@code CNCL_YN} field this class guessed does not exist
     * anywhere in {@code inquire-ccnl}'s real, official response (confirmed
     * against KIS's own column mapping) -- it always resolved to {@code
     * false}, silently. Replaced with a real-field-based heuristic: {@code
     * qty} ("잔량", remaining/un-filled quantity) reaching zero without the
     * order having fully filled means the remainder was cancelled or
     * rejected by the exchange -- both map to {@code CANCELLED} here,
     * matching this adapter's own established "reject-after-fill maps to
     * the cancel path" convention (see {@link #submitOrder}'s own Javadoc
     * for the identical reasoning applied to a different case). {@code
     * avg_idx} ("평균지수", average index) is the closest real field to an
     * average fill price for an index-futures contract; no separate
     * "average price" field exists in the real response.
     */
    private static OrderStatus toOrderStatus(JsonNode node) {
        BigDecimal orderedQty = parseBigDecimal(node, "ord_qty");
        BigDecimal filledQty = parseBigDecimal(node, "tot_ccld_qty");
        BigDecimal avgPrice = parseBigDecimal(node, "avg_idx");
        BigDecimal remainingQty = parseBigDecimal(node, "qty");

        boolean fullyFilled = orderedQty != null && filledQty != null && filledQty.compareTo(orderedQty) >= 0;
        boolean noRemainder = remainingQty == null || remainingQty.signum() == 0;
        boolean filledSomething = filledQty != null && filledQty.signum() > 0;

        String status;
        if (fullyFilled) {
            status = "FILLED";
        } else if (noRemainder) {
            // Remaining quantity exhausted without the order having fully
            // filled -- the rest was cancelled or rejected by KIS.
            status = "CANCELLED";
        } else if (filledSomething) {
            status = "PARTIALLY_FILLED";
        } else {
            status = "NEW";
        }

        return new OrderStatus(node.path("odno").asText(), status, filledQty, avgPrice);
    }

    private static BigDecimal parseBigDecimal(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        String text = value.asText();
        if (text.isBlank()) {
            return null;
        }
        try {
            return new BigDecimal(text);
        } catch (NumberFormatException e) {
            throw new ExchangeException("KIS response field '" + field + "' is not a valid decimal: '" + text + "'", e);
        }
    }
}
