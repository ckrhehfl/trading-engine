package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import engine.schemas.OrderIntent;
import engine.schemas.OrderType;
import engine.schemas.Side;
import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class DummySignalSourceTest {

    @Test
    void firesOnEveryCallWhenEveryNthCallIsOne() {
        DummySignalSource source = new DummySignalSource(
                "BTC-USDT", Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.001"), null, 1);

        assertTrue(source.nextSignal().isPresent());
        assertTrue(source.nextSignal().isPresent());
    }

    @Test
    void emptyOnCallsThatAreNotTheNthCall() {
        DummySignalSource source = new DummySignalSource(
                "BTC-USDT", Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.001"), null, 3);

        assertFalse(source.nextSignal().isPresent()); // call 1
        assertFalse(source.nextSignal().isPresent()); // call 2
        assertTrue(source.nextSignal().isPresent()); // call 3
        assertFalse(source.nextSignal().isPresent()); // call 4
    }

    @Test
    void emittedIntentMatchesConfiguredShape() {
        DummySignalSource source = new DummySignalSource(
                "BTC-USDT", Side.SHORT, OrderType.LIMIT, new BigDecimal("0.002"), new BigDecimal("59000"), 1);

        OrderIntent intent = source.nextSignal().orElseThrow();

        assertEquals("BTC-USDT", intent.symbol());
        assertEquals(Side.SHORT, intent.side());
        assertEquals(OrderType.LIMIT, intent.orderType());
        assertEquals(0, new BigDecimal("0.002").compareTo(intent.quantity()));
        assertEquals(0, new BigDecimal("59000").compareTo(intent.limitPrice()));
    }

    @Test
    void eachEmittedIntentHasADistinctIntentId() {
        DummySignalSource source = new DummySignalSource(
                "BTC-USDT", Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.001"), null, 1);

        OrderIntent first = source.nextSignal().orElseThrow();
        OrderIntent second = source.nextSignal().orElseThrow();

        assertFalse(first.intentId().equals(second.intentId()));
    }

    @Test
    void constructorRejectsNonPositiveEveryNthCall() {
        assertThrows(
                IllegalArgumentException.class,
                () -> new DummySignalSource(
                        "BTC-USDT", Side.LONG, OrderType.GUARDED_MARKET, new BigDecimal("0.001"), null, 0));
    }

    @Test
    void constructorRejectsLimitOrderTypeWithoutALimitPrice() {
        // Delegated to OrderIntent's own validation (not re-implemented
        // here) -- this test proves that delegation actually happens.
        assertThrows(
                IllegalArgumentException.class,
                () -> new DummySignalSource(
                        "BTC-USDT", Side.LONG, OrderType.LIMIT, new BigDecimal("0.001"), null, 1));
    }
}
