"""Fixed invented witness for EL decision and execution branches."""

from fractions import Fraction
from hashlib import sha256

from research import intraday_event_comparison as comparison
from research import intraday_event_evaluation as evaluation
from research import intraday_event_features as features
from research import intraday_event_labels as labels
from research.intraday_event_comparison_verify import verify_replay_day
from research.intraday_event_models import HEADS, Query
from research.intraday_event_scenarios import EventFrame, MinuteBar


DAY = "2026-01-05"
MODEL = "invented-policy-witness-v1"
BINDING = sha256(MODEL.encode()).hexdigest()
EPISODES = ("immediate_hold", "wait_reduce", "abstain")


def _expect(actual, expected, context):
    if actual != expected:
        raise ValueError(f"EL invented policy witness differs: {context}")


def _candidate(episode, market, decision, *, confirmed):
    state = "CONFIRMED" if confirmed else "WAITING"
    frame = EventFrame(episode.family, episode.event_id, episode.cluster_id, 30, decision - 3,
        decision, 27, decision - 2, "terminal" if confirmed else "at_onset", state, confirmed,
        Fraction(1), Fraction(600000), Fraction(599999), ())
    chart = features.chart_features(frame, market.bars, session_length=market.session_length)
    query = Query(f"{episode.event_id}@{decision}", episode.session, episode.symbol, episode.event_id,
        episode.cluster_id, decision, episode.family, state, tuple(map(tuple, chart["sequence"])), tuple(chart["static"]))
    return evaluation.Candidate(episode, frame, labels.prepare_query(frame, chart, session_length=market.session_length), query)


def _prediction(query, **values):
    return {"key": query.key, "session": query.session, "query_status": "supported_features",
            "predictions": {**dict.fromkeys(HEADS), **values}, "head_status": dict.fromkeys(HEADS, "invented")}


def _predict_held(_artifact, queries):
    return [_prediction(q, remaining_gross="-1/100" if q.original_episode_id == "wait_reduce" else "1/100")
            for q in queries]


def verify():
    """Run the production replay and its independent reference on invented bars."""
    price, length = 600000, 100
    market = evaluation.SessionMarket(length,
        {i: MinuteBar(i, price, price + 1, price - 1, price, i + 3) for i in range(length)}, {}, {})
    episodes = tuple(evaluation.Episode(DAY, f"KRX:{i:06d}", event, f"invented-{i}", "A_DOWNFAIL", 33)
                     for i, event in enumerate(EPISODES, start=1))
    markets = {(episode.session, episode.symbol): market for episode in episodes}
    first = tuple(_candidate(episode, market, 33, confirmed=False) for episode in episodes)
    confirmed = tuple(_candidate(episode, market, 36, confirmed=True) for episode in episodes)
    predictions = tuple(_prediction(candidate.query, net_immediate=immediate, net_wait3=wait)
        for candidate, (immediate, wait) in zip((*first, *confirmed),
            (("1/100", "1/200"), ("1/200", "1/100"), (None, "1/100"), (0, 0), (0, 0), (0, 0)), strict=True))
    policies = tuple(evaluation.freeze_policy(episodes, (*first, *confirmed),
        predictions if control == "model" else (), control=control, model=MODEL, scope="full_chart",
        prediction_binding=BINDING) for control in evaluation.CONTROLS)
    books, audits = comparison._replay_days(policies, markets, {"invented": True}, _predict_held, verify_replay_day)

    expected_choices = {
        "onset": (("immediate", 33), ("immediate", 33), ("immediate", 33)),
        "confirmation": (("immediate", 36), ("immediate", 36), ("immediate", 36)),
        "model": (("immediate", 33), ("wait3", 33), ("no_commit", None)),
    }
    model = next(policy for policy in policies if policy.control == "model")
    _expect(tuple((row[0], row[4], row[5]) for row in model.trace), (
        ("immediate_hold", "immediate", "first_positive_paired_value"),
        ("wait_reduce", "wait3", "first_positive_paired_value"),
        ("abstain", "abstain", "missing_paired_value_heads"),
        ("abstain", "observe", "observe_without_commitment"),
    ), "model trace")
    report = {}
    for policy, audit in zip(policies, audits, strict=True):
        control = policy.control
        expected = dict(zip(EPISODES, expected_choices[control], strict=True))
        actual = {c.episode.event_id: (c.action, None if c.candidate is None else c.candidate.frame.decision_offset)
                  for c in policy.choices}
        _expect(actual, expected, f"{control} choices")
        expected_outcomes = {"closed": 12, "no_entry": 6} if control == "model" else {"closed": 18}
        _expect(audit, {"episodes": 3, "ledgers": 18, "outcome_counts": expected_outcomes}, f"{control} audit coverage")
        report[control] = {"choices": {event: list(choice) for event, choice in actual.items()}, "audit": audit}

    _expect(len(books), 54, "total ledger rows")
    for row in books:
        control, event, management = (row[key] for key in ("control", "original_episode_id", "management"))
        action, offset = report[control]["choices"][event]
        execution = row["execution"]
        sales = [(leg["execute_offset"], leg["quantity"], leg["reason"]) for leg in execution["sales"]]
        if action == "no_commit":
            _expect((row["entry_decision_offset"], execution["filled_quantity"], execution["net"], sales),
                    (None, 0, "0", []), "abstention ledger")
            continue
        entry = offset + (4 if action == "wait3" else 1)
        expected_sales = [(entry + 30, 3, "time")]
        if event == "wait_reduce" and management == "forecast_full_exit":
            expected_sales = [(entry + 11, 3, "forecast_full")]
        elif event == "wait_reduce" and management == "forecast_half_then_exit":
            expected_sales = [(entry + 11, 1, "forecast_half"), (entry + 21, 2, "forecast_full")]
        _expect((row["entry_decision_offset"], execution["entry_offset"], execution["filled_quantity"], execution["net"], sales),
                (entry - 1, entry, 3, "-5940", expected_sales), f"{control}/{event}/{management} ledger")
    return {"schema": MODEL, "ledger_rows": 54, "controls": report}
