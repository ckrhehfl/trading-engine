package engine.runtime;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Minimal fake BingX server for this package's tests -- same
 * {@code com.sun.net.httpserver.HttpServer}-backed technique as
 * {@code engine.exchange.BingXAdapterTest}'s private {@code FakeExchangeServer}
 * (JDK built-in, not a mocking framework -- this codebase has none), shared
 * here across {@link BingXPriceFeedTest} and {@link TradingLoopTest} since
 * both need the exact same "canned recent-trades response" fake rather than
 * duplicating it per class. {@link #hangForever()} (GitHub issue #74,
 * {@code PaperTradingAppTest}'s shutdown-termination-confirmation tests)
 * adds a third mode -- accept the connection but never respond -- on top of
 * the original canned-response behavior.
 */
final class FakeBingXTradesServer implements AutoCloseable {
    private final HttpServer server;
    // Daemon threads -- see hangForever()'s own Javadoc: a handler thread
    // deliberately blocked forever must never be able to keep the test JVM
    // alive past the test itself. Set on the server (via setExecutor(),
    // before start()) unconditionally, not only when hangForever() is used,
    // so this constructor has exactly one code path regardless of which
    // mode a given test ends up choosing.
    private final ExecutorService handlerExecutor = Executors.newCachedThreadPool(FakeBingXTradesServer::newDaemonThread);
    private volatile String lastMethod;
    private volatile String lastPath;
    private volatile Map<String, String> lastQueryParams = Map.of();
    private volatile int responseStatus = 200;
    private volatile String responseBody = "{\"code\":0,\"msg\":\"\",\"data\":[{\"time\":1,\"price\":\"60000\"}]}";
    private volatile boolean hangForever = false;

    FakeBingXTradesServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.setExecutor(handlerExecutor);
        server.createContext("/", this::handle);
        server.start();
    }

    private static Thread newDaemonThread(Runnable r) {
        Thread t = new Thread(r, "fake-bingx-trades-server-handler");
        t.setDaemon(true);
        return t;
    }

    void respondWith(int status, String body) {
        this.responseStatus = status;
        this.responseBody = body;
    }

    /** Canned single-trade success response at the given price. */
    void respondWithPrice(String price) {
        respondWith(200, "{\"code\":0,\"msg\":\"\",\"data\":[{\"time\":1,\"price\":\"" + price + "\"}]}");
    }

    /**
     * Switches every future request to never receive a response -- the
     * deterministic "force a real tick to hang" technique GitHub issue #74
     * asks for. A connection is still accepted, and {@link #lastPath()}
     * still observably updates (so a caller can confirm a request has
     * genuinely arrived before racing anything against it -- see {@code
     * PaperTradingAppTest}'s use of this), but the handler thread then
     * blocks indefinitely instead of ever writing a response, leaving the
     * real client-side {@code BingXPriceFeed} call genuinely in-flight
     * until either its own fixed 10s {@code HttpRequest} timeout elapses,
     * or the calling thread is interrupted (e.g. by a real {@code
     * ExecutorService#shutdownNow()} against the thread running it --
     * empirically confirmed, separately from this class, to unblock a
     * blocking {@code HttpClient#send} call in low single-digit
     * milliseconds on this project's own dev environment; see
     * {@code .planning/paper-trading-issue-74-shutdown-confirmation-
     * test.md}). Because the handler thread runs on this server's own
     * daemon executor (see the constructor), a hung handler can never
     * prevent {@link #close()} or the test JVM itself from exiting.
     */
    void hangForever() {
        this.hangForever = true;
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

        if (hangForever) {
            // Deliberately never responds -- see hangForever()'s own
            // Javadoc above. Blocks this (daemon) handler thread until this
            // server is closed (close()'s handlerExecutor.shutdownNow()
            // interrupts it); the CLIENT side of the still-open connection
            // is unblocked separately, by its own caller's interrupt or its
            // own fixed request timeout -- never by anything this method
            // does.
            try {
                new CountDownLatch(1).await();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return;
        }

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
        // Interrupts any still-hung handler thread (see hangForever()'s own
        // Javadoc) before tearing down the listening socket, so this method
        // never leaves a blocked handler thread orphaned -- even though it
        // is already a daemon thread and so could never block the JVM from
        // exiting on its own.
        handlerExecutor.shutdownNow();
        server.stop(0);
    }
}
