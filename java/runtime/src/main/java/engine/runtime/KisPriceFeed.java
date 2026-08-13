package engine.runtime;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import engine.exchange.ExchangeException;
import engine.exchange.KisTokenProvider;
import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Objects;

/**
 * Polls KIS's KOSPI200 futures quote endpoint (`GET /uapi/domestic-
 * futureoption/v1/quotations/inquire-price`) for the latest price.
 *
 * <p><b>Why this is a separate class from {@code engine.exchange.
 * KisAdapter}, not folded together the way one might expect from {@code
 * BingXPriceFeed}/{@code BingXAdapter}'s own separateness</b>: for BingX,
 * that split exists because the quote endpoint is genuinely public and
 * unauthenticated while {@code BingXAdapter} is authenticated-only — a
 * reason that does not apply here (KIS's quote endpoint needs the exact
 * same bearer token as every trading endpoint, confirmed against KIS's
 * own real source). The real reason this must be a separate class for
 * KIS is structural, not stylistic: {@link PriceFeed} lives in this
 * module ({@code :runtime}), and {@code KisAdapter} lives in {@code
 * :exchange}, which {@code :runtime} depends on -- not the other way
 * around. A class in {@code :exchange} implementing an interface from
 * {@code :runtime} would create a module dependency cycle, so {@code
 * KisAdapter} cannot implement {@link PriceFeed} regardless of the
 * auth-sharing question. This class and {@code KisAdapter} instead share
 * one {@link KisTokenProvider} (itself in {@code :exchange}, reachable
 * from here since {@code :runtime} already depends on it) so neither
 * duplicates the other's token-caching logic.
 *
 * <p>Same disclosure as {@code KisAdapter}: request parameters below are
 * confirmed against KIS's own real {@code koreainvestment/open-trading-
 * api} source; the response field name for the actual price
 * ({@code futs_prpr}, inferred from KIS's {@code stck_prpr} naming
 * convention for the equivalent stock-quote field) is this class's own
 * best-effort inference, not yet empirically verified against a real
 * response.
 *
 * <p><b>No staleness/execution-time signal</b> (raised on real CodeRabbit
 * review): {@link #latestPrice} returns a bare price with no timestamp or
 * market-open indicator, so a caller polling this outside KRX's real
 * trading hours could keep receiving the same last-traded price
 * indefinitely with no way to tell it apart from a genuinely fresh one.
 * This is a deliberate separation of concerns, not an oversight: knowing
 * *when* it's safe to act on a price at all is exactly {@code
 * engine.runtime.TradingCalendar}'s job (see CLAUDE.md's KIS/KOSPI200
 * Phase 1 design), which gates whether {@code TradingLoop.tick()} -- and
 * therefore this method -- is ever called during closed-market hours in
 * the first place, once wired into {@code PaperTradingApp} (Task 4). This
 * class does not duplicate that gating itself. Whether KIS's real {@code
 * inquire-price} response also carries its own execution-timestamp field
 * (which could serve as an independent staleness check) is unconfirmed --
 * a real Task 4 verification item, not assumed here either way.
 */
public final class KisPriceFeed implements PriceFeed {

    private static final String QUOTE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price";
    // Same tr_id for both real and demo trading per KIS's own real source
    // -- quote data isn't account-specific, unlike order/balance endpoints.
    private static final String TR_ID_INQUIRE_PRICE = "FHMIF10000000";
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(10);

    private final KisTokenProvider tokenProvider;
    private final String baseUrl;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public KisPriceFeed(KisTokenProvider tokenProvider, String baseUrl) {
        this.tokenProvider = Objects.requireNonNull(tokenProvider, "tokenProvider is required");
        this.baseUrl = Objects.requireNonNull(baseUrl, "baseUrl is required");
        this.httpClient = HttpClient.newBuilder().connectTimeout(REQUEST_TIMEOUT).build();
    }

    @Override
    public BigDecimal latestPrice(String symbol) {
        Objects.requireNonNull(symbol, "symbol is required");
        String token = tokenProvider.currentToken();

        // FID_COND_MRKT_DIV_CODE=F selects the index-futures market
        // (vs. O for index options) -- this class is scoped to KOSPI200
        // futures only, matching this phase's own futures-only narrowing
        // (see CLAUDE.md's KIS/KOSPI200 Phase 1 section).
        String query = "FID_COND_MRKT_DIV_CODE=F&FID_INPUT_ISCD="
                + java.net.URLEncoder.encode(symbol, StandardCharsets.UTF_8);
        URI uri = URI.create(baseUrl + QUOTE_PATH + "?" + query);

        HttpRequest request = HttpRequest.newBuilder(uri)
                .timeout(REQUEST_TIMEOUT)
                .header("Content-Type", "application/json")
                .header("Accept", "text/plain")
                .header("authorization", "Bearer " + token)
                .header("appkey", tokenProvider.appKey())
                .header("appsecret", tokenProvider.appSecret())
                .header("tr_id", TR_ID_INQUIRE_PRICE)
                .header("custtype", "P")
                .GET()
                .build();

        HttpResponse<String> response;
        try {
            response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new ExchangeException("KIS price feed request failed: I/O error calling " + QUOTE_PATH, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ExchangeException("KIS price feed request interrupted calling " + QUOTE_PATH, e);
        }

        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new ExchangeException(
                    "KIS price feed request to " + QUOTE_PATH + " returned HTTP " + response.statusCode() + ": "
                            + response.body());
        }

        JsonNode root;
        try {
            root = objectMapper.readTree(response.body());
        } catch (IOException e) {
            throw new ExchangeException("KIS price feed response body is not valid JSON: " + response.body(), e);
        }

        if (!"0".equals(root.path("rt_cd").asText(null))) {
            throw new ExchangeException(
                    "KIS price feed returned rt_cd=" + root.path("rt_cd").asText("") + " msg1="
                            + root.path("msg1").asText(""));
        }

        JsonNode priceNode = root.path("output1").get("futs_prpr");
        if (priceNode == null || priceNode.isNull()) {
            throw new ExchangeException("KIS price feed: output1 missing 'futs_prpr' field: " + root.path("output1"));
        }
        try {
            return new BigDecimal(priceNode.asText());
        } catch (NumberFormatException e) {
            throw new ExchangeException(
                    "KIS price feed: 'futs_prpr' is not a valid decimal: '" + priceNode.asText() + "'", e);
        }
    }
}
