"""Invented holding forecasts with separately adjudicated execution."""
from datetime import datetime, timezone

import pytest

from research.intraday_chart_management import holding_decision
from research.intraday_ed_inputs import MINUTE, InputRefusal


ENTRY = datetime(2026, 1, 5, 10, 3, tzinfo=timezone.utc)


def update(history, step, forecast):
    return holding_decision(ENTRY, history, ENTRY + (step - 1) * MINUTE, forecast)


def test_two_weak_forecasts_reduce_then_exit_at_strictly_later_open():
    reduced = update((), 5, 0.0)
    assert reduced.action == "reduce_half" and reduced.execute_at == ENTRY + 5 * MINUTE
    closed = update((reduced,), 10, -0.01)
    assert closed.action == "exit" and closed.execute_at == ENTRY + 10 * MINUTE
    with pytest.raises(InputRefusal, match="already_finished"):
        update((reduced, closed), 15, -0.2)


@pytest.mark.parametrize("interrupt", [None, 0.02])
def test_unknown_or_positive_forecast_breaks_streak_without_repeating_reduction(interrupt):
    reduced = update((), 5, -0.1)
    hold = update((reduced,), 10, interrupt)
    assert hold.action == "hold" and hold.execute_at is None
    next_weak = update((reduced, hold), 15, -0.1)
    assert next_weak.action == "hold" and next_weak.reason == "reduction_already_requested"


def test_skipping_a_clock_or_using_a_nonfinite_prediction_refuses():
    with pytest.raises(InputRefusal, match="clock_required"):
        update((), 10, 0.1)
    with pytest.raises(InputRefusal, match="finite"):
        update((), 5, float("nan"))
