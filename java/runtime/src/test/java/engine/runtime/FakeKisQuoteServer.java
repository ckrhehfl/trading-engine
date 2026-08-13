package engine.runtime;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Map;

/**
 * Minimal fake KIS server for {@link KisPriceFeedTest} -- same technique as
 * {@code engine.exchange.FakeKisServer} (dual routes: a real OAuth2 token
 * round trip via {@code /oauth2/tokenP}, plus the quote endpoint under
 * test), duplicated rather than shared because {@code :exchange}'s test
 * sources aren't reachable from {@code :runtime}'s.
 */
final class FakeKisQuoteServer implements AutoCloseable {
    private final HttpServer server;
    private volatile String lastPath;
    private volatile Map<String, String> lastQueryParams = Map.of();
    private volatile int responseStatus = 200;
    private volatile String responseBody =
            "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":{\"futs_prpr\":\"350.5\"}}";

    FakeKisQuoteServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
        server.start();
    }

    void respondWithPrice(String price) {
        this.responseBody = "{\"rt_cd\":\"0\",\"msg_cd\":\"\",\"msg1\":\"\",\"output1\":{\"futs_prpr\":\"" + price + "\"}}";
    }

    void respondWith(int status, String body) {
        this.responseStatus = status;
        this.responseBody = body;
    }

    String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    String lastPath() {
        return lastPath;
    }

    Map<String, String> lastQueryParams() {
        return lastQueryParams;
    }

    private void handle(HttpExchange exchange) throws IOException {
        String path = exchange.getRequestURI().getPath();

        if ("/oauth2/tokenP".equals(path)) {
            String farFutureExpiry = ZonedDateTime.now(ZoneId.of("Asia/Seoul"))
                    .plusDays(1)
                    .format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
            String tokenBody =
                    "{\"access_token\":\"fake-token\",\"access_token_token_expired\":\"" + farFutureExpiry + "\"}";
            byte[] body = tokenBody.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(body);
            }
            return;
        }

        lastPath = path;
        lastQueryParams = parseQuery(exchange.getRequestURI().getRawQuery());

        byte[] body = responseBody.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(responseStatus, body.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(body);
        }
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> params = new java.util.LinkedHashMap<>();
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
