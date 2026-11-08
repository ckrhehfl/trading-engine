"""Invented EI pooled held questions and independent action-payoff checks."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_chart_cash as cash
from research import intraday_context_held as held
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_context_entry import context_entry_feature_sha256, fill_context_entry, prepare_context_entry
from research.intraday_context_features import project_context_features
from research.intraday_context_questions import scan_context_questions
from research.intraday_context_situations import context_frame, scan_context_frames
from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs
from test_intraday_context_features import BARS, OPEN


F = Fraction
SOURCE = "a" * 64
POLICY = "b" * 64


def fixture(*, day=0, symbol="invented", decision=37, repeated=False, missing=None, reduce=False):
    opening = OPEN + timedelta(days=day)
    bars = (*BARS, *((114, 115, 113, 114),) * 7)
    if repeated:
        bars = (*bars[:6], (114, 114, 113, 114), *bars[7:])
    minutes = {opening + index * MINUTE: Minute(symbol, opening + index * MINUTE,
        opening + (index + 3) * MINUTE, tuple(map(Decimal, bar)), Decimal(10))
        for bar_index, bar in enumerate(bars) for index in range(5 * bar_index, 5 * bar_index + 5)}
    if missing is not None:
        del minutes[opening + missing * MINUTE]
    session = SessionInputs(symbol, opening, opening + 390 * MINUTE, minutes)
    frames = tuple(scan_context_frames(session, through=opening + 32 * MINUTE))
    questions = tuple(scan_context_questions(frames, closing=session.closing))
    question = next(item for item in questions[-1].questions if item.economic_priority)
    entry = project_context_features(frames[-1], question)
    prepared = prepare_context_entry(entry, cash.new_book("invented-held"), mode="baseline",
        visible_minute=minutes[opening + 29 * MINUTE], risk_budget=F(100), cost_bps=33,
        causal_source_sha256=context_entry_feature_sha256(entry), execution_policy_sha256=POLICY, rule_enabled=True)
    episode, _ = fill_context_entry(prepared, opening + 33 * MINUTE, F(114))
    if reduce:
        episode = replace(episode, book=cash.reduce_half(episode.book, symbol, exit_price=F(114)).book,
                          last_at=opening + 34 * MINUTE)
    current = context_frame(session, decision_at=opening + decision * MINUTE)
    query = held.project_context_held(entry, current, episode, session,
                                      entry_feature_sha256=context_entry_feature_sha256(entry))
    visible = minutes.get(opening + (decision - 3) * MINUTE)
    return query, session, current, visible


def historical(query, *, coverage=None, mature=None, reason=None, source=SOURCE):
    cap = coverage or query.held.planning_cap
    maturity = None if reason else mature or cap + 3 * MINUTE
    return held.HistoricalHeldQuestion(query, cap, maturity, source, reason)


def fold(*days):
    training = tuple(sorted({(OPEN + timedelta(days=day)).date().isoformat() for day in days or (-1,)}))
    return ChronologicalFold(1, (0, len(training)), (len(training), len(training) + 1),
                             training, (OPEN.date().isoformat(),), OPEN)


def roster(*rows):
    days = tuple((row.question.opening.date() - OPEN.date()).days for row in rows)
    return held.ContextHeldRoster(rows, fold=fold(*days))


def assess(pool, query, visible, sessions, *, mode="chart_context", calls=None):
    def read(day, symbol, digest):
        assert digest == SOURCE
        if calls is not None:
            calls.append((day, symbol, digest))
        return sessions.get((day, symbol))
    return held.assess_context_held(pool, query, mode=mode, visible_minute=visible, read_prior_session=read)


def test_projection_is_a_fresh_remaining_time_question_with_original_context_and_inventory():
    query, _, _, _ = fixture(decision=57)
    assert held.SCHEMA == "ei-context-held-pooled-draft-v1"
    assert query.origin_context == "A" and query.current_context == "other"
    assert query.question_id == (query.entry.question_id, query.episode.first_lot_id, 1,
                                 OPEN + 57 * MINUTE, OPEN + 62 * MINUTE)
    assert query.held.minutes_held == 24 and query.held.minutes_remaining == 5
    assert query.held.planning_cap == query.entry.question.path.end_at == query.episode.end_at
    assert query.held.remaining_fraction == 1 and len(query.held.minutes) == 20
    assert len(held._vector(query, "chart")) == 9 and len(held._vector(query, "chart_context")) == 15
    assert held._vector(query, "chart_context")[-6:] == (1, 0, 0, 0, 0, 1)


def test_pooled_context_axes_change_actual_selected_sources_without_exact_strata():
    query, _, _, _ = fixture(decision=57)
    records = [historical(fixture(day=-1, symbol=symbol, decision=57, repeated=repeated)[0])
               for symbol, repeated in (("A", True), ("B", True), ("C", False))]
    pool = roster(*records)
    chart, context = (pool.match(query, mode=mode) for mode in held.MODES)
    assert chart.eligible_sha256 == context.eligible_sha256 and chart.pool_sha256 == context.pool_sha256
    assert chart.candidate_rows == context.eligible_rows == 3
    assert [row.question.held.symbol for row in chart.sources] == ["A", "B"]
    assert [row.question.held.symbol for row in context.sources] == ["C", "A"]
    assert chart.distances == (0, 0) and context.distances == (0, 2)
    assert [row.question.current_context for row in context.sources] == ["other", "B"]
    assert context.status == "ready" and context.distinct_dates == 1
    assert context.precision_reason == "single_training_date_inconclusive"


def test_different_held_time_is_a_distance_not_a_hard_bucket_filter():
    source, _, _, _ = fixture(day=-1, decision=37)
    query, _, _, _ = fixture(decision=42)
    selected = roster(historical(source)).match(query, mode="chart")
    assert selected.status == "ready" and selected.sources[0].question.held.minutes_held == 4
    assert query.held.minutes_held == 9
    assert selected.distances[0] > 0
    assert selected.weights == (F(1),) and selected.effective_date_count == 1


def test_query_coverage_filters_identical_pool_before_either_distance():
    source, _, _, _ = fixture(day=-1, decision=42)
    query, _, _, _ = fixture(decision=37)
    pool = roster(historical(source))
    chart, context = (pool.match(query, mode=mode) for mode in held.MODES)
    assert chart.eligible_sha256 == context.eligible_sha256
    assert chart.status == context.status == "no_eligible_paths"
    assert chart.excluded_by_reason == (("remaining_time_coverage_short", 1),)


def test_one_date_is_computed_while_eh_thirty_date_setting_stays_unchanged():
    from research.intraday_scenario_empirical import MIN_NEIGHBOR_DAYS
    source, _, _, _ = fixture(day=-1)
    query, _, _, _ = fixture()
    selection = roster(historical(source)).match(query, mode="chart_context")
    assert MIN_NEIGHBOR_DAYS == 30 and selection.status == "ready"
    assert selection.date_weights == ((source.coordinate[0], F(1)),)


def test_deterministic_caps_keep_64_two_per_date_and_one_symbol_day():
    rows = []
    for day in range(-33, 0):
        rows.extend(historical(fixture(day=day, symbol=symbol, decision=37)[0]) for symbol in ("C", "B", "A"))
    query = fixture()[0]
    first = roster(*rows).match(query, mode="chart")
    second = roster(*reversed(rows)).match(query, mode="chart")
    assert first == second and len(first.sources) == 64 and first.distinct_dates == 32
    assert {row.question.held.symbol for row in first.sources} == {"A", "B"}
    assert first.weights == (F(1, 64),) * 64 and first.effective_date_count == 32
    source37 = fixture(day=-1, decision=37)[0]
    source42 = fixture(day=-1, decision=42)[0]
    query42 = fixture(decision=42)[0]
    one = roster(historical(source37), historical(source42)).match(query42, mode="chart")
    assert len(one.sources) == 1 and one.sources[0].question.held.decision_at == source42.held.decision_at


def test_unsupported_current_context_is_not_encoded_as_other_in_either_arm():
    unknown, _, _, _ = fixture(missing=34)
    source = fixture(day=-1)[0]
    pool = roster(historical(source))
    assert unknown.current_context == "unknown" and unknown.status == "unsupported"
    assert "current_context_unknown" in unknown.reasons
    assert all(pool.match(unknown, mode=mode).status == "query_unsupported" for mode in held.MODES)


def test_unknown_training_and_unmatured_reasons_preserve_candidate_counts():
    unknown = historical(fixture(day=-2, missing=34)[0])
    immature = historical(fixture(day=-1)[0], reason="path_receipt_unknown")
    selection = roster(unknown, immature).match(fixture()[0], mode="chart")
    assert selection.candidate_rows == 2 and selection.eligible_rows == 0
    assert selection.excluded_by_reason == (("training_question_unsupported", 1),
                                           ("unmatured: path_receipt_unknown", 1))


@pytest.mark.parametrize("mature", [OPEN, OPEN + MINUTE, OPEN - timedelta(days=1)])
def test_actual_path_maturity_is_strict_and_covers_full_declared_window(mature):
    row = historical(fixture(day=-1)[0], mature=mature)
    with pytest.raises(InputRefusal, match="not_actually_mature"):
        roster(row)


def test_future_duplicate_wrong_fold_and_changed_source_digest_are_refused():
    first, later = fixture(day=-1)[0], fixture(day=-1, decision=42)[0]
    row = historical(first)
    with pytest.raises(InputRefusal, match="identity_or_coverage"):
        roster(row, row)
    with pytest.raises(InputRefusal, match="identity_or_coverage"):
        held.ContextHeldRoster((historical(fixture()[0]),), fold=fold(-1))
    with pytest.raises(InputRefusal, match="same_source_digest"):
        roster(row, historical(later, source="c" * 64))
    pool = roster(row)
    with pytest.raises(InputRefusal, match="query_mode_or_fold"):
        pool.match(first, mode="chart")
    with pytest.raises(InputRefusal, match="dates_overlap"):
        held.ContextHeldRoster((row,), fold=replace(fold(-1), training_sessions=fold(-1).test_sessions))


def test_reduced_current_inventory_is_allowed_but_training_is_baseline_full_position():
    query, _, _, _ = fixture(reduce=True)
    assert query.held.half_reduced and query.held.remaining_fraction == F(1, 2)
    assert roster(historical(fixture(day=-1)[0])).match(query, mode="chart").status == "ready"
    with pytest.raises(InputRefusal, match="baseline_full_position"):
        roster(historical(fixture(day=-1, reduce=True)[0]))


def test_projection_rejects_changed_origin_frame_anchor_clock_and_feature_digest():
    query, session, current, _ = fixture()
    inputs = (query.entry, current, query.episode, session)
    for changed in (replace(current, classification="B"), replace(current, available_at=current.available_at + MINUTE),
                    replace(current, anchor=replace(current.anchor, price=F(117)))):
        with pytest.raises(InputRefusal):
            held.project_context_held(query.entry, changed, query.episode, session,
                                      entry_feature_sha256=query.entry_feature_sha256)
    changed_entry = replace(query.entry, question=replace(query.entry.question, episode_origin_context="B"),
                            context=(F(0), F(1), F(0), *query.entry.context[3:]))
    with pytest.raises(InputRefusal, match="original_context_or_feature"):
        held.project_context_held(changed_entry, current, query.episode, session,
                                  entry_feature_sha256=context_entry_feature_sha256(changed_entry))
    with pytest.raises(InputRefusal, match="entry_feature_binding"):
        held.project_context_held(*inputs, entry_feature_sha256="c" * 64)
    with pytest.raises(InputRefusal):
        held.project_context_held(query.entry, current, replace(query.episode, end_at=query.episode.end_at + MINUTE),
                                  session, entry_feature_sha256=query.entry_feature_sha256)


class PrefixOnly(Mapping):
    def __init__(self, rows, end):
        self.rows, self.end = rows, end

    def __getitem__(self, key):
        assert key < self.end, "future suffix requested"
        return self.rows[key]

    def __iter__(self):
        raise AssertionError("source iteration")

    def __len__(self):
        raise AssertionError("source length")


def test_projection_never_reads_future_suffix_and_needs_no_initial_terminal_label():
    query, session, current, _ = fixture()
    prefix = replace(session, minutes=PrefixOnly(session.minutes, current.observation_end))
    actual = held.project_context_held(query.entry, current, query.episode, prefix,
                                       entry_feature_sha256=query.entry_feature_sha256)
    assert actual == query
    assert "path_label" not in actual.__dataclass_fields__


def test_saved_query_inventory_or_context_mutations_cannot_reuse_its_digest():
    query = fixture()[0]
    pool = roster(historical(fixture(day=-1)[0]))
    for bad in (replace(query, current_context="B"),
                replace(query, held=replace(query.held, minutes_remaining=24)),
                replace(query, episode=replace(query.episode, risk_budget=F(200)))):
        with pytest.raises(InputRefusal, match="question_digest"):
            pool.match(bad, mode="chart")


@pytest.mark.parametrize("reduced", [False, True])
def test_actions_use_same_selected_paths_current_inventory_and_independent_payoff(reduced):
    prior, source, _, _ = fixture(day=-1)
    query, _, _, visible = fixture(reduce=reduced)
    calls = []
    result = assess(roster(historical(prior)), query, visible, {prior.coordinate[:2]: source}, calls=calls)
    assert result.reason == "ready" and result.independently_verified is True
    assert calls == [(prior.coordinate[0], prior.coordinate[1], SOURCE)]
    actions = {row.action for row in result.projection.rollout.outcomes}
    assert actions == ({"hold", "exit"} if reduced else {"hold", "reduce_half", "exit"})
    assert all(row.weight == 1 for row in result.projection.rollout.outcomes)
    assert result.projection.rollout.values.hold == result.projection.rollout.values.exit == 0
    assert result.unsupported_mass_by_action == () and result.unassessed_mass == 0


def test_unknown_selected_suffix_keeps_mass_and_does_not_replace_source():
    prior, source, _, _ = fixture(day=-1, missing=45)
    query, _, _, visible = fixture()
    result = assess(roster(historical(prior)), query, visible, {prior.coordinate[:2]: source})
    assert result.reason == "unresolved_path_mass" and result.projection.rollout.values is None
    assert result.independently_verified is True
    assert dict(result.unsupported_mass_by_action) == {"hold": F(1), "reduce_half": F(1)}
    assert result.selection.weights == (F(1),)


def test_missing_selected_session_is_unassessed_not_zero_payoff():
    prior = fixture(day=-1)[0]
    query, _, _, visible = fixture()
    result = assess(roster(historical(prior)), query, visible, {})
    assert result.reason == "selected_prior_session_missing" and result.projection.rollout is None
    assert result.unassessed_mass == 1 and result.independently_verified is True


def test_empty_selection_does_not_call_any_price_reader():
    query, _, _, visible = fixture()
    pool = held.ContextHeldRoster((), fold=fold(-1))

    def forbidden(*args):
        raise AssertionError("no selected source")

    result = held.assess_context_held(pool, query, mode="chart", visible_minute=visible, read_prior_session=forbidden)
    assert result.reason == "no_eligible_paths" and result.independently_verified is None


def test_action_adapter_rejects_wrong_visible_mark_and_source_prefix():
    prior, source, _, _ = fixture(day=-1)
    query, _, _, visible = fixture()
    pool = roster(historical(prior))
    with pytest.raises(InputRefusal, match="visible_minute_clock"):
        assess(pool, query, replace(visible, available_at=visible.available_at + MINUTE), {})
    with pytest.raises(InputRefusal, match="visible_price_basis"):
        assess(pool, query, replace(visible, ohlc=(Decimal(115),) * 4), {})
    rows = dict(source.minutes)
    last = prior.held.decision_at - 3 * MINUTE
    rows[last] = replace(rows[last], ohlc=(Decimal(115),) * 4)
    with pytest.raises(InputRefusal, match="selected_source_prefix_changed"):
        assess(pool, query, visible, {prior.coordinate[:2]: replace(source, minutes=rows)})


def test_independent_payoff_rejects_tampered_producer_values(monkeypatch):
    prior, source, _, _ = fixture(day=-1)
    query, _, _, visible = fixture()
    original = held._on_selected_paths

    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, rollout=replace(result.rollout, values=replace(result.rollout.values, hold=F(99))))

    monkeypatch.setattr(held, "_on_selected_paths", corrupt)
    with pytest.raises(InputRefusal, match="independent_payoff"):
        assess(roster(historical(prior)), query, visible, {prior.coordinate[:2]: source})


@pytest.mark.parametrize("available", [OPEN, OPEN - timedelta(days=1) + 70 * MINUTE])
def test_used_source_metadata_cannot_disagree_with_declared_actual_maturity(available):
    prior, source, _, _ = fixture(day=-1)
    query, _, _, visible = fixture()
    rows = dict(source.minutes)
    clock = source.opening + 50 * MINUTE
    rows[clock] = replace(rows[clock], available_at=available)
    with pytest.raises(InputRefusal, match="selected_actual_maturity"):
        assess(roster(historical(prior)), query, visible, {prior.coordinate[:2]: replace(source, minutes=rows)})


def test_nonzero_wealth_change_uses_current_units_without_recharging_paid_entry_cost():
    prior, source, _, _ = fixture(day=-1)
    rows = {at: replace(row, ohlc=(Decimal(116),) * 4) if at >= source.opening + 38 * MINUTE else row
            for at, row in source.minutes.items()}
    source = replace(source, minutes=rows)
    pool = roster(historical(prior))
    for reduced in (False, True):
        query, _, _, visible = fixture(reduce=reduced)
        result = assess(pool, query, visible, {prior.coordinate[:2]: source})
        quantity = query.episode.book.lots[0].quantity
        assert result.projection.rollout.values.hold == result.projection.rollout.values.exit == 2 * quantity
        assert query.episode.book.lots[0].original_entry_cost > 0
        assert result.independently_verified is True
