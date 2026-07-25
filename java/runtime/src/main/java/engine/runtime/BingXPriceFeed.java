package engine.runtime;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import engine.exchange.ExchangeException;
import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Objects;

/**
 * Polls BingX's public, unauthenticated recent-trades endpoint for the
 * latest traded price. WebSocket market data stays deliberately deferred
 * (see .planning/07-exchange-adapter.md); this REST-polling class is what
 * {@link TradingLoop} drives instead, per this task's scope decision.
 *
 * <p>Takes {@code baseUrl} as a plain constructor argument, matching
 * {@code engine.exchange.BingXAdapter}'s "caller decides the host, no
 * live/paper flag baked in" pattern -- though unlike {@code BingXAdapter},
 * this class only ever calls a public, unauthenticated endpoint, so there
 * is no real live-vs-demo distinction that matters the way it does for
 * authenticated writes. It's still taken as a parameter for consistency
 * and testability (a local fake server in tests; VST or production in real
 * use -- either serves identical public market data, no API key needed
 * either way).
 *
 * <p>This class deliberately does not depend on {@code BingXAdapter} or
 * {@code BingXSigner} (those exist for authenticated write endpoints; this
 * endpoint is public and unauthenticated, needs no signing) -- it depends
 * on {@code engine.exchange} for {@link ExchangeException} only, the shared
 * error type for anything exchange-communication-related in this codebase.
 *
 * <p><b>Response shape</b>: this endpoint had never been called from Java
 * before this class, and CLAUDE.md's "Exchange API Facts" only names the
 * path, not the field shape -- so the shape below was verified directly
 * (2026-07-25) against the real, live, public endpoint
 * (`GET /openApi/swap/v2/quote/trades?symbol=BTC-USDT`), not assumed from
 * docs. The envelope matches the rest of BingX's API
 * ({@code {"code","msg","data"}}); {@code data} is a flat array of trade
 * objects, each with (at minimum) a string-decimal {@code price} field and
 * a {@code time} field (epoch millis). The array is ordered oldest-first /
 * newest-last -- confirmed empirically by {@code time} (and the separate
 * {@code fillId} field) strictly increasing across the array -- so the
 * latest trade is the *last* element, not the first; this is the one
 * assumption in this class most worth re-verifying if BingX's behavior
 * here is ever in doubt.
 */
public final class BingXPriceFeed {

    private static final String TRADES_PATH = "/openApi/swap/v2/quote/trades";
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(10);

    private final String baseUrl;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public BingXPriceFeed(String baseUrl) {
        this.baseUrl = Objects.requireNonNull(baseUrl, "baseUrl is required");
        this.httpClient = HttpClient.newBuilder().connectTimeout(REQUEST_TIMEOUT).build();
    }

    public BigDecimal latestPrice(String symbol) {
        Objects.requireNonNull(symbol, "symbol is required");

        URI uri = URI.create(baseUrl + TRADES_PATH + "?symbol=" + symbol);
        HttpRequest request = HttpRequest.newBuilder(uri).timeout(REQUEST_TIMEOUT).GET().build();

        HttpResponse<String> response;
        try {
            response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            // Covers java.net.http.HttpTimeoutException too (it extends IOException).
            throw new ExchangeException("BingX price feed request failed: I/O error calling " + TRADES_PATH, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ExchangeException("BingX price feed request interrupted calling " + TRADES_PATH, e);
        }

        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new ExchangeException(
                    "BingX price feed request to " + TRADES_PATH + " returned HTTP " + response.statusCode()
                            + ": " + response.body());
        }

        JsonNode root;
        try {
            root = objectMapper.readTree(response.body());
        } catch (IOException e) {
            throw new ExchangeException("BingX price feed response body is not valid JSON: " + response.body(), e);
        }

        int code = requireCode(root);
        if (code != 0) {
            throw new ExchangeException(
                    "BingX price feed returned a non-zero code=" + code + " msg=" + root.path("msg").asText(""));
        }

        JsonNode data = root.path("data");
        if (!data.isArray() || data.isEmpty()) {
            throw new ExchangeException(
                    "BingX price feed: expected a non-empty trades array under data, got: " + data);
        }

        JsonNode latestTrade = data.get(data.size() - 1);
        JsonNode priceNode = latestTrade.get("price");
        if (priceNode == null || priceNode.isNull()) {
            throw new ExchangeException("BingX price feed: latest trade missing 'price' field: " + latestTrade);
        }
        try {
            return new BigDecimal(priceNode.asText());
        } catch (NumberFormatException e) {
            throw new ExchangeException(
                    "BingX price feed: latest trade 'price' is not a valid decimal: '" + priceNode.asText() + "'", e);
        }
    }

    /**
     * Same defensive shape-check as {@code BingXAdapter}'s private
     * {@code requireCode} -- {@code JsonNode#asInt()} silently returns 0
     * (misreadable as success) for a missing field, a JSON null, an
     * object/array, or unparseable text, so an explicit {@code has}/
     * {@code isInt} check is required rather than trusting that fallback.
     * Deliberately re-implemented here rather than shared with
     * {@code BingXAdapter} -- this class does not depend on
     * {@code engine.exchange.BingXAdapter} (see class Javadoc).
     */
    private static int requireCode(JsonNode root) {
        if (root == null || !root.has("code")) {
            throw new ExchangeException("BingX price feed response is missing the 'code' field: " + root);
        }
        JsonNode codeNode = root.get("code");
        if (!codeNode.isInt()) {
            throw new ExchangeException("BingX price feed response has a non-integer 'code' field: " + codeNode);
        }
        return codeNode.asInt();
    }
}
