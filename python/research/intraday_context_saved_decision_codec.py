"""Read back one authenticated EI causal decision without outcome access."""

from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from research import intraday_campaign_wsl_transport as wire
from research import intraday_chart_cash as cash
from research.intraday_chart_inputs import _need
from research.intraday_context_economic_binding import COST_BPS, POLICY
from research.intraday_context_economic_fold import _plain
from research.intraday_context_entry import context_entry_feature_sha256, prepare_context_entry
from research.intraday_context_entry_compare import EntryDecisionPair
from research.intraday_context_entry_roster import Opportunity
from research.intraday_context_entry_values import (
    EntryValueQuery, EntryValueResult, PathPayoffMoments, PayoffSupport, StagePayoffMoments,
    _assess_stage, _digest, _reason, forecast_probability_boundary,
)
from research.intraday_context_snapshot import _fraction, _keys, _list, _read_time
from research.intraday_ed_inputs import STATES, InputRefusal, Minute
from research.intraday_scenario_forecast_targets import OUTCOMES


def _decimal(raw: str) -> Decimal:
    _need(type(raw) is str, "EI_saved_decision_decimal_required")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise InputRefusal("EI_saved_decision_invalid_decimal") from exc
    _need(value.is_finite() and str(value) == raw, "EI_saved_decision_noncanonical_decimal")
    return value


def _minute(raw: dict) -> Minute:
    _keys(raw, "symbol start_at available_at ohlc shares price_state volume_state")
    start, available = _read_time(raw["start_at"]), _read_time(raw["available_at"])
    _need(start is not None and available is not None and type(raw["symbol"]) is str,
          "EI_saved_decision_minute_identity_differs")
    price, volume = raw["price_state"], raw["volume_state"]
    _need(type(price) is str and price in STATES and type(volume) is str and volume in STATES,
          "EI_saved_decision_minute_state_differs")
    ohlc, shares = raw["ohlc"], raw["shares"]
    if ohlc is not None:
        _list(ohlc, 4)
        ohlc = tuple(map(_decimal, ohlc))
    return Minute(raw["symbol"], start, available, ohlc, None if shares is None else _decimal(shares), price, volume)


def _count(raw: int) -> int:
    _need(type(raw) is int and raw >= 0, "EI_saved_decision_count_differs")
    return raw


def _reasons(raw: list, *, weighted: bool = False) -> tuple:
    _list(raw)
    result = []
    for row in raw:
        _list(row, 3 if weighted else 2)
        _reason(row[0])
        values = (row[0], _count(row[1]))
        result.append((*values, _fraction(row[2])) if weighted else values)
    return tuple(result)


def _path(raw: dict, index: int) -> PathPayoffMoments:
    _keys(raw, "outcome_index mean_fill mean_filled_gross_in_R mean_filled_gap_in_R support")
    _need(_count(raw["outcome_index"]) == index, "EI_saved_decision_path_order_differs")
    support = raw["support"]
    _keys(support, "eligible_rows complete_rows known_no_fill_rows eligible_weight complete_weight "
          "remaining_unknown_rows remaining_unknown_weight unknown_by_reason")
    counts = [_count(support[key]) for key in ("eligible_rows", "complete_rows", "known_no_fill_rows")]
    weights = [_fraction(support[key]) for key in ("eligible_weight", "complete_weight")]
    unknown_rows = _count(support["remaining_unknown_rows"])
    unknown_weight = _fraction(support["remaining_unknown_weight"])
    reasons = _reasons(support["unknown_by_reason"], weighted=True)
    _need(counts[0] == counts[1] + unknown_rows and counts[2] <= counts[1]
          and weights[0] == weights[1] + unknown_weight and weights[1] >= 0 and unknown_weight >= 0
          and sum(row[1] for row in reasons) == unknown_rows and sum(row[2] for row in reasons) == unknown_weight,
          "EI_saved_decision_path_support_differs")
    moments = tuple(None if raw[key] is None else _fraction(raw[key])
                    for key in ("mean_fill", "mean_filled_gross_in_R", "mean_filled_gap_in_R"))
    _need((not weights[1] and moments == (None, None, None))
          or (weights[1] > 0 and all(value is not None for value in moments) and 0 <= moments[0] <= 1),
          "EI_saved_decision_path_moments_differ")
    return PathPayoffMoments(index, *moments, PayoffSupport(*counts, *weights, unknown_rows, unknown_weight, reasons))


def _entry_value(raw: dict, opportunity: Opportunity, *, forecast_sha256: str,
                 causal_sha256: str, execution_policy_sha256: str) -> EntryValueResult:
    _need(type(raw) is dict and type(raw.get("query")) is dict, "EI_saved_decision_value_required")
    numeric = raw["query"].get("numeric_boundary")
    _need(type(numeric) is dict, "EI_saved_decision_numeric_boundary_required")
    probabilities = numeric.get("original")
    _list(probabilities, len(OUTCOMES))
    boundary = forecast_probability_boundary(tuple(probabilities))
    features = opportunity.feature
    question = features.question
    query = EntryValueQuery(question.question_id, features.forecast_at, question.start_close,
        question.anchor_id[1].pre_peak_range, Fraction(COST_BPS), boundary.probabilities, "context_sequence",
        forecast_sha256, causal_sha256, execution_policy_sha256, boundary)
    stage = raw.get("training_support")
    _keys(stage, "stage candidate_rows label_unavailable_by_reason payoff_unavailable_by_reason paths")
    _list(stage["paths"], len(OUTCOMES))
    _need(stage["stage"] == question.stage, "EI_saved_decision_value_stage_differs")
    support = StagePayoffMoments(question.stage, _count(stage["candidate_rows"]),
        _reasons(stage["label_unavailable_by_reason"]), _reasons(stage["payoff_unavailable_by_reason"]),
        tuple(_path(path, index) for index, path in enumerate(stage["paths"])))
    model_sha, training_sha = raw.get("model_sha256"), raw.get("training_input_sha256")
    _digest(model_sha)
    _digest(training_sha)
    return _assess_stage(support, query, model_sha, training_sha)


def decode_saved_entry_pair(raw: dict, opportunity: Opportunity, *, scenario: str,
                            forecast_sha256: str, execution_policy_sha256: str) -> EntryDecisionPair:
    """Rebuild the sealed pair under the fixed policy and require exact byte equality.

    The caller binds the stream and independently supplies the opportunity. The
    matching folded forecast and training identity are checked by the caller.
    """
    _keys(raw, "opportunity baseline selected baseline_unavailable selected_unavailable")
    _need(type(opportunity) is Opportunity and scenario in ("S1", "S2"), "EI_saved_decision_scope_differs")
    _digest(forecast_sha256)
    _digest(execution_policy_sha256)
    if opportunity.status != "opportunity":
        pair = EntryDecisionPair(opportunity, None, None)
    elif raw["baseline"] is None:
        reason = raw["baseline_unavailable"]
        _need(reason in ("causal_close_missing", "causal_close_availability_differs"),
              "EI_saved_decision_common_causal_reason_differs")
        pair = EntryDecisionPair(opportunity, None, None, reason, reason)
    else:
        _need(type(raw["baseline"]) is dict, "EI_saved_decision_baseline_required")
        features = opportunity.feature
        book = cash.new_book("EI-" + scenario)
        causal_sha = context_entry_feature_sha256(features)
        common = dict(visible_minute=_minute(raw["baseline"].get("visible_minute")),
                      risk_budget=Fraction(POLICY["risk_budget_won"]), cost_bps=COST_BPS,
                      causal_source_sha256=causal_sha, execution_policy_sha256=execution_policy_sha256)
        baseline = prepare_context_entry(features, book, mode="baseline", rule_enabled=True, **common)
        if raw["selected"] is None:
            reason = raw["selected_unavailable"]
            _reason(reason)
            pair = EntryDecisionPair(opportunity, baseline, None, selected_unavailable=reason)
        else:
            _need(type(raw["selected"]) is dict, "EI_saved_decision_selection_required")
            value = _entry_value(raw["selected"].get("entry_value"), opportunity, forecast_sha256=forecast_sha256,
                                 causal_sha256=causal_sha, execution_policy_sha256=execution_policy_sha256)
            selected = prepare_context_entry(features, book, mode="selected", forecast_source_sha256=forecast_sha256,
                                             entry_value=value, **common)
            pair = EntryDecisionPair(opportunity, baseline, selected)
    _need(wire.encode(_plain(asdict(pair))) == wire.encode(raw), "EI_saved_decision_roundtrip_differs")
    return pair
