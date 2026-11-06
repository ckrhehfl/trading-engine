"""Invented date and support rows for EG's chronological fold selection."""

from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction

import pytest

from research.intraday_chart_folds import ObservedLabel
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_highvol_folds import CandidateSupport, EvaluationRow, select_fold
from research.intraday_pattern_calibration import session_open


DAYS = tuple((date(2025, 8, 1) + timedelta(days=i)).isoformat() for i in range(257))
SYMBOL = "KRX:000001"


def candidate(day, high=15, *, delay=10, chart=True, peers=True, symbol=SYMBOL):
    return CandidateSupport((day, symbol, high), session_open(day) + (high + delay + 2) * MINUTE, chart, peers)


def observed(row):
    return ObservedLabel(Fraction(1, 300), row.decision_at + 34 * MINUTE)


def select(rows, labels, *, fold=1, minimum_rows=1, minimum_days=1):
    return select_fold(DAYS, fold, rows, labels, min_train_rows=minimum_rows, min_train_days=minimum_days)


def test_common_training_only_and_every_test_support_row_kept():
    early, later = candidate(DAYS[0]), candidate(DAYS[1], high=55, delay=30)
    test_rows = (candidate(DAYS[100]), candidate(DAYS[100], high=20, peers=False),
                 candidate(DAYS[100], high=25, chart=False, peers=True))
    rows = (early, later, candidate(DAYS[2], peers=False), candidate(DAYS[3], chart=False),
            *test_rows, candidate(DAYS[132]))
    outcomes = {row.key: observed(row) for row in (early, later)}
    requested = []

    def read_label(key):
        requested.append(key)
        return outcomes[key]

    result = select(rows, read_label, minimum_rows=2, minimum_days=2)

    assert requested == [early.key, later.key]
    assert result.supported
    assert [(row.key, row.decision_at, row.outcome) for row in result.training] == [
        (row.key, row.decision_at, outcomes[row.key]) for row in (early, later)
    ]
    assert result.evaluation == tuple(EvaluationRow(row.key, row.decision_at, status) for row, status in zip(
        test_rows, ("common", "chart_only", "chart_unavailable"), strict=True,
    ))
    assert result.fold.first_test_opening == session_open(DAYS[100])
    assert result.fold.test_sessions == DAYS[100:132]


def test_label_equal_to_first_test_opening_is_purged_and_absence_is_distinct():
    first, second, third = (candidate(DAYS[index]) for index in range(3))
    cutoff = session_open(DAYS[100])
    outcomes = {first.key: ObservedLabel(Fraction(2), cutoff), second.key: observed(second), third.key: None}
    result = select((first, second, third), outcomes.__getitem__, minimum_days=2)

    assert not result.supported
    assert [row.key for row in result.training] == [second.key]
    counts = dict(result.counts)
    assert counts["training_primary_immature"] == 1
    assert counts["training_primary_unavailable"] == 1
    assert counts["training_used"] == counts["training_sessions_used"] == 1


def test_maturity_uses_the_confirmation_clock_and_requires_exact_fraction():
    row = candidate(DAYS[0], high=15, delay=30)
    maturity = observed(row).mature_at
    for invalid in (ObservedLabel(Fraction(0), maturity - MINUTE), ObservedLabel(0, maturity)):
        with pytest.raises(InputRefusal, match="invalid_eg_primary_label_maturity_or_value"):
            select((row,), lambda _: invalid)


def test_short_session_outside_clock_rows_survive_and_zero_fit_support_is_unsupported():
    day = "2026-01-02"
    assert day in DAYS[132:164]
    first, second = candidate(day, high=270, delay=5), candidate(day, high=275)

    def never_read(key):
        raise AssertionError(key)

    result = select((first, second), never_read, fold=2)
    assert not result.supported and result.training == ()
    assert [row.status for row in result.evaluation] == ["common", "outside_clock"]


def test_one_day_with_many_rows_cannot_meet_distinct_day_minimum():
    rows = (candidate(DAYS[0]), candidate(DAYS[0], high=55))
    outcome = {row.key: observed(row) for row in rows}
    result = select(rows, outcome.__getitem__, minimum_rows=2, minimum_days=2)
    assert not result.supported
    assert dict(result.counts)["training_used"] == 2
    assert dict(result.counts)["training_sessions_used"] == 1


def test_duplicate_event_identity_is_rejected_even_with_distinct_confirmations():
    row = candidate(DAYS[100])
    with pytest.raises(InputRefusal, match="duplicate_or_unordered"):
        select((row, replace(row, decision_at=row.decision_at + 5 * MINUTE)), lambda _: None)


@pytest.mark.parametrize("delay", [0, 7, 35])
def test_rejects_confirmation_outside_five_minute_attempt_grid(delay):
    with pytest.raises(InputRefusal, match="invalid_eg_confirmation_clock"):
        select((candidate(DAYS[100], delay=delay),), lambda _: None)
