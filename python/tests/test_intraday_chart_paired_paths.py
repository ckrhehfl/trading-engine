"""Hand-calculated fixed and dynamic paths sharing one nominal lot."""
from fractions import Fraction
from types import SimpleNamespace

from research.intraday_chart_paired_paths import paired_exit_paths


KEY = "2025-09-04", "KRX:000001", 60


class Quotes:
    def __init__(self, prices):
        self.prices = prices

    def row(self, day, symbol, offset):
        raw = self.prices[offset]
        return SimpleNamespace(session=day, symbol=symbol, offset=offset,
                               execution_open=None if raw is None else Fraction(raw),
                               execution_state="unavailable" if raw is None else "available")


def test_paths_preserve_initial_lot_cost_and_exposure():
    quotes = Quotes({60: 400_000, 65: 410_000, 70: 390_000, 90: 350_000})
    fixed, managed = paired_exit_paths(quotes, KEY, cost_bps=33, gross15_at=lambda _: -0.1)
    assert fixed.original_quantity == managed.original_quantity == 5
    assert fixed.original_notional == managed.original_notional == 2_000_000
    assert fixed.initial_cost == managed.initial_cost == 6600
    assert fixed.gross_return == Fraction(-1, 8) and managed.gross_return == Fraction(-1, 200)
    assert managed.net_return == Fraction(-83, 10000)
    assert (fixed.average_hold_minutes, managed.average_hold_minutes) == (30, 8)
    assert [leg.quantity for leg in managed.legs] == [2, 3]


def test_unavailable_action_does_not_become_a_later_exit_or_a_zero_return():
    quotes = Quotes({60: 400_000, 65: None, 90: 350_000})
    fixed, managed = paired_exit_paths(quotes, KEY, cost_bps=43, gross15_at=lambda _: 0.0)
    assert fixed.status == "closed" and managed.status == "unavailable"
    assert managed.net_return is None and managed.average_hold_minutes is None
    assert managed.unresolved_quantity == 5 and len(managed.legs) == len(managed.decisions) == 1
