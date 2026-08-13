package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;
import org.junit.jupiter.api.Test;

class AlwaysOpenTradingCalendarTest {

    @Test
    void isOpenTrueForArbitraryInstant() {
        AlwaysOpenTradingCalendar calendar = new AlwaysOpenTradingCalendar();

        assertTrue(calendar.isOpen(Instant.parse("2020-01-01T00:00:00Z")));
        assertTrue(calendar.isOpen(Instant.now()));
        assertTrue(calendar.isOpen(Instant.parse("2099-12-31T23:59:59Z")));
    }

    /** Same null-rejection contract as {@link TradingCalendar#isOpen} documents -- see that method's own Javadoc. */
    @Test
    void isOpenRejectsNullInstant() {
        AlwaysOpenTradingCalendar calendar = new AlwaysOpenTradingCalendar();

        assertThrows(NullPointerException.class, () -> calendar.isOpen(null));
    }
}
