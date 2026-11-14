"""Invented target-free policies and exact matched event books only."""

from dataclasses import replace
from fractions import Fraction as F
import inspect

import pytest

from research import intraday_event_evaluation as evaluation
from research import intraday_event_features as features
from research import intraday_event_labels as labels
from research.intraday_event_models import HEADS, Query, TrainingLabels
from research.intraday_event_scenarios import EventFrame, MinuteBar


DAY = "2026-01-05"
BINDING = "a" * 64


def market(price=100, *, length=100):
    bars = {i: MinuteBar(i, price, price + 1, price - 1, price, i + 3) for i in range(length)}
    return evaluation.SessionMarket(length, bars, {}, {})


def candidate(decision=33, *, state="WAITING", day=DAY, symbol="KRX:000001", event_id="e1",
              price=100, scale=2, stop=95, onset_decision=33, supplied=None):
    supplied = market(price) if supplied is None else supplied
    episode = evaluation.Episode(day, symbol, event_id, day + symbol, "A_DOWNFAIL", onset_decision)
    terminal = state in ("CONFIRMED", "INVALIDATED", "EXPIRED_NO_CONFIRMATION", "UNKNOWN_GAP")
    frame = EventFrame(episode.family, event_id, episode.cluster_id, onset_decision - 3, decision - 3,
        decision, onset_decision - 6, decision - 2, "at_onset" if decision == onset_decision else "terminal" if terminal else "scheduled",
        state, terminal, F(scale), F(stop + 1), F(stop), ())
    chart = features.chart_features(frame, supplied.bars, session_length=supplied.session_length, volumes=supplied.volumes)
    query = Query(event_id + "@" + str(decision), day, symbol, event_id, episode.cluster_id, decision,
        episode.family, state, tuple(map(tuple, chart["sequence"])), tuple(chart["static"]))
    return evaluation.Candidate(episode, frame, labels.prepare_query(frame, chart, session_length=supplied.session_length), query)


def prediction(query, *, immediate=0, wait=0, remaining=None, path=(0.2, 0.6, 0.2), fill=0.5):
    held = query.state.startswith("HELD_")
    values = {"path_class": None if held else list(path), "fill": None if held else fill,
              "net_immediate": None if held else immediate, "net_wait3": None if held else wait,
              "remaining_gross": remaining if held else None}
    return {"key": query.key, "session": query.session, "query_status": "supported_features",
            "predictions": values, "head_status": {head: "invented" for head in HEADS}}


def policy(candidates, predictions=(), control="model", *, episodes=None):
    episodes = tuple({c.episode.event_id: c.episode for c in candidates}.values()) if episodes is None else episodes
    return evaluation.freeze_policy(episodes, candidates, predictions, control=control,
        model="invented-fixed-fold", scope="chart_all", prediction_binding=BINDING)


def arm(books, entry="next_open_capped", management="fixed_exit"):
    return next(row for row in books if row["entry_policy"] == entry and row["management"] == management)


def test_policy_requires_both_q_heads_and_commits_once_with_immediate_tie():
    a, b, c = candidate(), candidate(36), candidate(39, state="CONFIRMED")
    result = policy((a, b, c), (prediction(a.query, immediate=2, wait=None),
        prediction(b.query, immediate=0.5, wait=0.5), prediction(c.query, immediate=9, wait=10)))
    assert result.choices[0].candidate == b and result.choices[0].action == "immediate"
    assert len(result.trace) == 2 and result.trace[0][5] == "missing_paired_value_heads"
    assert "labels" not in inspect.signature(evaluation.freeze_policy).parameters
    evaluation.verify_policy(result)
    with pytest.raises(ValueError, match="seal"):
        evaluation.verify_policy(replace(result, control="onset"))


def test_never_positive_observation_is_uncommitted_and_never_claimed_zero_value():
    c = candidate()
    result = policy((c,), (prediction(c.query, immediate=-1, wait=0),))
    assert result.choices[0].action == "no_commit"
    assert result.trace[0][4:6] == ("observe", "observe_without_commitment")
    books = evaluation.replay_policy(result, {})
    assert all(row["outcome"]["net"] == "0" and row["observation_continuation_value"] is None for row in books)


def test_onset_and_confirmation_controls_share_roster_and_causal_failures():
    first, confirmed = candidate(), candidate(36, state="CONFIRMED")
    onset = policy((first, confirmed), control="onset")
    confirmation = policy((first, confirmed), control="confirmation")
    assert onset.roster_sha256 == confirmation.roster_sha256
    assert onset.choices[0].candidate == first and confirmation.choices[0].candidate == confirmed
    invalid = candidate(state="INVALIDATED")
    assert policy((invalid,), control="onset").choices[0].reason == "terminal_not_admissible"
    no_query = replace(first, query=None, label_query=replace(first.label_query, reference_offset=None, reference_close=None))
    assert policy((no_query,), control="onset").choices[0].reason == "reference_unknown"


def test_original_episode_without_coalesced_frame_is_not_removed():
    c = candidate()
    frozen = policy((), episodes=(c.episode,))
    assert len(frozen.choices) == 1 and frozen.choices[0].reason == "no_causal_frame"
    assert len(evaluation.replay_policy(frozen, {})) == 6


def test_duplicate_clock_or_nominal_cluster_split_is_rejected():
    c = candidate()
    duplicate = replace(c, query=replace(c.query, key="second-key"))
    with pytest.raises(ValueError, match="Coalesce"):
        policy((c, duplicate))
    second = candidate(event_id="e2")
    second = replace(second, episode=replace(second.episode, cluster_id="fake"),
        frame=replace(second.frame, overlap_group_id="fake"), query=replace(second.query, cluster_id="fake"))
    with pytest.raises(ValueError, match="one cluster"):
        policy((c, second))


def test_wait3_commits_once_and_held_callback_uses_new_clocks_without_labels():
    c, later = candidate(), candidate(36)
    frozen = policy((c, later), (prediction(c.query, immediate=0.1, wait=0.2), prediction(later.query, immediate=8, wait=9)))
    seen = []
    def infer(query):
        assert type(query) is Query and query.original_episode_id == c.episode.event_id
        assert not hasattr(query, "net_wait3") and query.state in ("HELD_10", "HELD_20")
        seen.append(query)
        return prediction(query, remaining=-0.01)
    books = evaluation.replay_policy(frozen, {(DAY, c.episode.symbol): market()}, held_predict=infer, prediction_binding=BINDING)
    assert len(frozen.trace) == 1
    assert [q.decision_offset for q in seen] == [47, 57]  # Entry37, not immediate entry34.
    assert arm(books)["entry_decision_offset"] == 36
    assert arm(books, management="forecast_full_exit")["execution"]["sales"][0]["execute_offset"] == 48
    assert all(row["initial_denominator"] == str(frozen.choices[0].initial_denominator) for row in books)
    with pytest.raises(ValueError, match="binding"):
        evaluation.replay_policy(frozen, {}, held_predict=infer, prediction_binding="b" * 64)


@pytest.mark.parametrize("missing,structural,expected", [(True, False, "unknown"), (False, True, "no_entry")])
def test_wait_requires_causal_known_structure_and_never_repredicts(missing, structural, expected):
    c, tape = candidate(), market()
    if missing:
        del tape.bars[31]
    if structural:
        tape.bars[31] = MinuteBar(31, 100, 101, 94, 94, 34)
    frozen = policy((c,), (prediction(c.query, immediate=0, wait=1),))
    books = evaluation.replay_policy(frozen, {(DAY, c.episode.symbol): tape})
    assert all(row["outcome"]["status"] == expected for row in books)
    assert all(row["outcome"]["net"] == (None if expected == "unknown" else "0") for row in books)


def test_matched_entry_arms_and_odd_share_reduction_charge_cost_once():
    c = candidate(price=600000, scale=1, stop=599999)
    frozen = policy((c,), control="onset")
    books = evaluation.replay_policy(frozen, {(DAY, c.episode.symbol): market(600000)},
        held_predict=lambda q: prediction(q, remaining=0), prediction_binding=BINDING)
    assert all(row["execution"]["filled_quantity"] == 3 for row in books)
    reduced = arm(books, management="forecast_half_then_exit")["execution"]
    assert [leg["quantity"] for leg in reduced["sales"]] == [1, 2]
    assert [leg["execute_offset"] for leg in reduced["sales"]] == [45, 55]
    assert sum(F(leg["allocated_cost"]) for leg in reduced["sales"]) == F(reduced["entry_cost"])
    assert reduced["cost"] == arm(books)["execution"]["cost"]


def test_reference_arm_no_fill_is_known_zero_but_missing_entry_is_unknown():
    c, tape = candidate(), market()
    frozen = policy((c,), control="onset")
    tape.bars[34] = MinuteBar(34, 101, 102, 99, 100, 37)
    books = evaluation.replay_policy(frozen, {(DAY, c.episode.symbol): tape})
    assert arm(books)["outcome"]["filled_quantity"] > 0
    assert arm(books, "reference_limit_one_shot")["outcome"]["net"] == "0"
    assert arm(books, "reference_limit_one_shot")["outcome"]["status"] == "no_fill"
    del tape.bars[34]
    books = evaluation.replay_policy(frozen, {(DAY, c.episode.symbol): tape})
    assert all(row["outcome"]["net"] is None and row["outcome"]["filled_quantity"] is None for row in books)


def test_late_expiry_receipt_preserves_unmatured_cash_separately():
    c, tape = candidate(), market()
    tape.bars[64] = replace(tape.bars[64], available_offset=100)
    book = arm(evaluation.replay_policy(policy((c,), control="onset"), {(DAY, c.episode.symbol): tape}))
    assert book["execution"]["net"] is not None
    assert book["outcome"]["net"] is None and book["outcome"]["reason"] == "outcome_not_mature_in_session"
    assert book["outcome"]["filled_quantity"] > 0


def test_structural_stop_precedes_forecast_and_future_does_not_change_held_query():
    c, tape, seen = candidate(), market(), []
    tape.bars[41] = MinuteBar(41, 100, 101, 94, 94, 44)
    books = evaluation.replay_policy(policy((c,), control="onset"), {(DAY, c.episode.symbol): tape},
        held_predict=lambda q: seen.append(q) or prediction(q, remaining=-1), prediction_binding=BINDING)
    assert seen == []
    assert arm(books, management="forecast_full_exit")["execution"]["sales"][0]["reason"] == "structural"
    seen_first, seen_changed = [], []
    normal, altered = market(), market()
    for i in range(55, 100):
        altered.bars[i] = MinuteBar(i, 110, 111, 109, 110, i + 3)
    for market_value, collected in ((normal, seen_first), (altered, seen_changed)):
        evaluation.replay_policy(policy((c,), control="onset"), {(DAY, c.episode.symbol): market_value},
            held_predict=lambda q: collected.append(q) or prediction(q, remaining=1), prediction_binding=BINDING)
    assert seen_first == seen_changed


def test_forecast_reports_maturity_calibration_state_and_original_episode_weight():
    a, b = candidate(), candidate(day="2026-01-06", event_id="e2", state="INVALIDATED")
    target_a = TrainingLabels(a.query.key, 1, 1, F(1, 100), F(2, 100), DAY)
    target_b = TrainingLabels(b.query.key, 0, None, None, None, "2026-01-07")
    result = evaluation.forecast_report((a.query, b.query), (prediction(a.query), prediction(b.query)),
        (target_a, target_b), (DAY, "2026-01-06"), model="invented", scope="chart_all", as_of_session="2026-01-06",
        onset_keys=(a.query.key,))
    all_rows = next(row for row in result["groups"] if row["dimension"] == "all")
    assert all_rows["heads"]["path_class"]["known_labels"] == 1
    assert all_rows["heads"]["path_class"]["score"] == pytest.approx(0.24)
    assert all_rows["heads"]["fill"]["score"] == 0.25
    assert all_rows["heads"]["path_class"]["calibration"]
    phases = {row["value"] for row in result["groups"] if row["dimension"] == "phase"}
    assert phases == {"onset", "invalid_or_terminal"}
    weights = evaluation._weights((a.query, replace(a.query, key="second", decision_offset=36), b.query), {})
    assert weights[a.query.key] == weights["second"] == 0.25 and weights[b.query.key] == 0.5


def test_cash_reports_zero_calendar_weeks_unknown_dates_and_never_sum_arms():
    a, b = candidate(), candidate(day="2026-01-06", event_id="e2")
    tape = market()
    del tape.bars[34]
    books = evaluation.replay_policy(policy((a, b), control="onset"),
        {(DAY, a.episode.symbol): market(), (b.episode.session, b.episode.symbol): tape})
    report = evaluation.cash_report(books, (DAY, "2026-01-06", "2026-01-12", "2026-01-19"))
    all_groups = [r for r in report["groups"] if r["dimension"] == "all"]
    assert len(all_groups) == 6
    for row in all_groups:
        assert row["rows"] == row["original_episodes"] == 2
        assert row["known_cash_rows"] == row["unknown_cash_rows"] == 1
        assert row["zero_opportunity_weeks"] == 2
        assert row["total_net_cash"] is None and row["daily_cash_drawdown"] is None
        assert row["unknown_dates"] == ["2026-01-06"]
        assert row["date_uncertainty"]["dates"] == 3
        assert row["absolute_net_concentration"]["top_share"] == 1
    paired = [row for row in report["paired_differences"] if row["dimension"] == "all"]
    assert len(paired) == 7
    assert all(row["joint_known_opportunities"] == row["excluded_unpaired_unknown"] == 1 for row in paired)
    assert all(row["all_opportunity_cash_difference"] is None and row["alternative_minus_baseline_known_cash"] == "0" for row in paired)
    with pytest.raises(ValueError, match="one attempt"):
        evaluation.cash_report((*books, books[0]), (DAY, "2026-01-06"))


def test_probability_and_prediction_identity_fail_closed():
    c = candidate()
    invalid = prediction(c.query, path=(0.2, 0.6, 0.6))
    with pytest.raises(ValueError, match="probability"):
        policy((c,), (invalid,))
    with pytest.raises(ValueError, match="identity"):
        policy((c,), ({**prediction(c.query), "session": "2026-01-06"},))
    with pytest.raises(ValueError, match="Nonfinite"):
        policy((c,), (prediction(c.query, immediate=float("nan")),))


def test_unavailable_holding_has_no_cash_interval_even_on_zero_opportunity_dates():
    c = candidate()
    tape = {(DAY, c.episode.symbol): market()}
    books = evaluation.replay_policy(policy((c,), control="onset"), tape)
    dates = (DAY, "2026-01-06", "2026-01-07")
    unavailable = {(books[0]["model"], books[0]["scope"], "holding"): set(dates)}
    report = evaluation.cash_report(evaluation.supported_cash_rows(books, {"entry": True, "holding": False}),
                                    dates, unavailable=unavailable)
    managed = next(row for row in report["groups"] if row["dimension"] == "all" and row["management"] == "forecast_full_exit")
    assert managed["unknown_dates"] == list(dates) and managed["date_uncertainty"]["dates"] == 0
    assert managed["known_subset_net_cash"] is None
    effects = [row for row in report["paired_differences"] if row["comparison"] == "management"]
    assert effects and all(row["date_uncertainty"]["dates"] == 0 and row["joint_known_opportunities"] == 0 for row in effects)


def test_cpu_float32_probability_roundoff_uses_the_registered_model_tolerance():
    c = candidate()
    result = policy((c,), (prediction(c.query, path=(0.2, 0.6, 0.20000006), immediate=1),))
    assert result.choices[0].action == "immediate"
    with pytest.raises(ValueError, match="probability"):
        policy((c,), (prediction(c.query, path=(0.2, 0.6, 0.20001)),))
