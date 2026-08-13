package engine.exchange;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.IOException;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class KisTokenProviderTest {

    private FakeKisServer server;
    private KisTokenProvider provider;

    @BeforeEach
    void setUp() throws IOException {
        server = new FakeKisServer();
        provider = new KisTokenProvider("test-app-key", "test-app-secret", server.baseUrl());
    }

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.close();
        }
    }

    @Test
    void currentTokenIssuesAndReturnsARealToken() {
        server.respondToTokenRequestWith(farFutureTokenResponse("fake-token-1"));

        assertEquals("fake-token-1", provider.currentToken());
    }

    @Test
    void currentTokenCachesAndDoesNotReissueWhileStillValid() {
        server.respondToTokenRequestWith(farFutureTokenResponse("fake-token-1"));
        String first = provider.currentToken();

        // Change what the server would return -- if currentToken() issued a
        // fresh request, the second call would observe this new value.
        server.respondToTokenRequestWith(farFutureTokenResponse("fake-token-2"));
        String second = provider.currentToken();

        assertEquals(first, second, "a cached, still-valid token must not be silently re-issued");
    }

    @Test
    void currentTokenReissuesWhenCachedTokenIsWithinSafetyMarginOfExpiry() {
        // Expires in 1 minute -- inside the 5-minute safety margin, so this
        // must be treated as needing reissue even though it's not yet
        // technically expired.
        String soonExpiry = ZonedDateTime.now(ZoneId.of("Asia/Seoul"))
                .plusMinutes(1)
                .format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        server.respondToTokenRequestWith(
                "{\"access_token\":\"about-to-expire\",\"access_token_token_expired\":\"" + soonExpiry + "\"}");
        String first = provider.currentToken();
        assertEquals("about-to-expire", first);

        server.respondToTokenRequestWith(farFutureTokenResponse("reissued-token"));
        String second = provider.currentToken();

        assertEquals("reissued-token", second);
    }

    @Test
    void appKeyAndAppSecretAreStripped() {
        KisTokenProvider strippingProvider =
                new KisTokenProvider("test-app-key\r\n \t", " \ttest-app-secret\r\n", server.baseUrl());

        assertEquals("test-app-key", strippingProvider.appKey());
        assertEquals("test-app-secret", strippingProvider.appSecret());
    }

    @Test
    void currentTokenThrowsExchangeExceptionOnConnectionFailure() {
        server.close();

        assertThrows(ExchangeException.class, () -> provider.currentToken());
    }

    @Test
    void currentTokenThrowsExchangeExceptionOnNon2xxTokenResponse() {
        // Distinct from the connection-failure case above -- a real non-2xx
        // HTTP response from a live token endpoint, not just an unreachable
        // one (the fake server's own token route previously always returned
        // 200 regardless of what a test asked for; fixed on real CodeRabbit
        // review of the PR that added this test).
        server.respondToTokenRequestWith(401, "{\"rt_cd\":\"1\",\"msg1\":\"invalid appkey\"}");

        assertThrows(ExchangeException.class, () -> provider.currentToken());
    }

    @Test
    void currentTokenThrowsExchangeExceptionWhenAccessTokenFieldMissing() {
        server.respondToTokenRequestWith("{\"access_token_token_expired\":\"2099-01-01 00:00:00\"}");

        assertThrows(ExchangeException.class, () -> provider.currentToken());
    }

    @Test
    void currentTokenThrowsExchangeExceptionWhenExpiryFieldIsUnparseable() {
        server.respondToTokenRequestWith("{\"access_token\":\"tok\",\"access_token_token_expired\":\"not-a-date\"}");

        assertThrows(ExchangeException.class, () -> provider.currentToken());
    }

    private static String farFutureTokenResponse(String token) {
        String farFutureExpiry = ZonedDateTime.now(ZoneId.of("Asia/Seoul"))
                .plusDays(1)
                .format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        return "{\"access_token\":\"" + token + "\",\"access_token_token_expired\":\"" + farFutureExpiry + "\"}";
    }
}
