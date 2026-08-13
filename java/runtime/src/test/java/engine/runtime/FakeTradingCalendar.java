package engine.runtime;

import java.time.Instant;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Hand-written {@link TradingCalendar} test double -- same no-mocking-
 * framework convention as {@link FakeExchangeAdapter}/{@link
 * FakePriceFeed}. Settable, ignores the {@link Instant} argument
 * (deliberately -- the tests that use this care only about whether {@link
 * PaperTradingApp#runTick()} obeys whatever {@link #isOpen} currently
 * returns, not about real KRX calendar logic, which {@code
 * KrxMarketCalendarTest} already covers independently). Counts calls so a
 * test can also assert the gate was actually consulted.
 */
final class FakeTradingCalendar implements TradingCalendar {

    private volatile boolean open;
    private final AtomicInteger callCount = new AtomicInteger();

    FakeTradingCalendar(boolean open) {
        this.open = open;
    }

    void setOpen(boolean open) {
        this.open = open;
    }

    int callCount() {
        return callCount.get();
    }

    @Override
    public boolean isOpen(Instant now) {
        callCount.incrementAndGet();
        return open;
    }
}
