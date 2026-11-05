"""Fixed, paired EF training and inference selection over sealed coordinates."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
import re

from research.intraday_campaign_ml_labels import ChronologicalFold, fixed_calendar
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_pattern_calibration import session_close, session_open


ENTRY_OFFSETS = tuple(range(60, 301, 15))
HEAD_HORIZONS = (30, 15, 15, 15, 30, 30)
MIN_TRAIN_ROWS = 100
MIN_TRAIN_SESSIONS = 30
Key = tuple[str, str, int]


@dataclass(frozen=True)
class EntrySupport:
    key: Key  # original date, symbol and bar-end offset
    chart: bool
    peers: bool


@dataclass(frozen=True)
class ObservedLabel:
    value: Fraction
    mature_at: datetime


Labels = tuple[ObservedLabel | None, ...]


@dataclass(frozen=True)
class TrainingRow:
    key: Key
    outcomes: Labels


@dataclass(frozen=True)
class EvaluationRow:
    key: Key
    status: str


@dataclass(frozen=True)
class FoldSelection:
    fold: ChronologicalFold
    scenario: str
    training: tuple[TrainingRow, ...]
    evaluation: tuple[EvaluationRow, ...]
    counts: tuple[tuple[str, int], ...]
    supported: bool

    @property
    def train_mean_pct(self) -> Fraction | None:
        if not self.supported:
            return None
        return 100 * sum((row.outcomes[0].value for row in self.training), Fraction()) / len(self.training)


def _support(row: EntrySupport) -> str:
    day, _, offset = row.key
    if session_open(day) + (offset + 3 + 30) * MINUTE >= session_close(day) - 15 * MINUTE:
        return "outside_clock"
    if not row.chart:
        return "chart_unavailable"
    return "common" if row.peers else "chart_only"


def _mature(key: Key, heads: Labels, cutoff: datetime) -> Labels:
    if type(heads) is not tuple or len(heads) != len(HEAD_HORIZONS):
        raise InputRefusal("six_exact_ef_label_heads_required")
    day, _, offset = key
    opened = session_open(day)
    accepted = []
    for head, horizon in zip(heads, HEAD_HORIZONS, strict=True):
        if head is not None:
            if (type(head) is not ObservedLabel or type(head.value) is not Fraction
                    or not isinstance(head.mature_at, datetime) or head.mature_at.utcoffset() is None
                    or head.mature_at < opened + (offset + 3 + horizon + 3) * MINUTE):
                raise InputRefusal("invalid_ef_label_maturity_or_value")
        accepted.append(head if head is not None and head.mature_at < cutoff else None)
    return tuple(accepted)


def select_fold(
    source_dates: Sequence[str],
    scenario: str,
    fold_index: int,
    features: Iterable[EntrySupport],
    training_labels: Callable[[Key], Labels],
) -> FoldSelection:
    """Retain every supplied evaluation coordinate without reading its label.

    The sealed-snapshot reader verifies coordinate completeness against the
    original population before handing off this chronological metadata view.
    """
    calendar = fixed_calendar(source_dates)
    if type(scenario) is not str or scenario not in {"S1", "S2"} or type(fold_index) is not int or not 1 <= fold_index <= 5:
        raise InputRefusal("registered_ef_fold_and_scenario_required")
    fold = calendar.folds[fold_index - 1]
    fit_dates, test_dates = set(fold.training_sessions), set(fold.test_sessions)
    selected_dates = fit_dates | test_dates
    counts: Counter[str] = Counter()
    training, evaluation = [], []
    last = None
    for row in features:
        if (type(row) is not EntrySupport or type(row.key) is not tuple or len(row.key) != 3
                or type(row.chart) is not bool or type(row.peers) is not bool):
            raise InputRefusal("typed_entry_support_required")
        day, symbol, offset = row.key
        ordered = day, offset, symbol
        if (type(day) is not str or day not in selected_dates or type(symbol) is not str
                or re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) is None
                or type(offset) is not int or offset not in ENTRY_OFFSETS
                or (last is not None and ordered <= last)):
            raise InputRefusal("invalid_or_unordered_ef_entry_coordinate")
        last = ordered
        support = _support(row)
        if day in test_dates:
            evaluation.append(EvaluationRow(row.key, support))
            counts[f"evaluation_{support}"] += 1
            continue
        counts[f"training_{support}"] += 1
        if support != "common":
            continue
        labels = training_labels(row.key)
        outcomes = _mature(row.key, labels, fold.first_test_opening)
        if outcomes[0] is None:
            counts["training_primary_unavailable" if labels[0] is None else "training_primary_immature"] += 1
            continue
        training.append(TrainingRow(row.key, outcomes))
    sessions = len({row.key[0] for row in training})
    counts["training_used"] = len(training)
    counts["training_sessions_used"] = sessions
    return FoldSelection(fold, scenario, tuple(training), tuple(evaluation), tuple(sorted(counts.items())),
                         len(training) >= MIN_TRAIN_ROWS and sessions >= MIN_TRAIN_SESSIONS)
