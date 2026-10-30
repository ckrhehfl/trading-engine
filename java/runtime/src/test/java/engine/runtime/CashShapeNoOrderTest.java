package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.ObjectMapper;
import engine.oms.Order;
import engine.oms.OrderState;
import engine.oms.OrderStore;
import engine.risk.AccountState;
import engine.risk.RiskGateway;
import engine.risk.RiskLimits;
import engine.risk.SteppedNotionalCalculator;
import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.SchemaObjectMapper;
import engine.schemas.Side;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * NO_ORDER: invented schema-to-risk arithmetic ending at an in-memory NEW order.
 * Unit step 1 and price multiplier 1 are fictional; neither physical shares nor
 * archived vendor-price units are certified. No executor, app or scheduler is built.
 * This does not verify cash inventory, costs, price clocks or a real session.
 */
class CashShapeNoOrderTest {

    private static final String SYMBOL = "MOCK:KRX:005930";
    private static final Instant CREATED_AT = Instant.parse("2026-01-05T00:30:00Z");
    private static final BigDecimal PRICE = new BigDecimal("70000");
    private final ObjectMapper mapper = SchemaObjectMapper.create();

    @Test
    void decimalStringFixtureClampsInventedIntegerUnitsAndRemainsNew(@TempDir Path tempDir)
            throws IOException {
        OrderIntent intent = readInventedIntent(tempDir, "50");
        FakeTradingCalendar calendar = new FakeTradingCalendar(true);
        // Explicit fixture assumption only: OrderPipeline has no calendar dependency.
        assertTrue(calendar.isOpen(intent.createdAt()));
        FakePriceFeed prices = new FakePriceFeed(PRICE);
        AccountState account = fictionalAccount();
        RiskLimits limits = RiskLimits.canary();
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = new OrderPipeline(
                new RiskGateway(limits, new SteppedNotionalCalculator(BigDecimal.ONE)), store);

        BigDecimal referencePrice = prices.latestPrice(intent.symbol());
        Order order = pipeline.submitIntent(intent, referencePrice, account).orElseThrow();

        assertEquals(SYMBOL, order.symbol());
        assertEquals(Side.LONG, order.side());
        assertEquals(OrderType.GUARDED_MARKET, order.orderType());
        assertEquals(new BigDecimal("50"), order.requestedQuantity());
        assertEquals(new BigDecimal("28"), order.approvedQuantity());
        assertEquals(BigDecimal.ZERO, order.approvedQuantity().remainder(BigDecimal.ONE));
        BigDecimal maxNotional = account.equity().multiply(limits.maxOrderNotionalPercent());
        assertTrue(order.approvedQuantity().multiply(referencePrice).compareTo(maxNotional) <= 0);
        assertTrue(order.approvedQuantity().add(BigDecimal.ONE).multiply(referencePrice)
                .compareTo(maxNotional) > 0);
        assertEquals(limits.baseLeverage(), order.approvedLeverage());
        assertSame(order, store.findByClientOrderId(intent.intentId()).orElseThrow());
        assertEquals(OrderState.NEW, order.state());
        assertEquals(List.of(OrderState.NEW), order.history().stream().map(t -> t.state()).toList());
        assertNull(order.exchangeOrderId());
        assertEquals(BigDecimal.ZERO, order.filledQuantity());
    }

    @Test
    void fractionalInventedUnitsBelowNotionalCapAreRejectedWithoutCreatingOrder(@TempDir Path tempDir)
            throws IOException {
        OrderIntent intent = readInventedIntent(tempDir, "28.5");
        AccountState account = fictionalAccount();
        RiskLimits limits = RiskLimits.canary();
        assertTrue(intent.quantity().multiply(PRICE)
                .compareTo(account.equity().multiply(limits.maxOrderNotionalPercent())) < 0);
        OrderStore store = new OrderStore();
        OrderPipeline pipeline = new OrderPipeline(
                new RiskGateway(limits, new SteppedNotionalCalculator(BigDecimal.ONE)), store);

        assertTrue(pipeline.submitIntent(intent, PRICE, account).isEmpty());
        assertTrue(store.findByClientOrderId(intent.intentId()).isEmpty());
    }

    private OrderIntent readInventedIntent(Path tempDir, String quantity) throws IOException {
        OrderIntent original = new OrderIntent(
                UUID.fromString("00000000-0000-4000-8000-000000000001"),
                SYMBOL,
                Side.LONG,
                OrderType.GUARDED_MARKET,
                new BigDecimal(quantity),
                null,
                "1m",
                CREATED_AT);
        String json = mapper.writeValueAsString(original);
        assertTrue(mapper.readTree(json).get("quantity").isTextual());
        assertEquals(quantity, mapper.readTree(json).get("quantity").textValue());
        Path fixture = tempDir.resolve("invented-no-order.json");
        Files.writeString(fixture, json);
        OrderIntent decoded = new FileSignalSource(fixture).nextSignal().orElseThrow();
        assertEquals(original, decoded);
        return decoded;
    }

    private AccountState fictionalAccount() {
        return new AccountState(new BigDecimal("100000000"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
    }
}
