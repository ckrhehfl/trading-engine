"""Three EI holding branches from one prepared entry and one modeled first fill.

The outer EntryRoster retains no-event coordinates and all suppressed questions.
This case adapter retains its original Opportunity even when entry is skipped or
unknown. Results are nominal event ledgers, not a concurrent account return.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from research import intraday_chart_cash as cash
from research.intraday_campaign_readiness import KST
from research.intraday_chart_inputs import _need
from research.intraday_context_entry import PreparedContextEntry, fill_context_entry, prepare_context_entry
from research.intraday_context_entry_roster import Opportunity
from research.intraday_context_features import project_context_features
from research.intraday_context_held import (
    ContextHeldAssessment, ContextHeldQuestion, ContextHeldRoster, ReadPrior, assess_context_held, project_context_held,
)
from research.intraday_context_reentry import ContextReentryIncrement, replay_context_reentry
from research.intraday_context_situations import context_frame
from research.intraday_ed_inputs import SessionInputs
from research.intraday_scenario_management_compare import ManagementReplay, _position_replay, compare_management
from research.intraday_scenario_policy import Episode


SCHEMA = "ei-context-matched-management-draft-v1"


class _ObservedOpens(Mapping):
    """Bind each requested clock to its first observation, including missingness."""

    def __init__(self, source):
        self.source, self.observed = source, {}

    def __getitem__(self, at):
        if at not in self.observed:
            price = self.source.get(at)
            _need(price is None or type(price) is Fraction and price > 0, "EI_management_exact_open_required")
            self.observed[at] = price
        return self.observed[at]

    def __iter__(self):
        return iter(self.observed)

    def __len__(self):
        return len(self.observed)


@dataclass(frozen=True)
class ContextHeldDecision:
    mode: str
    question: ContextHeldQuestion
    assessment: ContextHeldAssessment


@dataclass(frozen=True)
class ContextManagementCase:
    opportunity: Opportunity
    prepared: PreparedContextEntry | None
    status: str
    reason: str
    initial: Episode | None = None
    initial_fill: cash.Decision | None = None
    fixed: ManagementReplay | None = None
    chart: ManagementReplay | None = None
    chart_context: ManagementReplay | None = None
    decisions: tuple[ContextHeldDecision, ...] = ()
    reentry: ContextReentryIncrement | None = None
    holding_forecast: str = "mature_prior_path_analogue_not_neural_continuation"
    claim: str = "nominal_event_comparison_not_concurrent_account_return"
    modeled_open_receipts: tuple[tuple[datetime, Fraction | None], ...] = ()


def replay_context_management(
    opportunity: Opportunity, prepared: PreparedContextEntry | None, session: SessionInputs | None,
    opens: Mapping[datetime, Fraction | None], roster: ContextHeldRoster, *, read_prior_session: ReadPrior,
    include_reentry: bool = False,
) -> ContextManagementCase:
    """Keep one opportunity/entry/cost basis across fixed, chart and context holding.

    Initial preparation precedes this API and has no future-open argument. Every
    holding callback gets a new remaining-time question; it cannot reuse initial
    context-sequence probabilities. The optional rule increment always starts
    from chart_context's close, regardless of the other branches' results.
    """
    _need(type(opportunity) is Opportunity and type(roster) is ContextHeldRoster
          and isinstance(opens, Mapping) and callable(read_prior_session) and type(include_reentry) is bool,
          "EI_management_typed_case_inputs_required")
    _need(prepared is None or type(prepared) is PreparedContextEntry and prepared.features == opportunity.feature,
          "EI_management_opportunity_preparation_differs")
    _need(opportunity.status in ("opportunity", "feature_unsupported", "question_unsupported", "overlap_suppressed"),
          "EI_management_opportunity_status_differs")
    if opportunity.status != "opportunity":
        return ContextManagementCase(opportunity, prepared, opportunity.status, ", ".join(opportunity.reasons))
    if prepared is None:
        return ContextManagementCase(opportunity, None, "unresolved", "entry_preparation_missing")
    replay = prepare_context_entry(prepared.features, prepared.book_before, mode=prepared.mode,
        visible_minute=prepared.visible_minute, risk_budget=prepared.risk_budget, cost_bps=prepared.cost_bps,
        causal_source_sha256=prepared.causal_source_sha256, execution_policy_sha256=prepared.execution_policy_sha256,
        forecast_source_sha256=prepared.forecast_source_sha256, entry_value=prepared.entry_value,
        rule_enabled=prepared.rule_enabled)
    _need(replay == prepared, "EI_management_preparation_changed")
    if prepared.status == "abstain":
        return ContextManagementCase(opportunity, prepared, "entry_unsupported", prepared.reason)
    if prepared.step.action != "enter":
        return ContextManagementCase(opportunity, prepared, "no_entry", prepared.reason)
    if session is None:
        return ContextManagementCase(opportunity, prepared, "unresolved", "evaluation_session_missing")
    _need(type(session) is SessionInputs and session.symbol == opportunity.feature.question.anchor_id[0]
          and session.opening == opportunity.feature.question.anchor_id[1].start_at
          and opportunity.feature.question.path.end_at < session.closing
          and session.opening.astimezone(KST).date().isoformat() in roster.fold.test_sessions
          and session.opening >= roster.fold.first_test_opening, "EI_management_original_session_or_fold_differs")
    original = context_frame(session, decision_at=prepared.features.forecast_at)
    origin = context_frame(session, decision_at=prepared.features.question.episode_origin_at)
    _need(project_context_features(original, prepared.features.question) == prepared.features
          and origin.classification == prepared.features.question.episode_origin_context
          and origin.anchor == prepared.features.question.anchor_id[1], "EI_management_original_feature_differs")
    observed_opens = _ObservedOpens(opens)
    request = prepared.step.episode.pending
    _need(request is not None, "EI_management_initial_request_missing")
    initial, fill = fill_context_entry(prepared, request.execute_at, observed_opens.get(request.execute_at))
    if not fill.filled:
        return ContextManagementCase(opportunity, prepared, "unresolved" if fill.outcome == "indeterminate" else "declined",
                                     fill.reason, initial, fill, modeled_open_receipts=tuple(observed_opens.observed.items()))
    decisions = []

    def callback(mode):
        def manage(view):
            current = context_frame(session, decision_at=view.at)
            question = project_context_held(prepared.features, current, view.episode, session,
                                            entry_feature_sha256=prepared.feature_sha256)
            assessment = assess_context_held(roster, question, mode=mode, visible_minute=view.minute,
                                             read_prior_session=read_prior_session)
            decisions.append(ContextHeldDecision(mode, question, assessment))
            return assessment.projection.rollout.values if assessment.projection and assessment.projection.rollout else None
        return manage

    compared = compare_management(initial, session, observed_opens, values_at=callback("chart_context"))
    chart = _position_replay(initial, session, observed_opens, callback("chart"))
    reentry = replay_context_reentry(prepared, compared.dynamic, session, observed_opens) if include_reentry else None
    branches = (compared.fixed, chart, compared.dynamic)
    traces = tuple(branch.events for branch in branches) + (() if reentry is None else (reentry.events,))
    _need(all(event.at in observed_opens.observed and event.open_price == observed_opens.observed[event.at]
              for trace in traces for event in trace if event.fill_status is not None),
          "EI_management_fill_observation_differs")
    return ContextManagementCase(opportunity, prepared, "confirmed", "same_initial_fill_three_holding_branches",
        initial, fill, compared.fixed, chart, compared.dynamic, tuple(decisions), reentry,
        modeled_open_receipts=tuple(sorted(observed_opens.observed.items())))
