"""Invented EN target, causal selection and nominal cash reconciliation witnesses."""

from dataclasses import asdict
from fractions import Fraction

import pytest

from research import intraday_event_evaluation as evaluation
from research import intraday_event_value_audit as audit
from research.intraday_event_labels import LabelQuery, build_targets
from research.intraday_event_models import HEADS, Query
from research.intraday_event_scenarios import EventFrame, MinuteBar


def plain(value):
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def event(name="E", day="2024-01-02", symbol="INVENTED"):
    return {"event_id": name, "session": day, "symbol": symbol, "cluster_id": day + ":" + symbol,
            "onset_decision_offset": 10, "terminal_state": "CONFIRMED", "confirmation_key": name + "@15",
            "confirmation_decision_offset": 15, "confirmation_reference": "100"}


def wrapper(e, clock=10, state="WAITING_PULLBACK", *, chart=True, peer=True, reference=100, scale=2, length=100):
    terminal = state in ("CONFIRMED", "INVALIDATED", "SESSION_END")
    frame = EventFrame("C_FIRSTPULLBACK", e["event_id"], e["cluster_id"], 9, clock - 1, clock,
                       9, clock, "terminal" if terminal else "at_onset" if clock == 10 else "periodic",
                       state, terminal, Fraction(scale), Fraction(100), Fraction(98), ())
    label_query = LabelQuery(e["event_id"], frame.family, state, terminal, 9, clock - 1, clock,
                            clock - 1 if chart else None, Fraction(reference) if chart else None,
                            Fraction(scale), Fraction(98), length)
    q = None if not chart else Query(e["event_id"] + "@" + str(clock), e["session"], e["symbol"],
        e["event_id"], e["cluster_id"], clock, frame.family, state,
        tuple((0,) * 13 for _ in range(64)), (0,) * 16, (0, 0, 0) if peer else None,
        original_episode_id=e["event_id"])
    return {"kind": "query", "query_kind": "entry", "key": e["event_id"] + "@" + str(clock),
            "session": e["session"], "symbol": e["symbol"], "continuous_length": length,
            "frame": frame.as_dict(), "label_query": plain(asdict(label_query)),
            "query": None if q is None else plain(asdict(q)), "peer_support": None, "origin": None,
            "query_status": "supported_chart" if chart else "unknown_chart",
            "reference_close": str(reference) if chart else None}


def prediction(w, qi=0.01, qw=0.005):
    values = dict.fromkeys(HEADS)
    values.update(net_immediate=qi, net_wait3=qw)
    return {"key": w["key"], "session": w["session"], "query_status": "supported_features",
            "predictions": values, "head_status": dict.fromkeys(HEADS, "trained_tcn")}


def label(w, bars=None):
    raw = w["label_query"]
    q = LabelQuery(**{**raw, **{name: None if raw[name] is None else Fraction(raw[name])
                              for name in ("reference_close", "scale", "structure_stop")}})
    if bars is None:
        bars = {i: MinuteBar(i, 100, 100, 100, 100, i + 1) for i in range(100)}
    targets = build_targets(q, bars)
    admitted = targets.entry_admissibility == "admissible"
    known = {"path_class": targets.path.value if targets.path.matured_offset is not None else None,
             "fill": targets.immediate.fill if admitted and targets.immediate.fill_matured_offset is not None else None,
             "net_immediate": targets.immediate.value if admitted and targets.immediate.matured_offset is not None else None,
             "net_wait3": targets.wait3.value if admitted and targets.wait3.matured_offset is not None else None,
             "remaining_gross": None}
    return plain({"kind": "labels", "query_kind": "entry", "key": w["key"], "session": w["session"],
                  "symbol": w["symbol"], "targets": asdict(targets),
                  "training_labels": None if w["query"] is None else {"key": w["key"], **known,
                      "matured_session": w["session"] if any(v is not None for v in known.values()) else None}})


def measured(e=None, **kw):
    e = event() if e is None else e
    w = wrapper(e, **kw)
    causal = audit.causal_query(e, w, prediction(w) if w["query"] and w["query"]["peer"] is not None else None, 0)
    choice = audit.select_event(e, [causal])
    return e, w, causal, choice, audit.measure_query(causal, label(w), choice)


def episode(e=None, *, qi=0.01, qw=0.005, bars=None, head_statuses=None):
    e = event() if e is None else e
    bars = bars or {i: MinuteBar(i, 100, 100, 100, 100, i + 1) for i in range(100)}
    wrappers = [wrapper(e), wrapper(e, 15, "CONFIRMED")]
    predictions = [prediction(w, qi, qw) for w in wrappers]
    causal = [audit.causal_query(e, w, p, 0) for w, p in zip(wrappers, predictions)]
    choice = audit.select_event(e, causal)
    measured_rows = [audit.measure_query(c, label(w, bars), choice) for c, w in zip(causal, wrappers)]
    original = evaluation.Episode(e["session"], e["symbol"], e["event_id"], e["cluster_id"], "C_FIRSTPULLBACK", 10)
    candidates = []
    for w in wrappers:
        f, q, lq = w["frame"], w["query"], w["label_query"]
        frame = EventFrame(**{**f, "conditions": (), **{k: Fraction(f[k]) for k in ("scale", "level", "structure")}})
        causal_label = LabelQuery(**{**lq, **{k: Fraction(lq[k]) for k in ("reference_close", "scale", "structure_stop")}})
        candidates.append(evaluation.Candidate(original, frame, causal_label, Query(**q)))
    markets = {(e["session"], e["symbol"]): evaluation.SessionMarket(100, bars, {}, {})}
    ledgers = {}
    for control in ("model", "confirmation"):
        policy = evaluation.freeze_policy([original], candidates, predictions, control=control,
            model="tcn", scope="common_chart_peer", prediction_binding="a" * 64)
        if control == "model":
            assert plain(policy.trace) == choice["trace"]
        for row in evaluation.replay_policy(policy, markets):
            arm = tuple(row[k] for k in ("control", "entry_policy", "management"))
            if arm in audit.ARM_KEYS:
                ledgers[arm] = row
    statuses = head_statuses or dict.fromkeys(HEADS, "trained_tcn")
    record = audit.measure_episode(e, choice, {r["key"]: r for r in measured_rows}, ledgers, statuses, 0)
    return e, measured_rows, choice, ledgers, record


def test_terminal_operational_zero_is_not_a_training_target():
    _, w, _, choice, row = measured(clock=15, state="INVALIDATED")
    assert label(w)["targets"]["immediate"]["net_cash"] == "0"
    assert choice["action"] == "no_commit"
    assert row["heads"]["net_immediate"]["target_state"] == "inapplicable"
    assert row["heads"]["net_immediate"]["target"] is None
    assert not row["paired"]["scored"]


def test_first_retained_frame_is_not_automatically_onset_and_clocks_are_fixed():
    e = event()
    for clock, phase, clock_bin in ((13, "waiting_pullback", "<60"), (60, "pullback", "60_to_180"),
                                    (180, "invalid_or_terminal", "180_plus")):
        w = wrapper(e, clock, "PULLBACK" if clock == 60 else "SESSION_END" if clock == 180 else "WAITING_PULLBACK", length=300)
        row = audit.causal_query(e, w, prediction(w), 0)
        assert (row["phase"], row["clock_bin"]) == (phase, clock_bin)
        assert "sequence" not in str(row)


def test_unknown_chart_peer_and_missing_paired_heads_abstain_without_removal():
    e = event()
    wrappers = [wrapper(e, 10, chart=False), wrapper(e, 11, peer=False), wrapper(e, 12), wrapper(e, 13)]
    rows = [audit.causal_query(e, w, None if i < 2 else prediction(w, None, 1) if i == 2 else prediction(w, -1, 0), 0)
            for i, w in enumerate(wrappers)]
    choice = audit.select_event(e, rows)
    assert choice["scope_frames"] == 2
    assert [t[2] for t in choice["trace"]] == [12, 13]
    assert choice["trace"][0][5] == "missing_paired_value_heads"
    assert choice["action"] == "no_commit"
    measured_rows = [audit.measure_query(r, label(w), choice) for r, w in zip(rows, wrappers)]
    assert len(measured_rows) == 4
    assert measured_rows[0]["heads"]["net_immediate"]["target_state"] == "unknown_chart"
    with pytest.raises(ValueError, match="common scope"):
        audit.causal_query(e, wrappers[1], prediction(wrappers[1]), 0)


def test_first_positive_tie_commits_once_and_duplicate_clocks_fail():
    e = event()
    rows = []
    for clock, qi, qw in ((10, -1, -1), (13, 0.01, 0.01), (16, 999, 0)):
        w = wrapper(e, clock)
        rows.append(audit.causal_query(e, w, prediction(w, qi, qw), 0))
    choice = audit.select_event(e, reversed(rows))
    assert (choice["key"], choice["action"], len(choice["trace"])) == ("E@13", "immediate", 2)
    assert choice["selected_prediction"] == "1/100"
    with pytest.raises(ValueError, match="Duplicate"):
        audit.select_event(e, [rows[0], rows[0]])


def test_wait_three_keeps_original_denominator_after_reference_changes():
    e, w, causal, choice, _ = measured()
    bars = {i: MinuteBar(i, 100, 100, 100, 100, i + 1) for i in range(100)}
    bars[12] = MinuteBar(12, 101, 101, 101, 101, 13)
    saved = label(w, bars)
    wait = saved["targets"]["wait3"]
    assert wait["reference_close"] == "101"
    assert wait["denominator"] == causal["initial_denominator"]
    assert Fraction(wait["requested_quantity"]) * Fraction(wait["guard_cap"]) != Fraction(wait["denominator"])
    result = audit.measure_query(causal, saved, choice)
    assert Fraction(result["heads"]["net_wait3"]["target"]) == Fraction(wait["net_cash"]) / Fraction(wait["denominator"])
    saved["targets"]["wait3"]["denominator"] = "1"
    with pytest.raises(ValueError, match="denominator"):
        audit.measure_query(causal, saved, choice)


def test_zero_requested_notional_preserves_saved_zero_but_does_not_score_zero_over_zero():
    e = event()
    w = wrapper(e, reference=10_000_000, scale=1)
    bars = {i: MinuteBar(i, 10_000_000, 10_000_000, 10_000_000, 10_000_000, i + 1) for i in range(100)}
    causal = audit.causal_query(e, w, prediction(w), 0)
    choice = audit.select_event(e, [causal])
    row = audit.measure_query(causal, label(w, bars), choice)
    assert causal["initial_denominator"] == "0"
    assert row["heads"]["net_immediate"]["training_target"] == "0"
    assert row["heads"]["net_immediate"]["target_state"] == "undefined_zero_denominator"
    assert not row["paired"]["scored"]


def test_individual_head_support_is_separate_from_joint_known():
    _, w, causal, choice, _ = measured()
    bars = {i: MinuteBar(i, 100, 100, 100, 100, i + 1) for i in range(100)}
    del bars[44]  # Immediate expiry is 41; delayed expiry is 44.
    row = audit.measure_query(causal, label(w, bars), choice)
    assert row["heads"]["net_immediate"]["scored"]
    assert not row["heads"]["net_wait3"]["scored"]
    assert not row["paired"]["scored"]


def test_immaturity_overrides_closed_execution_and_unavailable_model_preserves_roster():
    e, rows, choice, ledgers, _ = episode()
    for arm in audit.ARM_KEYS[:3]:
        ledgers[arm]["outcome"].update(status="unknown", reason="outcome_not_mature_in_session", net=None, gross=None)
    result = audit.measure_episode(e, choice, {r["key"]: r for r in rows}, ledgers, dict.fromkeys(HEADS, "trained_tcn"), 0)
    assert result["arms"][0]["fill_state"] == "filled"
    assert result["arms"][0]["cash_state"] == "unknown"
    assert result["decomposition"]["status"] == "unknown"
    statuses = dict.fromkeys(HEADS, "unsupported")
    result = audit.measure_episode(e, choice, {r["key"]: r for r in rows}, ledgers, statuses, 0)
    assert all(not b["available"] and b["fill_state"] == "unavailable" for b in result["arms"])


def test_exact_residual_identity_retains_numeric_label_versus_cap_gap():
    e, rows, choice, ledgers, record = episode()
    d = record["decomposition"]
    assert Fraction(d["total_residual"]) == sum(Fraction(d[k]) for k in ("cap_residual", "entry_effect", "holding_effect"))
    assert record["reconciliation"]["status"] == "matched"
    rows[0]["heads"]["net_immediate"]["net_cash"] = "7"
    rows[0]["heads"]["net_immediate"]["target"] = str(Fraction(7) / Fraction(choice["initial_denominator"]))
    record = audit.measure_episode(e, choice, {r["key"]: r for r in rows}, ledgers, dict.fromkeys(HEADS, "trained_tcn"), 0)
    assert record["reconciliation"]["status"] == "cash_mismatch"
    d = record["decomposition"]
    assert Fraction(d["total_residual"]) == sum(Fraction(d[k]) for k in ("label_error", "cap_minus_label_cash", "entry_effect", "holding_effect"))
    assert d["cap_minus_label_cash"] != "0"


def test_wait_commit_cancellation_has_no_execution_clock_but_retains_choice():
    bars = {i: MinuteBar(i, 100, 100, 100, 100, i + 1) for i in range(100)}
    bars[11] = MinuteBar(11, 97, 97, 97, 97, 12)
    _, _, choice, ledgers, record = episode(qi=0.005, qw=0.01, bars=bars)
    assert choice["action"] == "wait3"
    assert ledgers[audit.ARM_KEYS[0]]["entry_decision_offset"] is None
    assert record["arms"][0]["committed"]
    assert record["arms"][0]["cash_state"] == "known_zero"


def test_no_commit_is_operational_zero_without_a_forecast_error():
    _, rows, _, _, record = episode(qi=-1, qw=0)
    assert record["decomposition"]["status"] == "no_commit"
    assert all(not b["committed"] and b["cash_state"] == "known_zero" for b in record["arms"][:3])
    assert not any(r["selected"] for r in rows)


def test_hierarchical_weights_do_not_count_repeated_frames_or_names_as_dates():
    rows = []
    for e, repeats, value in ((event("A"), 4, 1), (event("B"), 1, 3),
                              (event("C", symbol="SECOND"), 1, 10),
                              (event("D", day="2024-01-03"), 1, 100)):
        for i in range(repeats):
            row = measured(e, clock=10 + i)[4]
            row["heads"]["net_immediate"].update(prediction=str(value), target="0", scored=True)
            rows.append(row)
    # day1: average(cluster1: average(episodeA=1, episodeB=3), cluster2=10)=6
    # day2: 100. Equal-date answer is 53, not a frame average.
    result = audit._head_summary(rows, "net_immediate")
    assert result["prediction_mean"] == 53
    assert result["scored"] == {"frames": 7, "episodes": 4, "clusters": 3, "dates": 2}


def test_calendar_zero_weeks_fixed_bins_and_duplicate_missing_keys():
    e, rows, _, _, record = episode()
    calendar = [e["session"], "2024-01-15"]
    summary = audit.summarize(calendar, rows, [record], {"0": {"status": "invented"}})
    name = "|".join(audit.ARM_KEYS[0])
    assert summary["calendar_weeks"] == 2
    assert summary["weekly"]["2024-W03"][name]["fills"] == 0
    assert summary["arms"][name]["fills_per_calendar_week"] == "1/2"
    assert summary["arms"][name]["wins"] == 0
    assert summary["prediction_bins"]["all"]["net_immediate"]["above_33bp"]["population"]["frames"] == 2
    for bad_rows, bad_episodes in ((rows + rows[:1], [record]), (rows[1:], [record]), (rows, [record, record])):
        with pytest.raises(ValueError):
            audit.summarize(calendar, bad_rows, bad_episodes, {})


@pytest.mark.parametrize("mutation", ["key", "clock", "family", "target", "training", "cash", "choice", "entry", "frozen_prediction"])
def test_identity_target_and_cash_tampering_fail_closed(mutation):
    e, w, causal, choice, _ = measured()
    if mutation in ("key", "clock", "family"):
        if mutation == "key":
            w["key"] = "elsewhere"
        elif mutation == "clock":
            w["label_query"]["decision_offset"] += 1
        else:
            w["frame"]["family"] = "A_DOWNFAIL"
        with pytest.raises(ValueError):
            audit.causal_query(e, w, prediction(w), 0)
    elif mutation in ("target", "training"):
        saved = label(w)
        if mutation == "target":
            saved["targets"]["immediate"]["value"] = "1"
        else:
            saved["training_labels"]["net_immediate"] = "1"
        with pytest.raises(ValueError):
            audit.measure_query(causal, saved, choice)
    else:
        e, rows, choice, ledgers, _ = episode()
        if mutation == "cash":
            ledgers[audit.ARM_KEYS[0]]["outcome"]["net"] = "999"
        elif mutation == "choice":
            ledgers[audit.ARM_KEYS[0]]["candidate_key"] = "other"
        elif mutation == "frozen_prediction":
            choice["selected_prediction"] = "999"
        else:
            ledgers[audit.ARM_KEYS[2]]["execution"]["guard_cap"] = "101"
        with pytest.raises(ValueError):
            audit.measure_episode(e, choice, {r["key"]: r for r in rows}, ledgers, dict.fromkeys(HEADS, "trained_tcn"), 0)


def test_nonfinite_prediction_and_float_cash_are_rejected():
    e, w, causal, choice, _ = measured()
    with pytest.raises(ValueError, match="Nonfinite"):
        audit.causal_query(e, w, prediction(w, float("nan")), 0)
    saved = label(w)
    saved["targets"]["immediate"]["net_cash"] = 0.0
    with pytest.raises(ValueError, match="Exact amount"):
        audit.measure_query(causal, saved, choice)
