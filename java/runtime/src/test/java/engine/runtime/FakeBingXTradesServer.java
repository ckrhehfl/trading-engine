package engine.runtime;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Minimal fake BingX server for this package's tests -- same
 * {@code com.sun.net.httpserver.HttpServer}-backed technique as
 * {@code engine.exchange.BingXAdapterTest}'s private {@code FakeExchangeServer}
 * (JDK built-in, not a mocking framework -- this codebase has none), shared
 * here across {@link BingXPriceFeedTest} and {@link TradingLoopTest} since
 * both need the exact same "canned recent-trades response" fake rather than
 * duplicating it per class.
 */
final class FakeBingXTradesServer implements AutoCloseable {
    private final HttpServer server;
    private volatile String lastMethod;
    private volatile String lastPath;
    private volatile Map<String, String> lastQueryParams = Map.of();
    private volatile int responseStatus = 200;
    private volatile String responseBody = "{\"code\":0,\"msg\":\"\",\"data\":[{\"time\":1,\"price\":\"60000\"}]}";

    FakeBingXTradesServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
        server.start();
    }

    void respondWith(int status, String body) {
        this.responseStatus = status;
        this.responseBody = body;
    }

    /** Canned single-trade success response at the given price. */
    void respondWithPrice(String price) {
        respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":[{\"time\":1,\"price\":\"" + price + "\"}]}");
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

    private void handle(HttpExchange exchange) throws IOException {
        lastMethod = exchange.getRequestMethod();
        lastPath = exchange.getRequestURI().getPath();
        lastQueryParams = parseQuery(exchange.getRequestURI().getRawQuery());

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
