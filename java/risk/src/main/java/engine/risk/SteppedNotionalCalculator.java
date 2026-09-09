package engine.risk;

import engine.schemas.OrderIntent;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Objects;
import java.util.Optional;

/**
 * A {@link NotionalCalculator} for an instrument that trades 1:1 with the
 * priced unit but only in whole multiples of an exchange-defined
 * <em>step</em> -- e.g. BingX {@code BTC-USDT}, where one quantity unit is
 * one BTC and the published {@code size} is {@code 0.0001}.
 *
 * <p>This is {@link SimpleNotionalCalculator} plus the step constraint, and
 * it exists because omitting that constraint caused a real incident.
 *
 * <h2>The incident this closes</h2>
 *
 * <p>On 2026-09-08 {@code daily-tsmom-ensemble} produced this project's
 * first real live signal, carrying a quantity of <strong>29 fractional
 * digits</strong> -- 28 significant --
 * ({@code 0.02318401746487214694970992456}), from a {@code Decimal}
 * division under Python's default 28-digit context, which nothing
 * downstream reduced because nothing downstream was asked to. BingX
 * accepted the order and filled {@code 0.0231}: that same quantity
 * truncated to its 4-decimal step.
 *
 * <p>The fill was therefore permanently smaller than this project's own
 * {@code approvedQuantity}, so {@code ExchangeOrderExecutor} held the order
 * at {@code PARTIALLY_FILLED} while the venue reported {@code FILLED},
 * correctly refused to reconcile the two, and dropped it from pending
 * tracking. The {@code Reconciler} then found {@code ORPHANED_IN_BROKER}
 * and tripped the kill switch -- every one of those refusals working as
 * designed, and none of them able to prevent the malformed order in the
 * first place. Full account: {@code
 * .planning/quantity-precision-discuss.md}, GitHub issue #151.
 *
 * <h2>Why the fix lives here and not at the adapter</h2>
 *
 * <p>Rounding inside {@code BingXAdapter} would make this worse, not
 * better. The {@code Order} carrying {@code approvedQuantity} is built by
 * {@code OrderPipeline} <em>before</em> the adapter is called, so rounding
 * only the value put on the wire leaves {@code approvedQuantity} at 29
 * digits and guarantees the venue's fill can never equal it -- turning an
 * accident into a certainty.
 *
 * <p>So the constraint is enforced where {@link RiskGateway} already asks
 * about it, through {@link #quantityRejectionReason}, before any {@code
 * Order} exists. That hook is not new: {@link
 * FixedMultiplierNotionalCalculator} has used it since PR #105 to reject a
 * fractional KOSPI200 contract count. {@link SimpleNotionalCalculator} is
 * the one implementation that opted out, and that opt-out is the defect.
 *
 * <h2>Reject, never silently round</h2>
 *
 * <p>This class <strong>refuses</strong> a non-conforming quantity rather
 * than adjusting it. Adjusting would mean {@link RiskGateway} silently
 * changing an approved order size for a reason unrelated to risk, and it
 * would hide a caller that is producing malformed quantities. Emitting a
 * step-valid quantity is the caller's job -- {@code
 * live/generate_daily_signal.py} does it -- and this is the independent
 * check that fails closed when the caller gets it wrong, loudly and
 * before any venue sees it.
 */
public final class SteppedNotionalCalculator implements NotionalCalculator {

    private final BigDecimal step;

    /**
     * @param step the exchange's own minimum quantity increment (BingX
     *     publishes it as {@code size} on {@code /quote/contracts}), which
     *     must be positive
     */
    public SteppedNotionalCalculator(BigDecimal step) {
        Objects.requireNonNull(step, "step is required");
        if (step.signum() <= 0) {
            throw new IllegalArgumentException("step must be positive, got " + step);
        }
        this.step = step;
    }

    /** The step this calculator enforces. */
    public BigDecimal step() {
        return step;
    }

    @Override
    public Optional<String> quantityRejectionReason(OrderIntent intent) {
        // No positivity check here on purpose: OrderIntent's own compact
        // constructor already runs `Decimals.requirePositive(quantity)`, so
        // a non-positive quantity cannot reach this method through any
        // construction path, Jackson included. A branch that cannot be
        // reached is a branch nobody can prove works, and this repository
        // has enough inert guards on record already. SteppedNotionalCalculatorTest
        // pins that assumption instead, so relaxing OrderIntent breaks loudly
        // here rather than silently.
        BigDecimal quantity = intent.quantity();
        // `remainder` compares by value, not by scale, so 0.02310 and
        // 0.0231 both pass -- rejecting on scale would refuse a valid
        // quantity written with trailing zeros.
        if (quantity.remainder(step).signum() != 0) {
            return Optional.of(
                    "quantity "
                            + quantity.toPlainString()
                            + " is not a multiple of this instrument's step "
                            + step.toPlainString()
                            + " -- the venue would truncate it, leaving the fill permanently"
                            + " smaller than the approved quantity");
        }
        return Optional.empty();
    }

    @Override
    public BigDecimal notionalOf(OrderIntent intent, BigDecimal price) {
        return intent.quantity().multiply(price);
    }

    @Override
    public BigDecimal maxQuantityFor(BigDecimal maxNotional, BigDecimal price) {
        // Down to the step, not to a fixed decimal count. SimpleNotionalCalculator
        // clamps to 8 decimals, which BingX (4) cannot accept either -- so its
        // clamp path reproduces the same defect on precisely the orders that
        // were too large. Dividing by the step, flooring, and multiplying back
        // yields a value that is both within budget and submittable.
        BigDecimal steps = maxNotional.divide(price.multiply(step), 0, RoundingMode.DOWN);
        return steps.signum() <= 0 ? BigDecimal.ZERO : steps.multiply(step);
    }
}
