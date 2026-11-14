"""Invented execution ledgers and independent verification regressions."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_event_evaluation as evaluation
from research import intraday_event_features as features
from research.intraday_event_comparison_verify import verify_replay
from research.intraday_event_labels import prepare_query
from research.intraday_event_models import HEADS, Query
from research.intraday_event_scenarios import EventFrame, MinuteBar


DAY = "2026-01-05"
SYMBOL = "KRX:000001"
BINDING = "a" * 64


def market(price=100):
    return evaluation.SessionMarket(100, {
        i: MinuteBar(i, price, price + 1, price - 1, price, i + 3) for i in range(100)}, {}, {})


def frozen_policy(*, price=100, stop=95, scale=2, commit=True, wait=False):
    episode = evaluation.Episode(DAY, SYMBOL, "event", "cluster", "A_DOWNFAIL", 33)
    frame = EventFrame(episode.family, episode.event_id, episode.cluster_id, 30, 30, 33, 27, 31,
                       "at_onset", "WAITING", False, Fraction(scale), Fraction(stop + 1), Fraction(stop), ())
    chart = features.chart_features(frame, market(price).bars, session_length=100)
    query = Query("event@33", DAY, SYMBOL, episode.event_id, episode.cluster_id, 33, frame.family,
                  frame.state, tuple(map(tuple, chart["sequence"])), tuple(chart["static"]))
    candidate = evaluation.Candidate(episode, frame, prepare_query(frame, chart, session_length=100), query)
    predictions = ()
    if wait:
        prediction = held_prediction(query)
        prediction["predictions"].update(remaining_gross=None, net_immediate=0, net_wait3=1)
        predictions = (prediction,)
    return evaluation.freeze_policy((episode,), (candidate,) if commit else (), predictions,
                                    control="model" if wait else "onset",
                                    model="invented", scope="full_chart", prediction_binding=BINDING)


def held_prediction(query):
    return {"key": query.key, "session": query.session, "query_status": "supported_features",
            "predictions": {head: 0 if head == "remaining_gross" else None for head in HEADS},
            "head_status": dict.fromkeys(HEADS, "invented")}


def test_reconstructs_odd_share_reduction_and_catches_ledger_tampering():
    policy = frozen_policy(price=600_000, stop=599_999, scale=1)
    markets = {(DAY, SYMBOL): market(600_000)}
    settings = {"held_predict": held_prediction, "prediction_binding": BINDING}
    ledgers = evaluation.replay_policy(policy, markets, **settings)
    assert verify_replay(policy, markets, reversed(ledgers), **settings) == {
        "episodes": 1, "ledgers": 6, "outcome_counts": {"closed": 6}}
    damaged = deepcopy(ledgers)
    half = next(row for row in damaged if row["management"] == "forecast_half_then_exit")
    half["execution"]["sales"][0]["allocated_cost"] = "0"
    with pytest.raises(ValueError, match="fields.*execution"):
        verify_replay(policy, markets, damaged, **settings)
    damaged = deepcopy(ledgers)
    damaged[0]["held_predictions"][0]["value"] = "-1"
    with pytest.raises(ValueError, match="fields.*held_predictions"):
        verify_replay(policy, markets, damaged, **settings)


def test_no_commit_still_requires_every_arm_and_preserves_null_continuation_value():
    policy = frozen_policy(commit=False)
    ledgers = evaluation.replay_policy(policy, {})
    assert verify_replay(policy, {}, ledgers)["outcome_counts"] == {"no_entry": 6}
    with pytest.raises(ValueError, match="Missing comparison ledger arm"):
        verify_replay(policy, {}, ledgers[:-1])
    with pytest.raises(ValueError, match="Duplicate comparison ledger arm"):
        verify_replay(policy, {}, (*ledgers, ledgers[0]))
    with pytest.raises(ValueError, match="Unexpected comparison ledger arm"):
        verify_replay(policy, {}, (*ledgers, {**ledgers[0], "original_episode_id": "extra"}))
    damaged = deepcopy(ledgers)
    damaged[0]["observation_continuation_value"] = 0
    with pytest.raises(ValueError, match="observation_continuation_value"):
        verify_replay(policy, {}, damaged)


def test_receipt_audit_distinguishes_late_exit_from_known_reference_miss():
    policy = frozen_policy()
    tape = market()
    tape.bars[34] = MinuteBar(34, 101, 102, 99, 100, 37)
    tape.bars[64] = replace(tape.bars[64], available_offset=100)
    markets = {(DAY, SYMBOL): tape}
    ledgers = evaluation.replay_policy(policy, markets)
    assert verify_replay(policy, markets, ledgers)["outcome_counts"] == {"no_fill": 3, "unknown": 3}
    damaged = deepcopy(ledgers)
    unknown = next(row for row in damaged if row["outcome"]["status"] == "unknown")
    unknown["outcome"]["net"] = "0"
    with pytest.raises(ValueError, match="fields.*outcome"):
        verify_replay(policy, markets, damaged)


def test_wait3_checks_receipt_deadline_and_keeps_structural_failure_distinct():
    policy = frozen_policy(wait=True)
    tape = market()
    tape.bars[31] = replace(tape.bars[31], available_offset=37)
    markets = {(DAY, SYMBOL): tape}
    ledgers = evaluation.replay_policy(policy, markets)
    assert verify_replay(policy, markets, ledgers)["outcome_counts"] == {"unknown": 6}
    tape.bars[32] = MinuteBar(32, 100, 101, 94, 94, 35)
    with pytest.raises(ValueError, match="fields.*outcome"):
        verify_replay(policy, markets, ledgers)
    ledgers = evaluation.replay_policy(policy, markets)
    assert verify_replay(policy, markets, ledgers)["outcome_counts"] == {"no_entry": 6}
