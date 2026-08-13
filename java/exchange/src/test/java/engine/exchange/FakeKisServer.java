package engine.exchange;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.ZonedDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Minimal fake KIS server for this package's tests -- same {@code
 * com.sun.net.httpserver.HttpServer}-backed technique as {@code
 * BingXAdapterTest}'s own {@code FakeExchangeServer} (JDK built-in, not a
 * mocking framework -- this codebase has none), extended with a second,
 * always-available route for KIS's OAuth2 token endpoint ({@code
 * /oauth2/tokenP}) -- unlike BingX, every KIS request under test needs a
 * real token round trip first, via {@link KisTokenProvider}, before the
 * actual endpoint under test is ever reached.
 */
final class FakeKisServer implements AutoCloseable {
    private final HttpServer server;
    private volatile String lastMethod;
    private volatile String lastPath;
    private volatile Map<String, String> lastQueryParams = Map.of();
    private volatile String lastRequestBody = "";
    private volatile String lastAuthorizationHeader;
    private volatile String lastAppKeyHeader;
    private volatile String lastAppSecretHeader;
    private volatile String lastTrIdHeader;
    private volatile int responseStatus = 200;
    private volatile String responseBody = "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output\":{}}";
    private volatile String responseTrContHeader = "";
    private volatile String lastTrContHeader;
    private final java.util.Queue<String[]> queuedResponses = new java.util.concurrent.ConcurrentLinkedQueue<>();
    private volatile String tokenResponseBody;
    private volatile int tokenResponseStatus = 200;

    FakeKisServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
        server.start();
        tokenResponseBody = defaultTokenResponse();
    }

    /** A valid token, far from expiry -- the default so most tests never need to think about the token round trip at all. */
    private static String defaultTokenResponse() {
        String farFutureExpiry = ZonedDateTime.now(ZoneId.of("Asia/Seoul"))
                .plusDays(1)
                .format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        return "{\"access_token\":\"fake-token\",\"access_token_token_expired\":\"" + farFutureExpiry + "\"}";
    }

    void respondToTokenRequestWith(String body) {
        this.tokenResponseBody = body;
        this.tokenResponseStatus = 200;
    }

    /** Lets a test exercise a real non-2xx token failure, not just a connection-level one. */
    void respondToTokenRequestWith(int status, String body) {
        this.tokenResponseStatus = status;
        this.tokenResponseBody = body;
    }

    /**
     * Queues a sequence of (non-token) responses to serve one per request,
     * in order, each with its own {@code tr_cont} response header --
     * for testing {@code queryOrder}'s multi-page pagination. Once
     * exhausted, falls back to whatever {@link #respondWith} last set.
     */
    void queueResponse(int status, String body, String trContHeader) {
        queuedResponses.add(new String[] {String.valueOf(status), body, trContHeader});
    }

    String lastTrContHeader() {
        return lastTrContHeader;
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

    String lastRequestBody() {
        return lastRequestBody;
    }

    String lastAuthorizationHeader() {
        return lastAuthorizationHeader;
    }

    String lastAppKeyHeader() {
        return lastAppKeyHeader;
    }

    String lastAppSecretHeader() {
        return lastAppSecretHeader;
    }

    String lastTrIdHeader() {
        return lastTrIdHeader;
    }

    private void handle(HttpExchange exchange) throws IOException {
        String path = exchange.getRequestURI().getPath();

        if ("/oauth2/tokenP".equals(path)) {
            byte[] body = tokenResponseBody.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(tokenResponseStatus, body.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(body);
            }
            return;
        }

        lastMethod = exchange.getRequestMethod();
        lastPath = path;
        lastQueryParams = parseQuery(exchange.getRequestURI().getRawQuery());
        lastRequestBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        lastAuthorizationHeader = exchange.getRequestHeaders().getFirst("authorization");
        lastAppKeyHeader = exchange.getRequestHeaders().getFirst("appkey");
        lastAppSecretHeader = exchange.getRequestHeaders().getFirst("appsecret");
        lastTrIdHeader = exchange.getRequestHeaders().getFirst("tr_id");
        lastTrContHeader = exchange.getRequestHeaders().getFirst("tr_cont");

        String[] queued = queuedResponses.poll();
        int status = queued != null ? Integer.parseInt(queued[0]) : responseStatus;
        String bodyText = queued != null ? queued[1] : responseBody;
        String trCont = queued != null ? queued[2] : responseTrContHeader;

        byte[] body = bodyText.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.getResponseHeaders().add("tr_cont", trCont);
        exchange.sendResponseHeaders(status, body.length);
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
                params.put(
                        java.net.URLDecoder.decode(pair.substring(0, eq), StandardCharsets.UTF_8),
                        java.net.URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8));
            }
        }
        return params;
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
