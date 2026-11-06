from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_highvol_labels import label_path


OPEN = datetime(2026, 1, 30, 9, tzinfo=timezone(timedelta(hours=9)))


def at(minutes):
    return OPEN + minutes * MINUTE


def row(offset, prices):
    return Minute("TARGET", at(offset), at(offset + 3), tuple(map(Decimal, prices)), None,
                  volume_state="missing")


def session(*changes):
    rows = {at(offset): row(offset, (100, 101, 99, 100)) for offset in range(18, 49)}
    rows.update((item.start_at, item) for item in changes)
    return SessionInputs("TARGET", OPEN, at(390), rows)


def label(value, *, cutoff=51):
    return label_path(value, at(17), Fraction(98), Fraction(104), horizon_minutes=30,
                      label_cutoff=at(cutoff))


@pytest.mark.parametrize(("prices", "order"), [
    ((100, 104, 98, 103), "same_minute_unknown"),
    ((104, 105, 98, 103), "target_first"),
    ((98, 105, 97, 100), "stop_first"),
])
def test_open_can_resolve_two_touches_and_later_gaps_cannot_change_order(prices, order):
    value = session(row(20, prices), row(48, (102, 110, 80, 101)))
    value.minutes.pop(at(21))
    result = label(value)
    assert (result.first_touch.status, result.first_touch.minute, result.first_touch.mature_at) == (
        order, at(20), at(23))
    assert result.fixed_gross == Fraction(1, 50)


def test_gap_before_a_later_touch_prevents_order_but_keeps_fixed_return():
    value = session(row(20, (100, 105, 99, 103)))
    value.minutes.pop(at(19))
    result = label(value)
    assert result.first_touch.status == "unavailable"
    assert result.first_touch.issue.reason == "missing_minute"
    assert result.first_touch.minute == at(19)
    assert result.fixed_gross == 0


def test_time_path_ignores_exit_minute_extrema_and_tracks_distinct_maturity():
    value = session(row(48, (100, 120, 80, 101)))
    result = label(value, cutoff=50)
    assert result.first_touch.status == "time"
    assert result.first_touch.mature_at == at(50)
    assert result.fixed_exit_issue.reason == "minute_not_mature_at_cutoff"
    assert result.fixed_gross is None


def test_refused_entry_retains_the_open_and_never_reads_the_outcome():
    value = session(row(18, (104, 105, 103, 104)))

    class OnlyEntry(dict):
        def get(self, key, default=None):
            assert key == at(18)
            return super().get(key, default)

    result = label(replace(value, minutes=OnlyEntry(value.minutes)))
    assert result.status == "entry_refused"
    assert result.entry_open == 104
    assert result.first_touch is None
