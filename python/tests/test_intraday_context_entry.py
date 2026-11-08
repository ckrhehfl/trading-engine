"""Invented EI entry requests, next-open branches and unchanged cash accounting."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_chart_cash as cash
from research import intraday_context_entry as adapter
from research.intraday_context_entry_values import EntryValueQuery, assess_entry_value, fit_entry_values
from research.intraday_context_features import project_context_features
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_scenario_policy import Step
from test_intraday_context_entry_values import FORECAST, POLICY, historical, mass
from test_intraday_context_features import BARS, OPEN, at, pair, session


F = Fraction


def features(bars=BARS, *, closing=None):
    original = session(bars)
    if closing is not None:
        original = replace(original, closing=closing)
    feature = project_context_features(*pair(original))
    return feature, original.minutes[feature.forecast_at - 3 * MINUTE]


def assessment(feature, *, gross=F(1), cost=33, probabilities=None):
    model = fit_entry_values([historical(day=-1, gross=gross, stage=feature.question.stage)],
                             cutoff_at=OPEN, execution_policy_sha256=POLICY)
    query = EntryValueQuery(feature.question_id, feature.forecast_at, feature.question.start_close,
        feature.question.anchor_id[1].pre_peak_range, F(cost), probabilities or mass(),
        "context_sequence", FORECAST, adapter.context_entry_feature_sha256(feature), POLICY)
    return assess_entry_value(model, query)


def prepare(*, mode="selected", feature=None, visible=None, value=None, gross=F(1), cost=33,
            risk=F(100), book=None, rule=True, **kwargs):
    default, minute = features()
    feature, visible = feature or default, visible or minute
    value = (value or assessment(feature, gross=gross, cost=cost)) if mode == "selected" else value
    return adapter.prepare_context_entry(feature, book or cash.new_book("invented-EI-entry"), mode=mode,
        visible_minute=visible, risk_budget=risk, cost_bps=cost,
        causal_source_sha256=adapter.context_entry_feature_sha256(feature), execution_policy_sha256=POLICY,
        forecast_source_sha256=FORECAST if mode == "selected" else None, entry_value=value,
        rule_enabled=None if mode == "selected" else rule, **kwargs)


def test_selected_and_explicit_baseline_have_same_frozen_request_and_fill_policy():
    selected, baseline = prepare(), prepare(mode="baseline")
    assert adapter.SCHEMA == "ei-context-entry-draft-v1"
    assert selected.mode == "selected" and baseline.mode == "baseline"
    assert baseline.entry_value is baseline.forecast_source_sha256 is None and baseline.rule_enabled is True
    assert selected.step == baseline.step
    step, question = selected.step, selected.features.question
    request = step.episode.pending
    assert (step.episode.start_at, step.episode.end_at, request.execute_at) == (at(32), question.path.end_at, at(33))
    assert (request.bounds.stop, request.bounds.max_price, request.quantity) == (112, 115, 14)
    assert request.funds == (selected.book_before.cash, selected.book_before.cash)
    cheap, first = adapter.fill_context_entry(selected, at(33), F(113))
    dear, second = adapter.fill_context_entry(baseline, at(33), F(115))
    assert first.filled and second.filled and cheap.book.lots[0].quantity == dear.book.lots[0].quantity == 14
    assert cheap.end_at == dear.end_at == question.path.end_at


def test_selected_passes_computed_value_instead_of_a_forced_entry_flag(monkeypatch):
    captured = []
    original = adapter.decide

    def observing(*args, **kwargs):
        captured.append(kwargs["values"])
        return original(*args, **kwargs)

    monkeypatch.setattr(adapter, "decide", observing)
    prepared = prepare()
    assert captured[0].enter == prepared.entry_value.expected_net != 1
    assert captured[0].wait == 0
    losing = prepare(gross=F(0))
    assert losing.entry_value.expected_net < 0
    assert losing.step.action == "wait" and losing.step.episode.pending is None
    assert captured[-1].enter == losing.entry_value.expected_net


def test_zero_net_tie_and_disabled_baseline_wait():
    feature, _ = features()
    cost = F(33, 10_000) * feature.question.start_close
    tied = prepare(gross=cost / feature.question.anchor_id[1].pre_peak_range)
    assert tied.entry_value.expected_net == 0 and tied.step.action == "wait"
    disabled = prepare(mode="baseline", rule=False)
    assert disabled.step.action == "wait" and disabled.rule_enabled is False
    with pytest.raises(InputRefusal, match="pending_first_request"):
        adapter.fill_context_entry(disabled, at(33), F(114))


def test_unsupported_path_mass_abstains_without_a_request():
    feature, minute = features()
    missing = assessment(feature, probabilities=mass((9, 1)))
    prepared = prepare(feature=feature, visible=minute, value=missing)
    assert prepared.status == "abstain" and prepared.step is None
    assert "positive_mass_without_complete_payoff" in prepared.reason


@pytest.mark.parametrize("field,value", [
    ("forecast_at", at(33)), ("decision_close", F(115)), ("risk", F(5)), ("cost_bps", F(43)),
    ("forecast_model_key", "chart_sequence"), ("forecast_source_sha256", "e" * 64),
    ("causal_source_sha256", "e" * 64), ("execution_policy_sha256", "e" * 64),
])
def test_stale_value_clock_geometry_cost_method_and_source_bindings_refused(field, value):
    feature, _ = features()
    result = assessment(feature)
    changed = replace(result, query=replace(result.query, **{field: value}))
    with pytest.raises(InputRefusal, match="value_binding"):
        prepare(value=changed)


def test_other_question_cannot_borrow_the_same_value():
    feature, _ = features()
    result = assessment(feature)
    key = result.query.question_id
    changed_key = ((key[0][0], key[0][1] + 1), key[1], key[2])
    with pytest.raises(InputRefusal, match="value_binding"):
        prepare(value=replace(result, query=replace(result.query, question_id=changed_key)))
    malformed = ((key[0][0], True), key[1], key[2])
    with pytest.raises(InputRefusal, match="question_identity"):
        prepare(value=replace(result, query=replace(result.query, question_id=malformed)))


@pytest.mark.parametrize("mutation", [
    {"expected_net": F(10)}, {"expected_cost": F(-1)}, {"action": "wait"},
    {"unsupported_probability_mass": F(1)}, {"class_net_values": (F(10),) * 10},
    {"model_sha256": "bad"}, {"status": "abstain"},
])
def test_result_arithmetic_or_proof_identity_mutations_refused(mutation):
    feature, _ = features()
    with pytest.raises(InputRefusal):
        prepare(value=replace(assessment(feature), **mutation))


def test_same_question_with_changed_valid_context_requires_a_new_feature_source_digest():
    feature, minute = features()
    result = assessment(feature)
    changed_question = replace(feature.question, episode_origin_context="B")
    changed = replace(feature, question=changed_question, context=(F(0), F(1), F(0), *feature.context[3:]))
    assert changed.question_id == feature.question_id
    with pytest.raises(InputRefusal, match="causal_feature_digest"):
        adapter.prepare_context_entry(changed, cash.new_book("invented"), mode="selected", visible_minute=minute,
            risk_budget=F(100), cost_bps=33, causal_source_sha256=result.query.causal_source_sha256,
            execution_policy_sha256=POLICY, forecast_source_sha256=FORECAST, entry_value=result)


@pytest.mark.parametrize("mutation", [
    {"available_at": at(33)}, {"available_at": at(31)}, {"start_at": at(30)}, {"symbol": "different"},
])
def test_late_early_future_or_wrong_symbol_visible_minute_refused(mutation):
    _, minute = features()
    with pytest.raises(InputRefusal, match="same_clock_price"):
        prepare(visible=replace(minute, **mutation))


def test_visible_ohlc_cannot_disagree_with_ready_causal_features():
    _, minute = features()
    changed = replace(minute, ohlc=(Decimal(115),) * 4)
    with pytest.raises(InputRefusal, match="visible_feature_price"):
        prepare(visible=changed)
    changed_high = replace(minute, ohlc=(minute.ohlc[0], Decimal(120), *minute.ohlc[2:]))
    with pytest.raises(InputRefusal, match="visible_feature_price"):
        prepare(visible=changed_high)


def test_unknown_visible_price_abstains_instead_of_assuming_no_fill():
    _, minute = features()
    prepared = prepare(visible=replace(minute, ohlc=None, price_state="missing"))
    assert prepared.status == "abstain" and prepared.reason == "visible_price_unsupported"
    assert prepared.step is None and not prepared.book_before.indeterminate


def test_outside_session_and_lower_same_clock_priority_do_not_prepare_requests():
    outside, minute = features(closing=at(50))
    declined = prepare(feature=outside, visible=minute)
    assert declined.status == "abstain" and "outside_session" in declined.reason
    feature, minute = features()
    lower = replace(feature, question=replace(feature.question, economic_priority=False))
    declined = prepare(feature=lower, visible=minute)
    assert declined.status == "abstain" and declined.reason == "lower_same_clock_question_priority"


def test_unsupported_current_geometry_abstains_before_next_open():
    feature, minute = features((*BARS[:-1], (112, 126, 111, 126)))
    declined = prepare(feature=feature, visible=minute)
    assert declined.status == "abstain" and "unsupported_price_geometry" in declined.reason


def test_target_caps_bound_and_a_gap_exactly_to_target_is_known_no_fill():
    feature, minute = features((*BARS[:-1], (112, 125, 111, Decimal("123.5"))))
    prepared = prepare(feature=feature, visible=minute)
    target = feature.question.path.target
    assert prepared.step.episode.pending.bounds.max_price == target
    episode, outcome = adapter.fill_context_entry(prepared, at(33), target)
    assert outcome.outcome == "declined" and outcome.reason == "entry_at_or_above_target"
    assert episode.book == prepared.book_before and episode.pending is None and episode.attempts == 1
    assert episode.entered_at is None and episode.stop is None and not episode.book.inventory_uncertain


@pytest.mark.parametrize("price,outcome,reason", [
    (F(116), "declined", "open_outside_frozen_entry_bounds"),
    (F(112), "declined", "open_outside_frozen_entry_bounds"),
    (F(124), "declined", "entry_at_or_above_target"),
    (None, "indeterminate", "entry_price_unknown"),
])
def test_next_open_decline_and_unknown_keep_distinct_frozen_states(price, outcome, reason):
    prepared = prepare()
    episode, fill = adapter.fill_context_entry(prepared, at(33), price)
    assert (fill.outcome, fill.reason) == (outcome, reason)
    assert episode.book.cash == prepared.book_before.cash and not episode.book.lots
    assert episode.pending is None and episode.entered_at is None
    assert episode.book.inventory_uncertain == (price is None)
    assert episode.end_at == prepared.features.question.path.end_at


def test_zero_quantity_preserves_existing_risk_sizing_and_does_not_request():
    prepared = prepare(risk=F(1, 10_000))
    assert prepared.step.action == "wait" and prepared.step.reason == "insufficient_risk_or_cash_for_one_unit"
    assert prepared.step.episode.pending is None


@pytest.mark.parametrize("cost", [33, 43])
def test_exact_fraction_cost_and_reduction_never_charge_entry_cost_twice(cost):
    prepared = prepare(cost=cost)
    episode, fill = adapter.fill_context_entry(prepared, at(33), F(114))
    quantity = fill.quantity
    assert fill.assumed_cost == F(quantity * 114 * cost, 10_000)
    reduced = cash.reduce_half(episode.book, episode.symbol, exit_price=F(118))
    remaining = reduced.book.lots[0].quantity
    exited = cash.exit(reduced.book, episode.symbol, quantity=remaining, exit_price=F(118))
    assert reduced.assumed_cost == exited.assumed_cost == 0
    assert exited.book.realized_gross == quantity * 4
    assert exited.book.realized_net == quantity * 4 - fill.assumed_cost
    assert sum(leg.allocated_entry_cost for leg in exited.book.realized_exits) == fill.assumed_cost


@pytest.mark.parametrize("cost", [0, 32, 34, 44, F(33), 33.0, True])
def test_cost_contract_does_not_increase_or_relax_existing_cases(cost):
    with pytest.raises(InputRefusal, match="episode_limits"):
        prepare(mode="baseline", cost=cost)


def test_known_flat_book_required_and_risk_budget_stays_exact():
    book = cash.new_book("invented")
    held = cash.enter(book, "invented", entry_price=F(114), observed_prices={}, cost_bps=33).book
    for invalid in (held, cash.freeze(book, "invented_missing")):
        with pytest.raises(InputRefusal, match="known_flat_book"):
            prepare(book=invalid)
    for risk in (0, F(0), -1, F(-1), 100.0):
        with pytest.raises(InputRefusal, match="episode_limits"):
            prepare(mode="baseline", risk=risk)


def test_explicit_baseline_cannot_accept_predictions_or_an_implicit_rule_flag():
    with pytest.raises(InputRefusal, match="baseline_requires"):
        prepare(mode="baseline", rule=None)
    feature, _ = features()
    with pytest.raises(InputRefusal, match="baseline_requires"):
        prepare(mode="baseline", value=assessment(feature))


@pytest.mark.parametrize("clock,price", [(at(32), F(114)), (at(34), F(114)), (at(33), 114.0), (at(33), F(0))])
def test_fill_requires_exact_requested_clock_and_positive_exact_price(clock, price):
    with pytest.raises(InputRefusal, match="pending_clock_or_price"):
        adapter.fill_context_entry(prepare(), clock, price)


def test_changed_pending_quantity_expiry_or_preparation_digest_is_refused():
    prepared = prepare()
    episode, request = prepared.step.episode, prepared.step.episode.pending
    changes = [replace(prepared, feature_sha256="e" * 64),
               replace(prepared, step=replace(prepared.step, episode=replace(episode, end_at=episode.end_at + MINUTE))),
               replace(prepared, step=replace(prepared.step, episode=replace(episode,
                    pending=replace(request, quantity=request.quantity + 1)))),
               replace(prepared, step=Step(replace(episode, pending=None), "enter", prepared.step.reason))]
    for changed in changes:
        with pytest.raises(InputRefusal, match="preparation_changed"):
            adapter.fill_context_entry(changed, at(33), F(114))


def test_preparation_has_no_future_open_argument_and_resolved_episode_cannot_be_reused_as_pending():
    feature, minute = features()
    with pytest.raises(TypeError):
        adapter.prepare_context_entry(feature, cash.new_book("invented"), mode="baseline", visible_minute=minute,
            risk_budget=F(100), cost_bps=33, causal_source_sha256=adapter.context_entry_feature_sha256(feature),
            execution_policy_sha256=POLICY, rule_enabled=True, next_open=F(114))
    prepared = prepare()
    episode, _ = adapter.fill_context_entry(prepared, at(33), F(114))
    changed = replace(prepared, step=replace(prepared.step, episode=episode))
    with pytest.raises(InputRefusal, match="preparation_changed"):
        adapter.fill_context_entry(changed, at(33), F(114))
