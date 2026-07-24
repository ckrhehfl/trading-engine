package engine.exchange;

/**
 * Umbrella exception for anything that goes wrong talking to an exchange:
 * non-2xx HTTP responses, network I/O failures, and exchange-reported error
 * payloads (e.g. BingX's non-zero {@code code} field). Unchecked, matching
 * this codebase's existing exception style — no checked exceptions appear
 * anywhere else in this repo (see {@code engine.oms.Order}).
 */
public final class ExchangeException extends RuntimeException {

    public ExchangeException(String message) {
        super(message);
    }

    public ExchangeException(String message, Throwable cause) {
        super(message, cause);
    }
}
