"""Independent exact EI entry-value audit against caller-pinned original rows.

This recomputes training identities, support, weighted moments and query values.
It neither authenticates sources nor reconstructs modeled fills from prices.
Complete-case representativeness and normalized payoff/gap transfer remain
assumptions, including when every arithmetic and binding check passes.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from fractions import Fraction
from hashlib import sha256
from math import fsum, isfinite

from research.intraday_context_empirical import EmpiricalTrainingRow
from research.intraday_context_entry_values import (
    EntryValueModel, EntryValueQuery, EntryValueResult, ForecastProbabilityBoundary,
    HistoricalEntryPayoff, PathPayoffMoments, PayoffSupport, StagePayoffMoments,
)
from research.intraday_context_model_inputs import encode
from research.intraday_context_situations import ResistanceAnchor
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_forecast_targets import OUTCOMES, TrainingOutcome


SCHEMA = "ei-context-entry-values-reference-v1"
_MODEL_SCHEMA = "ei-context-entry-values-draft-v1"
_WEIGHTS = "one_per_label_supported_symbol_session_stage_before_payoff_missingness"
_TRANSFER = "stage_path_complete_case_normalized_gross_gap_and_fill_transfer"
_STAGES = ("approach", "breakout")
_MODELS = ("pooled_empirical", "context_empirical", "chart_lightgbm", "context_lightgbm",
           "chart_sequence", "context_sequence")
_KST = timezone(timedelta(hours=9))


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal("EI_entry_reference_" + reason)


def _clock(value: datetime) -> None:
    _require(isinstance(value, datetime) and value.utcoffset() is not None
             and value.second == value.microsecond == 0, "aware_minute_required")


def _digest(value: str) -> None:
    _require(type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value),
             "digest_required")


def _reason(value: str | None) -> None:
    _require(type(value) is str and bool(value.strip()) and len(value) <= 128, "reason_required")


def _stamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _ratio(value: Fraction | None) -> list[int] | None:
    return None if value is None else [value.numerator, value.denominator]


def _hash(value: object) -> str:
    return sha256(encode(value)).hexdigest()


def _same(actual: object, expected: object) -> None:
    """Equality must not let True masquerade as one, or int as Fraction."""
    _require(type(actual) is type(expected), "value_type_differs")
    if is_dataclass(expected):
        for field in fields(expected):
            _same(getattr(actual, field.name), getattr(expected, field.name))
    elif type(expected) is tuple:
        _require(len(actual) == len(expected), "tuple_length_differs")
        for left, right in zip(actual, expected, strict=True):
            _same(left, right)
    else:
        _require(actual == expected, "value_differs")


def _question(identity: tuple, at: datetime, close: Fraction, risk: Fraction) -> list:
    _clock(at)
    _require(type(identity) is tuple and len(identity) == 3 and identity[1] in _STAGES
             and type(identity[0]) is tuple and len(identity[0]) == 2
             and type(identity[0][0]) is tuple and len(identity[0][0]) == 2
             and type(identity[0][0][0]) is str and bool(identity[0][0][0])
             and type(identity[0][0][1]) is ResistanceAnchor
             and type(identity[0][1]) is int and identity[0][1] >= 0
             and (identity[0][1] > 0 or identity[1] == "breakout")
             and type(identity[2]) is tuple and len(identity[2]) == 6, "question_identity_differs")
    (symbol, anchor), episode = identity[0]
    start, level, band, target, stop, end = identity[2]
    for clock in (anchor.start_at, anchor.peak_end, anchor.confirmed_at, start, end):
        _clock(clock)
    numbers = (anchor.start_price, anchor.price, anchor.pre_peak_range, anchor.advance,
               anchor.band, level, band, target, stop, close, risk)
    _require(all(type(value) is Fraction and value > 0 for value in numbers)
             and anchor.start_at < anchor.peak_end <= anchor.confirmed_at <= start == at
             and end - start == timedelta(minutes=30) and level == anchor.price and band == anchor.band
             and anchor.advance == anchor.price - anchor.start_price
             and 0 < stop <= level - band < level + band < target
             and risk == anchor.pre_peak_range and band == risk / 4
             and target == level + 2 * risk and stop == level - risk, "question_geometry_differs")
    return [symbol, [_stamp(anchor.start_at), _ratio(anchor.start_price), _stamp(anchor.peak_end),
            _stamp(anchor.confirmed_at), *map(_ratio, (anchor.price, anchor.pre_peak_range, anchor.advance, anchor.band))],
            episode, identity[1], [_stamp(start), *map(_ratio, (level, band, target, stop)), _stamp(end)]]


def _training_rows(original: tuple[HistoricalEntryPayoff, ...], cutoff: datetime) -> None:
    seen, episode_stages, origins, anchors = set(), {}, {}, {}
    for row in original:
        _require(type(row) is HistoricalEntryPayoff and type(row.question) is EmpiricalTrainingRow,
                 "typed_original_required")
        question = row.question
        _question(question.question_id, question.forecast_at, row.decision_close, row.risk)
        key, stage, _ = question.question_id
        _require(type(question.symbol) is str and question.symbol == key[0][0]
                 and type(question.session) is str and question.stage == stage
                 and question.origin_context in ("A", "B", "other"), "training_identity_differs")
        try:
            session = date.fromisoformat(question.session).isoformat()
        except ValueError as exc:
            raise InputRefusal("EI_entry_reference_session_differs") from exc
        _require(session == question.session == question.forecast_at.astimezone(_KST).date().isoformat()
                 == key[0][1].start_at.astimezone(_KST).date().isoformat()
                 and question.forecast_at < cutoff, "training_clock_differs")
        _require(question.question_id not in seen and (key, stage) not in episode_stages,
                 "duplicate_question_or_episode_stage")
        seen.add(question.question_id)
        episode_stages[key, stage] = question.forecast_at
        _require(key not in origins or origins[key] == question.origin_context, "episode_origin_changed")
        origins[key] = question.origin_context
        coordinate = question.symbol, question.session
        _require(coordinate not in anchors or anchors[coordinate] == key[0][1], "first_anchor_changed")
        anchors[coordinate] = key[0][1]
        outcome = question.outcome
        if outcome is None:
            _reason(question.unavailable_reason)
        else:
            _require(type(outcome) is TrainingOutcome and question.unavailable_reason is None
                     and type(outcome.index) is int and 0 <= outcome.index < len(OUTCOMES), "outcome_differs")
            _clock(outcome.mature_at)
            path, horizon = OUTCOMES[outcome.index]
            earliest = 32 if path == "P4" else {5: 3, 15: 8, 30: 18}[horizon]
            _require(question.forecast_at + timedelta(minutes=earliest) <= outcome.mature_at < cutoff,
                     "label_not_mature")
    for key in origins:
        approach, breakout = (episode_stages.get((key, stage)) for stage in _STAGES)
        _require(approach is None or breakout is None or approach <= breakout, "stage_order_differs")


def _payoff_payload(row: HistoricalEntryPayoff, cutoff: datetime, denominator: int | None) -> list:
    question = row.question
    _digest(row.source_sha256)
    weight = None if denominator is None else Fraction(1, denominator)
    _same(row.weight, weight)
    _require(row.filled is None or type(row.filled) is bool, "fill_state_differs")
    if row.filled is None:
        _reason(row.unsupported_reason)
        _require(all(value is None for value in (row.actual_mature_at, row.gross_in_R, row.gap_in_R,
                                                row.no_fill_reason)), "unknown_payoff_has_values")
    else:
        _clock(row.actual_mature_at)
        _require(question.forecast_at < row.actual_mature_at < cutoff, "payoff_not_mature")
        _require(row.unsupported_reason is None and type(row.gross_in_R) is Fraction
                 and type(row.gap_in_R) is Fraction, "exact_payoff_required")
        if row.filled:
            entry = row.decision_close + row.risk * row.gap_in_R
            _require(row.no_fill_reason is None and question.question_id[2][4] < entry < question.question_id[2][3]
                     and entry + row.risk * row.gross_in_R > 0, "filled_geometry_differs")
        else:
            _reason(row.no_fill_reason)
            _require(row.gross_in_R == row.gap_in_R == 0, "known_no_fill_not_zero")
    outcome = question.outcome
    return [_question(question.question_id, question.forecast_at, row.decision_close, row.risk),
            question.session, question.origin_context,
            None if outcome is None else [outcome.index, _stamp(outcome.mature_at)], question.unavailable_reason,
            _ratio(row.decision_close), _ratio(row.risk), row.filled, _stamp(row.actual_mature_at),
            _ratio(row.gross_in_R), _ratio(row.gap_in_R), _ratio(row.weight), row.source_sha256,
            row.unsupported_reason, row.no_fill_reason]


def _reconstruct(original: tuple[HistoricalEntryPayoff, ...], cutoff: datetime, policy: str) -> EntryValueModel:
    _clock(cutoff)
    _digest(policy)
    _training_rows(original, cutoff)
    groups = Counter((row.question.symbol, row.question.session, row.question.stage)
                     for row in original if row.question.outcome is not None)
    payloads = [_payoff_payload(row, cutoff, None if row.question.outcome is None else
                groups[row.question.symbol, row.question.session, row.question.stage]) for row in original]
    input_digest = _hash([_MODEL_SCHEMA, _stamp(cutoff), policy, sorted(payloads, key=encode)])
    stages = []
    for stage in _STAGES:
        subset = tuple(row for row in original if row.question.stage == stage)
        paths = []
        for index in range(len(OUTCOMES)):
            selected = tuple(row for row in subset if row.question.outcome is not None
                             and row.question.outcome.index == index)
            complete = tuple(row for row in selected if row.filled is not None)
            missing = tuple(row for row in selected if row.filled is None)
            eligible_weight = sum((row.weight for row in selected), Fraction())
            complete_weight = sum((row.weight for row in complete), Fraction())
            reasons = tuple((reason, sum(row.unsupported_reason == reason for row in missing),
                             sum((row.weight for row in missing if row.unsupported_reason == reason), Fraction()))
                            for reason in sorted({row.unsupported_reason for row in missing}))
            support = PayoffSupport(len(selected), len(complete), sum(row.filled is False for row in complete),
                eligible_weight, complete_weight, len(missing), eligible_weight - complete_weight, reasons)
            if complete_weight:
                sums = [Fraction(), Fraction(), Fraction()]
                for row in complete:
                    if row.filled:
                        sums[0] += row.weight
                        sums[1] += row.weight * row.gross_in_R
                        sums[2] += row.weight * row.gap_in_R
                values = tuple(value / complete_weight for value in sums)
            else:
                values = (None, None, None)
            paths.append(PathPayoffMoments(index, *values, support))
        labels = Counter(row.question.unavailable_reason for row in subset if row.question.outcome is None)
        payoffs = Counter(row.unsupported_reason for row in subset if row.filled is None)
        stages.append(StagePayoffMoments(stage, len(subset), tuple(sorted(labels.items())),
                                        tuple(sorted(payoffs.items())), tuple(paths)))
    sources = tuple(sorted({row.source_sha256 for row in original}))
    serialized = []
    for stage in stages:
        paths = []
        for path in stage.paths:
            support = path.support
            paths.append([path.outcome_index, *map(_ratio, (path.mean_fill, path.mean_filled_gross_in_R,
                path.mean_filled_gap_in_R)), support.eligible_rows, support.complete_rows, support.known_no_fill_rows,
                _ratio(support.eligible_weight), _ratio(support.complete_weight), support.remaining_unknown_rows,
                _ratio(support.remaining_unknown_weight),
                [[reason, count, _ratio(weight)] for reason, count, weight in support.unknown_by_reason]])
        serialized.append([stage.stage, stage.candidate_rows, stage.label_unavailable_by_reason,
                           stage.payoff_unavailable_by_reason, paths])
    digest = _hash([_MODEL_SCHEMA, _WEIGHTS, _TRANSFER, OUTCOMES, _stamp(cutoff), policy,
                    input_digest, sources, serialized])
    return EntryValueModel(cutoff, policy, input_digest, sources, tuple(stages), digest)


def _query(query: EntryValueQuery, model: EntryValueModel) -> EntryValueResult:
    _require(type(query) is EntryValueQuery, "typed_query_required")
    _question(query.question_id, query.forecast_at, query.decision_close, query.risk)
    for digest in (query.forecast_source_sha256, query.causal_source_sha256, query.execution_policy_sha256):
        _digest(digest)
    _require(query.forecast_at >= model.cutoff_at and query.execution_policy_sha256 == model.execution_policy_sha256
             and query.forecast_model_key in _MODELS, "query_cutoff_policy_or_model_differs")
    _require(type(query.cost_bps) is Fraction and query.cost_bps >= 0, "cost_differs")
    probabilities = query.probabilities
    _require(type(probabilities) is tuple and len(probabilities) == len(OUTCOMES)
             and all(type(p) is Fraction and 0 <= p <= 1 for p in probabilities)
             and sum(probabilities) == 1, "probability_mass_differs")
    if query.numeric_boundary is not None:
        boundary = query.numeric_boundary
        _require(type(boundary) is ForecastProbabilityBoundary and type(boundary.original) is tuple
                 and len(boundary.original) == len(OUTCOMES)
                 and all(type(p) is float and isfinite(p) and 0 <= p <= 1 for p in boundary.original)
                 and abs(fsum(boundary.original) - 1) <= 1e-9, "probability_boundary_differs")
        original = tuple(Fraction.from_float(p) for p in boundary.original)
        total = sum(original, Fraction())
        normalized = tuple(p / total for p in original)
        correction = max(abs(a - b) for a, b in zip(normalized, original, strict=True))
        _same(boundary, ForecastProbabilityBoundary(boundary.original, normalized, total, correction))
        _same(probabilities, normalized)
    stage = model.stages[_STAGES.index(query.question_id[1])]
    expected_gross = expected_cost = unsupported = Fraction()
    nets = []
    invalid = False
    for probability, path in zip(probabilities, stage.paths, strict=True):
        if path.mean_fill is None:
            nets.append(None)
            unsupported += probability
            continue
        gross = query.risk * path.mean_filled_gross_in_R
        notional = query.decision_close * path.mean_fill + query.risk * path.mean_filled_gap_in_R
        cost = notional * query.cost_bps / 10_000
        nets.append(gross - cost)
        invalid |= bool(probability and path.mean_fill and notional <= 0)
        expected_gross += probability * gross
        expected_cost += probability * cost
    reason = ("positive_mass_without_complete_payoff" if unsupported else
              "nonpositive_transferred_entry_notional" if invalid else None)
    if reason:
        return EntryValueResult("abstain", "abstain", reason, query, model.model_sha256, model.input_sha256,
                                None, None, None, tuple(nets), unsupported, stage, _TRANSFER)
    net = expected_gross - expected_cost
    return EntryValueResult("supported", "enter" if net > 0 else "wait", None, query,
                            model.model_sha256, model.input_sha256, expected_gross, expected_cost,
                            net, tuple(nets), unsupported, stage, _TRANSFER)


def verify_entry_values(model: EntryValueModel, original_rows: Iterable[HistoricalEntryPayoff], *,
                        cutoff_at: datetime, execution_policy_sha256: str,
                        queries: tuple[EntryValueQuery, ...] = (),
                        results: tuple[EntryValueResult, ...] = ()) -> dict:
    """Verify one fitted model and ordered saved values against external inputs.

    Empty query/result tuples audit training only. Original rows must include
    unavailable labels and payoffs; a caller-selected subset is not detectable
    without an independently authenticated roster outside this pure component.
    """
    original = tuple(original_rows)
    expected = _reconstruct(original, cutoff_at, execution_policy_sha256)
    _same(model, expected)
    _require(type(queries) is tuple and type(results) is tuple and len(queries) == len(results),
             "query_result_roster_differs")
    actions = Counter()
    for query, result in zip(queries, results, strict=True):
        rebuilt = _query(query, expected)
        _same(result, rebuilt)
        actions[rebuilt.action] += 1
    return {"schema": SCHEMA, "verified": True, "original_rows": len(original),
            "label_supported_rows": sum(row.question.outcome is not None for row in original),
            "complete_payoff_rows": sum(row.filled is not None for row in original),
            "known_no_fill_rows": sum(row.filled is False for row in original),
            "unknown_payoff_rows": sum(row.filled is None for row in original),
            "queries": len(queries), "actions": dict(sorted(actions.items())),
            "model_sha256": expected.model_sha256, "training_input_sha256": expected.input_sha256,
            "cutoff_at": _stamp(cutoff_at), "execution_policy_sha256": execution_policy_sha256,
            "source_authenticated": False, "market_payoffs_reconstructed": False,
            "transfer_assumption": _TRANSFER, "maximum_absolute_difference": 0}
