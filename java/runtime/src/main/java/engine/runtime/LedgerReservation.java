package engine.runtime;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/**
 * One in-flight reservation against a {@link AccountLedger}'s {@code
 * allocatedVirtualCapital} -- the shared-ledger analogue of what {@code
 * AccountStateProvider#reserveForIntent} produces process-locally today
 * (see that interface's Javadoc). Recorded under {@link AccountLedgerLock}
 * by whichever {@code kis-paper} OS process reserved it; read back by every
 * process sharing the same ledger file so a sibling process's committed
 * exposure is visible before this process reserves its own.
 *
 * <p>{@code clientOrderId} keys a reservation, matching {@code
 * TradingLoop}/{@code OrderPipeline}'s own existing per-order identifier
 * convention (not {@code intentId} -- a real {@code SharedKisAccountLedger}
 * implementation, Task C, decides which identifier it actually keys by;
 * this record only carries the field, it does not prescribe which ID a
 * caller populates it with). {@code processId}/{@code hostname} are
 * debugging/attribution only, matching this class's own field-level
 * comment in the governing plan -- neither is a correctness input to any
 * lock or reservation-sizing decision.
 *
 * <p>Package-private, like every other class in this file's "shared
 * ledger" group except the {@link AccountStateProvider} interface itself
 * -- see the governing plan's "2. The shared ledger" section header.
 */
record LedgerReservation(
        UUID clientOrderId,
        String symbol,
        long processId,
        String hostname,
        BigDecimal notional,
        Instant reservedAt) {

    LedgerReservation {
        Objects.requireNonNull(clientOrderId, "clientOrderId is required");
        Objects.requireNonNull(symbol, "symbol is required");
        Objects.requireNonNull(hostname, "hostname is required");
        Objects.requireNonNull(notional, "notional is required");
        Objects.requireNonNull(reservedAt, "reservedAt is required");
    }
}
