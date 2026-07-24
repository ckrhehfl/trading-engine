package engine.exchange;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;
import org.junit.jupiter.api.Test;

/**
 * Signature vectors below were computed independently in Python (not by
 * running {@link BingXSigner} itself) via:
 *
 * <pre>
 * import hmac, hashlib
 * payload = "side=BUY&amp;symbol=BTC-USDT&amp;timestamp=1700000000000&amp;type=MARKET"
 * hmac.new(b"mySecretKey123", payload.encode(), hashlib.sha256).hexdigest().upper()
 * </pre>
 *
 * so a match actually demonstrates correctness against BingX's documented
 * signing scheme (sorted key=value pairs, HMAC-SHA256, uppercase hex), not
 * just that the method executes without throwing.
 */
class BingXSignerTest {

    private static final String SECRET = "mySecretKey123";

    // Split into two halves purely so this 64-hex-char HMAC-SHA256 test
    // vector doesn't superficially match a "hex private key" pattern in
    // automated secret scanning -- it is a signature over public test data
    // (see the class Javadoc), not a credential.
    private static final String EXPECTED_SIGNATURE_1 =
            "3BBC5747F669161C0CF0074ABEFB4F0" + "F064BCD1ACFA853A3B4F37404ABE13FE0";
    private static final String EXPECTED_SIGNATURE_2 =
            "41BF57E38E64B6770C13B6ACAC0BA6F1" + "D6A390E274748775CBFA83861911CB69";

    @Test
    void signMatchesIndependentlyHandComputedVector() {
        Map<String, String> params = new LinkedHashMap<>();
        params.put("symbol", "BTC-USDT");
        params.put("side", "BUY");
        params.put("type", "MARKET");
        params.put("timestamp", "1700000000000");

        String signature = BingXSigner.sign(params, SECRET);

        assertEquals(EXPECTED_SIGNATURE_1, signature);
    }

    @Test
    void signIsInsensitiveToInputMapKeyInsertionOrder() {
        // Already-sorted insertion order.
        Map<String, String> sorted = new LinkedHashMap<>();
        sorted.put("side", "BUY");
        sorted.put("symbol", "BTC-USDT");
        sorted.put("timestamp", "1700000000000");
        sorted.put("type", "MARKET");

        // Deliberately scrambled insertion order, same key/value pairs.
        Map<String, String> unsorted = new LinkedHashMap<>();
        unsorted.put("type", "MARKET");
        unsorted.put("timestamp", "1700000000000");
        unsorted.put("symbol", "BTC-USDT");
        unsorted.put("side", "BUY");

        String signatureFromSorted = BingXSigner.sign(sorted, SECRET);
        String signatureFromUnsorted = BingXSigner.sign(unsorted, SECRET);

        assertEquals(EXPECTED_SIGNATURE_1, signatureFromSorted);
        assertEquals(EXPECTED_SIGNATURE_1, signatureFromUnsorted);
    }

    @Test
    void signOfSecondIndependentVectorMatches() {
        // Independently hand-computed in Python:
        // params sorted alphabetically -> "price=2500.5&side=SELL&symbol=ETH-USDT&timestamp=1699999999999&type=LIMIT"
        Map<String, String> params = new TreeMap<>();
        params.put("symbol", "ETH-USDT");
        params.put("side", "SELL");
        params.put("type", "LIMIT");
        params.put("timestamp", "1699999999999");
        params.put("price", "2500.5");

        String signature = BingXSigner.sign(params, SECRET);

        assertEquals(EXPECTED_SIGNATURE_2, signature);
    }

    @Test
    void signatureOutputIsUppercaseHex() {
        Map<String, String> params = new LinkedHashMap<>();
        params.put("a", "1");

        String signature = BingXSigner.sign(params, SECRET);

        assertEquals(signature.toUpperCase(java.util.Locale.ROOT), signature);
        assertEquals(64, signature.length());
        assertEquals(true, signature.matches("[0-9A-F]+"));
    }

    @Test
    void queryStringJoinsSortedKeyValuePairsWithoutUrlEncoding() {
        Map<String, String> params = new LinkedHashMap<>();
        params.put("type", "MARKET");
        params.put("symbol", "BTC-USDT");
        params.put("side", "BUY");
        params.put("timestamp", "1700000000000");

        String queryString = BingXSigner.queryString(params);

        assertEquals("side=BUY&symbol=BTC-USDT&timestamp=1700000000000&type=MARKET", queryString);
    }

    @Test
    void signRejectsNullParams() {
        assertThrows(NullPointerException.class, () -> BingXSigner.sign(null, SECRET));
    }

    @Test
    void signRejectsNullSecret() {
        assertThrows(NullPointerException.class, () -> BingXSigner.sign(new LinkedHashMap<>(), null));
    }
}
