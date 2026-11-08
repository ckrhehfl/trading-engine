"""Build prior held questions from the causal EI rule-entry roster.

The caller supplies the authenticated feature roster, original session reader
and input pin. Each rule fill starts from the same flat book. The fixed
position replay emits the decision clocks before any continuation support is
checked. Labels and realized returns do not select the prior pool.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from research import intraday_chart_cash as cash
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_campaign_readiness import KST
from research.intraday_chart_inputs import _clock, _need, _session
from research.intraday_context_entry import context_entry_feature_sha256, fill_context_entry, prepare_context_entry
from research.intraday_context_entry_roster import CoordinateOpportunities, EntryRoster, Opportunity
from research.intraday_context_entry_values import _digest
from research.intraday_context_features import project_context_features
from research.intraday_context_held import ContextHeldQuestion, ContextHeldRoster, HistoricalHeldQuestion, project_context_held
from research.intraday_context_situations import context_frame
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_scenario_management_compare import _RequestedOpens, _position_replay
from research.intraday_scenario_rollout import _visible


@dataclass(frozen=True)
class HeldTrainingCase:
    session: str
    symbol: str
    opportunity: Opportunity
    status: str
    reason: str
    held_rows: int = 0


@dataclass(frozen=True)
class HeldTrainingPool:
    roster: ContextHeldRoster
    original_coordinates: tuple[CoordinateOpportunities, ...]
    cases: tuple[HeldTrainingCase, ...]
    source_sha256: str
    execution_policy_sha256: str


def _history(question: ContextHeldQuestion, session: SessionInputs, cutoff: datetime,
             source_sha256: str) -> HistoricalHeldQuestion:
    start = question.held.decision_at - 3 * MINUTE
    cap = question.held.planning_cap
    mature_at = None
    for offset in range((cap - start) // MINUTE + 1):
        at = start + offset * MINUTE
        row = session.minutes.get(at)
        if row is None:
            if at == cap:
                return HistoricalHeldQuestion(question, cap, None, source_sha256, "expiry_receipt_missing")
            continue  # A selected path retains any interior support gap.
        _need(type(row) is Minute and row.symbol == session.symbol and row.start_at == at,
              "EI_held_training_continuation_identity_differs")
        if row.available_at is None:
            return HistoricalHeldQuestion(question, cap, None, source_sha256, "continuation_availability_missing")
        _clock(row.available_at)
        _need(row.available_at >= at + 3 * MINUTE, "EI_held_training_continuation_before_declared_lag")
        if row.available_at >= cutoff:
            return HistoricalHeldQuestion(question, cap, None, source_sha256, "continuation_not_mature_before_cutoff")
        mature_at = row.available_at if mature_at is None else max(mature_at, row.available_at)
    return HistoricalHeldQuestion(question, cap, mature_at, source_sha256)


def _baseline(
    opportunity: Opportunity, session: SessionInputs, *, book: cash.CashBook,
    risk_budget: Fraction, cost_bps: int, execution_policy_sha256: str,
) -> tuple[str, str, tuple[ContextHeldQuestion, ...]]:
    feature = opportunity.feature
    question = feature.question
    at = feature.forecast_at
    _need(session.symbol == question.anchor_id[0] and session.opening == question.anchor_id[1].start_at
          and at < question.path.end_at < session.closing, "EI_held_training_original_session_differs")
    original = context_frame(session, decision_at=at)
    origin = context_frame(session, decision_at=question.episode_origin_at)
    _need(project_context_features(original, question) == feature
          and origin.classification == question.episode_origin_context
          and origin.anchor == question.anchor_id[1], "EI_held_training_original_feature_differs")
    visible, issue = _visible(session, at - 3 * MINUTE, at)
    if visible is None or visible.available_at != at:
        return "unresolved", issue or "causal_close_clock_differs", ()
    feature_sha256 = context_entry_feature_sha256(feature)
    prepared = prepare_context_entry(feature, book, mode="baseline", visible_minute=visible,
        risk_budget=risk_budget, cost_bps=cost_bps, causal_source_sha256=feature_sha256,
        execution_policy_sha256=execution_policy_sha256, rule_enabled=True)
    if prepared.status == "abstain":
        return "entry_unsupported", prepared.reason, ()
    if prepared.step.action != "enter":
        return "no_entry", prepared.reason, ()
    opens = _RequestedOpens(session)
    execute_at = prepared.step.episode.pending.execute_at
    initial, fill = fill_context_entry(prepared, execute_at, opens[execute_at])
    if not fill.filled:
        return ("unresolved" if fill.outcome == "indeterminate" else "declined"), fill.reason, ()

    observations = []

    def observe(view):
        current = context_frame(session, decision_at=view.at)
        observations.append(project_context_held(feature, current, view.episode, session,
            entry_feature_sha256=feature_sha256))

    replay = _position_replay(initial, session, opens, observe)
    # Earlier observed questions survive a later unknown close or exit fill.
    return "held_" + replay.status, replay.reason, tuple(observations)


def build_held_training_pool(
    entry_roster: EntryRoster, *, fold: ChronologicalFold, read_session: Callable[[str, str], SessionInputs | None],
    book: cash.CashBook, risk_budget: Fraction, cost_bps: int, source_sha256: str,
    execution_policy_sha256: str, checkpoint: Callable[[str], None] | None = None,
) -> HeldTrainingPool:
    """Read each supported training coordinate at most once and retain its status."""
    ContextHeldRoster((), fold=fold)  # Reject a bad split before calling the reader.
    _need(type(entry_roster) is EntryRoster and entry_roster.scenario in ("S1", "S2")
          and type(book) is cash.CashBook and not book.lots and not book.indeterminate
          and type(risk_budget) is Fraction and risk_budget > 0 and type(cost_bps) is int and cost_bps in (33, 43)
          and callable(read_session) and (checkpoint is None or callable(checkpoint)),
          "EI_held_training_parameters_differ")
    for digest in (entry_roster.source_sha256, source_sha256, execution_policy_sha256):
        _digest(digest)
    dates = frozenset(fold.training_sessions)
    cutoff = fold.first_test_opening
    coordinates = tuple(row for row in entry_roster.coordinates if row.session in dates)
    keys = tuple((row.session, row.symbol) for row in coordinates)
    _need(tuple(sorted(set(keys))) == keys and {day for day, _ in keys} == dates
          and dates <= set(entry_roster.original_dates), "EI_held_training_original_roster_differs")
    history, cases = [], []
    for row in coordinates:
        if checkpoint is not None:
            checkpoint("ei_held_training_coordinate")
        opened = False
        session = None
        for opportunity in row.questions:
            feature = opportunity.feature
            status, reason, questions = opportunity.status, ", ".join(opportunity.reasons), ()
            if status == "opportunity":
                if feature.forecast_at >= cutoff or feature.question.path.end_at + 3 * MINUTE >= cutoff:
                    status, reason = "unmatured", "expiry_not_mature_before_cutoff"
                else:
                    if not opened:
                        session = read_session(row.session, row.symbol)
                        opened = True
                        if session is not None:
                            _session(session)
                            _need(session.symbol == row.symbol and all(
                                at.astimezone(KST).date().isoformat() == row.session
                                for at in (session.opening, session.closing)), "EI_held_training_source_coordinate_differs")
                    if session is None:
                        status, reason = "unresolved", "training_session_missing"
                    else:
                        status, reason, questions = _baseline(opportunity, session, book=book,
                            risk_budget=risk_budget, cost_bps=cost_bps, execution_policy_sha256=execution_policy_sha256)
                        history.extend(_history(question, session, cutoff, source_sha256) for question in questions)
            cases.append(HeldTrainingCase(row.session, row.symbol, opportunity, status, reason, len(questions)))
    return HeldTrainingPool(ContextHeldRoster(history, fold=fold), coordinates, tuple(cases),
                            source_sha256, execution_policy_sha256)
