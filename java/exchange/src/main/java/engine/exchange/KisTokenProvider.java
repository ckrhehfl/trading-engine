package engine.exchange;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Map;
import java.util.Objects;

/**
 * Issues and caches KIS's OAuth2 access token (`POST /oauth2/tokenP`,
 * {@code grant_type=client_credentials}) -- genuinely new, no {@link
 * BingXSigner} precedent: BingX signs each request statelessly with
 * HMAC-SHA256, KIS instead issues a single bearer token valid ~24h that
 * every subsequent request reuses via an {@code Authorization: Bearer
 * <token>} header (confirmed against KIS's own official
 * {@code koreainvestment/open-trading-api} GitHub repo, {@code
 * examples_user/kis_auth.py} -- exact request/response field names below
 * are taken directly from that real source, not inferred).
 *
 * <p>Shared between {@link KisAdapter} (this module, {@code :exchange})
 * and {@code engine.runtime.KisPriceFeed} (the {@code :runtime} module,
 * which already depends on {@code :exchange}) -- KIS's quote/price
 * endpoint requires the same bearer token as every trading endpoint
 * (confirmed: {@code inquire-price}'s real implementation calls the same
 * authenticated request-signing path as order placement), unlike BingX
 * where the public trades endpoint needed no auth at all. This is why
 * {@code PriceFeed} could stay folded into a single class for BingX
 * ({@code BingXPriceFeed} is deliberately separate from {@code
 * BingXAdapter} anyway, but only because of that endpoint's own public/
 * private split) but genuinely cannot be folded into {@link KisAdapter}
 * itself for KIS: {@code engine.runtime.PriceFeed} lives in {@code
 * :runtime}, which depends on {@code :exchange} -- not the reverse -- so
 * a class in {@code :exchange} cannot implement it without a module
 * dependency cycle. This class is the shared collaborator that lets both
 * sides reuse one token cache and one HTTP client instead of duplicating
 * this logic.
 *
 * <p>Thread-safe: {@link #currentToken()} is synchronized, matching this
 * codebase's existing concurrency convention for shared mutable state
 * (e.g. {@code ExchangeOrderExecutor}'s per-order state).
 */
public final class KisTokenProvider {

    private static final String TOKEN_PATH = "/oauth2/tokenP";
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(10);
    // access_token_token_expired is a bare "yyyy-MM-dd HH:mm:ss" with no
    // timezone in KIS's real response -- interpreted as KST (Asia/Seoul),
    // the natural reading for a Korean-only API. Unverified against a real
    // token response (no real credentials exist yet for this project as of
    // this writing) -- flagged the same way as every other "documented, not
    // yet empirically verified" KIS/BingX fact in CLAUDE.md.
    private static final ZoneId EXPIRY_ZONE = ZoneId.of("Asia/Seoul");
    private static final DateTimeFormatter EXPIRY_FORMAT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    // Re-issue a fresh token this far ahead of the reported expiry, so a
    // token that's technically still valid when currentToken() is called
    // doesn't expire mid-flight during the request that goes on to use it.
    private static final Duration EXPIRY_SAFETY_MARGIN = Duration.ofMinutes(5);

    private final String appKey;
    private final String appSecret;
    private final String baseUrl;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    private CachedToken cached;

    /** {@code appKey}/{@code appSecret} are stripped, mirroring {@link BingXAdapter}'s own constructor -- see its Javadoc for why this is a real, not cosmetic, fix. */
    public KisTokenProvider(String appKey, String appSecret, String baseUrl) {
        this.appKey = Objects.requireNonNull(appKey, "appKey is required").strip();
        this.appSecret = Objects.requireNonNull(appSecret, "appSecret is required").strip();
        this.baseUrl = Objects.requireNonNull(baseUrl, "baseUrl is required");
        this.httpClient = HttpClient.newBuilder().connectTimeout(REQUEST_TIMEOUT).build();
    }

    /**
     * Returns a currently-valid access token, issuing a fresh one if none is
     * cached yet or the cached one is within {@link #EXPIRY_SAFETY_MARGIN}
     * of its reported expiry. Never returns a stale/expired token silently.
     *
     * <p><b>Known, accepted limitation, not fixed here</b> (raised on real
     * CodeRabbit review, deferred rather than fixed given its own severity
     * there was rated trivial and Phase 1 is paper-only/dummy-signal, so
     * the real-world blast radius today is small): this whole method is
     * {@code synchronized}, so a token reissue (a real HTTP round trip, up
     * to {@link #REQUEST_TIMEOUT}) blocks every other caller of this
     * provider -- including {@link KisAdapter}'s order-submission path,
     * since it shares this same provider instance with {@code
     * engine.runtime.KisPriceFeed}'s quote polling (see this class's own
     * "Shared between" Javadoc). A genuinely non-blocking design
     * (background pre-emptive reissue before the safety margin is reached,
     * or falling back to a still-technically-valid cached token if a
     * reissue attempt itself fails) is real, worthwhile future work before
     * this matters for anything beyond paper trading -- not attempted here
     * to avoid introducing a real concurrency bug under review pressure for
     * a Trivial-severity, not-yet-live-relevant concern.
     */
    public synchronized String currentToken() {
        if (cached == null || Instant.now().isAfter(cached.expiresAt().minus(EXPIRY_SAFETY_MARGIN))) {
            cached = issueToken();
        }
        return cached.token();
    }

    /**
     * Every authenticated KIS request -- not just token issuance -- also
     * carries {@code appkey}/{@code appsecret} as separate headers alongside
     * the bearer token (confirmed against the same real source cited in
     * this class's own Javadoc). Exposed so {@link KisAdapter} and {@code
     * engine.runtime.KisPriceFeed} can attach them without each needing
     * their own separately-stripped copy of the same two credentials.
     */
    public String appKey() {
        return appKey;
    }

    public String appSecret() {
        return appSecret;
    }

    private CachedToken issueToken() {
        // Built via objectMapper, not string concatenation (tightened on
        // real CodeRabbit review) -- a raw appKey/appSecret containing a
        // quote or backslash would otherwise corrupt the JSON structure,
        // turning an auth failure into a confusing parse error instead of a
        // clear one.
        String body = toJson(Map.of("grant_type", "client_credentials", "appkey", appKey, "appsecret", appSecret));
        URI uri = URI.create(baseUrl + TOKEN_PATH);
        HttpRequest request = HttpRequest.newBuilder(uri)
                .timeout(REQUEST_TIMEOUT)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();

        HttpResponse<String> response;
        try {
            response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new ExchangeException("KIS token request failed: I/O error calling " + TOKEN_PATH, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ExchangeException("KIS token request interrupted calling " + TOKEN_PATH, e);
        }

        // None of the exception messages below embed response.body()/root
        // (tightened on real CodeRabbit review) -- unlike KisAdapter's own
        // trading-endpoint responses (which can carry account numbers/
        // balances), a real token response body carries the access token
        // itself, the single most sensitive value this whole class exists
        // to protect. Leaking it into a log/exception message would defeat
        // the point.
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new ExchangeException("KIS token request returned HTTP " + response.statusCode());
        }

        JsonNode root;
        try {
            root = objectMapper.readTree(response.body());
        } catch (IOException e) {
            throw new ExchangeException("KIS token response body is not valid JSON", e);
        }

        JsonNode tokenNode = root.get("access_token");
        if (tokenNode == null || tokenNode.isNull()) {
            throw new ExchangeException("KIS token response missing 'access_token' field");
        }
        JsonNode expiryNode = root.get("access_token_token_expired");
        if (expiryNode == null || expiryNode.isNull()) {
            throw new ExchangeException("KIS token response missing 'access_token_token_expired' field");
        }

        Instant expiresAt;
        try {
            expiresAt = LocalDateTime.parse(expiryNode.asText(), EXPIRY_FORMAT).atZone(EXPIRY_ZONE).toInstant();
        } catch (java.time.format.DateTimeParseException e) {
            throw new ExchangeException("KIS token response 'access_token_token_expired' is not a valid timestamp", e);
        }

        return new CachedToken(tokenNode.asText(), expiresAt);
    }

    private String toJson(Map<String, String> body) {
        try {
            return objectMapper.writeValueAsString(body);
        } catch (IOException e) {
            // Never embeds `body` itself -- it carries appKey/appSecret.
            throw new ExchangeException("failed to serialize KIS token request body", e);
        }
    }

    private record CachedToken(String token, Instant expiresAt) {}
}
