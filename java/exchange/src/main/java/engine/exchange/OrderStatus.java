package engine.exchange;

import java.math.BigDecimal;

/**
 * A venue's own view of one order, as returned by {@link
 * ExchangeAdapter#queryOrder}.
 *
 * <p>{@code commission} is the fee the venue says it actually charged, and
 * is <b>nullable</b> — not every venue reports one, and not every poll of
 * an order that does carries it. BingX reports a charged fee as a
 * **negative** number (observed: {@code "-0.032441"}); KIS reports none at
 * all. A caller must therefore treat absence as "unknown", never as zero:
 * a fabricated zero reads as "the venue charged nothing", which is a
 * measurement rather than an absence.
 *
 * <p>The field was added 2026-09-12 (GitHub issue #163). Until then
 * {@code ExchangeOrderExecutor} discarded the venue's real figure and
 * substituted its own {@code notional × feeBps} model — the same formula
 * {@code PaperBroker} and {@code simulate_fill} use — so the live loop and
 * the backtest agreed on fees by construction and no evidence about
 * whether that model is right could accumulate. {@code
 * ExchangeOrderExecutor}'s own Javadoc had named capturing it as work for
 * a later task; this is that task.
 */
public record OrderStatus(
        String exchangeOrderId,
        String status,
        BigDecimal filledQuantity,
        BigDecimal avgPrice,
        BigDecimal commission) {

    /**
     * The four-argument form, for a venue or a call site that has no
     * commission to report. Kept so adding the field stayed additive
     * rather than breaking every existing construction site.
     */
    public OrderStatus(
            String exchangeOrderId, String status, BigDecimal filledQuantity, BigDecimal avgPrice) {
        this(exchangeOrderId, status, filledQuantity, avgPrice, null);
    }
}
