"""Invented-only orchestration: fixed folds, no fake policies, paired domains."""
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_ed_comparison as ed
from research import intraday_ed_models as models
from research import intraday_ed_replay as replay
from research.intraday_campaign_ml_labels import fixed_calendar


def calendar():
    days, at = [], date(2025, 1, 2)
    while len(days) < 257:
        if at.weekday() < 5:
            days.append(at.isoformat())
        at += timedelta(days=1)
    return fixed_calendar(days)


def opportunity(index=100, symbol="KRX:000001", *, known=True, parent=True):
    day = calendar().source_reference_sessions[index]
    opening = datetime.fromisoformat(day + "T09:00:00+09:00")
    candidate = replay.EDCandidate(symbol + "-" + day, symbol, opening,
        opening + timedelta(minutes=390), opening + timedelta(minutes=32),
        opening + timedelta(minutes=35), opening + timedelta(minutes=95), Fraction(90)) if parent else None
    features = (0.25,) * 191 + (1.0, 0.0, 0.0, 0.0) + (1.0,) + (0.0,) * 10
    label = None if not parent else replay.EDUnitLabel(candidate, "known_traded", "time_cap",
        Fraction(100), Fraction(101), candidate.cap_exit_at, candidate.cap_exit_at + timedelta(minutes=3),
        Fraction(1, 100), Fraction(67, 10000), Fraction(57, 10000))
    return ed.PolicyOpportunity("S1", symbol, day,
        "parent" if parent else "known_no_parent", None, candidate, known and parent,
        features if known and parent else None, ("NO_EVENT",) if known else (),
        "declined" if known else "unknown", "accepted" if known else "unknown", label)


def prices(row):
    c = row.candidate
    return {(c.symbol, c.entry_at + timedelta(minutes=i)): replay.Minute(
        c.symbol, c.entry_at + timedelta(minutes=i), c.entry_at + timedelta(minutes=i + 3),
        tuple(Decimal(v) for v in (("101",) * 4 if i == 60 else ("100",) * 4)), Decimal(100))
        for i in range(61)}


def test_rules_use_same_support_and_no_outcome_to_choose():
    row = opportunity()
    no_labels = replace(row, label=object())
    rule = ed.plan_rule(calendar(), (no_labels,), scenario="S1")
    control = ed.plan_rule(calendar(), (no_labels,), scenario="S1", without_filing=True)
    assert rule.decisions[0].status == "decline"
    assert control.decisions[0].status == "accept"
    assert rule.evaluation_sessions == control.evaluation_sessions
    unknown = replace(row, common_support=False, features=None)
    assert ed.plan_rule(calendar(), (unknown,), scenario="S1").decisions[0].status == "unknown"
    assert ed.plan_rule(calendar(), (unknown,), scenario="S1", without_filing=True).decisions[0].status == "unknown"


def test_parent_gap_has_causal_unknown_but_known_no_parent_has_no_action():
    row = opportunity(parent=False)
    plan = ed.plan_rule(calendar(), (row,), scenario="S1")
    assert plan.decisions[0].action is None
    at = datetime.fromisoformat(row.session + "T09:32:00+09:00")
    gap = replace(row, parent_status="parent_identity_unknown", unknown_at=at)
    result = ed.plan_rule(calendar(), (gap,), scenario="S1")
    assert result.decisions[0].action.at == at and result.decisions[0].status == "unknown"
    with pytest.raises(ed.ComparisonRefusal, match="unknown_parent_causal_clock_required"):
        ed.plan_rule(calendar(), (replace(gap, unknown_at=None),), scenario="S1")


def test_undersupported_model_never_becomes_zero_return_cash_policy(monkeypatch):
    row = replace(opportunity(256), label=object())
    binding = models.EDModelBinding("a" * 64, "b" * 64, "c" * 64, "d" * 64, 1, "S1")
    # The unavailable path must not import a native backend or make a fit.
    def forbidden(*args, **kwargs):
        raise AssertionError("native backend called for unavailable training")
    monkeypatch.setattr(models.numeric, "_libraries", forbidden)
    plan = ed.plan_lightgbm(calendar(), (row,), scenario="S1", binding=binding,
                            checkpoint=lambda _: None)
    assert len(plan.fits) == 5
    assert plan.unavailable_sessions == calendar().evaluation_reference_sessions
    assert all(fit.artifact is None for _, fit, _, _ in plan.fits)
    session = replay.EDSession(row.candidate.opening, row.candidate.closing)
    result = ed.replay_policy_day(plan, session, {}, cost_bps=33)
    assert result.state.first_unknown_at == session.opening
    assert result.snapshots[-1].full_nav is None


def test_fold_checker_never_reads_test_outcomes_and_detects_membership_mutation():
    rows = tuple(opportunity(i, f"KRX:{j:06d}") for i in range(40) for j in range(3))
    rows += (opportunity(100),)
    examples = ed.training_examples(rows)
    changed = examples[:-1] + (replace(examples[-1], label_status=object(), primary_label=object(),
                                      label_end_at=object(), mature_at=object()),)
    view = models.prepare_ed_fold(calendar(), 1, "S1", changed)
    assert ed.verify_fold_membership(calendar(), view, changed)["training_rows"] == 120
    with pytest.raises(ed.ComparisonRefusal, match="independent_fold_membership_mismatch"):
        ed.verify_fold_membership(calendar(), replace(view, testing=()), changed)
    with pytest.raises(ed.ComparisonRefusal, match="independent_fold_membership_mismatch"):
        ed.verify_fold_membership(calendar(), replace(view, training=view.training[1:]), changed)


def test_full_and_restricted_accounts_never_hide_future_missing_label():
    row = opportunity()
    missing = replace(opportunity(symbol="KRX:000002", parent=False),
        parent_status="parent_identity_unknown", unknown_at=row.candidate.opening)
    plan = ed.plan_rule(calendar(), (row, missing), scenario="S1", without_filing=True)
    session = replay.EDSession(row.candidate.opening, row.candidate.closing)
    full = ed.replay_policy_day(plan, session, prices(row), cost_bps=33)
    assert full.snapshots[-1].full_nav is None
    conditional = ed.replay_policy_day(plan, session, prices(row), cost_bps=33,
                                      observed_support_only=True)
    assert conditional.snapshots[-1].full_nav is not None
    assert "conditional" in conditional.scenario
    broken = prices(row)
    del broken[row.symbol, row.candidate.cap_exit_at]
    unknown = ed.replay_policy_day(plan, session, broken, cost_bps=33, observed_support_only=True)
    assert unknown.snapshots[-1].full_nav is None
    assert unknown.state.book.lots and unknown.state.first_unknown_at is not None


def test_daily_diagnostic_rejects_state_stitch_and_reference_calendar_stays_full():
    row = opportunity()
    plan = ed.plan_rule(calendar(), (row,), scenario="S1", without_filing=True)
    session = replay.EDSession(row.candidate.opening, row.candidate.closing)
    result = ed.replay_policy_day(plan, session, prices(row), cost_bps=43, fresh_day=True)
    assert "never_continuous_NAV" in result.scenario
    with pytest.raises(ed.ComparisonRefusal, match="fresh_day_must_not_reuse_continuous_state"):
        ed.replay_policy_day(plan, session, prices(row), cost_bps=43, fresh_day=True, state=result.state)
    support = ed.support_plan(calendar(), (row,))
    assert support["S1"]["evaluation_sessions"] == 157
    assert len(support["S1"]["no_identified_parent_dates"]) == 156
    assert support["S1"]["known_zero_parent_dates"] == ()
    assert support["S2"]["counts"]["original_coordinates"] == 0
    assert support["S1"]["performance_means_computed"] is False


def test_duplicate_coordinate_or_changed_parent_identity_refused():
    row = opportunity()
    with pytest.raises(ed.ComparisonRefusal, match="duplicate_original_coordinate"):
        ed.plan_rule(calendar(), (row, row), scenario="S1")
    with pytest.raises(ed.ComparisonRefusal, match="candidate_coordinate_mismatch"):
        ed.plan_rule(calendar(), (replace(row, symbol="other"),), scenario="S1")


def test_known_nofill_maturity_is_entry_and_never_inflates_traded_minimum():
    row = opportunity(10)
    declined = replace(row.label, status="known_declined_entry", reason="invalidated_entry",
        exit_at=None, mature_at=row.candidate.entry_at + timedelta(minutes=3),
        gross=Fraction(), net33=Fraction(), net43=Fraction())
    example = ed.training_examples((replace(row, label=declined),))[0]
    assert example.label_end_at == row.candidate.entry_at
    view = models.prepare_ed_fold(calendar(), 1, "S1", (example,))
    assert view.counts["known_declined_entry"] == 1 and view.counts["known_traded"] == 0
    assert view.status == "unavailable"
    assert ed.verify_fold_membership(calendar(), view, (example,))["known_traded"] == 0


def test_support_domain_and_timing_cannot_be_relabelled_on_resumption():
    first, second = opportunity(), opportunity(101)
    plan = ed.plan_rule(calendar(), (first, second), scenario="S1", without_filing=True)
    session = replay.EDSession(first.candidate.opening, first.candidate.closing)
    result = ed.replay_policy_day(plan, session, prices(first), cost_bps=33, observed_support_only=True)
    carry = ed.policy_carry(plan, result)
    next_session = replay.EDSession(second.candidate.opening, second.candidate.closing)
    resumed = ed.replay_policy_day(plan, next_session, prices(second), cost_bps=33,
                                  state=carry, observed_support_only=True)
    assert resumed.state.book.cash > result.state.book.cash
    with pytest.raises(ed.ComparisonRefusal, match="policy_account_domain_changed"):
        ed.replay_policy_day(plan, next_session, prices(second), cost_bps=33, state=carry)
    with pytest.raises(ed.ComparisonRefusal, match="policy_account_domain_changed"):
        ed.replay_policy_day(replace(plan, scenario="S2"), next_session, prices(second),
                            cost_bps=33, state=carry, observed_support_only=True)
    with pytest.raises(ed.ComparisonRefusal, match="policy_account_domain_changed"):
        ed.replay_policy_day(plan, next_session, prices(second), cost_bps=33, state=result.state)


def test_matched_pair_is_frozen_before_outcomes_and_weekly_denominator_is_full():
    rows = tuple(replace(opportunity(i), volatility=Fraction(1), liquidity=Fraction(10))
                 for i in range(40))
    treated = replace(opportunity(), event_types=("ORIGINAL_ANNUAL",), rule_status="accepted",
                      volatility=Fraction(1), liquidity=Fraction(10))
    control = replace(opportunity(symbol="KRX:000002"), volatility=Fraction(1), liquidity=Fraction(10))
    all_rows = (*rows, treated, control)
    pairs = ed.match_non_event(calendar(), all_rows, scenario="S1")
    assert len(pairs) == 1 and pairs[0].control_parent_id == control.candidate.parent_id
    assert pairs[0].distance == 0 and pairs[0].gross_difference == 0
    missing = replace(control, label=replace(control.label, status="unknown", gross=None,
                                            net33=None, net43=None))
    changed = ed.match_non_event(calendar(), (*rows, treated, missing), scenario="S1")
    assert changed[0].control_parent_id == pairs[0].control_parent_id
    assert changed[0].reason == "matched_pair_outcome_unknown" and changed[0].gross_difference is None
    plan = ed.plan_rule(calendar(), all_rows, scenario="S1")
    summary = ed.unit_summary(calendar(), all_rows, plan)
    assert len(summary["weekly_accepted_opportunities"]) == len(calendar().evaluation_reference_weeks)
    assert sum(summary["weekly_accepted_opportunities"].values()) == 1
    assert summary["groups"]["event"]["outcomes"]["net33"]["mean"] == treated.label.net33
