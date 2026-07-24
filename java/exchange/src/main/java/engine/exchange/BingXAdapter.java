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
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * {@link ExchangeAdapter} implementation for BingX USDT-M perpetual swap
 * futures. Endpoint paths, auth scheme, and response envelope are per
 * CLAUDE.md's "Exchange API Facts — BingX" (Documented, not yet empirically
 * verified) — treat this class's behavior against real BingX responses with
 * the same reduced confidence as that section, until exercised against a
 * live VST key.
 *
 * <p>Deliberately takes {@code baseUrl} as a plain constructor argument and
 * does no environment reading itself: the caller decides which host to
 * point at (VST demo-trading vs production — see CLAUDE.md's "Exchange API
 * Facts — BingX" for the actual hostnames, deliberately not repeated here)
 * and passes it in. There is no live/paper flag anywhere in this class.
 */
public final class BingXAdapter implements ExchangeAdapter {

    private static final Logger log = LoggerFactory.getLogger(BingXAdapter.class);

    private static final String ORDER_PATH = "/openApi/swap/v2/trade/order";
    private static final String POSITIONS_PATH = "/openApi/swap/v2/user/positions";
    private static final String BALANCE_PATH = "/openApi/swap/v3/user/balance";
    private static final String LEVERAGE_PATH = "/openApi/swap/v2/trade/leverage";
    private static final String POSITION_MODE_PATH = "/openApi/swap/v1/positionSide/dual";

    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(10);

    private final String apiKey;
    private final String apiSecret;
    private final String baseUrl;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public BingXAdapter(String apiKey, String apiSecret, String baseUrl) {
        this.apiKey = Objects.requireNonNull(apiKey, "apiKey is required");
        this.apiSecret = Objects.requireNonNull(apiSecret, "apiSecret is required");
        this.baseUrl = Objects.requireNonNull(baseUrl, "baseUrl is required");
        this.httpClient = HttpClient.newBuilder().connectTimeout(REQUEST_TIMEOUT).build();
    }

    @Override
    public void submitOrder(Order order) {
        Objects.requireNonNull(order, "order is required");
        order.submit();

        Map<String, String> params = new LinkedHashMap<>();
        params.put("symbol", order.symbol());
        params.put("side", bingxSide(order.side()));
        params.put("positionSide", bingxPositionSide(order.side()));
        params.put("type", bingxOrderType(order.orderType()));
        params.put("quantity", order.approvedQuantity().toPlainString());
        if (order.limitPrice() != null) {
            params.put("price", order.limitPrice().toPlainString());
        }
        params.put("clientOrderID", order.clientOrderId().toString());

        // A network/HTTP-level failure here leaves the order exactly where
        // submit() already put it (SUBMITTED) -- we genuinely don't know
        // whether BingX received the order, so no further transition is
        // safe to make. This is what OrderState.SUBMITTED exists for.
        JsonNode root = request("POST", ORDER_PATH, params);

        int code = requireCode(root, "submitOrder");
        if (code != 0) {
            // Unlike the HTTP-failure case above, a 200 response with a
            // non-zero BingX `code` is a definitive answer from the
            // exchange: it saw the order and declined it (e.g. insufficient
            // margin). That is a real, expected outcome of order
            // submission, so it maps to REJECTED rather than being treated
            // as an ambiguous/unknown-outcome error.
            String reason = rejectionReason(root, code);
            log.info("BingX rejected order {}: {}", order.clientOrderId(), reason);
            order.reject(reason);
            return;
        }
        String exchangeOrderId = extractOrderId(root.path("data"));
        log.info("BingX acknowledged order {} as {}", order.clientOrderId(), exchangeOrderId);
        order.acknowledge(exchangeOrderId);
    }

    @Override
    public void cancelOrder(Order order) {
        Objects.requireNonNull(order, "order is required");
        order.requestCancel();

        Map<String, String> params = new LinkedHashMap<>();
        params.put("symbol", order.symbol());
        params.put("orderId", order.exchangeOrderId());

        JsonNode root = request("DELETE", ORDER_PATH, params);

        int code = requireCode(root, "cancelOrder");
        if (code != 0) {
            // Unlike submitOrder's reject(), Order has no "cancel failed,
            // revert to previous state" transition -- CANCEL_PENDING is the
            // only place left to leave it, so this must throw rather than
            // attempt a state change that doesn't exist.
            throw new ExchangeException(
                    "BingX rejected cancel for order " + order.clientOrderId() + ": " + errorSummary(root, code));
        }
        log.info("BingX confirmed cancel for order {}", order.clientOrderId());
        order.confirmCancel();
    }

    @Override
    public OrderStatus queryOrder(Order order) {
        Objects.requireNonNull(order, "order is required");

        Map<String, String> params = new LinkedHashMap<>();
        params.put("symbol", order.symbol());
        params.put("orderId", order.exchangeOrderId());

        // Pure read: order is never touched below, regardless of outcome.
        JsonNode root = request("GET", ORDER_PATH, params);

        int code = requireCode(root, "queryOrder");
        if (code != 0) {
            throw new ExchangeException(
                    "BingX queryOrder failed for order " + order.clientOrderId() + ": " + errorSummary(root, code));
        }
        JsonNode orderNode = unwrapOrderNode(root.path("data"));
        return new OrderStatus(
                orderNode.path("orderId").asText(),
                orderNode.path("status").asText(),
                parseBigDecimal(orderNode, "executedQty"),
                parseBigDecimal(orderNode, "avgPrice"));
    }

    @Override
    public List<PositionSnapshot> getPositions() {
        JsonNode root = request("GET", POSITIONS_PATH, new LinkedHashMap<>());
        int code = requireCode(root, "getPositions");
        if (code != 0) {
            throw new ExchangeException("BingX getPositions failed: " + errorSummary(root, code));
        }
        List<PositionSnapshot> positions = new ArrayList<>();
        for (JsonNode node : root.path("data")) {
            positions.add(new PositionSnapshot(
                    node.path("symbol").asText(),
                    node.path("positionSide").asText(),
                    parseBigDecimal(node, "positionAmt"),
                    parseBigDecimal(node, "avgPrice"),
                    parseBigDecimal(node, "leverage"),
                    parseBigDecimal(node, "unrealizedProfit"),
                    parseBigDecimal(node, "liquidationPrice")));
        }
        return positions;
    }

    @Override
    public BalanceSnapshot getBalance() {
        JsonNode root = request("GET", BALANCE_PATH, new LinkedHashMap<>());
        int code = requireCode(root, "getBalance");
        if (code != 0) {
            throw new ExchangeException("BingX getBalance failed: " + errorSummary(root, code));
        }
        JsonNode balanceNode = selectBalanceNode(root.path("data"));
        return new BalanceSnapshot(
                parseBigDecimal(balanceNode, "balance"),
                parseBigDecimal(balanceNode, "equity"),
                parseBigDecimal(balanceNode, "availableMargin"),
                parseBigDecimal(balanceNode, "usedMargin"),
                parseBigDecimal(balanceNode, "unrealizedProfit"));
    }

    /**
     * BingX's v3 balance response wraps {@code data} as an array of
     * per-asset balance objects, confirmed against a real VST call (see
     * .planning/07-exchange-adapter.md) — not the single nested-object
     * shape originally assumed here (that assumption was wrong and shipped
     * a real bug, since fixed). This system only ever cares about the
     * account's one margin asset (VST in demo, USDT in production), so
     * index 0 is taken directly rather than matched against a hardcoded
     * asset name/constant — a constant here would reintroduce the same
     * kind of implicit live/demo fork {@link BingXAdapter} is deliberately
     * built without (see this class's "no live/paper flag" design note).
     * Revisit if a genuinely multi-asset margin account ever needs
     * supporting; not something to guess a "right" selection rule for
     * without real evidence of that shape.
     */
    private static JsonNode selectBalanceNode(JsonNode data) {
        if (!data.isArray() || data.isEmpty()) {
            throw new ExchangeException("BingX getBalance: expected a non-empty array under data, got: " + data);
        }
        return data.get(0);
    }

    @Override
    public void setLeverage(String symbol, Side side, int leverage) {
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(side, "side is required");
        if (leverage <= 0) {
            throw new IllegalArgumentException("leverage must be positive, was " + leverage);
        }
        Map<String, String> params = new LinkedHashMap<>();
        params.put("symbol", symbol);
        params.put("side", side == Side.LONG ? "LONG" : "SHORT");
        params.put("leverage", String.valueOf(leverage));

        JsonNode root = request("POST", LEVERAGE_PATH, params);
        int code = requireCode(root, "setLeverage");
        if (code != 0) {
            throw new ExchangeException("BingX setLeverage failed for " + symbol + ": " + errorSummary(root, code));
        }
    }

    @Override
    public void setPositionMode(PositionMode mode) {
        Objects.requireNonNull(mode, "mode is required");
        Map<String, String> params = new LinkedHashMap<>();
        params.put("dualSidePosition", mode == PositionMode.HEDGE ? "true" : "false");

        JsonNode root = request("POST", POSITION_MODE_PATH, params);
        int code = requireCode(root, "setPositionMode");
        if (code != 0) {
            throw new ExchangeException("BingX setPositionMode failed: " + errorSummary(root, code));
        }
    }

    /**
     * Signs and sends one request, returning the parsed JSON body. Throws
     * {@link ExchangeException} for anything that isn't a clean HTTP 2xx
     * with a parseable JSON body -- callers still need to separately check
     * the returned envelope's {@code code} field for exchange-level
     * (business) errors, which arrive as a normal 200.
     */
    private JsonNode request(String httpMethod, String path, Map<String, String> params) {
        Map<String, String> signedParams = new LinkedHashMap<>(params);
        signedParams.put("timestamp", String.valueOf(System.currentTimeMillis()));
        String signature = BingXSigner.sign(signedParams, apiSecret);
        // Reuses BingXSigner's own (unencoded) query-string builder so the
        // string that was signed and the string that gets sent can never
        // silently diverge.
        String query = BingXSigner.queryString(signedParams) + "&signature=" + signature;
        URI uri = URI.create(baseUrl + path + "?" + query);

        HttpRequest.Builder builder = HttpRequest.newBuilder(uri).timeout(REQUEST_TIMEOUT).header("X-BX-APIKEY", apiKey);
        HttpRequest httpRequest =
                switch (httpMethod) {
                    case "GET" -> builder.GET().build();
                    case "POST" -> builder.POST(HttpRequest.BodyPublishers.noBody()).build();
                    case "DELETE" -> builder.method("DELETE", HttpRequest.BodyPublishers.noBody())
                            .build();
                    default -> throw new IllegalArgumentException("unsupported HTTP method: " + httpMethod);
                };

        HttpResponse<String> response;
        try {
            response = httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            // Covers java.net.http.HttpTimeoutException too (it extends IOException).
            throw new ExchangeException("BingX request failed: I/O error calling " + path, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ExchangeException("BingX request interrupted calling " + path, e);
        }

        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new ExchangeException(
                    "BingX request to " + path + " returned HTTP " + response.statusCode() + ": " + response.body());
        }

        try {
            return objectMapper.readTree(response.body());
        } catch (IOException e) {
            throw new ExchangeException(
                    "BingX response body for " + path + " is not valid JSON: " + response.body(), e);
        }
    }

    private static String bingxSide(Side side) {
        return side == Side.LONG ? "BUY" : "SELL";
    }

    /**
     * Hedge-mode convention (LONG/SHORT), not BOTH. {@code Order} carries no
     * position-mode context to pick between them, and hedge-mode
     * {@code positionSide} values are also valid to send in one-way mode
     * accounts in practice for most BingX swap endpoints -- but this is an
     * assumption, not a verified fact; see {@code .planning/07-exchange-adapter.md}
     * for why this is flagged as a known limitation rather than silently
     * assumed correct.
     */
    private static String bingxPositionSide(Side side) {
        return side == Side.LONG ? "LONG" : "SHORT";
    }

    private static String bingxOrderType(OrderType orderType) {
        // BingX has no distinct "guarded market" order type; the slippage
        // guard is this system's own concept (enforced by Risk Gateway /
        // PaperBroker-equivalent logic before/around this call), not
        // something the exchange API itself models.
        return orderType == OrderType.LIMIT ? "LIMIT" : "MARKET";
    }

    private static JsonNode unwrapOrderNode(JsonNode dataNode) {
        return dataNode.has("order") ? dataNode.get("order") : dataNode;
    }

    private static String extractOrderId(JsonNode dataNode) {
        JsonNode orderNode = unwrapOrderNode(dataNode);
        JsonNode orderIdNode = orderNode.get("orderId");
        if (orderIdNode == null || orderIdNode.isNull()) {
            throw new ExchangeException("BingX order response missing orderId field: " + dataNode);
        }
        return orderIdNode.asText();
    }

    private static BigDecimal parseBigDecimal(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        try {
            return new BigDecimal(value.asText());
        } catch (NumberFormatException e) {
            throw new ExchangeException(
                    "BingX response field '" + field + "' is not a valid decimal: '" + value.asText() + "'", e);
        }
    }

    /**
     * Extracts the response envelope's {@code code} field, throwing if it's
     * absent entirely. {@code JsonNode#path("code").asInt()} silently
     * returns {@code 0} for a missing field, which would otherwise be
     * misread as "success" for a response that doesn't even have the shape
     * this adapter expects, rather than surfacing as the error it is.
     */
    private static int requireCode(JsonNode root, String context) {
        if (!root.has("code")) {
            throw new ExchangeException("BingX response for " + context + " is missing the 'code' field: " + root);
        }
        return root.path("code").asInt();
    }

    private static String rejectionReason(JsonNode root, int code) {
        String msg = root.path("msg").asText("");
        return msg.isBlank() ? ("BingX rejected order, code=" + code) : msg;
    }

    private static String errorSummary(JsonNode root, int code) {
        return "code=" + code + " msg=" + root.path("msg").asText("");
    }
}
