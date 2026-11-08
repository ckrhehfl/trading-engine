"""Invented EI same-entry fixed/chart/context management and isolated reentry."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_context_management as management
from research.intraday_context_entry_roster import Opportunity
from research.intraday_context_held import ContextHeldRoster
from research.intraday_ed_inputs import MINUTE, InputRefusal
from test_intraday_context_held import SOURCE, fixture, fold, historical, roster
from test_intraday_context_reentry import RequestedOnly, prepared_for, reset_fixture


F = Fraction


def case_fixture():
    query, session, _, _ = fixture()
    prepared = prepared_for(query, session)
    opportunity = Opportunity(query.entry, "opportunity", (), None)
    prior, source, _, _ = fixture(day=-1)
    pool = roster(historical(prior))
    calls = []

    def read(day, symbol, digest):
        assert (day, symbol, digest) == (*prior.coordinate[:2], SOURCE)
        calls.append((day, symbol))
        return source

    return opportunity, prepared, session, pool, read, calls


def test_three_branches_share_one_initial_fill_and_fresh_held_receipts():
    opportunity, prepared, session, pool, read, _ = case_fixture()
    opening = session.opening
    opens = RequestedOnly({opening + 33 * MINUTE: F(114), opening + 62 * MINUTE: F(114)})
    result = management.replay_context_management(opportunity, prepared, session, opens, pool, read_prior_session=read)
    assert management.SCHEMA == "ei-context-matched-management-draft-v1"
    assert result.opportunity is opportunity and result.prepared is prepared
    assert result.status == "confirmed" and result.initial_fill.filled
    assert result.fixed.episode == result.chart.episode == result.chart_context.episode
    assert result.fixed.cap == result.chart.cap == result.chart_context.cap
    assert all(tuple(event for event in branch.events if event.fill_status is not None)
               == tuple(event for event in result.fixed.events if event.fill_status is not None)
               for branch in (result.chart, result.chart_context))
    assert opens.calls.count(opening + 33 * MINUTE) == 1
    assert len(result.decisions) == 10 and {item.mode for item in result.decisions} == {"chart", "chart_context"}
    for mode in ("chart", "chart_context"):
        rows = [item for item in result.decisions if item.mode == mode]
        assert len({item.question.question_id for item in rows}) == 5
        assert [item.question.held.minutes_remaining for item in rows] == [25, 20, 15, 10, 5]
        assert all(item.assessment.independently_verified for item in rows)
        assert all(item.question.entry == opportunity.feature for item in rows)
    assert result.reentry is None
    assert result.holding_forecast == "mature_prior_path_analogue_not_neural_continuation"
    assert result.claim == "nominal_event_comparison_not_concurrent_account_return"
    assert result.fixed.episode.book.realized_net == -result.initial_fill.assumed_cost


@pytest.mark.parametrize("status", ["feature_unsupported", "question_unsupported", "overlap_suppressed"])
def test_original_nonopportunities_remain_without_reads_or_branches(status):
    opportunity, _, session, pool, read, calls = case_fixture()
    original = replace(opportunity, status=status, reasons=("invented original reason",))
    opens = RequestedOnly({})
    result = management.replay_context_management(original, None, session, opens, pool, read_prior_session=read)
    assert result.opportunity is original and result.status == status
    assert result.initial is result.fixed is result.chart is result.chart_context is None
    assert calls == opens.calls == []


def test_no_preparation_skip_and_missing_session_preserve_one_case_without_open_lookup():
    opportunity, prepared, session, pool, read, _ = case_fixture()
    opens = RequestedOnly({})
    absent = management.replay_context_management(opportunity, None, session, opens, pool, read_prior_session=read)
    assert absent.status == "unresolved" and absent.reason == "entry_preparation_missing"
    from research.intraday_context_entry import prepare_context_entry
    skip = prepare_context_entry(prepared.features, prepared.book_before, mode="baseline",
        visible_minute=prepared.visible_minute, risk_budget=prepared.risk_budget, cost_bps=prepared.cost_bps,
        causal_source_sha256=prepared.causal_source_sha256, execution_policy_sha256=prepared.execution_policy_sha256,
        rule_enabled=False)
    skipped = management.replay_context_management(opportunity, skip, session, opens, pool, read_prior_session=read)
    missing = management.replay_context_management(opportunity, prepared, None, opens, pool, read_prior_session=read)
    assert skipped.status == "no_entry" and missing.reason == "evaluation_session_missing"
    assert opens.calls == []


@pytest.mark.parametrize("price,status", [(F(116), "declined"), (None, "unresolved")])
def test_first_no_fill_or_unknown_has_identical_absent_holding_branches(price, status):
    opportunity, prepared, session, pool, read, calls = case_fixture()
    opens = RequestedOnly({session.opening + 33 * MINUTE: price})
    result = management.replay_context_management(opportunity, prepared, session, opens, pool,
                                                   read_prior_session=read, include_reentry=True)
    assert result.status == status and result.fixed is result.chart is result.chart_context is result.reentry is None
    assert result.initial_fill.outcome == ("indeterminate" if price is None else "declined")
    assert calls == [] and len(opens.calls) == 1


def test_no_mature_analogue_values_keeps_structural_time_rule_and_support_receipts():
    opportunity, prepared, session, _, read, calls = case_fixture()
    pool = ContextHeldRoster((), fold=fold(-1))
    opens = RequestedOnly({session.opening + 33 * MINUTE: F(114), session.opening + 62 * MINUTE: F(114)})
    result = management.replay_context_management(opportunity, prepared, session, opens, pool, read_prior_session=read)
    assert result.fixed.episode == result.chart.episode == result.chart_context.episode
    assert all(item.assessment.reason == "no_eligible_paths" and item.assessment.unassessed_mass == 1
               for item in result.decisions)
    assert calls == []


def test_reentry_parent_is_fixed_context_branch_even_when_fixed_holding_earns_more(monkeypatch):
    prepared, _, session = reset_fixture()
    opportunity = Opportunity(prepared.features, "opportunity", (), None)
    prior, source, _, _ = fixture(day=-1)
    rows = {at: replace(row, ohlc=(Decimal(110),) * 4) if at >= source.opening + 39 * MINUTE else row
            for at, row in source.minutes.items()}
    source = replace(source, minutes=rows)
    pool = roster(historical(prior))
    opening = session.opening
    opens = RequestedOnly({opening + n * MINUTE: F(price) for n, price in ((33, 114), (38, 114), (53, 118), (62, 118))})
    seen = []
    original = management.replay_context_reentry

    def capture(preparation, parent, *args):
        seen.append(parent)
        return original(preparation, parent, *args)

    monkeypatch.setattr(management, "replay_context_reentry", capture)
    result = management.replay_context_management(opportunity, prepared, session, opens, pool,
        read_prior_session=lambda day, symbol, digest: source, include_reentry=True)
    assert result.chart_context.episode.closed_at == opening + 38 * MINUTE
    assert result.fixed.episode.book.cash > result.chart_context.episode.book.cash
    assert seen == [result.chart_context]
    assert result.reentry.parent_arm == "chart_context" and result.reentry.episode.attempts == 2
    assert result.reentry.baseline_cash == result.chart_context.episode.book.cash


def test_unresolved_current_holding_keeps_case_and_cannot_start_reentry():
    opportunity, prepared, session, pool, read, _ = case_fixture()
    rows = dict(session.minutes)
    del rows[session.opening + 45 * MINUTE]
    session = replace(session, minutes=rows)
    opens = RequestedOnly({session.opening + 33 * MINUTE: F(114)})
    result = management.replay_context_management(opportunity, prepared, session, opens, pool,
                                                   read_prior_session=read, include_reentry=True)
    assert result.status == "confirmed"  # The original first fill remains known.
    assert result.fixed.status == result.chart.status == result.chart_context.status == "unresolved"
    assert result.reentry.status == "unresolved" and result.reentry.net_increment is None


def test_initial_preparation_revalidation_occurs_before_any_modeled_open(monkeypatch):
    opportunity, prepared, session, pool, read, _ = case_fixture()
    opens = RequestedOnly({session.opening + 33 * MINUTE: None})
    original = management.prepare_context_entry

    def observe(*args, **kwargs):
        assert opens.calls == []
        assert "opens" not in kwargs and "next_open" not in kwargs
        return original(*args, **kwargs)

    monkeypatch.setattr(management, "prepare_context_entry", observe)
    management.replay_context_management(opportunity, prepared, session, opens, pool, read_prior_session=read)
    assert opens.calls == [session.opening + 33 * MINUTE]


def test_changed_opportunity_preparation_or_current_session_is_refused():
    opportunity, prepared, session, pool, read, _ = case_fixture()
    with pytest.raises(InputRefusal, match="preparation_changed"):
        management.replay_context_management(opportunity, replace(prepared, feature_sha256="f" * 64), session,
                                               RequestedOnly({}), pool, read_prior_session=read)
    with pytest.raises(InputRefusal, match="original_session"):
        management.replay_context_management(opportunity, prepared, replace(session, symbol="other"),
                                               RequestedOnly({}), pool, read_prior_session=read)


def test_selected_initial_forecast_is_used_only_for_the_first_entry(monkeypatch):
    from research.intraday_context_entry import prepare_context_entry
    from test_intraday_context_entry import assessment
    opportunity, baseline, session, pool, read, _ = case_fixture()
    value = assessment(opportunity.feature)
    selected = prepare_context_entry(opportunity.feature, baseline.book_before, mode="selected",
        visible_minute=baseline.visible_minute, risk_budget=baseline.risk_budget, cost_bps=33,
        causal_source_sha256=value.query.causal_source_sha256, execution_policy_sha256=value.query.execution_policy_sha256,
        forecast_source_sha256=value.query.forecast_source_sha256, entry_value=value)
    seen = []
    original = management.project_context_held

    def observe(entry_features, current, episode, source, **kwargs):
        assert entry_features is opportunity.feature
        assert current.available_at > value.query.forecast_at
        result = original(entry_features, current, episode, source, **kwargs)
        seen.append(result.question_id)
        return result

    monkeypatch.setattr(management, "project_context_held", observe)
    opens = RequestedOnly({session.opening + 33 * MINUTE: F(114), session.opening + 62 * MINUTE: F(114)})
    result = management.replay_context_management(opportunity, selected, session, opens, pool, read_prior_session=read)
    assert result.prepared.entry_value is value and result.initial_fill.filled and len(seen) == 10


def test_real_matched_path_values_reduce_once_then_use_remaining_inventory_without_extra_cost():
    opportunity, prepared, session, _, _, _ = case_fixture()
    records, sources = [], {}
    for symbol, early, terminal in (("A", 113, 116), ("B", 116, 113)):
        prior, source, _, _ = fixture(day=-1, symbol=symbol)
        rows = dict(source.minutes)
        for offset, price in ((38, early), (62, terminal)):
            at = source.opening + offset * MINUTE
            rows[at] = replace(rows[at], ohlc=(Decimal(price),) * 4)
        records.append(historical(prior))
        sources[prior.coordinate[:2]] = replace(source, minutes=rows)
    opens = RequestedOnly({session.opening + n * MINUTE: F(114) for n in (33, 38, 62)})
    result = management.replay_context_management(opportunity, prepared, session, opens, roster(*records),
        read_prior_session=lambda day, symbol, digest: sources[day, symbol])
    for branch in (result.chart, result.chart_context):
        reductions = [event for event in branch.events if event.action == "reduce_half" and event.fill_status == "modeled"]
        assert len(reductions) == 1 and reductions[0].quantity == 7
        assert len(branch.episode.book.realized_exits) == 2
        assert sum(leg.allocated_entry_cost for leg in branch.episode.book.realized_exits) == result.initial_fill.assumed_cost
    assert all(item.question.held.half_reduced for item in result.decisions
               if item.question.held.decision_at > session.opening + 37 * MINUTE)
    assert all(item.assessment.independently_verified for item in result.decisions)


def test_original_entry_prefix_is_checked_before_structural_exit_can_bypass_held_callback():
    opportunity, prepared, session, pool, read, calls = case_fixture()
    rows = dict(session.minutes)
    at = session.opening + 29 * MINUTE
    rows[at] = replace(rows[at], ohlc=(*rows[at].ohlc[:3], Decimal("113.5")))
    at = session.opening + 33 * MINUTE
    rows[at] = replace(rows[at], ohlc=(Decimal(114), Decimal(115), Decimal(112), Decimal(112)))
    opens = RequestedOnly({session.opening + n * MINUTE: F(114) for n in (33, 37)})
    with pytest.raises(InputRefusal):
        management.replay_context_management(opportunity, prepared, replace(session, minutes=rows), opens, pool,
                                             read_prior_session=read)
    assert opens.calls == calls == []


@pytest.mark.parametrize("first_price", [F(114), None])
def test_each_modeled_open_clock_and_missingness_is_observed_once_for_all_branches(first_price):
    opportunity, prepared, session, pool, read, _ = case_fixture()
    opening = session.opening

    class ChangingOpen(RequestedOnly):
        def __getitem__(self, key):
            if key == opening + 62 * MINUTE:
                count = self.calls.count(key)
                self.calls.append(key)
                return first_price if count == 0 else F(114 + count)
            return super().__getitem__(key)

    opens = ChangingOpen({opening + 33 * MINUTE: F(114)})
    result = management.replay_context_management(opportunity, prepared, session, opens, pool, read_prior_session=read)
    assert opens.calls == [opening + 33 * MINUTE, opening + 62 * MINUTE]
    assert result.modeled_open_receipts == ((opening + 33 * MINUTE, F(114)), (opening + 62 * MINUTE, first_price))
    assert result.fixed.episode == result.chart.episode == result.chart_context.episode
    for branch in (result.fixed, result.chart, result.chart_context):
        assert branch.status == ("closed" if first_price is not None else "unresolved")
        assert branch.events[-1].open_price == first_price
