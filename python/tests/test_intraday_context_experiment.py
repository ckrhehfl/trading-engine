"""Invented CB/CC/CX shapes only. No real archives or runner imports."""

from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import hashlib

import pytest

from research import intraday_context_experiment as eb
from test_intraday_autonomous_experiment import inputs, SYMBOLS


cc = eb.cc
DAYS = ("2025-09-04", "2025-09-11", "2025-09-18")


def rows(day, symbol, setup=None, terminal="112"):
    opening = cc.session_open(day)
    result = [{"symbol": symbol, "interval": "1m",
        "open_time_ms": int((opening + i * cc.MINUTE).timestamp()) * 1000 + 123,
        "open": "100", "high": "100", "low": "100", "close": "100",
        "fetched_at": "2026-10-20T00:00:00+00:00"}
        for i in range(int((cc.session_close(day)-opening)/cc.MINUTE))]
    if setup == "EA-A":
        for row in result[:60]:
            row.update(high="112", low="98", close="110")
        for a, b, op, hi, lo, cl in ((60,65,"109","110","107","109"),
                (65,70,"108","109","107","108"),(70,75,"110","111","108","110")):
            for row in result[a:b]:
                row.update(open=op, high=hi, low=lo, close=cl)
        for row in result[75:]:
            row.update(open="110", high="112", low="109", close="110")
        result[140].update(open=terminal, high=terminal, low=terminal, close=terminal)
    if setup == "EA-B":
        for row in result[:30]:
            row.update(high="110", low="90")
        for a, b, op, hi, lo, cl in ((30,35,"89","90","88","89"),
                (35,40,"90","90","89","90"),(40,45,"91","92","90","91")):
            for row in result[a:b]:
                row.update(open=op, high=hi, low=lo, close=cl)
        for row in result[45:]:
            row.update(open="91", high="93", low="90", close="91")
        result[110].update(open=terminal, high=terminal, low=terminal, close=terminal)
    return result


def fixture(setup="EA-B", days=DAYS, *, missing_peer=False):
    observations = {(day,symbol): rows(day,symbol, setup if symbol == SYMBOLS[0] else None,
        terminal=str(112+i)) for i,day in enumerate(days) for symbol in SYMBOLS}
    if setup == "EA-B":
        for (day,symbol), values in observations.items():
            if symbol != SYMBOLS[0]:
                for row in values[:30]:
                    row.update(low="99", close="99")
    if missing_peer:
        observations[(days[-1], SYMBOLS[-1])].pop(10)
    upstream, prices = inputs(days, observations)
    for variant in upstream["variants"].values():
        for member in variant["days"]:
            day = date.fromisoformat(member["session"])
            member["observation_date"] = (day.replace(day=1) - timedelta(days=1)).isoformat()
            member["available_at"] = day.replace(day=1).isoformat() + "T08:00:00+09:00"
    volumes = [{**{key:deepcopy(chunk[key]) for key in chunk if key != "rows"},
        "daily_rows": [], "daily_retrieved_at": chunk["retrieved_at"],
        "rows": [{"open_time_ms": row["open_time_ms"], "volume": "2" if chunk["session"] == days[-1] else "1",
                  "quote_volume": None, "fetched_at": row["fetched_at"]} for row in chunk["rows"]]}
        for chunk in prices]
    return upstream, prices, volumes


def build(data, setup="EA-B"):
    upstream, prices, volumes = data
    return eb.build_experiment(upstream, iter(prices), iter(volumes) if setup == "EA-A" else None,
        setup_id=setup, inputs_authenticated=True, authorization_reference="invented-authorized-fixture")


def candidate(report, policy="D+1", day=-1):
    return report["variants"][policy]["days"][day*20 if day >= 0 else day*20]


def test_b_common_control_exact_costs_and_full_denominator_without_volume():
    data = fixture()
    original = deepcopy(data)
    report = build(data)
    assert data == original
    assert report["volume_sources"] == []
    assert report["policy_results_not_pooled"]
    for variant in report["variants"].values():
        assert variant["counts"]["targets"] == 60
        assert variant["counts"]["signals"] == 3
        assert variant["counts"]["common_feature_and_price_candidates"] == 3
        assert variant["counts"]["known_no_signal"] == 57
        statistics = variant["statistics"]["basic"]
        assert statistics["gross"]["mean_exact"] == Fraction(22,91)
        assert statistics["net33"]["mean_exact"] == Fraction(22,91)-Fraction(33,10000)
        assert statistics["net43"]["positive_proportion_exact"] == 1
        assert variant["selected_minus_common"]["market_context"]["mean_delta_exact"] == 0
        assert variant["selected_minus_common"]["market_context"]["contrast_interval"] is None
        assert len(variant["by_symbol"]) == 20
        assert variant["calendar_halves"]["second"]["counts"]["targets"] == 0
    assert not report["strategy_pass"] and not report["followup_pass"]


def test_a_does_not_replace_missing_twenty_calendar_dates_with_observations():
    report = build(fixture("EA-A"), "EA-A")
    for variant in report["variants"].values():
        assert variant["counts"]["signals"] == 3
        assert variant["counts"]["context_unknown"] == 3
        assert variant["statistics"]["basic"]["known_labels"] == 0
        assert variant["statistics"]["basic_all"]["known_labels"] == 3
        assert variant["statistics"]["market_relative_only"]["known_labels"] == 0
        assert variant["own_sizing"]["basic"]["status"] == "uninformative"


def test_fixed_full_basket_missing_peer_removes_common_but_not_basic_all():
    report = build(fixture(missing_peer=True))
    row = candidate(report)
    assert len(row["fixed_peers"]) == 19
    assert row["arms"]["market_context"] == "unknown"
    assert not row["common_context_observable"]
    assert "incomplete_fixed_peer_basket" in row["features"]["peer_first30_return"]["reasons"]
    variant = report["variants"]["D+1"]
    assert variant["statistics"]["basic"]["known_labels"] == 2
    assert variant["statistics"]["basic_all"]["known_labels"] == 3


def test_unknown_outcomes_and_declines_are_distinct_from_feature_observability():
    data = fixture()
    data[1][0]["rows"].pop(60)
    data[1][20]["rows"][50].update(open="87", high="87",low="87",close="87")
    report = build(data)
    variant = report["variants"]["D+1"]
    assert variant["counts"]["signals"] == 3
    assert variant["counts"]["unknown_labels"] == 1
    assert variant["counts"]["entry_declined"] == 1
    assert variant["counts"]["context_unknown"] == 0
    assert variant["statistics"]["basic"]["known_labels"] == 1
    assert variant["arm_counts"]["market_context"]["selected_predecline"] == 3
    assert variant["arm_counts"]["market_context"]["selected_unknown_price"] == 1
    assert variant["arm_counts"]["market_context"]["selected_entry_declined"] == 1


def test_every_own_sizing_is_computed_before_any_reported_mean(monkeypatch):
    events = []
    original_plan, original_statistics = eb._own_sizing, eb._statistics
    def plan(*args):
        events.append("plan")
        assert "mean" not in events
        return original_plan(*args)
    def statistics(*args):
        assert events.count("plan") == 6
        events.append("mean")
        return original_statistics(*args)
    monkeypatch.setattr(eb, "_own_sizing", plan)
    monkeypatch.setattr(eb, "_statistics", statistics)
    build(fixture())
    assert events == ["plan"]*6+["mean"]*6


def test_original_raw_timestamp_fetched_at_order_is_bound_by_digest():
    data = fixture()
    rows0 = data[1][0]["rows"]
    rows0[15]["fetched_at"] = None
    report = build(data)
    source = report["sources"][0]
    linkage = [[r["open_time_ms"],r["fetched_at"]] for r in rows0]
    assert source["row_linkage_sha256"] == hashlib.sha256(cc._canonical(linkage)).hexdigest()
    assert source["row_linkage_sha256"] != hashlib.sha256(cc._canonical(list(reversed(linkage)))).hexdigest()
    assert source["source_row_metadata_location"] == "original_pinned_archive_chunk"


def test_twenty_prior_first60_windows_not_old15_or_same_wall_clock():
    days = tuple((date(2025,12,13)+timedelta(days=i)).isoformat() for i in range(21))
    data = fixture("EA-A", days)
    report = build(data,"EA-A")
    row = candidate(report)
    assert row["features"]["relative_volume"]["value"] == 2
    assert row["arms"]["both"] == "selected"
    assert row["common_price_observable"]
    # Exceptional Jan 2 opening is 10:00; each historical date uses its own open.
    assert cc.session_open(days[-1]).hour == 10
    data[2][0]["rows"][40]["volume"] = None
    report2 = build(data,"EA-A")
    row2 = candidate(report2)
    assert row2["features"]["relative_volume"]["value"] is None
    assert row2["arms"]["both"] == "unknown"
    assert row2["expected_volume_reference_dates"] == list(days[:-1])


@pytest.mark.parametrize("kind", ["price_missing", "price_extra", "volume_missing", "volume_extra", "reordered"])
def test_chunk_order_and_population_fail_closed(kind):
    data = fixture("EA-A")
    if kind == "price_missing": data[1].pop()
    if kind == "price_extra": data[1].append(deepcopy(data[1][-1]))
    if kind == "volume_missing": data[2].pop()
    if kind == "volume_extra": data[2].append(deepcopy(data[2][-1]))
    if kind == "reordered": data[1][0],data[1][1] = data[1][1],data[1][0]
    with pytest.raises((eb.ExperimentRefusal, eb.cx.ReadinessRefusal)):
        build(data,"EA-A")


def test_zero_and_empty_dispersion_are_uninformative():
    records = [{"session":day,"label":Fraction(1,10)} for day in DAYS]
    plan = eb._own_sizing(records,list(DAYS))
    assert not plan["sizing_pass"] and plan["status"] == "uninformative"
    assert eb._statistics(records,plan)["gross"]["interval95"] is None
    empty = eb._own_sizing([],list(DAYS))
    assert empty["status"] == "uninformative"
    assert eb._statistics([],empty)["gross"]["mean_exact"] is None


def test_underpower_is_disclosed_and_does_not_become_pass():
    records = [{"session":day,"label":Fraction(i-1,2)} for i,day in enumerate(DAYS)]
    plan = eb._own_sizing(records,list(DAYS))
    assert plan["status"] == "underpowered" and not plan["sizing_pass"]
    assert plan["underpowered_descriptive_authorized"]
    assert eb._statistics(records,plan)["gross"]["interval95"] is not None


@pytest.mark.parametrize("setup,volume,authenticated", [("EA-C",None,True),("EA-A",None,True),("EA-B",[],True),("EA-B",None,False)])
def test_scope_and_authentication_acknowledgement_required(setup,volume,authenticated):
    data = fixture()
    with pytest.raises(eb.ExperimentRefusal):
        eb.build_experiment(data[0],data[1],volume,setup_id=setup,
            inputs_authenticated=authenticated,authorization_reference="invented")


@pytest.mark.parametrize("kind", ["exit", "declined", "held", "duplicate_exit"])
def test_unique_raw_open_survives_bad_later_hlc_but_held_ohlc_and_conflicts_do_not(kind):
    data = fixture()
    values = data[1][0]["rows"]
    if kind == "declined":
        values[50].update(open="87",high=None,low=None,close=None)
    elif kind == "held":
        values[60].update(high=None,low=None,close=None)
    else:
        values[110].update(high=None,low=None,close=None)
        if kind == "duplicate_exit":
            values.insert(111,deepcopy(values[110]))
    report = build(data)
    row = candidate(report,day=0)
    assert row["signal"]["state"] == "signal"
    assert row["outcome"]["status"] == {"exit":"known","declined":"declined",
        "held":"unknown","duplicate_exit":"unknown"}[kind]


def test_feature_rejected_candidates_still_belong_to_common_basic():
    data = fixture()
    for chunk in data[1][-20:]:
        if chunk["symbol"] != SYMBOLS[0]:
            for row in chunk["rows"][:30]:
                row.update(low="100", close="100")
    report = build(data)
    row = candidate(report)
    assert row["arms"]["market_context"] == "rejected"
    assert row["common_context_observable"] and row["common_price_observable"]
    variant = report["variants"]["D+1"]
    assert variant["statistics"]["basic"]["known_labels"] == 3
    assert variant["statistics"]["market_context"]["known_labels"] == 2
    assert variant["selected_minus_common"]["market_context"]["mean_delta_exact"] == Fraction(-1,182)


def test_missing_calendar_coordinate_stays_unknown_without_older_substitution():
    data = fixture("EA-A")
    first = date.fromisoformat(DAYS[0])
    calendar = [(first-timedelta(days=n)).isoformat() for n in range(20,0,-1)]+list(DAYS)
    data[0]["calendar"]["rows"] = [{"date": d} for d in calendar]
    # _target_matrix only accepts dates within its registered window, so earlier
    # dates supply the baseline calendar but no unauthorized archive coordinates.
    report = build(data,"EA-A")
    row = candidate(report,day=0)
    assert row["volume_reference_calendar_complete"]
    assert row["features"]["relative_volume"]["value"] is None
    assert "missing_volume_reference" in row["features"]["relative_volume"]["reasons"]


@pytest.mark.parametrize("mutation", ["timestamp", "retrieval", "duplicate"])
def test_price_volume_linkage_preserves_timestamps_retrieval_and_multiplicity(mutation):
    data = fixture("EA-A")
    raw = data[2][0]["rows"]
    if mutation == "timestamp": raw[0]["open_time_ms"] += 1
    if mutation == "retrieval": raw[0]["fetched_at"] = None
    if mutation == "duplicate": raw.append(deepcopy(raw[0]))
    with pytest.raises(eb.ExperimentRefusal,match="price_volume_raw_timestamp_retrieval_linkage_changed"):
        build(data,"EA-A")


def test_peer_windows_are_aggregated_once_across_both_policies(monkeypatch):
    original, calls = eb._window, []
    def counted(session, source, peer, start, end):
        calls.append((peer,start,end))
        return original(session,source,peer,start,end)
    monkeypatch.setattr(eb,"_window",counted)
    report = build(fixture())
    assert len(calls) == len(set(calls)) == len(DAYS)*19*2
    assert report["variants"]["D+1"]["statistics"] == report["variants"]["D+2"]["statistics"]


def test_volume_memoization_preserves_the_entire_comparison_report(monkeypatch):
    days = tuple((date(2025, 9, 4) + timedelta(days=i)).isoformat() for i in range(22))
    data = fixture("EA-A", days)
    cached = build(data, "EA-A")
    def uncached(self, window, symbol, day, opening, decision):
        return eb.ea._volume_total(window, symbol, day, opening, decision)
    monkeypatch.setattr(eb.ea.VolumeValidationCache, "total", uncached)
    assert build(data, "EA-A") == cached
