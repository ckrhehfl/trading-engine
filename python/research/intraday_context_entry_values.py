"""Training-only EI path payoff moments and causal per-unit entry values.

This pure prototype does not select a primary forecaster, read prices, execute
orders or build a cash ledger. Historical modeled payoffs are caller supplied;
their source digests are bindings, not authentication of the underlying data.
Transferring normalized payoff/gap moments to today's C and R is an assumption.
Complete-case means additionally assume missing payoffs are representative;
their original denominators and missing weights remain visible.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from hashlib import sha256
from typing import Literal

from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_empirical import EmpiricalTrainingRow, STAGES, weighted_training_rows
from research.intraday_context_forecast_fold import MODEL_KEYS, _probabilities
from research.intraday_context_model_inputs import encode, question_payload
from research.intraday_context_questions import (
    BAND_IN_R, STOP_BELOW_LEVEL_IN_R, TARGET_ABOVE_LEVEL_IN_R, QuestionId,
)
from research.intraday_scenario_forecast_targets import OUTCOMES


SCHEMA = "ei-context-entry-values-draft-v1"
WEIGHT_POLICY = "one_per_label_supported_symbol_session_stage_before_payoff_missingness"
TRANSFER_ASSUMPTION = "stage_path_complete_case_normalized_gross_gap_and_fill_transfer"


@dataclass(frozen=True)
class ForecastProbabilityBoundary:
    """Normalize all ten binary64 probabilities once, never a supported subset."""
    original: tuple[float, ...]
    probabilities: tuple[Fraction, ...]
    original_sum: Fraction
    maximum_absolute_correction: Fraction


def forecast_probability_boundary(values: tuple) -> ForecastProbabilityBoundary:
    original = _probabilities(values)  # One shared forecast tolerance and type contract.
    exact = tuple(Fraction.from_float(value) for value in original)
    total = sum(exact, Fraction())
    probabilities = tuple(value / total for value in exact)
    correction = max(abs(value - before) for value, before in zip(probabilities, exact, strict=True))
    return ForecastProbabilityBoundary(original, probabilities, total, correction)


@dataclass(frozen=True)
class HistoricalEntryPayoff:
    question: EmpiricalTrainingRow
    decision_close: Fraction
    risk: Fraction
    filled: bool | None
    actual_mature_at: datetime | None
    gross_in_R: Fraction | None
    gap_in_R: Fraction | None
    weight: Fraction | None
    source_sha256: str
    unsupported_reason: str | None = None
    no_fill_reason: str | None = None


@dataclass(frozen=True)
class PayoffSupport:
    eligible_rows: int
    complete_rows: int
    known_no_fill_rows: int
    eligible_weight: Fraction
    complete_weight: Fraction
    remaining_unknown_rows: int
    remaining_unknown_weight: Fraction
    unknown_by_reason: tuple[tuple[str, int, Fraction], ...]

    @property
    def complete_fraction(self) -> Fraction | None:
        return self.complete_weight / self.eligible_weight if self.eligible_weight else None


@dataclass(frozen=True)
class PathPayoffMoments:
    outcome_index: int
    mean_fill: Fraction | None
    mean_filled_gross_in_R: Fraction | None
    mean_filled_gap_in_R: Fraction | None
    support: PayoffSupport


@dataclass(frozen=True)
class StagePayoffMoments:
    stage: str
    candidate_rows: int
    label_unavailable_by_reason: tuple[tuple[str, int], ...]
    payoff_unavailable_by_reason: tuple[tuple[str, int], ...]
    paths: tuple[PathPayoffMoments, ...]


@dataclass(frozen=True)
class EntryValueModel:
    cutoff_at: datetime
    execution_policy_sha256: str
    input_sha256: str
    source_sha256s: tuple[str, ...]
    stages: tuple[StagePayoffMoments, ...]
    model_sha256: str


@dataclass(frozen=True)
class EntryValueQuery:
    question_id: QuestionId
    forecast_at: datetime
    decision_close: Fraction
    risk: Fraction
    cost_bps: Fraction
    probabilities: tuple[Fraction, ...]
    forecast_model_key: str
    forecast_source_sha256: str
    causal_source_sha256: str
    execution_policy_sha256: str
    numeric_boundary: ForecastProbabilityBoundary | None = None


@dataclass(frozen=True)
class EntryValueResult:
    status: Literal["supported", "abstain"]
    action: Literal["enter", "wait", "abstain"]
    reason: str | None
    query: EntryValueQuery
    model_sha256: str
    training_input_sha256: str
    expected_gross: Fraction | None
    expected_cost: Fraction | None
    expected_net: Fraction | None
    class_net_values: tuple[Fraction | None, ...]
    unsupported_probability_mass: Fraction
    training_support: StagePayoffMoments
    transfer_assumption: str = TRANSFER_ASSUMPTION


def _hash(value: object) -> str:
    return sha256(encode(value)).hexdigest()


def _digest(value: str) -> None:
    _need(type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value),
          "EI_entry_source_digest_required")


def _reason(value: str | None) -> None:
    _need(type(value) is str and bool(value.strip()) and len(value) <= 128, "EI_entry_reason_required")


def _fraction(value: Fraction | None):
    return None if value is None else [value.numerator, value.denominator]


def _stamp(value: datetime | None):
    return None if value is None else value.astimezone(UTC).isoformat()


def _geometry(identity: QuestionId, at: datetime, close: Fraction, risk: Fraction) -> list:
    payload = question_payload(identity, at)
    anchor = identity[0][0][1]
    _, level, band, target, stop, _ = identity[2]
    _need(type(close) is Fraction and close > 0 and type(risk) is Fraction and risk > 0
          and risk == anchor.pre_peak_range and band == risk * BAND_IN_R
          and target == level + risk * TARGET_ABOVE_LEVEL_IN_R
          and stop == level - risk * STOP_BELOW_LEVEL_IN_R, "EI_entry_causal_risk_geometry_differs")
    return payload


def _payoff(row: HistoricalEntryPayoff, cutoff: datetime, weight: Fraction | None) -> list:
    identity = _geometry(row.question.question_id, row.question.forecast_at, row.decision_close, row.risk)
    _digest(row.source_sha256)
    _need((type(row.weight) is Fraction if weight is not None else row.weight is None) and row.weight == weight,
          "EI_entry_symbol_session_stage_weight_differs")
    _need(row.filled is None or type(row.filled) is bool, "EI_entry_fill_state_differs")
    if row.filled is None:
        _reason(row.unsupported_reason)
        _need(row.actual_mature_at is row.gross_in_R is row.gap_in_R is row.no_fill_reason is None,
              "EI_entry_unknown_payoff_has_payload")
    else:
        _clock(row.actual_mature_at)
        _need(row.question.forecast_at < row.actual_mature_at < cutoff,
              "EI_entry_actual_payoff_not_mature")
        _need(row.unsupported_reason is None and type(row.gross_in_R) is Fraction
              and type(row.gap_in_R) is Fraction, "EI_entry_complete_exact_payoff_required")
        if row.filled:
            entry = row.decision_close + row.risk * row.gap_in_R
            exit_price = entry + row.risk * row.gross_in_R
            _need(row.no_fill_reason is None and row.question.question_id[2][4] < entry
                  < row.question.question_id[2][3] and exit_price > 0, "EI_entry_filled_price_geometry_differs")
        else:
            _reason(row.no_fill_reason)
            _need(row.gross_in_R == row.gap_in_R == 0, "EI_entry_known_no_fill_must_be_zero")
    outcome = row.question.outcome
    return [identity, row.question.session, row.question.origin_context,
            None if outcome is None else [outcome.index, _stamp(outcome.mature_at)], row.question.unavailable_reason,
            _fraction(row.decision_close), _fraction(row.risk), row.filled, _stamp(row.actual_mature_at),
            _fraction(row.gross_in_R), _fraction(row.gap_in_R), _fraction(row.weight), row.source_sha256,
            row.unsupported_reason, row.no_fill_reason]


def _moments(index: int, rows: tuple[HistoricalEntryPayoff, ...]) -> PathPayoffMoments:
    complete = tuple(row for row in rows if row.filled is not None)
    unknown = tuple(row for row in rows if row.filled is None)
    eligible_weight = sum((row.weight for row in rows), Fraction())
    complete_weight = sum((row.weight for row in complete), Fraction())
    reasons = sorted({row.unsupported_reason for row in unknown})
    support = PayoffSupport(len(rows), len(complete), sum(row.filled is False for row in complete),
        eligible_weight, complete_weight, len(unknown), eligible_weight - complete_weight,
        tuple((reason, sum(row.unsupported_reason == reason for row in unknown),
               sum((row.weight for row in unknown if row.unsupported_reason == reason), Fraction())) for reason in reasons))
    if not complete_weight:
        return PathPayoffMoments(index, None, None, None, support)
    return PathPayoffMoments(index,
        sum((row.weight for row in complete if row.filled), Fraction()) / complete_weight,
        sum((row.weight * row.gross_in_R for row in complete), Fraction()) / complete_weight,
        sum((row.weight * row.gap_in_R for row in complete), Fraction()) / complete_weight, support)


def _model_digest(cutoff, policy, input_digest, sources, stages) -> str:
    payload = []
    for stage in stages:
        paths = []
        for path in stage.paths:
            support = path.support
            paths.append([path.outcome_index, *map(_fraction, (path.mean_fill, path.mean_filled_gross_in_R,
                path.mean_filled_gap_in_R)), support.eligible_rows, support.complete_rows, support.known_no_fill_rows,
                _fraction(support.eligible_weight), _fraction(support.complete_weight), support.remaining_unknown_rows,
                _fraction(support.remaining_unknown_weight),
                [[reason, count, _fraction(weight)] for reason, count, weight in support.unknown_by_reason]])
        payload.append([stage.stage, stage.candidate_rows, stage.label_unavailable_by_reason,
                        stage.payoff_unavailable_by_reason, paths])
    return _hash([SCHEMA, WEIGHT_POLICY, TRANSFER_ASSUMPTION, OUTCOMES, _stamp(cutoff), policy,
                  input_digest, sources, payload])


def fit_entry_values(rows: Iterable[HistoricalEntryPayoff], *, cutoff_at: datetime,
                     execution_policy_sha256: str) -> EntryValueModel:
    """Fit stage × ten path classes; require both label and payoff maturity.

    Supplied weights must equal the forecast model's supported-label group
    weights. An unavailable payoff retains its original weight; it never
    increases another observation's weight. Missing labels carry no fit weight.
    No label-to-fixed-profit substitution or missing-payoff imputation occurs.
    """
    _clock(cutoff_at)
    _digest(execution_policy_sha256)
    original = tuple(rows)
    _need(all(type(row) is HistoricalEntryPayoff and type(row.question) is EmpiricalTrainingRow for row in original),
          "EI_entry_typed_historical_payoff_required")
    weighted = weighted_training_rows((row.question for row in original), cutoff_at=cutoff_at)
    weights = {question.question_id: weight for question, weight in weighted}
    payloads = sorted((_payoff(row, cutoff_at, weights.get(row.question.question_id)) for row in original), key=encode)
    input_digest = _hash([SCHEMA, _stamp(cutoff_at), execution_policy_sha256, payloads])
    stages = []
    for stage in STAGES:
        subset = tuple(row for row in original if row.question.stage == stage)
        missing = Counter(row.question.unavailable_reason for row in subset if row.question.outcome is None)
        missing_payoffs = Counter(row.unsupported_reason for row in subset if row.filled is None)
        paths = tuple(_moments(index, tuple(row for row in subset if row.question.outcome is not None
                                            and row.question.outcome.index == index)) for index in range(len(OUTCOMES)))
        stages.append(StagePayoffMoments(stage, len(subset), tuple(sorted(missing.items())),
                                        tuple(sorted(missing_payoffs.items())), paths))
    stages = tuple(stages)
    sources = tuple(sorted({row.source_sha256 for row in original}))
    digest = _model_digest(cutoff_at, execution_policy_sha256, input_digest, sources, stages)
    return EntryValueModel(cutoff_at, execution_policy_sha256, input_digest, sources, stages, digest)


def assess_entry_value(model: EntryValueModel, query: EntryValueQuery) -> EntryValueResult:
    """Compute R E[Fg] - c (C E[F] + R E[Fe]); a zero net value waits.

    c is a round-trip proportional cost charged once against modeled entry
    notional. The value is per unit, before sizing/cash/inventory constraints.
    Positive mass on any path with no complete payoff causes abstention. Zero
    mass on such a path is harmless; no remaining mass is renormalized.
    """
    _need(type(model) is EntryValueModel and type(query) is EntryValueQuery, "EI_entry_typed_query_model_required")
    _clock(model.cutoff_at)
    _geometry(query.question_id, query.forecast_at, query.decision_close, query.risk)
    for digest in (model.execution_policy_sha256, model.input_sha256, model.model_sha256,
                   query.forecast_source_sha256, query.causal_source_sha256, query.execution_policy_sha256):
        _digest(digest)
    _need(model.model_sha256 == _model_digest(model.cutoff_at, model.execution_policy_sha256,
              model.input_sha256, model.source_sha256s, model.stages), "EI_entry_model_digest_differs")
    _need(query.forecast_at >= model.cutoff_at and query.execution_policy_sha256 == model.execution_policy_sha256
          and query.forecast_model_key in MODEL_KEYS, "EI_entry_query_clock_policy_or_forecaster_differs")
    _need(type(query.cost_bps) is Fraction and query.cost_bps >= 0, "EI_entry_exact_nonnegative_cost_required")
    mass = query.probabilities
    _need(type(mass) is tuple and len(mass) == len(OUTCOMES)
          and all(type(value) is Fraction and 0 <= value <= 1 for value in mass)
          and sum(mass) == 1, "EI_entry_exact_probability_mass_differs")
    if query.numeric_boundary is not None:
        boundary = query.numeric_boundary
        _need(type(boundary) is ForecastProbabilityBoundary
              and boundary == forecast_probability_boundary(boundary.original)
              and boundary.probabilities == mass, "EI_entry_probability_boundary_differs")
    stage = model.stages[STAGES.index(query.question_id[1])]
    cost = query.cost_bps / 10_000
    gross, costs, net = [], [], []
    invalid_transfer = False
    for path in stage.paths:
        if path.mean_fill is None:
            gross.append(None)
            costs.append(None)
            net.append(None)
        else:
            gross.append(query.risk * path.mean_filled_gross_in_R)
            entry_notional = query.decision_close * path.mean_fill + query.risk * path.mean_filled_gap_in_R
            if mass[path.outcome_index] and path.mean_fill and entry_notional <= 0:
                invalid_transfer = True
            costs.append(cost * entry_notional)
            net.append(gross[-1] - costs[-1])
    unsupported = sum((probability for probability, value in zip(mass, net, strict=True) if value is None), Fraction())
    if unsupported:
        return EntryValueResult("abstain", "abstain", "positive_mass_without_complete_payoff", query,
            model.model_sha256, model.input_sha256, None, None, None, tuple(net), unsupported, stage)
    if invalid_transfer:
        return EntryValueResult("abstain", "abstain", "nonpositive_transferred_entry_notional", query,
            model.model_sha256, model.input_sha256, None, None, None, tuple(net), unsupported, stage)
    expected_gross = sum((p * value for p, value in zip(mass, gross, strict=True) if p), Fraction())
    expected_cost = sum((p * value for p, value in zip(mass, costs, strict=True) if p), Fraction())
    expected_net = expected_gross - expected_cost
    return EntryValueResult("supported", "enter" if expected_net > 0 else "wait", None, query,
        model.model_sha256, model.input_sha256, expected_gross, expected_cost, expected_net, tuple(net), unsupported, stage)
