"""EF paired fold geometry, support and independent label cutoff."""
from datetime import date, timedelta
from fractions import Fraction

import pytest

from research.intraday_chart_folds import EntrySupport, ObservedLabel, select_fold
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_pattern_calibration import session_open


DAYS = tuple((date(2025, 2, 1) + timedelta(days=i)).isoformat() for i in range(257))
SYMBOL = "KRX:000001"


def row(day, offset=60, *, chart=True, peers=True, symbol=SYMBOL):
    return EntrySupport((day, symbol, offset), chart, peers)


def observed(key):
    day, _, offset = key
    return ObservedLabel(Fraction(1, 250), session_open(day) + (offset + 36) * MINUTE)


def test_identical_support_selects_past_labels_only_and_retains_every_evaluation_row():
    past = [row(day, offset) for day in DAYS[:30] for offset in (60, 75, 90, 105)]
    past.append(row(DAYS[60], chart=False))
    future = [row(DAYS[100]), row(DAYS[100], 75, peers=False), row(DAYS[100], 90, chart=False)]
    requested = []

    def labels(key):
        assert key[0] in DAYS[:30]
        requested.append(key)
        return (observed(key),) + (None,) * 5

    split = select_fold(DAYS, "S1", 1, [*past, *future], labels)
    assert split.supported and len(requested) == len(split.training) == 120
    assert split.train_mean_pct == Fraction(2, 5)
    assert [test.status for test in split.evaluation] == ["common", "chart_only", "chart_unavailable"]
    assert split.fold.test_sessions == DAYS[100:132]
    assert split.fold.first_test_opening == session_open(DAYS[100])


def test_maturity_purges_primary_and_masks_each_auxiliary_before_cutoff():
    late = ObservedLabel(Fraction(7), session_open(DAYS[100]))
    first, second, third = (row(DAYS[index]) for index in range(3))
    label_map = {first.key: (late, None, None, None, None, None),
                 second.key: (observed(second.key), late, None, None, None, None),
                 third.key: (None,) * 6}
    split = select_fold(DAYS, "S2", 1, (first, second, third), label_map.__getitem__)
    assert split.training[0].key == second.key
    assert split.training[0].outcomes == (observed(second.key),) + (None,) * 5
    assert dict(split.counts)["training_primary_immature"] == 1
    assert dict(split.counts)["training_primary_unavailable"] == 1
    assert not split.supported and split.train_mean_pct is None


def test_future_exceptional_close_is_filtered_without_outcome_lookup():
    calendar = tuple((date(2025, 8, 1) + timedelta(days=i)).isoformat() for i in range(257))
    first = calendar.index("2026-01-02")
    assert 132 <= first < 164

    def not_called(key):
        raise AssertionError(key)

    rows = (row("2026-01-02", 270), row("2026-01-02", 285), row("2026-01-02", 300))
    split = select_fold(calendar, "S2", 2, rows, not_called)
    assert [item.status for item in split.evaluation] == ["common", "outside_clock", "outside_clock"]


def test_duplicate_and_early_maturity_are_integrity_failures():
    future = row(DAYS[100])
    with pytest.raises(InputRefusal, match="unordered"):
        select_fold(DAYS, "S1", 1, (future, future), lambda _: None)
    past = row(DAYS[0])
    early = ObservedLabel(Fraction(0), session_open(DAYS[0]))
    with pytest.raises(InputRefusal, match="maturity"):
        select_fold(DAYS, "S1", 1, (past,), lambda _: (early,) + (None,) * 5)
