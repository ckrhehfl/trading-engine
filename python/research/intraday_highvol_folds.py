"""Past-only EG fold selection over sealed predecision candidate support."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
import re
from typing import Literal

from research.intraday_campaign_ml_labels import ChronologicalFold, fixed_calendar
from research.intraday_chart_folds import Key, ObservedLabel
from research.intraday_ed_inputs import FIVE, MINUTE, _clock, _require
from research.intraday_highvol_events import OFFSETS
from research.intraday_highvol_scenarios import CONFIRMATION_WINDOW
from research.intraday_pattern_calibration import session_close, session_open


PRIMARY_HORIZON_MINUTES = 30
Support = Literal["common", "chart_only", "chart_unavailable", "outside_clock"]


@dataclass(frozen=True)
class CandidateSupport:
    key: Key  # original day, symbol, first high offset
    decision_at: datetime  # confirmation bar end plus two minutes
    chart: bool
    peers: bool


@dataclass(frozen=True)
class TrainingRow:
    key: Key
    decision_at: datetime
    outcome: ObservedLabel


@dataclass(frozen=True)
class EvaluationRow:
    key: Key
    decision_at: datetime
    status: Support


@dataclass(frozen=True)
class FoldSelection:
    fold: ChronologicalFold
    training: tuple[TrainingRow, ...]
    evaluation: tuple[EvaluationRow, ...]
    counts: tuple[tuple[str, int], ...]
    supported: bool


def select_fold(
    source_dates: Sequence[str],
    fold_index: int,
    candidates: Iterable[CandidateSupport],
    training_labels: Callable[[Key], ObservedLabel | None],
    *,
    min_train_rows: int,
    min_train_days: int,
) -> FoldSelection:
    """Keep every test candidate and request outcomes only for common training rows.

    The caller authenticates the sealed candidate stream for one branch and
    timing policy. Candidates are ordered by day, first high offset, symbol.
    An event stays keyed to its first high even when its confirmation is later.
    A missing label or one maturing at or after the test opening is excluded
    from training. Evaluation support is determined without outcome access.
    """
    calendar = fixed_calendar(source_dates)
    _require(type(fold_index) is int and 1 <= fold_index <= len(calendar.folds)
             and type(min_train_rows) is int and min_train_rows > 0
             and type(min_train_days) is int and min_train_days > 0,
             "registered_eg_fold_and_training_minimums_required")
    fold = calendar.folds[fold_index - 1]
    source = set(calendar.source_reference_sessions)
    fit_dates, test_dates = set(fold.training_sessions), set(fold.test_sessions)
    counts: Counter[str] = Counter()
    training: list[TrainingRow] = []
    evaluation: list[EvaluationRow] = []
    last = None
    for row in candidates:
        _require(type(row) is CandidateSupport and type(row.key) is tuple and len(row.key) == 3
                 and type(row.chart) is bool and type(row.peers) is bool, "typed_eg_candidate_support_required")
        day, symbol, high_offset = row.key
        _require(type(day) is str and day in source and type(symbol) is str
                 and re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) is not None
                 and type(high_offset) is int and high_offset in OFFSETS, "invalid_eg_event_key")
        order = day, high_offset, symbol
        _require(last is None or order > last, "duplicate_or_unordered_eg_event_key")
        last = order

        _clock(row.decision_at)
        closing = session_close(day)
        bar_end = row.decision_at - 2 * MINUTE
        elapsed = bar_end - (session_open(day) + high_offset * MINUTE)
        _require(FIVE <= elapsed <= CONFIRMATION_WINDOW and not elapsed % FIVE and bar_end <= closing,
                 "invalid_eg_confirmation_clock")
        exit_at = row.decision_at + (1 + PRIMARY_HORIZON_MINUTES) * MINUTE
        support: Support
        if exit_at >= closing - 15 * MINUTE:
            support = "outside_clock"
        elif not row.chart:
            support = "chart_unavailable"
        else:
            support = "common" if row.peers else "chart_only"

        if day in test_dates:
            evaluation.append(EvaluationRow(row.key, row.decision_at, support))
            counts[f"evaluation_{support}"] += 1
        elif day in fit_dates:
            counts[f"training_{support}"] += 1
            if support != "common":
                continue
            outcome = training_labels(row.key)
            if outcome is None:
                counts["training_primary_unavailable"] += 1
                continue
            _require(type(outcome) is ObservedLabel, "typed_eg_primary_label_required")
            _clock(outcome.mature_at)
            _require(type(outcome.value) is Fraction and outcome.mature_at >= exit_at + 3 * MINUTE,
                     "invalid_eg_primary_label_maturity_or_value")
            if outcome.mature_at >= fold.first_test_opening:
                counts["training_primary_immature"] += 1
                continue
            training.append(TrainingRow(row.key, row.decision_at, outcome))

    days = len({row.key[0] for row in training})
    counts["training_used"] = len(training)
    counts["training_sessions_used"] = days
    supported = len(training) >= min_train_rows and days >= min_train_days
    return FoldSelection(fold, tuple(training), tuple(evaluation), tuple(sorted(counts.items())), supported)
