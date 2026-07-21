package engine.execution;

import engine.oms.Order;
import engine.schemas.OrderType;
import engine.schemas.Side;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Simulated exchange counterparty for paper trading — acknowledges an
 * approved {@link Order} and resolves it to a {@link Fill} against
 * caller-supplied prices. Does not fetch prices itself; a real
 * network/BingX client is Priority #7's ({@code ExchangeAdapter}) job,
 * kept decoupled here so this stays testable with plain injected prices.
 *
 * <p>Unlike {@code python/backtest}, this reacts to live-ish price ticks
 * rather than replaying historical bars — there is no lookahead concept
 * here (nothing is being replayed), so {@link Instant#now()} for fill
 * timestamps is correct, not a bug.
 */
public final class PaperBroker {

    private static final Logger log = LoggerFactory.getLogger(PaperBroker.class);
    private static final BigDecimal BPS_DIVISOR = new BigDecimal("10000");
    private static final BigDecimal ONE = BigDecimal.ONE;

    private final BigDecimal feeBps;
    private final BigDecimal slippageBps;
    private final Map<UUID, Order> pendingOrders = new ConcurrentHashMap<>();

    public PaperBroker(BigDecimal feeBps, BigDecimal slippageBps) {
        this.feeBps = Objects.requireNonNull(feeBps, "feeBps is required");
        this.slippageBps = Objects.requireNonNull(slippageBps, "slippageBps is required");
    }

    public Map<UUID, Order> pendingOrders() {
        return Collections.unmodifiableMap(pendingOrders);
    }

    public Optional<Fill> submit(Order order, BigDecimal currentPrice) {
        Objects.requireNonNull(order, "order is required");
        Objects.requireNonNull(currentPrice, "currentPrice is required");

        order.submit();
        order.acknowledge("PAPER-" + UUID.randomUUID());

        Optional<Fill> fill = tryFill(order, currentPrice);
        if (fill.isEmpty()) {
            pendingOrders.put(order.clientOrderId(), order);
        }
        return fill;
    }

    public List<Fill> onPriceUpdate(String symbol, BigDecimal price) {
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(price, "price is required");

        List<Fill> fills = new ArrayList<>();
        for (Order order : new ArrayList<>(pendingOrders.values())) {
            if (!order.symbol().equals(symbol)) {
                continue;
            }
            tryFill(order, price).ifPresent(fill -> {
                fills.add(fill);
                pendingOrders.remove(order.clientOrderId());
            });
        }
        return fills;
    }

    public void cancel(Order order) {
        Objects.requireNonNull(order, "order is required");
        order.requestCancel();
        order.confirmCancel();
        pendingOrders.remove(order.clientOrderId());
    }

    /**
     * Attempts an immediate fill and returns it if marketable. GUARDED_MARKET
     * always fills, adjusted by slippage against the trader — a market order
     * doesn't control its execution price. LIMIT fills at {@code currentPrice}
     * (full price improvement, never capped at the limit) only when at least
     * as good as the limit, and slippage is never applied to it — the whole
     * point of a limit order is a price guarantee, it fills at the limit or
     * better or not at all, never worse.
     */
    private Optional<Fill> tryFill(Order order, BigDecimal currentPrice) {
        BigDecimal fillPrice;
        if (order.orderType() == OrderType.GUARDED_MARKET) {
            BigDecimal slippageFactor = slippageBps.divide(BPS_DIVISOR);
            fillPrice = order.side() == Side.LONG
                    ? currentPrice.multiply(ONE.add(slippageFactor))
                    : currentPrice.multiply(ONE.subtract(slippageFactor));
        } else { // LIMIT
            boolean marketable = order.side() == Side.LONG
                    ? currentPrice.compareTo(order.limitPrice()) <= 0
                    : currentPrice.compareTo(order.limitPrice()) >= 0;
            if (!marketable) {
                return Optional.empty();
            }
            fillPrice = currentPrice;
        }

        BigDecimal quantity = order.approvedQuantity();
        BigDecimal notional = quantity.multiply(fillPrice);
        BigDecimal fee = notional.multiply(feeBps).divide(BPS_DIVISOR);

        order.fill(quantity);
        log.info("order {} filled at {} (fee={})", order.clientOrderId(), fillPrice, fee);

        return Optional.of(new Fill(
                order.clientOrderId(), order.symbol(), fillPrice, quantity, notional, fee, Instant.now()));
    }
}
