"""Independent exact held-position payoff calculation for EH path audits."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from research.intraday_ed_inputs import MINUTE, _clock, _require
from research.intraday_scenario_policy import Values


_CLOSE_LAG = 3 * MINUTE


@dataclass(frozen=True)
class PayoffPath:
    """An immutable rebased path with separate as-of close and fill clocks.

    A close may become known at a later cutoff. Once known, the same close
    remains available with exactly the same value at every later cutoff.
    """

    weight: Fraction
    close_at: Callable[[datetime, datetime], Fraction | None]
    open_at: Callable[[datetime], Fraction | None]


def _quote(price: Fraction | None) -> Fraction | None:
    _require(price is None or type(price) is Fraction and price > 0, "eh_payoff_reference_invalid_quote")
    return price


def _liquidation_price(
    path: PayoffPath, decision: datetime, unreviewed: datetime, cap: datetime, stop: Fraction,
) -> tuple[Fraction | None, bool]:
    """Return the sale price and whether the initial decision safely held."""
    first_decision = decision
    while decision < cap:
        missing = breached = False
        through = decision - _CLOSE_LAG
        while unreviewed <= through:
            close = _quote(path.close_at(unreviewed, decision))
            unreviewed += MINUTE
            if close is None:
                missing = True
            elif close <= stop:
                breached = True
                break
        execute = decision + MINUTE
        if breached or execute == cap:
            return _quote(path.open_at(execute)), decision > first_decision
        if missing:
            return None, False
        decision = execute
    return None, False


def _utility(paths: tuple[PayoffPath, ...], payoffs: Iterable[Fraction], loss_scale: Fraction) -> Fraction:
    return sum((path.weight * (payoff * loss_scale if payoff < 0 else payoff)
                for path, payoff in zip(paths, payoffs, strict=True)), Fraction())


def score_held_payoffs(
    paths: tuple[PayoffPath, ...], *, at: datetime, cap: datetime, entered_at: datetime,
    quantity: int, half_reduced: bool, stop: Fraction, visible_mark: Fraction, downside_penalty: Fraction,
) -> Values | None:
    """Score feasible actions with independent sales and the frozen visible close.

    The initial mark is the close for the minute starting three minutes before
    the decision. Existing cash and paid entry costs cancel from the change in
    marked wealth. A missing branch payoff with positive weight prevents the
    entire comparison, rather than becoming a zero or a reweighted sample.
    """
    for clock in (at, cap, entered_at):
        _clock(clock)
        _require(clock.second == clock.microsecond == 0, "eh_payoff_reference_off_minute_grid")
    _require(entered_at < at and MINUTE <= cap - at <= 30 * MINUTE
             and type(quantity) is int and quantity > 0 and type(half_reduced) is bool
             and type(stop) is Fraction and stop > 0 and type(visible_mark) is Fraction and visible_mark > 0
             and type(downside_penalty) is Fraction and downside_penalty >= 0,
             "eh_payoff_reference_invalid_position")
    _require(type(paths) is tuple and paths and all(type(path) is PayoffPath
             and type(path.weight) is Fraction and path.weight > 0
             and callable(path.close_at) and callable(path.open_at) for path in paths)
             and sum((path.weight for path in paths), Fraction()) == 1, "eh_payoff_reference_invalid_weights")

    first = at + MINUTE
    unreviewed = max(entered_at, first - _CLOSE_LAG)
    mark_after_entry = at - _CLOSE_LAG >= entered_at
    forced_exit = first == cap or (mark_after_entry and visible_mark <= stop)
    marked_value = quantity * visible_mark
    loss_scale = 1 + downside_penalty
    hold = reduce_half = None
    if not forced_exit:
        hold_sales = tuple(_liquidation_price(path, first, unreviewed, cap, stop) for path in paths)
        if any(price is None for price, _ in hold_sales):
            return None
        hold = _utility(paths, (quantity * price - marked_value for price, _ in hold_sales), loss_scale)
        if not mark_after_entry:
            return Values(at, hold=hold)

    first_sales = tuple(_quote(path.open_at(first)) for path in paths)
    if any(price is None for price in first_sales):
        return None
    if not forced_exit and not half_reduced and quantity > 1:
        sold = quantity // 2
        after_reduction = first + MINUTE
        # When hold passes its first check, the half-sale branch reaches the
        # same next decision. Its catch-up close is either absent by geometry
        # or already known safe. Only in that case can both share liquidation.
        reduction_sales = tuple(
            price if first_safe else _liquidation_price(path, after_reduction, unreviewed, cap, stop)[0]
            for path, (price, first_safe) in zip(paths, hold_sales, strict=True)
        )
        if any(price is None for price in reduction_sales):
            return None
        reduce_half = _utility(paths, (sold * price + (quantity - sold) * terminal - marked_value
                                       for price, terminal in zip(first_sales, reduction_sales, strict=True)), loss_scale)
    exit_value = _utility(paths, (quantity * price - marked_value for price in first_sales), loss_scale)
    return Values(at, hold=hold, reduce_half=reduce_half, exit=exit_value)
