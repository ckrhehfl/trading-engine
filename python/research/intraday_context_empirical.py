"""Prospective EI pooled and context-conditioned probabilities on one roster.

This pure component has no reader, evaluation outcomes, order path or actual
execution registration. Context is a representation of the same past chart,
not a new external information source. Counts are exact; probabilities are not
profit estimates and the grouped weights do not make dates independent.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from fractions import Fraction
from hashlib import sha256
import json
from typing import Literal

from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_questions import QuestionId
from research.intraday_context_situations import ResistanceAnchor
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome


SCHEMA = "ei-context-empirical-draft-v1"
STAGES = ("approach", "breakout")
CONTEXTS = ("A", "B", "other")
JEFFREYS_PER_CLASS = Fraction(1, 2)
CONTEXT_PRIOR_WEIGHT = Fraction(20)
_KST = timezone(timedelta(hours=9))
Stage = Literal["approach", "breakout"]
Context = Literal["A", "B", "other"]


@dataclass(frozen=True)
class EmpiricalTrainingRow:
    question_id: QuestionId
    symbol: str
    session: str
    stage: Stage
    origin_context: Context
    forecast_at: datetime
    outcome: TrainingOutcome | None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class EmpiricalSupport:
    candidate_rows: int
    supported_rows: int
    unavailable_by_reason: tuple[tuple[str, int], ...]
    symbol_days: int
    dates: int
    weighted_count: Fraction


@dataclass(frozen=True)
class ContextCounts:
    context: Context
    counts: tuple[Fraction, ...]
    support: EmpiricalSupport


@dataclass(frozen=True)
class StageCounts:
    stage: Stage
    counts: tuple[Fraction, ...]
    support: EmpiricalSupport
    contexts: tuple[ContextCounts, ...]
    candidate_roster_sha256: str
    supported_roster_sha256: str


@dataclass(frozen=True)
class EmpiricalPrediction:
    status: Literal["supported", "unsupported"]
    stage: Stage
    origin_context: Context
    forecast_at: datetime
    pooled: tuple[Fraction, ...] | None
    conditioned: tuple[Fraction, ...] | None
    context_falls_back_to_pooled: bool
    reason: str | None
    common_support: EmpiricalSupport
    context_support: EmpiricalSupport
    common_roster_sha256: str


@dataclass(frozen=True)
class ContextEmpiricalModel:
    cutoff_at: datetime
    stages: tuple[StageCounts, ...]
    input_sha256: str

    def predict(self, *, stage: Stage, origin_context: Context, forecast_at: datetime) -> EmpiricalPrediction:
        """Use the same stage roster in both arms, never backdate a forecast."""
        _clock(forecast_at)
        _need(stage in STAGES and origin_context in CONTEXTS and forecast_at >= self.cutoff_at,
              "EI_empirical_prediction_identity_or_clock_differs")
        group = self.stages[STAGES.index(stage)]
        context = group.contexts[CONTEXTS.index(origin_context)]
        if not group.support.supported_rows:
            return EmpiricalPrediction("unsupported", stage, origin_context, forecast_at, None, None,
                                       False, "no_mature_supported_training_rows", group.support,
                                       context.support, group.supported_roster_sha256)
        pooled = tuple((count + JEFFREYS_PER_CLASS) /
                       (group.support.weighted_count + JEFFREYS_PER_CLASS * len(OUTCOMES))
                       for count in group.counts)
        conditioned = tuple((count + CONTEXT_PRIOR_WEIGHT * probability) /
                            (context.support.weighted_count + CONTEXT_PRIOR_WEIGHT)
                            for count, probability in zip(context.counts, pooled, strict=True))
        return EmpiricalPrediction("supported", stage, origin_context, forecast_at, pooled, conditioned,
                                   not context.support.supported_rows, None, group.support, context.support,
                                   group.supported_roster_sha256)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _exact(value: Fraction) -> list[int]:
    return [value.numerator, value.denominator]


def _identity(row: EmpiricalTrainingRow) -> list:
    (symbol, anchor), episode = row.question_id[0]
    start, level, band, target, stop, end = row.question_id[2]
    return [symbol, row.session, row.stage, row.origin_context, _stamp(row.forecast_at),
            [_stamp(anchor.start_at), _exact(anchor.start_price), _stamp(anchor.peak_end),
             _stamp(anchor.confirmed_at), *map(_exact, (anchor.price, anchor.pre_peak_range, anchor.advance, anchor.band))],
            episode, [_stamp(start), *map(_exact, (level, band, target, stop)), _stamp(end)]]


def _encode(value) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _validate(row: EmpiricalTrainingRow, cutoff_at: datetime) -> None:
    _need(type(row) is EmpiricalTrainingRow and type(row.symbol) is str and bool(row.symbol)
          and type(row.session) is str and row.stage in STAGES and row.origin_context in CONTEXTS,
          "EI_empirical_typed_training_identity_required")
    _clock(row.forecast_at)
    _need(row.forecast_at < cutoff_at and row.forecast_at.astimezone(_KST).date().isoformat() == row.session
          and date.fromisoformat(row.session).isoformat() == row.session,
          "EI_empirical_training_clock_or_day_differs")
    identity = row.question_id
    _need(type(identity) is tuple and len(identity) == 3 and identity[1] == row.stage
          and type(identity[0]) is tuple and len(identity[0]) == 2
          and type(identity[0][0]) is tuple and len(identity[0][0]) == 2
          and identity[0][0][0] == row.symbol and type(identity[0][0][1]) is ResistanceAnchor
          and type(identity[0][1]) is int and identity[0][1] >= 0
          and (identity[0][1] > 0 or row.stage == "breakout")
          and type(identity[2]) is tuple and len(identity[2]) == 6,
          "EI_empirical_question_identity_differs")
    anchor = identity[0][0][1]
    start, level, band, target, stop, end = identity[2]
    for clock in (anchor.start_at, anchor.peak_end, anchor.confirmed_at, start, end):
        _clock(clock)
    values = (anchor.start_price, anchor.price, anchor.pre_peak_range, anchor.advance, anchor.band,
              level, band, target, stop)
    _need(all(type(value) is Fraction and value > 0 for value in values)
          and anchor.start_at < anchor.peak_end <= anchor.confirmed_at <= start == row.forecast_at
          and anchor.start_at.astimezone(_KST).date().isoformat() == row.session
          and end - start == timedelta(minutes=30) and level == anchor.price and band == anchor.band
          and anchor.advance == anchor.price - anchor.start_price
          and 0 < stop <= level - band < level + band < target,
          "EI_empirical_question_geometry_or_availability_differs")
    if row.outcome is None:
        _need(type(row.unavailable_reason) is str and bool(row.unavailable_reason.strip())
              and len(row.unavailable_reason) <= 128, "EI_empirical_unavailable_reason_required")
        return
    _need(type(row.outcome) is TrainingOutcome and row.unavailable_reason is None
          and type(row.outcome.index) is int and 0 <= row.outcome.index < len(OUTCOMES),
          "EI_empirical_observed_outcome_required")
    _clock(row.outcome.mature_at)
    # A terminal in the 5/15/30 bucket cannot be known before minute 1/6/16
    # plus the declared two-minute availability lag. Timeout needs all 30.
    earliest_minutes = (3, 8, 18)[row.outcome.index // 3] if row.outcome.index < 9 else 32
    _need(start + timedelta(minutes=earliest_minutes) <= row.outcome.mature_at < cutoff_at,
          "EI_empirical_training_outcome_not_mature")


def _support(rows: tuple[EmpiricalTrainingRow, ...], weights: dict[QuestionId, Fraction]) -> EmpiricalSupport:
    observed = tuple(row for row in rows if row.outcome is not None)
    missing = Counter(row.unavailable_reason for row in rows if row.outcome is None)
    return EmpiricalSupport(len(rows), len(observed), tuple(sorted(missing.items())),
        len({(row.symbol, row.session) for row in observed}), len({row.session for row in observed}),
        sum((weights[row.question_id] for row in observed), Fraction()))


def _counts(rows: tuple[EmpiricalTrainingRow, ...], weights: dict[QuestionId, Fraction]) -> tuple[Fraction, ...]:
    counts = [Fraction() for _ in OUTCOMES]
    for row in rows:
        if row.outcome is not None:
            counts[row.outcome.index] += weights[row.question_id]
    return tuple(counts)


def _validated_rows(rows: Iterable[EmpiricalTrainingRow], cutoff_at: datetime) -> tuple[EmpiricalTrainingRow, ...]:
    _clock(cutoff_at)
    original = tuple(rows)
    seen = set()
    episode_stages, origins, anchors = {}, {}, {}
    for row in original:
        _validate(row, cutoff_at)
        _need(row.question_id not in seen, "EI_empirical_duplicate_question_identity")
        seen.add(row.question_id)
        episode = row.question_id[0]
        key = episode, row.stage
        _need(key not in episode_stages, "EI_empirical_duplicate_episode_stage")
        episode_stages[key] = row.forecast_at
        _need(episode not in origins or origins[episode] == row.origin_context,
              "EI_empirical_episode_origin_changed")
        origins[episode] = row.origin_context
        coordinate = row.symbol, row.session
        _need(coordinate not in anchors or anchors[coordinate] == episode[0][1],
              "EI_empirical_first_anchor_changed")
        anchors[coordinate] = episode[0][1]
    for episode in origins:
        approach = episode_stages.get((episode, "approach"))
        breakout = episode_stages.get((episode, "breakout"))
        _need(approach is None or breakout is None or approach <= breakout,
              "EI_empirical_breakout_precedes_approach")
    return tuple(sorted(original, key=lambda row: _encode(_identity(row))))


def _weights(ordered: tuple[EmpiricalTrainingRow, ...]) -> dict[QuestionId, Fraction]:
    denominators = Counter((row.symbol, row.session, row.stage) for row in ordered if row.outcome is not None)
    return {row.question_id: Fraction(1, denominators[(row.symbol, row.session, row.stage)])
            for row in ordered if row.outcome is not None}


def weighted_training_rows(
    rows: Iterable[EmpiricalTrainingRow], *, cutoff_at: datetime,
) -> tuple[tuple[EmpiricalTrainingRow, Fraction], ...]:
    """Give other model arms the identical supported roster and exact weights.

    Missing rows are not fabricated as class zero or a zero-weight observation.
    Callers must retain the original rows and report their unavailable reasons,
    as ``fit_empirical`` does. This helper enforces the same identity/maturity
    checks and deterministic order as both empirical arms.
    """
    ordered = _validated_rows(rows, cutoff_at)
    weights = _weights(ordered)
    return tuple((row, weights[row.question_id]) for row in ordered if row.outcome is not None)


def fit_empirical(rows: Iterable[EmpiricalTrainingRow], *, cutoff_at: datetime) -> ContextEmpiricalModel:
    """Fit both arms once on identical rows; missing labels retain their reasons.

    Supported questions in each (symbol, session, stage) carry total weight one.
    Their contexts share those weights rather than independently renormalizing.
    A symbol-day with no supported label contributes no fitted outcome mass.
    The full candidate roster, support losses and supported roster remain visible.
    """
    ordered = _validated_rows(rows, cutoff_at)
    weights = _weights(ordered)
    stages = []
    for stage in STAGES:
        subset = tuple(row for row in ordered if row.stage == stage)
        supported = tuple(row for row in subset if row.outcome is not None)
        contexts = []
        for context in CONTEXTS:
            selected = tuple(row for row in subset if row.origin_context == context)
            contexts.append(ContextCounts(context, _counts(selected, weights), _support(selected, weights)))
        stages.append(StageCounts(stage, _counts(subset, weights), _support(subset, weights), tuple(contexts),
            sha256(_encode([_identity(row) for row in subset])).hexdigest(),
            sha256(_encode([_identity(row) for row in supported])).hexdigest()))
    payload = [[_identity(row), None if row.outcome is None else [row.outcome.index, _stamp(row.outcome.mature_at)],
                row.unavailable_reason] for row in ordered]
    return ContextEmpiricalModel(cutoff_at.astimezone(UTC), tuple(stages), sha256(_encode(payload)).hexdigest())
