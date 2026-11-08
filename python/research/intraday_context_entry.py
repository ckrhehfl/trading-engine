"""Pure EI initial-entry adapter over the existing nominal-unit cash policy.

Preparation has no future-open argument. The selected arm consumes one bound
context-sequence value; the explicitly named baseline consumes a rule flag.
The caller owns authenticated source digests, the approved risk budget, the
common opportunity roster, nonoverlap and once-only consumption of requests.
Neither a source hash nor this event-level adapter certifies execution or a
portfolio result. No reader, fit, account implementation or order API lives here.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
from typing import Literal

from research import intraday_chart_cash as cash
from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_entry_values import (
    EntryValueQuery, EntryValueResult, ForecastProbabilityBoundary, StagePayoffMoments,
    _digest, forecast_probability_boundary,
)
from research.intraday_context_features import ContextFeatures
from research.intraday_context_model_inputs import encode, question_payload
from research.intraday_context_questions import ContextQuestion
from research.intraday_context_snapshot import _feature, _question, _read_question, _validate_feature
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, window
from research.intraday_scenario_forecast_targets import OUTCOMES
from research.intraday_scenario_policy import (
    EntryBounds, Episode, Step, Values, apply_modeled_open, decide, start_episode,
)


SCHEMA = "ei-context-entry-draft-v1"
EntryMode = Literal["selected", "baseline"]


@dataclass(frozen=True)
class PreparedContextEntry:
    mode: EntryMode
    features: ContextFeatures
    feature_sha256: str
    book_before: cash.CashBook
    visible_minute: Minute
    risk_budget: Fraction
    cost_bps: int
    causal_source_sha256: str
    execution_policy_sha256: str
    forecast_source_sha256: str | None
    entry_value: EntryValueResult | None
    rule_enabled: bool | None
    status: Literal["prepared", "abstain"]
    reason: str
    step: Step | None


def context_entry_feature_sha256(features: ContextFeatures) -> str:
    """Bind the exact causal feature/question bytes, not merely a question ID.

    Use this as EntryValueQuery.causal_source_sha256. Authentication of the
    original snapshot containing this member remains the caller's responsibility.
    """
    _need(type(features) is ContextFeatures, "EI_entry_adapter_typed_features_required")
    return sha256(encode(_feature(features))).hexdigest()


def _value(features: ContextFeatures, value: EntryValueResult, cost: int,
           causal: str, policy: str, forecast: str) -> None:
    _need(type(value) is EntryValueResult and type(value.query) is EntryValueQuery
          and type(value.training_support) is StagePayoffMoments, "EI_entry_adapter_typed_value_required")
    query = value.query
    question = features.question
    _need(query.question_id == question.question_id and query.forecast_at == features.forecast_at
          and query.decision_close == question.start_close and type(query.decision_close) is Fraction
          and query.risk == question.anchor_id[1].pre_peak_range and type(query.risk) is Fraction
          and type(query.cost_bps) is Fraction and query.cost_bps == cost
          and query.forecast_model_key == "context_sequence"
          and query.causal_source_sha256 == causal and query.execution_policy_sha256 == policy
          and query.forecast_source_sha256 == forecast, "EI_entry_adapter_value_binding_differs")
    question_payload(query.question_id, query.forecast_at)
    for digest in (value.model_sha256, value.training_input_sha256):
        _digest(digest)
    mass = query.probabilities
    paths = value.class_net_values
    _need(type(mass) is tuple and len(mass) == len(OUTCOMES)
          and all(type(p) is Fraction and 0 <= p <= 1 for p in mass) and sum(mass) == 1
          and type(paths) is tuple and len(paths) == len(OUTCOMES)
          and all(item is None or type(item) is Fraction for item in paths)
          and value.training_support.stage == question.stage, "EI_entry_adapter_value_mass_or_stage_differs")
    if query.numeric_boundary is not None:
        boundary = query.numeric_boundary
        _need(type(boundary) is ForecastProbabilityBoundary and boundary == forecast_probability_boundary(boundary.original)
              and boundary.probabilities == mass, "EI_entry_adapter_numeric_boundary_differs")
    unsupported = sum((p for p, net in zip(mass, paths, strict=True) if net is None), Fraction())
    _need(type(value.unsupported_probability_mass) is Fraction and value.unsupported_probability_mass == unsupported,
          "EI_entry_adapter_unknown_mass_differs")
    if value.status == "supported":
        _need(all(type(item) is Fraction for item in (value.expected_gross, value.expected_cost, value.expected_net))
              and value.expected_cost >= 0 and value.expected_net == value.expected_gross - value.expected_cost
              and unsupported == 0 and value.reason is None
              and value.action == ("enter" if value.expected_net > 0 else "wait")
              and value.expected_net == sum((p * net for p, net in zip(mass, paths, strict=True) if p), Fraction()),
              "EI_entry_adapter_supported_value_differs")
    else:
        _need(value.status == value.action == "abstain" and type(value.reason) is str and bool(value.reason)
              and value.expected_gross is value.expected_cost is value.expected_net is None,
              "EI_entry_adapter_abstention_differs")


def prepare_context_entry(
    features: ContextFeatures, book: cash.CashBook, *, mode: EntryMode,
    visible_minute: Minute, risk_budget: Fraction, cost_bps: int,
    causal_source_sha256: str, execution_policy_sha256: str,
    forecast_source_sha256: str | None = None, entry_value: EntryValueResult | None = None,
    rule_enabled: bool | None = None,
) -> PreparedContextEntry:
    """Freeze bounds and existing policy quantity using only this causal row.

    ``selected`` uses the per-unit value's sign, not its magnitude, for entry;
    the unchanged policy sizes at the worst permitted entry price. ``baseline``
    has no prediction and records its explicit rule flag. Both use the same
    close, stop, target cap, absolute question expiry and next-minute execution.
    """
    _need(type(features) is ContextFeatures and type(features.question) is ContextQuestion
          and type(book) is cash.CashBook and type(visible_minute) is Minute,
          "EI_entry_adapter_typed_inputs_required")
    _need(mode in ("selected", "baseline"), "EI_entry_adapter_mode_differs")
    for digest in (causal_source_sha256, execution_policy_sha256):
        _digest(digest)
    question, at = features.question, features.forecast_at
    _need(_read_question(_question(question)) == question, "EI_entry_adapter_question_metadata_differs")
    anchor = question.anchor_id[1]
    _need(all(type(value) is Fraction for value in (question.start_close, anchor.start_price, anchor.price,
              anchor.pre_peak_range, anchor.advance, anchor.band)), "EI_entry_adapter_exact_geometry_required")
    for clock in (at, question.path.start_at, question.path.end_at, features.observation_end, features.window_start):
        _clock(clock)
    _validate_feature(features)
    feature_digest = context_entry_feature_sha256(features)
    _need(causal_source_sha256 == feature_digest, "EI_entry_adapter_causal_feature_digest_differs")
    episode = start_episode(book, question.anchor_id[0], at, question.path.end_at,
                            risk_budget=risk_budget, cost_bps=cost_bps)
    if mode == "selected":
        _digest(forecast_source_sha256)
        _need(rule_enabled is None, "EI_entry_adapter_selected_rule_flag_forbidden")
        _value(features, entry_value, cost_bps, causal_source_sha256, execution_policy_sha256, forecast_source_sha256)
    else:
        _need(entry_value is None and forecast_source_sha256 is None and type(rule_enabled) is bool,
              "EI_entry_adapter_baseline_requires_explicit_rule_only")
    minute_at = at - 3 * MINUTE
    _need(visible_minute.symbol == episode.symbol and visible_minute.start_at == minute_at
          and visible_minute.available_at == at, "EI_entry_adapter_same_clock_price_required")
    observed = SessionInputs(episode.symbol, minute_at, minute_at + MINUTE, {minute_at: visible_minute})
    issues = window(observed, 0, 1, at).issues
    if not issues:
        _need(features.prices_in_R is None or features.prices_in_R[-1] == tuple(
            (Fraction(price) - question.path.level) / question.anchor_id[1].pre_peak_range
            for price in visible_minute.ohlc), "EI_entry_adapter_visible_feature_price_differs")
        _need(Fraction(visible_minute.ohlc[3]) == question.start_close, "EI_entry_adapter_causal_close_differs")
    reason = None
    if features.status != "ready":
        reason = "features_unsupported"
    elif issues:
        reason = "visible_price_unsupported"
    elif question.unsupported_reasons:
        reason = "question_unsupported: " + ", ".join(question.unsupported_reasons)
    elif not question.economic_priority:
        reason = "lower_same_clock_question_priority"
    elif not 0 < question.path.stop < question.start_close < question.path.target:
        reason = "unsupported_entry_price_geometry"
    elif mode == "selected" and entry_value.status == "abstain":
        reason = "entry_value_unsupported: " + entry_value.reason
    step = None
    if reason is None:
        bounds = EntryBounds(question.path.stop, min(question.start_close + question.path.band, question.path.target))
        # A baseline rule flag is explicitly different from a modeled payoff.
        utility = entry_value.expected_net if mode == "selected" else Fraction(int(rule_enabled))
        step = decide(episode, at, visible_minute=visible_minute, bounds=bounds,
                      values=Values(at, wait=Fraction(), enter=utility))
        reason = step.reason
    return PreparedContextEntry(mode, features, feature_digest, book, visible_minute, risk_budget, cost_bps,
        causal_source_sha256, execution_policy_sha256, forecast_source_sha256, entry_value, rule_enabled,
        "prepared" if step is not None else "abstain", reason, step)


def fill_context_entry(prepared: PreparedContextEntry, at: datetime,
                       open_price: Fraction | None) -> tuple[Episode, cash.Decision]:
    """Resolve only the frozen first request; unknown open freezes the book.

    Reconstructing the pure preparation detects stale or altered requests; it
    does not fit a model or inspect future data. The caller consumes the returned
    episode once. Reusing an immutable input deliberately creates a branch.
    """
    _need(type(prepared) is PreparedContextEntry, "EI_entry_adapter_typed_preparation_required")
    replay = prepare_context_entry(prepared.features, prepared.book_before, mode=prepared.mode,
        visible_minute=prepared.visible_minute, risk_budget=prepared.risk_budget, cost_bps=prepared.cost_bps,
        causal_source_sha256=prepared.causal_source_sha256, execution_policy_sha256=prepared.execution_policy_sha256,
        forecast_source_sha256=prepared.forecast_source_sha256, entry_value=prepared.entry_value,
        rule_enabled=prepared.rule_enabled)
    _need(replay == prepared, "EI_entry_adapter_preparation_changed")
    step = prepared.step
    _need(prepared.status == "prepared" and step is not None and step.action == "enter",
          "EI_entry_adapter_pending_first_request_required")
    episode, request = step.episode, step.episode.pending
    _clock(at)
    _need(request is not None and request.action == "enter" and episode.attempts == 1
          and not episode.book.lots and not episode.book.indeterminate and episode.entered_at is None
          and episode.closed_at is None and request.execute_at == episode.last_at + MINUTE == at
          and (open_price is None or type(open_price) is Fraction and open_price > 0),
          "EI_entry_adapter_pending_clock_or_price_differs")
    if open_price is not None and open_price >= prepared.features.question.path.target:
        # The core bound is inclusive; target itself leaves no positive target
        # distance. Preserve this known no-fill without manufacturing a price.
        outcome = cash.Decision(episode.book, "entry_at_or_above_target")
        return replace(episode, pending=None, last_at=at), outcome
    return apply_modeled_open(episode, at, open_price)
