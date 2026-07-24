package engine.exchange;

import java.nio.charset.StandardCharsets;
import java.security.InvalidKeyException;
import java.security.NoSuchAlgorithmException;
import java.util.Map;
import java.util.Objects;
import java.util.TreeMap;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * BingX request signing per CLAUDE.md's "Exchange API Facts — BingX"
 * (Documented, not yet empirically verified): params sorted alphabetically
 * by key, joined as {@code key=value&key=value...} with no URL-encoding,
 * HMAC-SHA256 over that string using the API secret, hex-encoded uppercase.
 *
 * <p>No URL-encoding is applied here, matching how {@link BingXAdapter}
 * builds the actual request query string ({@link #queryString} is reused
 * by both) — signing and sending must never disagree on this, or every
 * signature would be rejected by the exchange.
 */
public final class BingXSigner {

    private static final String HMAC_ALGORITHM = "HmacSHA256";

    private BingXSigner() {}

    /** Params joined as {@code key=value&key=value...}, sorted alphabetically by key. */
    public static String queryString(Map<String, String> params) {
        Objects.requireNonNull(params, "params is required");
        TreeMap<String, String> sorted = new TreeMap<>(params);
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> entry : sorted.entrySet()) {
            if (sb.length() > 0) {
                sb.append('&');
            }
            sb.append(entry.getKey()).append('=').append(entry.getValue());
        }
        return sb.toString();
    }

    /** HMAC-SHA256 of {@link #queryString(Map)}, hex-encoded uppercase. */
    public static String sign(Map<String, String> params, String apiSecret) {
        Objects.requireNonNull(params, "params is required");
        Objects.requireNonNull(apiSecret, "apiSecret is required");
        String payload = queryString(params);
        try {
            Mac mac = Mac.getInstance(HMAC_ALGORITHM);
            mac.init(new SecretKeySpec(apiSecret.getBytes(StandardCharsets.UTF_8), HMAC_ALGORITHM));
            byte[] digest = mac.doFinal(payload.getBytes(StandardCharsets.UTF_8));
            return toUppercaseHex(digest);
        } catch (NoSuchAlgorithmException | InvalidKeyException e) {
            // Both are effectively "can't happen" here: HmacSHA256 is a
            // standard JDK algorithm and SecretKeySpec never rejects a
            // non-empty key. Wrapped unchecked to match this codebase's
            // no-checked-exceptions convention rather than forcing every
            // caller to declare a throws clause for a case that isn't
            // actually reachable in practice.
            throw new IllegalStateException("failed to compute BingX HMAC-SHA256 signature", e);
        }
    }

    private static String toUppercaseHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            sb.append(String.format("%02X", b));
        }
        return sb.toString();
    }
}
