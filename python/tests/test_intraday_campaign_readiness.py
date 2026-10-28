"""Invented metadata fixtures; no actual reports, databases or network."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import pytest

from research import intraday_campaign_readiness as readiness


def reports(sessions=2, targets=20):
    if type(sessions) is not int or sessions < 1 or targets != 20:
        raise ValueError("positive sessions and original twenty targets required")
    kst = timezone(timedelta(hours=9))
    dates = []
    current = date(2025, 9, 4)
    while len(dates) < sessions:
        if current.weekday() < 5:
            dates.append(current.isoformat())
        current += timedelta(days=1)
    if sessions == 257:
        # Invented omissions preserve weeks without asserting KRX holidays.
        span = []
        current = date(2025, 9, 4)
        while current <= date(2026, 9, 23):
            if current.weekday() < 5:
                span.append(current.isoformat())
            current += timedelta(days=1)
        mondays = [day for day in span if date.fromisoformat(day).weekday() == 0]
        omitted = set(mondays[::3][:len(span) - sessions])
        dates = [day for day in span if day not in omitted]
        assert len(dates) == sessions
    symbols = [f"KRX:{index:06d}" for index in range(1, 21)]
    weeks = sorted({(date.fromisoformat(day) - timedelta(days=date.fromisoformat(day).weekday())).isoformat()
                    for day in dates})
    matrix = {}
    for policy in ("D+1", "D+2"):
        matrix[policy] = {"days": [{"session": day,
            "observation_date": (date.fromisoformat(day).replace(day=1) - timedelta(days=1)).isoformat(),
            "available_at": day + "T07:00:00+09:00", "selected_at": day + "T08:00:00+09:00",
            "retrieved_at": {"invented": "2026-10-20T00:00:00+00:00"},
            "source": "invented monthly membership", "is_final": None,
            "targets": [{"symbol": symbol, "rank": rank} for rank, symbol in enumerate(symbols, 1)]}
            for day in dates]}
    reports = []
    for setup in ("EA-A", "EA-B"):
        features = (("relative_volume", "stock_minus_peer_return", "peer_open_to_signal_return", "peer_last15_return")
                    if setup == "EA-A" else ("peer_first30_return", "peer_last15_return"))
        arm_names = (("basic", "volume_only", "market_relative_only", "both")
                     if setup == "EA-A" else ("basic", "market_context"))
        sources = [{"session": day, "symbol": symbol, "input_rows": 0 if index % 8 == 6 else 390,
                    "data_vintage": hashlib.sha256(f"invented-{day}-{symbol}".encode()).hexdigest(),
                    "row_linkage_sha256": hashlib.sha256(f"invented-link-{day}-{symbol}".encode()).hexdigest()}
                   for day in dates for index, symbol in enumerate(symbols)]
        report = {"mode": "fixed-ea-context-exploratory-comparison", "setup_id": setup,
            "reference_sessions": list(dates), "reference_weeks": weeks,
            "source_target_matrix": deepcopy(matrix),
            "upstream_calendar": {"rows": [{"date": day} for day in dates]},
            "sources": sources, "volume_sources": deepcopy(sources) if setup == "EA-A" else [],
            "variants": {policy: {"days": [], "statistics": {"never_read": "-999999999999999"}}
                         for policy in ("D+1", "D+2")}}
        for policy in ("D+1", "D+2"):
            for day_index, day in enumerate(dates):
                opening = datetime.combine(date.fromisoformat(day), datetime.min.time(), kst).replace(
                    hour=10 if day in {"2025-11-13", "2026-01-02"} else 9)
                for index, symbol in enumerate(symbols):
                    kind = index % 8
                    state = "known_no_signal" if kind == 5 else "unknown" if kind == 6 else "signal"
                    end = opening + timedelta(minutes=75)
                    entry = end + timedelta(minutes=5)
                    cap = entry + timedelta(minutes=60)
                    feature_map = {name: {"value": "-999999999999999/17", "reasons": []} for name in features}
                    arms = dict.fromkeys(arm_names, "selected")
                    if kind == 7:
                        for name in features:
                            feature_map[name] = {"value": None, "reasons": ["incomplete_fixed_peer_basket"]}
                        arms.update(dict.fromkeys(arm_names[1:], "unknown"))
                    elif kind == 1:
                        arms.update(dict.fromkeys(arm_names[1:], "rejected"))
                    status = "unknown" if kind in {2, 3} else "declined" if kind == 4 else "known"
                    reason = {1: "delayed_structural_stop", 2: "required_path_missing", 3: "exit_open_missing",
                              4: "invalidated_entry"}.get(kind, "time_cap")
                    exit_at = entry + timedelta(minutes=10) if kind == 1 else cap
                    if kind in {2, 4}:
                        exit_at = None
                    outcome = {"status": status, "reason": reason, "entry_at": entry.isoformat(),
                        "exit_at": exit_at.isoformat() if exit_at else None,
                        "gross": "-999999999999999/17", "primary_net": "-999999999999999/13",
                        "entry_open": "999999999999999", "exit_open": "999999999999997",
                        "structural_stop": "999999999999991"}
                    if state != "signal":
                        feature_map, outcome = {}, None
                        arms = dict.fromkeys(arm_names, "rejected" if state == "known_no_signal" else "unknown")
                    common = state == "signal" and kind != 7
                    source_index = day_index * 20 + index
                    row = {"session": day, "symbol": symbol, "membership_day_index": day_index,
                        "membership_target": deepcopy(matrix[policy]["days"][day_index]["targets"][index]),
                        "expected_volume_reference_dates": dates[max(0, day_index - 20):day_index] if setup == "EA-A" else [],
                        "volume_reference_calendar_complete": day_index >= 20 if setup == "EA-A" else None,
                        "source_index": source_index, "volume_source_index": source_index if setup == "EA-A" else None,
                        "peer_source_indices": {peer: day_index * 20 + p for p, peer in enumerate(symbols) if peer != symbol},
                        "fixed_peers": [peer for peer in symbols if peer != symbol],
                        "signal": {"hypothesis_id": "H1-morning-trend-pullback-reclaim" if setup == "EA-A" else "H3-failed-downside-range-break",
                            "state": state, "reason": ("reclaim" if setup == "EA-A" else "downside_reclaimed") if state == "signal"
                                else "initial_condition_false" if state == "known_no_signal" else "missing_prefix",
                            "decision_at": (end + timedelta(minutes=2)).isoformat(),
                            "observed_through": None if state == "unknown" else end.isoformat(),
                            "signal_bar_start": (end - timedelta(minutes=5)).isoformat() if state == "signal" else None,
                            "signal_bar_end": end.isoformat() if state == "signal" else None,
                            "setup_bar_end": (end - timedelta(minutes=5)).isoformat() if state == "signal" else None},
                        "entry_at": entry.isoformat() if state == "signal" else None,
                        "cap_at": cap.isoformat() if state == "signal" else None,
                        "structural_stop": "999999999999991", "features": feature_map, "arms": arms,
                        "outcome": outcome, "common_context_observable": common,
                        "common_price_observable": common and status == "known"}
                    report["variants"][policy]["days"].append(row)
        reports.append(report)
    return tuple(reports)


def test_projection_preserves_denominator_states_and_excludes_values():
    a, _ = reports()
    before = deepcopy(a)
    projected = readiness.project_report(a, "EA-A")
    assert a == before
    for variant in projected["variants"].values():
        assert variant["counts"] == {"targets": 40, "signals": 32, "known_no_signal": 4,
            "unknown_signals": 4, "empty_sources": 4, "outcome_absent": 8, "outcome_known": 16,
            "outcome_unknown": 12, "entry_declined": 4, "common_context_observable": 28,
            "common_price_observable": 12, "interval_known": 22, "interval_unknown": 6,
            "interval_not_applicable": 12}
        assert variant["feature_counts"]["relative_volume"] == {"observed": 28, "unknown": 4, "not_applicable": 8}
        assert variant["arm_counts"]["volume_only"] == {"selected": 22, "rejected": 10, "unknown": 8}
        assert variant["days"][3]["outcome_status"] == "unknown"
        assert variant["days"][3]["exit_at"] is not None
        assert variant["days"][2]["exit_at"] is None
        assert variant["days"][5]["outcome_status"] == "absent"
        assert variant["days"][4]["outcome_status"] == "declined"
    encoded = json.dumps(projected)
    for forbidden in ("999999999999", '"gross"', '"value"', '"entry_open"', '"structural_stop"', '"statistics"'):
        assert forbidden not in encoded
    assert projected["account_path_proven"] is False


def test_combination_counts_candidates_and_time_intervals_without_account_claim():
    a, b = reports(sessions=1)
    joined = readiness.combine_readiness(readiness.project_report(a, "EA-A"), readiness.project_report(b, "EA-B"))
    assert joined["policy_results_not_pooled"] is True
    assert joined["performance_computed"] is joined["account_path_proven"] is False
    for counts in joined["variants"].values():
        assert counts == {"same_symbol_session_targets": 20, "same_symbol_session_signals": 16,
            "same_symbol_session_simultaneous_entries": 16, "same_symbol_session_known_time_overlap": 11,
            "same_symbol_session_unknown_interval": 3, "known_time_overlap_pairs": 121,
            "simultaneous_entry_pairs": 256, "known_interval_candidates": {"EA-A": 11, "EA-B": 11},
            "unknown_interval_candidates": {"EA-A": 3, "EA-B": 3},
            "entry_declined_candidates": {"EA-A": 2, "EA-B": 2}}


def test_exit_at_other_entry_is_not_overlap_and_timezones_are_normalized():
    a, b = reports(sessions=1)
    for variant in b["variants"].values():
        for row in variant["days"]:
            if row["signal"]["state"] != "signal":
                continue
            for key in ("decision_at", "observed_through", "signal_bar_start", "signal_bar_end", "setup_bar_end"):
                row["signal"][key] = (datetime.fromisoformat(row["signal"][key]) + timedelta(minutes=60)).isoformat()
            for key in ("entry_at", "cap_at"):
                row[key] = (datetime.fromisoformat(row[key]) + timedelta(minutes=60)).isoformat()
            for key in ("entry_at", "exit_at"):
                if row["outcome"][key] is not None:
                    row["outcome"][key] = (datetime.fromisoformat(row["outcome"][key]) + timedelta(minutes=60)).isoformat()
    joined = readiness.combine_readiness(readiness.project_report(a, "EA-A"), readiness.project_report(b, "EA-B"))
    assert joined["variants"]["D+1"]["known_time_overlap_pairs"] == 0
    assert joined["variants"]["D+1"]["simultaneous_entry_pairs"] == 0
    a["variants"]["D+1"]["days"][0]["entry_at"] = "2025-09-04T01:20:00+00:00"
    assert readiness.project_report(a, "EA-A")["variants"]["D+1"]["days"][0]["entry_at"] == "2025-09-04T10:20:00+09:00"


@pytest.mark.parametrize("damage,reason", [
    (lambda r: r.update(setup_id="EA-B"), "unexpected_report_identity"),
    (lambda r: r["variants"].pop("D+2"), "timing_policies_changed"),
    (lambda r: r["variants"]["D+1"]["days"].pop(), "report_target_population_changed"),
    (lambda r: r["variants"]["D+1"]["days"].__setitem__(1, deepcopy(r["variants"]["D+1"]["days"][0])), "duplicate_or_unregistered_target"),
    (lambda r: r["source_target_matrix"]["D+1"]["days"][0]["targets"].pop(), "target_population_changed"),
    (lambda r: r["reference_sessions"].reverse(), "invalid_reference_sessions"),
    (lambda r: r["sources"][0].update(input_rows=True), "invalid_source_row_count"),
    (lambda r: r["sources"][0].update(data_vintage="garbage"), "invalid_source_digest"),
    (lambda r: r["variants"]["D+1"]["days"][0].update(source_index=1), "source_index_identity_changed"),
    (lambda r: r["variants"]["D+1"]["days"][0].update(outcome=None), "signal_outcome_missing"),
    (lambda r: r["variants"]["D+1"]["days"][0]["outcome"].update(status="invalid"), "invalid_outcome_state_or_reason"),
    (lambda r: r["variants"]["D+1"]["days"][0]["outcome"].update(exit_at=None), "invalid_outcome_exit"),
    (lambda r: r["variants"]["D+1"]["days"][2]["outcome"].update(exit_at="2025-09-04T11:20:00+09:00"), "invalid_outcome_exit"),
    (lambda r: r["variants"]["D+1"]["days"][0]["signal"].update(state="invalid"), "invalid_signal_state_or_reason"),
    (lambda r: r["variants"]["D+1"]["days"][0]["signal"].update(decision_at="2025-09-04T10:17:00"), "invalid_timestamp"),
    (lambda r: r["variants"]["D+1"]["days"][0].update(cap_at="2025-09-05T11:20:00+09:00"), "invalid_timestamp"),
    (lambda r: r["variants"]["D+1"]["days"][0]["arms"].update(basic="missing"), "invalid_arm_states"),
    (lambda r: r["variants"]["D+1"]["days"][0]["features"]["relative_volume"].pop("value"), "feature_value_presence_missing"),
    (lambda r: r["variants"]["D+1"]["days"][0].update(common_context_observable=False), "observable_flags_conflict"),
])
def test_malformed_metadata_fails_instead_of_becoming_absence(damage, reason):
    a, _ = reports()
    damage(a)
    with pytest.raises(readiness.ReadinessRefusal, match=reason):
        readiness.project_report(a, "EA-A")


def test_week_without_signals_remains_in_denominator():
    a, _ = reports(sessions=7)
    for variant in a["variants"].values():
        for row in variant["days"]:
            if row["session"] < "2025-09-08":
                continue
            row["signal"].update(state="unknown", reason="missing_prefix", signal_bar_start=None,
                                 signal_bar_end=None, setup_bar_end=None, observed_through=None)
            row.update(entry_at=None, cap_at=None, outcome=None, features={},
                       arms=dict.fromkeys(row["arms"], "unknown"),
                       common_context_observable=False, common_price_observable=False)
    projection = readiness.project_report(a, "EA-A")
    counts = projection["variants"]["D+1"]["by_week"]["2025-09-08"]
    assert counts["targets"] == 100
    assert counts["signals"] == counts["outcome_known"] == 0
    assert counts["unknown_signals"] == 100
