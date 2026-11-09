"""Independently reconstruct a replacement entry from authenticated causal inputs.

The caller binds the original question, training moments and both forecasts to
their saved source streams. No execution price or evaluation outcome is used.
"""

from dataclasses import asdict, replace
from datetime import timedelta, timezone
from fractions import Fraction
from math import fsum, isfinite

from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_economic_fold import _plain
from research.intraday_context_entry_compare import EntryDecisionPair
from research.intraday_context_entry_training_reference import _ORDER_FRACTION
from research.intraday_context_forecast_fold import MODEL_KEYS, QuestionForecast
from research.intraday_ed_inputs import MINUTE, InputRefusal, SessionInputs
from research.intraday_scenario_forecast_targets import OUTCOMES
from research.intraday_scenario_open_reference import _price_row
from research.intraday_scenario_policy import EntryBounds, Episode, Request, Step


_KST = timezone(timedelta(hours=9))
_COST_BPS = 33


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal("EI_saved_fixed_exit_reference_" + reason)


def _weights(probabilities):
    _require(type(probabilities) is tuple and len(probabilities) == len(OUTCOMES)
             and all(type(p) in (int, float) and isfinite(p) and 0 <= p <= 1 for p in probabilities)
             and abs(fsum(probabilities) - 1) <= 1e-9, "forecast_mass_differs")
    return tuple(Fraction.from_float(float(p)) for p in probabilities)


def _net(paths, weights, close, risk):
    _require(tuple(path.outcome_index for path in paths) == tuple(range(len(OUTCOMES))), "path_order_differs")
    weighted_net = Fraction()
    incomplete = invalid_notional = False
    for path, weight in zip(paths, weights, strict=True):
        if not weight:
            continue
        if path.mean_fill is None:
            incomplete = True
            continue
        notional = close * path.mean_fill + risk * path.mean_filled_gap_in_R
        invalid_notional |= bool(path.mean_fill and notional <= 0)
        weighted_net += weight * (risk * path.mean_filled_gross_in_R - notional * Fraction(_COST_BPS, 10_000))
    if incomplete:
        return None, "positive_mass_without_complete_payoff"
    if invalid_notional:
        return None, "nonpositive_transferred_entry_notional"
    return weighted_net / sum(weights), None


def _baseline(prepared, features):
    """Return structural abstention, baseline static outcome and pending step."""
    question, at = features.question, features.forecast_at
    symbol = question.anchor_id[0]
    book, risk = prepared.book_before, prepared.risk_budget
    minute, start = prepared.visible_minute, at - 3 * MINUTE
    _require(prepared.mode == "baseline" and prepared.features == features and prepared.rule_enabled is True
             and prepared.cost_bps == _COST_BPS and not book.lots and not book.indeterminate
             and not book.inventory_uncertain and type(risk) is Fraction and risk > 0
             and (minute.symbol, minute.start_at, minute.available_at) == (symbol, start, at),
             "original_baseline_binding_differs")
    observed = SessionInputs(symbol, start, start + MINUTE, {start: minute})
    quote, issue = _price_row(observed, start, at)
    close = question.start_close
    if issue is None:
        _require(Fraction(quote.ohlc[3]) == close, "observed_close_differs")

    if features.status != "ready":
        reason = "features_unsupported"
    elif issue:
        reason = "visible_price_unsupported"
    elif question.unsupported_reasons:
        reason = "question_unsupported: " + ", ".join(question.unsupported_reasons)
    elif not question.economic_priority:
        reason = "lower_same_clock_question_priority"
    elif not 0 < question.path.stop < close < question.path.target:
        reason = "unsupported_entry_price_geometry"
    else:
        reason = None
    if reason is not None:
        _require((prepared.status, prepared.reason, prepared.step) == ("abstain", reason, None),
                 "original_abstention_differs")
        static = ["unsupported", reason]
        return static, static, None

    end, execute_at = question.path.end_at, at + MINUTE
    episode = Episode(symbol, at, end, risk, _COST_BPS, book, book.next_lot_id, at)
    bounds = EntryBounds(question.path.stop, min(close + question.path.band, question.path.target))
    structural = None
    if execute_at > end:
        action, reason = "end", "episode_expired"
    elif end - execute_at < 5 * MINUTE:
        action, reason = "end", "too_little_time_for_entry"
    elif not bounds.stop < close <= bounds.max_price:
        action, reason = "wait", "observed_close_outside_entry_bounds"
    else:
        rate = Fraction(_COST_BPS, 10_000)
        cash_budget = min(book.cash * _ORDER_FRACTION, book.cash / (1 + rate))
        loss_per_unit = bounds.max_price * (1 + rate) - bounds.stop
        quantity = min((risk / 2) // loss_per_unit, cash_budget // bounds.max_price)
        if quantity:
            action, reason = "enter", "higher_value_of_entry"
            request = Request(action, execute_at, quantity, bounds, (book.cash, book.cash))
            episode = replace(episode, attempts=1, pending=request)
        else:
            action, reason = "wait", "insufficient_risk_or_cash_for_one_unit"
    if reason in ("episode_expired", "too_little_time_for_entry", "observed_close_outside_entry_bounds"):
        structural = ["no_entry", reason]
    step = Step(episode, action, reason)
    _require((prepared.status, prepared.reason, prepared.step) == ("prepared", reason, step),
             "original_baseline_step_differs")
    if action == "enter":
        return None, None, step
    return structural, ["no_entry", reason], None


def reference_fixed_exit_fields(original: EntryDecisionPair, forecast: QuestionForecast, *,
                                forecast_sha256: str) -> dict:
    """Recompute the four stored LightGBM decision fields with exact arithmetic."""
    _require(type(original) is EntryDecisionPair and type(forecast) is QuestionForecast
             and wire.valid_sha(forecast_sha256), "typed_inputs_required")
    opportunity, baseline, prior = original.opportunity, original.baseline, original.selected
    features = opportunity.feature
    question, at = features.question, features.forecast_at
    _require(forecast.question == features and forecast.question_id == opportunity.question_id
             and forecast.session == at.astimezone(_KST).date().isoformat() and forecast.symbol == question.anchor_id[0]
             and tuple(arm.key for arm in forecast.predictions) == MODEL_KEYS, "forecast_question_differs")
    result = {"static": None, "baseline_static": None, "expected_net": None, "request_sha256": None}
    if opportunity.status != "opportunity" or baseline is None:
        _require(baseline is prior is None and original.selected_unavailable == original.baseline_unavailable,
                 "shared_causal_state_differs")
        static = [opportunity.status, ", ".join(opportunity.reasons)]
        if opportunity.status == "opportunity":
            reason = original.baseline_unavailable
            _require(type(reason) is str and bool(reason), "causal_absence_reason_required")
            static = ["unsupported", reason]
        return {**result, "static": static, "baseline_static": static}

    structural, baseline_static, pending = _baseline(baseline, features)
    result["baseline_static"] = baseline_static
    arms = {arm.key: arm for arm in forecast.predictions}
    sequence, tree = arms["context_sequence"], arms["context_lightgbm"]
    _require((sequence.status, sequence.reason) == (tree.status, tree.reason), "forecast_support_differs")
    if tree.status == "unsupported":
        _require(prior is None and sequence.probabilities is tree.probabilities is None
                 and type(tree.reason) is str and bool(tree.reason) and original.selected_unavailable == tree.reason,
                 "unsupported_forecast_differs")
        return {**result, "static": ["unsupported", tree.reason]}

    _require(tree.status == "predicted" and tree.reason is None and prior is not None
             and prior.entry_value is not None and original.selected_unavailable is None, "sequence_value_required")
    value = prior.entry_value
    query, stage = value.query, value.training_support
    close, risk = question.start_close, question.anchor_id[1].pre_peak_range
    _require(prior.mode == "selected" and prior.features == features and prior.forecast_source_sha256 == forecast_sha256
             and query.forecast_model_key == "context_sequence" and query.forecast_source_sha256 == forecast_sha256
             and query.question_id == question.question_id and query.forecast_at == at
             and (query.decision_close, query.risk, query.cost_bps) == (close, risk, Fraction(_COST_BPS))
             and query.causal_source_sha256 == baseline.causal_source_sha256
             and query.execution_policy_sha256 == baseline.execution_policy_sha256 and stage.stage == question.stage
             and query.numeric_boundary is not None and query.numeric_boundary.original == sequence.probabilities,
             "original_sequence_binding_differs")
    original_weights = _weights(sequence.probabilities)
    total = sum(original_weights)
    _require(query.probabilities == tuple(weight / total for weight in original_weights)
             and _net(stage.paths, original_weights, close, risk) == (value.expected_net, value.reason),
             "original_sequence_value_differs")

    net, reason = _net(stage.paths, _weights(tree.probabilities), close, risk)
    result["expected_net"] = None if net is None else str(net)
    if structural is not None and structural[0] == "unsupported":
        result["static"] = structural
    elif reason is not None:
        result["static"] = ["unsupported", "entry_value_unsupported: " + reason]
    elif structural is not None:
        result["static"] = structural
    elif net <= 0:
        result["static"] = ["no_entry", "entry_value_not_higher"]
    elif pending is None:
        result["static"] = baseline_static
    else:
        result["request_sha256"] = wire.digest(wire.encode(_plain(asdict(pending))))
    return result
