"""Fixed ED policy orchestration over an authenticated common snapshot.

No source readers, logger, transport, threshold search or model selection live
here. Snapshot authentication/scientific reconstruction precede these calls.
Fitted operations remain training-only; test labels never enter admission.
"""
from __future__ import annotations

from collections import Counter
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from fractions import Fraction

from research import intraday_ed_models as models
from research import intraday_ed_replay as replay
from research import intraday_ed_snapshot as snapshot
from research.intraday_campaign_ml_labels import MLCalendar
from research.intraday_pattern_calibration import dispersion


RULE = "ED-H1-RULE"
CONTROL = "ED-H1-WITHOUT-FILING"
LEARNER = "ED-H1-LGBM"
ABLATION = "ED-H1-LGBM-WITHOUT-FILING"


class ComparisonRefusal(ValueError):
    """A changed comparison contract is not an unavailable observation."""


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise ComparisonRefusal(reason)


@dataclass(frozen=True)
class PolicyOpportunity:
    scenario: str
    symbol: str
    session: str
    parent_status: str
    unknown_at: datetime | None
    candidate: replay.EDCandidate | None
    common_support: bool
    features: tuple[float, ...] | None
    event_types: tuple[str, ...]
    rule_status: str
    control_status: str
    label: replay.EDUnitLabel | None
    volatility: Fraction | None = None
    liquidity: Fraction | None = None


@dataclass(frozen=True)
class PolicyDecision:
    scenario: str
    symbol: str
    session: str
    status: str
    reason: str
    action: replay.EDAction | None
    parent_id: str | None
    event_types: tuple[str, ...]


@dataclass(frozen=True)
class PolicyPlan:
    arm: str
    scenario: str
    evaluation_sessions: tuple[str, ...]
    decisions: tuple[PolicyDecision, ...]
    unavailable_sessions: tuple[str, ...]
    fits: tuple[tuple[models.EDFoldView, models.EDFitResult,
                      models.EDPredictionBatch, dict], ...] = ()


@dataclass(frozen=True)
class PolicyCarry:
    arm: str
    scenario: str
    domain: str
    state: replay.EDAccountState


def policy_carry(plan: PolicyPlan, result: replay.EDAccountResult) -> PolicyCarry:
    _need(type(result) is replay.EDAccountResult and result.state.book.arm == plan.arm
          and result.state.risk_scope == "continuous", "continuous_policy_result_required")
    suffix = "/continuous_sticky_unknown"
    _need(result.scenario.endswith(suffix), "continuous_domain_binding_required")
    domain = result.scenario.removesuffix(suffix)
    _need(domain in ("full_original_population", "observed_predecision_support_conditional"),
          "registered_account_domain_required")
    return PolicyCarry(plan.arm, plan.scenario, domain, result.state)


def _validate(calendar: MLCalendar, rows: Sequence[PolicyOpportunity]) -> None:
    _need(type(calendar) is MLCalendar and len(calendar.source_reference_sessions) == 257
          and len(calendar.evaluation_reference_sessions) == 157, "fixed_ED_calendar_required")
    keys = set()
    for row in rows:
        _need(type(row) is PolicyOpportunity and row.scenario in models.SCENARIOS
              and row.session in calendar.source_reference_sessions
              and type(row.common_support) is bool, "invalid_ED_opportunity")
        key = row.scenario, row.symbol, row.session
        _need(key not in keys, "duplicate_original_coordinate")
        keys.add(key)
        _need(row.parent_status in ("parent", "known_no_parent", "parent_identity_unknown"),
              "invalid_parent_state")
        _need((row.parent_status == "parent") == (row.candidate is not None),
              "parent_candidate_mismatch")
        if row.parent_status == "parent_identity_unknown":
            _need(type(row.unknown_at) is datetime and row.unknown_at.utcoffset() is not None
                  and row.unknown_at.date().isoformat() == row.session,
                  "unknown_parent_causal_clock_required")
        if row.candidate is not None:
            _need(row.candidate.symbol == row.symbol
                  and row.candidate.opening.date().isoformat() == row.session,
                  "candidate_coordinate_mismatch")
        for status in (row.rule_status, row.control_status):
            _need(status in ("accepted", "declined", "unknown"), "invalid_rule_decision")
        _need(not row.common_support or (row.features is not None and row.candidate is not None),
              "observed_support_requires_features_and_parent")


def snapshot_opportunity(row: snapshot.SnapshotCoordinate) -> PolicyOpportunity:
    """Project the one already authenticated common record without reselecting."""
    _need(type(row) is snapshot.SnapshotCoordinate, "typed_common_snapshot_coordinate_required")
    context = row.opening_context.context
    event_types = (("NO_EVENT",) if context.state == "KNOWN_NO_WHITELISTED_LIST_EVENT"
                   else context.original_types if context.state == "ELIGIBLE_ORIGINAL" else ())
    raw = {"accept": "accepted", "decline": "declined", "unknown": "unknown"}
    exact = {} if row.window_inputs is None else dict(row.window_inputs.static_exact)
    # CDF ranks of nonnegative variance and its square root are identical.
    variance = None if row.window_inputs is None else row.window_inputs.past20_return_variance
    return PolicyOpportunity(row.coordinate.scenario, row.coordinate.symbol, row.coordinate.session,
        row.parent_scan.status, row.actions["rule"].at if row.parent_scan.status == "parent_identity_unknown" else None,
        row.candidate, row.common_support, row.features, event_types,
        raw[row.actions["rule"].status], raw[row.actions["without_filing"].status], row.label,
        variance, exact.get("past20_sameclock5_share_volume_median"))


def training_examples(rows: Sequence[PolicyOpportunity], *,
                      training_sessions: frozenset[str] | None = None) -> tuple[models.EDTrainingExample, ...]:
    """Create sidecars; inference projection deliberately has no outcome fields."""
    result = []
    for row in rows:
        if row.candidate is None:
            continue
        label = row.label if training_sessions is None or row.session in training_sessions else None
        result.append(models.EDTrainingExample(
            row.candidate.parent_id, row.symbol, row.scenario, row.session,
            row.candidate.decision_at, row.features, row.common_support, row.event_types,
            "unknown" if label is None else label.status,
            None if label is None else label.net33,
            None if label is None else (row.candidate.entry_at
                if label.status == "known_declined_entry" else label.exit_at),
            None if label is None else label.mature_at,
            "label_not_supplied" if label is None else label.reason))
    return tuple(result)


def verify_fold_membership(calendar: MLCalendar, view: models.EDFoldView,
                           examples: Sequence[models.EDTrainingExample]) -> dict:
    """Reconstruct membership/purge without calling the producer fold builder.

    Only training-side label properties are touched. Common snapshot science is
    verified separately; this does not certify upstream source provenance.
    """
    fold = calendar.folds[view.fold.index - 1]
    _need(view.fold == fold and view.source_reference_sessions == calendar.source_reference_sessions,
          "fold_calendar_changed")
    training_dates, test_dates = set(fold.training_sessions), set(fold.test_sessions)
    ordered = sorted((row for row in examples if row.scenario == view.scenario),
                     key=lambda row: (row.session, row.symbol, row.decision_at, row.parent_id))
    expected_train, expected_test = [], []
    dimensions = len(view.feature_names)
    for row in ordered:
        if row.session in test_dates:
            expected_test.append((row.parent_id, row.symbol, row.session, row.decision_at,
                None if not row.common_support or row.features is None else row.features[:dimensions],
                row.event_types))
            continue
        if row.session not in training_dates or not row.common_support or row.features is None:
            continue
        if row.label_status == "unknown":
            continue
        _need(row.label_status in ("known_traded", "known_declined_entry")
              and type(row.primary_label) is Fraction and type(row.label_end_at) is datetime
              and type(row.mature_at) is datetime, "known_training_label_required")
        if row.label_end_at >= fold.first_test_opening or row.mature_at >= fold.first_test_opening:
            continue
        expected_train.append((row.parent_id, row.symbol, row.session, row.decision_at,
            row.features[:dimensions], row.event_types, row.primary_label, row.label_status,
            row.label_end_at, row.mature_at))
    actual_train = [(r.parent_id, r.symbol, r.session, r.decision_at, r.features,
                     r.event_types, r.y, r.label_status, r.label_end_at, r.mature_at)
                    for r in view.training]
    actual_test = [(r.parent_id, r.symbol, r.session, r.decision_at, r.features, r.event_types)
                   for r in view.testing]
    _need(actual_train == expected_train and actual_test == expected_test,
          "independent_fold_membership_mismatch")
    filled = [r for r in expected_train if r[7] == "known_traded"]
    attainable = len(filled) >= 100 and len({r[2] for r in filled}) >= 30
    _need((view.status == "ready") == attainable, "independent_training_minimum_mismatch")
    return {"verified": True, "fold": fold.index, "scenario": view.scenario,
            "training_rows": len(expected_train), "testing_rows": len(expected_test),
            "known_traded": len(filled), "training_traded_sessions": len({r[2] for r in filled}),
            "test_labels_read": False, "model_fitted": False}


def _decision(row: PolicyOpportunity, status: str, reason: str) -> PolicyDecision:
    parent = row.candidate
    if row.parent_status == "known_no_parent":
        return PolicyDecision(row.scenario, row.symbol, row.session, "no_parent",
                              "known_no_broad_parent", None, None, row.event_types)
    if parent is None:
        status, reason, at = "unknown", "broad_parent_identity_unknown", row.unknown_at
    else:
        at = parent.decision_at
    _need(at is not None, "decision_clock_required")
    action = replay.EDAction(at, status, reason, parent, row.symbol,
                             None if parent is None else parent.parent_id)
    return PolicyDecision(row.scenario, row.symbol, row.session, status, reason,
                          action, action.parent_id, row.event_types)


def plan_rule(calendar: MLCalendar, rows: Sequence[PolicyOpportunity], *,
              scenario: str, without_filing: bool = False) -> PolicyPlan:
    _validate(calendar, rows)
    _need(scenario in models.SCENARIOS and type(without_filing) is bool, "invalid_rule_scope")
    decisions = []
    for row in rows:
        if row.scenario != scenario or row.session not in calendar.evaluation_reference_sessions:
            continue
        raw = row.control_status if without_filing else row.rule_status
        if row.parent_status == "parent" and not row.common_support:
            raw = "unknown"
        status = {"accepted": "accept", "declined": "decline", "unknown": "unknown"}[raw]
        decisions.append(_decision(row, status, "fixed_rule_" + raw))
    return PolicyPlan(CONTROL if without_filing else RULE, scenario,
                      calendar.evaluation_reference_sessions, tuple(decisions), ())


def plan_lightgbm(calendar: MLCalendar, rows: Sequence[PolicyOpportunity], *, scenario: str,
                   binding: models.EDModelBinding, without_filing: bool = False,
                   checkpoint: Callable[[str], object]) -> PolicyPlan:
    """Exactly five planned fold slots; unavailable slots never get a fake fit."""
    _validate(calendar, rows)
    _need(scenario in models.SCENARIOS and binding.scenario == scenario
          and type(without_filing) is bool, "invalid_learner_scope")
    fits, predictions, unavailable = [], {}, []
    for fold in calendar.folds:
        checkpoint("ED-before-fold-" + str(fold.index))
        examples = training_examples(rows, training_sessions=frozenset(fold.training_sessions))
        view = models.prepare_ed_fold(calendar, fold.index, scenario, examples,
                                      without_filing=without_filing)
        membership = verify_fold_membership(calendar, view, examples)
        bound = replace(binding, fold_index=fold.index,
                        context_mode="without_filing" if without_filing else "full")
        fit = models.fit_ed_lightgbm(view, bound)
        batch = models.predict_ed_lightgbm(fit, view)
        verification = models.verify_ed_model(fit, view, batch, binding=bound)
        fits.append((view, fit, batch, {"membership": membership, "numerics": verification}))
        unavailable.extend(batch.unavailable_sessions)
        for prediction in batch.rows:
            _need(prediction.parent_id not in predictions, "duplicate_out_of_fold_prediction")
            predictions[prediction.parent_id] = prediction
        checkpoint("ED-after-fold-" + str(fold.index))
    decisions = []
    for row in rows:
        if row.scenario != scenario or row.session not in calendar.evaluation_reference_sessions:
            continue
        if row.candidate is None:
            status, reason = "unknown", "common_parent_unknown"
        else:
            prediction = predictions[row.candidate.parent_id]
            status = "unknown" if prediction.accepted is None else (
                "accept" if prediction.accepted else "decline")
            reason = prediction.reason or "fixed_strict_positive_prediction"
        decisions.append(_decision(row, status, reason))
    return PolicyPlan(ABLATION if without_filing else LEARNER, scenario,
        calendar.evaluation_reference_sessions, tuple(decisions), tuple(unavailable), tuple(fits))


def replay_policy_day(plan: PolicyPlan, session: replay.EDSession,
                      minutes: Mapping[replay.PriceKey, replay.Minute], *,
                      cost_bps: int, state: PolicyCarry | None = None,
                      observed_support_only: bool = False,
                      fresh_day: bool = False,
                      compress_idle_minutes: bool = True) -> replay.EDAccountResult:
    """One dated account step; callers carry continuous state without resets.

    The restricted support domain omits unknown predecision actions, never an
    unknown future outcome/held mark. Its result cannot certify all targets.
    """
    _need(type(observed_support_only) is bool and type(fresh_day) is bool,
          "explicit_account_domain_required")
    domain = "observed_predecision_support_conditional" if observed_support_only else "full_original_population"
    if state is not None and not fresh_day:
        _need(type(state) is PolicyCarry and (state.arm, state.scenario, state.domain) ==
              (plan.arm, plan.scenario, domain), "policy_account_domain_changed")
    day = session.opening.date().isoformat()
    _need(day in plan.evaluation_sessions, "training_day_is_not_account_evaluation")
    if day in plan.unavailable_sessions:
        session = replace(session, policy_state="unavailable", reason="fold_training_unavailable")
    actions = tuple(d.action for d in plan.decisions if d.session == day and d.action is not None
                    and (not observed_support_only or d.status != "unknown"))
    if fresh_day:
        _need(state is None, "fresh_day_must_not_reuse_continuous_state")
        result = replay.replay_ed_daily_counterfactuals(actions, minutes, (session,),
            arm=plan.arm, cost_bps=cost_bps, compress_idle_minutes=compress_idle_minutes)[0]
    else:
        result = replay.replay_ed_account(actions, minutes, (session,), arm=plan.arm,
            cost_bps=cost_bps, state=None if state is None else state.state,
            compress_idle_minutes=compress_idle_minutes)
    scope = "fresh_day_counterfactual_never_continuous_NAV" if fresh_day else "continuous_sticky_unknown"
    return replace(result, scenario=domain + "/" + scope)


def support_plan(calendar: MLCalendar, rows: Sequence[PolicyOpportunity]) -> dict:
    """No means: retain every calendar date including zero-parent dates."""
    _validate(calendar, rows)
    result = {}
    for scenario in models.SCENARIOS:
        selected = [r for r in rows if r.scenario == scenario
                    and r.session in calendar.evaluation_reference_sessions]
        counts = Counter(r.parent_status for r in selected)
        counts["original_coordinates"] = len(selected)
        counts["common_support_parents"] = sum(r.common_support for r in selected)
        counts["known_traded_labels"] = sum(r.label is not None and r.label.known_filled_unit
                                            for r in selected)
        counts["event_common_parents"] = sum(r.common_support and "NO_EVENT" not in r.event_types
                                             for r in selected)
        result[scenario] = {"counts": dict(counts), "evaluation_sessions": 157,
            "reference_weeks": calendar.evaluation_reference_weeks,
            "no_identified_parent_dates": tuple(day for day in calendar.evaluation_reference_sessions
                if not any(r.session == day and r.parent_status == "parent" for r in selected)),
            "known_zero_parent_dates": tuple(day for day in calendar.evaluation_reference_sessions
                if any(r.session == day for r in selected)
                and all(r.parent_status == "known_no_parent" for r in selected if r.session == day)),
            "parent_unknown_dates": tuple(day for day in calendar.evaluation_reference_sessions
                if any(r.session == day and r.parent_status == "parent_identity_unknown" for r in selected)),
            "performance_means_computed": False, "exploratory_only": True}
    return result


@dataclass(frozen=True)
class MatchedPair:
    scenario: str
    fold: int
    session: str
    treated_parent_id: str
    control_parent_id: str | None
    reason: str
    distance: Fraction | None = None
    decision_minute_difference: int | None = None
    volatility_rank_difference: Fraction | None = None
    liquidity_rank_difference: Fraction | None = None
    gross_difference: Fraction | None = None
    net33_difference: Fraction | None = None
    net43_difference: Fraction | None = None


def match_non_event(calendar: MLCalendar, rows: Sequence[PolicyOpportunity], *,
                    scenario: str) -> tuple[MatchedPair, ...]:
    """Training-CDF, same-session one-to-one mechanism diagnostic.

    Freeze pairs before reading either outcome. A missing outcome never causes
    replacement, widening or a search on another date. Variance CDF equals the
    nonnegative-volatility CDF exactly, avoiding a needless sqrt rounding.
    """
    _validate(calendar, rows)
    _need(scenario in models.SCENARIOS, "invalid_matching_scenario")
    scoped = [row for row in rows if row.scenario == scenario and row.common_support]
    _need(all(type(r.volatility) is Fraction and r.volatility >= 0
              and type(r.liquidity) is Fraction and r.liquidity > 0 for r in scoped),
          "observed_matching_inputs_required")
    result = []
    for fold in calendar.folds:
        training = [row for row in scoped if row.session in fold.training_sessions]
        vol = sorted(row.volatility for row in training)
        liq = sorted(row.liquidity for row in training)
        evaluation = [row for row in scoped if row.session in fold.test_sessions]
        treated = sorted((row for row in evaluation if row.rule_status == "accepted"
                           and "NO_EVENT" not in row.event_types),
                          key=lambda row: (row.session, row.candidate.decision_at,
                                           row.symbol, row.candidate.parent_id))
        controls = [row for row in evaluation if row.event_types == ("NO_EVENT",)
                    and row.control_status == "accepted"]
        used = set()
        def ranks(row):
            return (Fraction(bisect_right(vol, row.volatility), len(vol)),
                    Fraction(bisect_right(liq, row.liquidity), len(liq)))
        for row in treated:
            parent = row.candidate
            if not training:
                result.append(MatchedPair(scenario, fold.index, row.session, parent.parent_id,
                                          None, "training_CDF_unavailable"))
                continue
            v, l = ranks(row)
            choices = []
            for control in controls:
                c = control.candidate
                if control.session != row.session or c.parent_id in used:
                    continue
                minutes = int((c.decision_at - parent.decision_at).total_seconds() // 60)
                cv, cl = ranks(control)
                dv, dl = cv - v, cl - l
                if abs(minutes) <= 5 and abs(dv) <= Fraction(1, 5) and abs(dl) <= Fraction(1, 5):
                    distance = Fraction(abs(minutes), 5) + 5 * abs(dv) + 5 * abs(dl)
                    choices.append((distance, c.decision_at, control.symbol, c.parent_id,
                                    minutes, dv, dl, control))
            if not choices:
                result.append(MatchedPair(scenario, fold.index, row.session, parent.parent_id,
                                          None, "no_unused_same_session_control_within_fixed_calipers"))
                continue
            distance, _, _, control_id, minutes, dv, dl, control = min(choices, key=lambda item: item[:4])
            used.add(control_id)  # Pair is fixed before any label is inspected.
            a, b = row.label, control.label
            known = a is not None and b is not None and a.status != "unknown" and b.status != "unknown"
            result.append(MatchedPair(scenario, fold.index, row.session, parent.parent_id, control_id,
                "known_pair" if known else "matched_pair_outcome_unknown", distance, minutes, dv, dl,
                None if not known else a.gross - b.gross,
                None if not known else a.net33 - b.net33,
                None if not known else a.net43 - b.net43))
    return tuple(result)


def unit_summary(calendar: MLCalendar, rows: Sequence[PolicyOpportunity], plan: PolicyPlan) -> dict:
    """Descriptive accepted-opportunity results, never realized account PnL."""
    lookup = {(row.scenario, row.symbol, row.session): row for row in rows}
    accepted = [lookup[d.scenario, d.symbol, d.session] for d in plan.decisions if d.status == "accept"]
    weekly = {week: 0 for week in calendar.evaluation_reference_weeks}
    for row in accepted:
        parsed = datetime.fromisoformat(row.session).date()
        week = (parsed - timedelta(days=parsed.weekday())).isoformat()
        _need(week in weekly, "accepted_opportunity_week_outside_calendar")
        weekly[week] += 1
    groups = {}
    for name, selected in (("all", accepted),
            ("event", [row for row in accepted if "NO_EVENT" not in row.event_types]),
            ("known_no_event", [row for row in accepted if row.event_types == ("NO_EVENT",)])):
        known = [row for row in selected if row.label is not None and row.label.status != "unknown"]
        records = [{"session": row.session, "label": None if row.label is None or row.label.status == "unknown"
                    else row.label.net33} for row in selected]
        planning = dispersion(records, list(calendar.evaluation_reference_sessions))
        fields = {}
        for key in ("gross", "net33", "net43"):
            values = [getattr(row.label, key) for row in known]
            _need(all(type(value) is Fraction for value in values), "known_exact_unit_outcomes_required")
            fields[key] = {"mean": sum(values, Fraction()) / len(values) if values else None,
                           "positive_count": sum(value > 0 for value in values)}
        groups[name] = {"accepted_opportunities": len(selected), "known_outcomes": len(known),
            "unknown_outcomes": len(selected) - len(known),
            "known_traded": sum(row.label.known_filled_unit for row in known),
            "known_declined_entry": sum(row.label.status == "known_declined_entry" for row in known),
            "before_mean_sample_plan": planning, "outcomes": fields,
            "small_effect_or_edge_claim": False}
    return {"arm": plan.arm, "scenario": plan.scenario,
        "weekly_accepted_opportunities": weekly,
        "weekly_average": Fraction(len(accepted), len(weekly)),
        "weeks_at_least15_observed_opportunities": sum(value >= 15 for value in weekly.values()),
        "weekly15_is_execution_guarantee": False,
        "decision_counts": dict(Counter(d.status for d in plan.decisions)),
        "groups": groups, "unavailable_sessions": plan.unavailable_sessions,
        "denominator": "original157 evaluation dates/weeks; accepted opportunities, not fills",
        "exploratory_only": True, "account_PnL": False,
        "S1_S2_independent": False, "actual_fills_certified": False}
