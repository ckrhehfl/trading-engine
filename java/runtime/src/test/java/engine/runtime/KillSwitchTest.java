package engine.runtime;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class KillSwitchTest {

    @Test
    void startsNotTripped() {
        assertFalse(new KillSwitch().isTripped());
    }

    @Test
    void tripSetsTrippedTrue() {
        KillSwitch killSwitch = new KillSwitch();

        killSwitch.trip();

        assertTrue(killSwitch.isTripped());
    }

    @Test
    void resetClearsTrippedState() {
        KillSwitch killSwitch = new KillSwitch();
        killSwitch.trip();

        killSwitch.reset();

        assertFalse(killSwitch.isTripped());
    }

    @Test
    void tripIsIdempotent() {
        KillSwitch killSwitch = new KillSwitch();

        killSwitch.trip();
        killSwitch.trip();

        assertTrue(killSwitch.isTripped());
    }
}
