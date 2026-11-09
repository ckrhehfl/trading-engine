"""Prepare an EI LightGBM decision, then project its identical fixed exit.

The caller authenticates predecessor artifacts and durably seals all decisions
before supplying outcomes. The functions have no input or execution authority.
"""

from dataclasses import dataclass
from fractions import Fraction

from research.intraday_chart_inputs import _need
from research.intraday_context_entry import prepare_context_entry
from research.intraday_context_entry_compare import (
    EntryArmOutcome, EntryDecisionPair, EntryPairOutcome, _NO_TRADE, _static_outcome,
)
from research.intraday_context_entry_values import EntryValueResult, _digest, reassess_saved_entry_value
from research.intraday_context_forecast_fold import MODEL_KEYS, QuestionForecast, _KST
from research.intraday_scenario_policy import EntryBounds, Step, Values, decide, start_episode


@dataclass(frozen=True)
class SavedFixedExitDecision:
    original: EntryDecisionPair
    alternate_value: EntryValueResult | None
    alternate_step: Step | None
    static_outcome: EntryArmOutcome | None


@dataclass(frozen=True)
class SavedFixedExitOutcome:
    decision: SavedFixedExitDecision
    original: EntryPairOutcome
    selected: EntryArmOutcome

    @property
    def baseline(self) -> EntryArmOutcome:
        return self.original.baseline

    @property
    def net_delta(self) -> Fraction | None:
        if self.baseline.payoff is None or self.selected.payoff is None:
            return None
        return self.selected.payoff.net - self.baseline.payoff.net


def prepare_saved_fixed_exit(original: EntryDecisionPair, forecast: QuestionForecast, *,
                             forecast_sha256: str) -> SavedFixedExitDecision:
    """Prepare one model substitution without accepting any observed payoff."""
    _digest(forecast_sha256)
    _need(type(original) is EntryDecisionPair and type(forecast) is QuestionForecast,
          "EI_saved_fixed_typed_forecast_and_decision_required")
    opportunity, baseline, prior = original.opportunity, original.baseline, original.selected
    features = opportunity.feature
    _need(forecast.question == features and forecast.question_id == opportunity.question_id
          and forecast.session == features.forecast_at.astimezone(_KST).date().isoformat()
          and forecast.symbol == features.question.anchor_id[0]
          and tuple(arm.key for arm in forecast.predictions) == MODEL_KEYS, "EI_saved_fixed_forecast_identity_differs")
    static = _static_outcome(opportunity, baseline, original.baseline_unavailable, "baseline")
    if opportunity.status != "opportunity" or baseline is None:
        _need(prior is None and original.selected_unavailable == original.baseline_unavailable,
              "EI_saved_fixed_shared_causal_state_differs")
        return SavedFixedExitDecision(original, None, None, static)

    common = {"visible_minute": baseline.visible_minute, "risk_budget": baseline.risk_budget,
              "cost_bps": baseline.cost_bps, "causal_source_sha256": baseline.causal_source_sha256,
              "execution_policy_sha256": baseline.execution_policy_sha256}
    _need(baseline.cost_bps == 33 and baseline == prepare_context_entry(features, baseline.book_before,
              mode="baseline", rule_enabled=True, **common), "EI_saved_fixed_original_baseline_differs")
    sequence = forecast.predictions[MODEL_KEYS.index("context_sequence")]
    tree = forecast.predictions[MODEL_KEYS.index("context_lightgbm")]
    _need((sequence.status, sequence.reason) == (tree.status, tree.reason), "EI_saved_fixed_arm_support_differs")
    if tree.status == "unsupported":
        _need(prior is None and sequence.probabilities is tree.probabilities is None
              and type(tree.reason) is str and bool(tree.reason) and original.selected_unavailable == tree.reason,
              "EI_saved_fixed_unsupported_forecast_differs")
        return SavedFixedExitDecision(original, None, None, EntryArmOutcome("unsupported", tree.reason))

    _need(tree.status == "predicted" and tree.reason is None and prior is not None
          and prior.entry_value is not None and prior.entry_value.query.numeric_boundary is not None
          and prior.entry_value.query.numeric_boundary.original == sequence.probabilities
          and original.selected_unavailable is None, "EI_saved_fixed_original_sequence_differs")
    _need(prior == prepare_context_entry(features, baseline.book_before, mode="selected",
              forecast_source_sha256=forecast_sha256, entry_value=prior.entry_value, **common),
          "EI_saved_fixed_original_preparation_differs")
    value = reassess_saved_entry_value(prior.entry_value, tree.probabilities)
    if baseline.status == "abstain":
        return SavedFixedExitDecision(original, value, None, static)
    if value.status == "abstain":
        return SavedFixedExitDecision(original, value, None,
                                      EntryArmOutcome("unsupported", "entry_value_unsupported: " + value.reason))

    at, question = features.forecast_at, features.question
    episode = start_episode(baseline.book_before, question.anchor_id[0], at, question.path.end_at,
                            risk_budget=baseline.risk_budget, cost_bps=baseline.cost_bps)
    bounds = EntryBounds(question.path.stop, min(question.start_close + question.path.band, question.path.target))
    step = decide(episode, at, visible_minute=baseline.visible_minute, bounds=bounds,
                  values=Values(at, wait=Fraction(), enter=value.expected_net))
    if step.action != "enter":
        return SavedFixedExitDecision(original, value, step, EntryArmOutcome("no_entry", step.reason, _NO_TRADE))
    _need(static is None and step == baseline.step, "EI_saved_fixed_pending_request_differs")
    return SavedFixedExitDecision(original, value, step, None)


def project_saved_fixed_exit(decision: SavedFixedExitDecision, saved: EntryPairOutcome) -> SavedFixedExitOutcome:
    """Join a previously sealed decision to the audited original fixed exit."""
    _need(type(decision) is SavedFixedExitDecision and type(saved) is EntryPairOutcome
          and decision.original == saved.decisions, "EI_saved_fixed_outcome_identity_differs")
    original = decision.original
    static = _static_outcome(original.opportunity, original.baseline, original.baseline_unavailable, "baseline")
    baseline = saved.baseline
    if static is not None:
        _need(baseline == static, "EI_saved_fixed_baseline_static_outcome_differs")
    else:
        _need(baseline.status in ("closed", "no_fill", "unresolved")
              and (baseline.payoff is None) == (baseline.status == "unresolved"),
              "EI_saved_fixed_baseline_terminal_outcome_differs")
    selected = decision.static_outcome
    if selected is None:
        value = decision.alternate_value
        _need(type(value) is EntryValueResult and value.query.forecast_model_key == "context_lightgbm"
              and value.action == "enter" and type(value.expected_net) is Fraction and value.expected_net > 0
              and static is None and decision.alternate_step == original.baseline.step,
              "EI_saved_fixed_pending_request_differs")
        selected = baseline
    return SavedFixedExitOutcome(decision, saved, selected)
