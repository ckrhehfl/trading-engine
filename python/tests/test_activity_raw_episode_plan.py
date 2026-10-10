"""Fabricated calendars/origins only; no saved financial metadata or IO."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_raw_episode_plan as module
from research.activity_raw_quote_view import calendar_sha256

PIN = "a" * 64


def dates(start, count):
    return [(date.fromisoformat(start) + timedelta(days=index)).isoformat() for index in range(count)]


def metadata():
    # Intentionally synthetic weekdays, then a gap before the retained endpoint.
    all_days = dates("2019-01-02", (date(2026, 9, 18) - date(2019, 1, 2)).days + 1)
    weekdays = [day for day in all_days if date.fromisoformat(day).weekday() < 5]
    calendar = weekdays[:1895] + ["2026-09-18", "2026-09-22", "2026-09-23"]
    origins = []
    for arm, entry, due in (("D1", "2021-04-16", "2021-10-21"), ("D2", "2021-04-19", "2021-10-22")):
        origins.append(dict(formation_on="2021-04-15", arm=arm, code="003550", isin="KR7003550001", entry_on=entry,
            entry_state="observed", due_on=due, due_state="observed", candidate_on=due, candidate_state="observed",
            end_on=due, original_end_on="2026-09-18", state="price_candidate_if_filled",
            mode="potential_origin_original_horizon", reason="price_candidate_if_filled", apr28_original_horizon_eligible=True))
    return dict(schema="bq-raw-episode-scope-review-v1", status="planning_metadata_only", projection_sha256=PIN,
        exit_scope_sha256="b" * 64, projection_bytes=100, exit_scope_bytes=200, event_reference_on="2021-04-28",
        eligibility_definition="entry_on <= 2021-04-28 <= original_end_on; date geometry only, not book/event eligibility",
        potential_origins=origins, apr28_groups=dict(eligible=module._bounds(origins), ineligible=module._bounds([])),
        calendar_dates=calendar, calendar_summary=dict(count=1898, first=calendar[0], last=calendar[-1]),
        activity_snapshot_sha256="c" * 64, original_quote_snapshot_sha256="d" * 64, original_read_scope_sha256="e" * 64,
        composite_quote_snapshot_sha256="f" * 64, content_review_completed=False, calendar_transported=True, origin_count=2,
        **dict.fromkeys(module.FALSE_FLAGS, False), **dict.fromkeys(module.ZERO_FIELDS, 0))


def test_complete_price_free_plan_uses_all_origins_and_known_kospi_only_without_io(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("plan attempted IO")
    for owner, name in ((Path, "open"), (socket, "create_connection"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    source = metadata(); before = deepcopy(source)
    plans = module.build_raw_episode_plan(source, scope_review_sha256=PIN)
    assert source == before
    unit, plan = plans["unit_plan"], plans["acquisition_plan"]
    assert set(unit) == {"schema", "activity_snapshot_sha256", "base_quote_snapshot_sha256", "calendar_sha256", "episodes", *module.UNITS}
    assert unit["schema"] == "activity-raw-episode-unit-plan-v1" and unit["base_quote_snapshot_sha256"] == "f" * 64
    assert unit["calendar_sha256"] == calendar_sha256(tuple(map(date.fromisoformat, source["calendar_dates"])))
    assert unit["episodes"] == [dict(code="003550", start="2021-04-16", end="2021-10-22"), dict(code="383800", start="2021-05-27", end="2021-10-22")]
    assert plan["scope_review_sha256"] == PIN and plan["original_end_on"] == "2026-09-18"
    assert source["calendar_dates"][-1] == "2026-09-23" and source["calendar_dates"][-3] == plan["original_end_on"]
    assert unit["calendar_sha256"] != calendar_sha256(tuple(map(date.fromisoformat, source["calendar_dates"][:-2])))
    assert plan["potential_origins"] == source["potential_origins"] and plan["potential_origins"] is not source["potential_origins"]
    assert plan["issue_identities"] == [dict(code=code, market="KOSPI", service="stk_bydd_trd") for code in ("003550", "383800")]
    expected = {code: [day for day in source["calendar_dates"] if start <= day <= end] for code, start, end in module.EPISODES}
    assert plan["requested_code_dates"] == expected and plan["unique_days"] == expected["003550"]
    matrix = plan["request_matrix"]
    assert all(row["service"] == "stk_bydd_trd" and set(row) == {"service", "bas_dd", "targets"} for row in matrix)
    assert all(row["targets"] == (["003550"] if row["bas_dd"] < "20210527" else ["003550", "383800"]) for row in matrix)
    cached = plan["cached_requests"]; new = plan["new_request_matrix"]
    assert {row["bas_dd"] for row in cached}.isdisjoint(row["bas_dd"] for row in new)
    assert sorted(row["bas_dd"] for row in cached + new) == [row["bas_dd"] for row in matrix]
    assert len(plan["cache_geometry"]["cached_dates"]) == 854
    counts = plan["counts"]
    assert counts["raw_code_dates"] == sum(map(len, expected.values())) == counts["cached_code_dates"] + counts["new_code_dates"]
    assert counts["raw_unique_days"] == len(matrix) == counts["cached_responses"] + counts["new_requests"]
    bp = {row["bas_dd"]: row for row in cached if row["role"] == "bp_raw"}
    assert set(bp) == {"20210428", "20210527"} and counts["bp_raw_responses"] == 2
    for day, (number, pin) in module.BP_RESPONSES.items():
        assert bp[day]["response_sha256"] == pin and bp[day]["filename"] == f"response-{number:02d}-stk_bydd_trd-{day}.json"
    assert all(row["response_sha256"] is None and row["response_pin_requires_receipt"] for row in cached if row["role"] != "bp_raw")
    assert plan["episode_end_is_actual_exit"] is plan["original_end_obligation_discharged"] is plan["cache_bodies_verified"] is False
    assert all(plan[key] is False for key in module.FALSE_FLAGS) and all(type(plan[key]) is int and plan[key] == 0 for key in module.ZERO_FIELDS)
    assert "prices" not in json.dumps(plans) and "returns" not in plan["counts"]
    assert hashlib.sha256(json.dumps(plans, sort_keys=True).encode()).hexdigest() == hashlib.sha256(json.dumps(module.build_raw_episode_plan(deepcopy(source), scope_review_sha256=PIN), sort_keys=True).encode()).hexdigest()


def test_geometry_uses_preceding_sessions_excludes_formation_and_unions_overlaps():
    calendar = dates("2021-01-01", 16)
    geometry = module.cache_geometry(calendar, ("20210105", "20210108", "20210113"), 4)
    assert geometry["history_windows"] == {"20210105": ["20210101", "20210102", "20210103", "20210104"],
        "20210108": ["20210104", "20210105", "20210106", "20210107"], "20210113": ["20210109", "20210110", "20210111", "20210112"]}
    history = sorted({day for values in geometry["history_windows"].values() for day in values})
    assert geometry["cached_dates"] == sorted(set(history) | {"20210105", "20210108", "20210113"})
    assert len(geometry["aq_matrix"]) == 12 and len(geometry["ar_matrix"]) == 2 * len(history)
    selected = module.cached_request("20210105", geometry)
    assert selected["role"] == "aq" and selected["logical_number"] == 1
    assert module.cached_request("20210108", geometry)["filename"] == "response-05-stk_bydd_trd-20210108.json"


def test_ar_original_and_recovery_ordinals_keep_global_filenames():
    calendar = dates("2020-01-01", 701)
    formations = tuple(calendar[index].replace("-", "") for index in (300, 650))
    geometry = module.cache_geometry(calendar, formations, 280)
    history_days = sorted({row["bas_dd"] for row in geometry["ar_matrix"]})
    original = module.cached_request(history_days[274], geometry)
    recovery = module.cached_request(history_days[275], geometry)
    assert (original["role"], original["logical_number"], original["number"]) == ("ar_original", 549, 549)
    assert (recovery["role"], recovery["logical_number"], recovery["number"]) == ("ar_recovery", 551, 2)
    assert recovery["filename"] == f"response-0551-stk_bydd_trd-{history_days[275]}.json"
    assert all(row["response_sha256"] is None for row in (original, recovery))


def test_bp_is_last_cache_choice_and_unknown_date_remains_new():
    geometry = {"aq_matrix": [], "ar_matrix": []}
    assert module.cached_request("20210428", geometry)["role"] == "bp_raw"
    geometry["ar_matrix"] = [dict(service=module.SERVICE, bas_dd="20210428")]
    assert module.cached_request("20210428", geometry)["role"] == "ar_original"
    geometry["aq_matrix"] = [dict(service=module.SERVICE, bas_dd="20210428")]
    assert module.cached_request("20210428", geometry)["role"] == "aq"
    assert module.cached_request("20210429", geometry) is None


@pytest.mark.parametrize("mutation", ["extra", "schema", "status", "flag", "zero", "origin_count", "duplicate_arm", "extra_origin", "entry", "due", "formation", "code", "horizon", "candidate", "eligible", "mode", "isin", "calendar", "summary", "groups", "group_bool", "pin", "geometry"])
def test_changed_registered_geometry_or_certification_is_rejected(mutation, monkeypatch):
    value = metadata(); row = value["potential_origins"][0]
    if mutation == "extra": value["raw_close"] = "123"
    elif mutation == "schema": value["schema"] = "other"
    elif mutation == "status": value["status"] = "verified"
    elif mutation == "flag": value["books_computed"] = True
    elif mutation == "zero": value["new_source_requests"] = False
    elif mutation == "origin_count": value["origin_count"] = True
    elif mutation == "duplicate_arm": value["potential_origins"][1]["arm"] = "D1"
    elif mutation == "extra_origin": value["potential_origins"].append(deepcopy(row))
    elif mutation == "entry": row["entry_on"] = "2021-04-19"
    elif mutation == "due": row["due_on"] = "2021-10-22"
    elif mutation == "formation": row["formation_on"] = "2021-04-14"
    elif mutation == "code": row["code"] = "383800"
    elif mutation == "horizon": row["original_end_on"] = "2021-10-22"
    elif mutation == "candidate": row["end_on"] = "2021-10-22"
    elif mutation == "eligible": row["apr28_original_horizon_eligible"] = False
    elif mutation == "mode": row["mode"] = "actual_filled"
    elif mutation == "isin": row["isin"] = "KR7003550002"
    elif mutation == "calendar": value["calendar_dates"][1] = value["calendar_dates"][0]
    elif mutation == "summary": value["calendar_summary"]["count"] = 1
    elif mutation == "groups": value["apr28_groups"]["eligible"]["count"] = 0
    elif mutation == "group_bool": value["apr28_groups"]["ineligible"]["count"] = False
    elif mutation == "pin": value["composite_quote_snapshot_sha256"] = "invalid"
    else: monkeypatch.setattr(module.formation, "DATES", module.formation.DATES[:-1])
    with pytest.raises(ValueError): module.build_raw_episode_plan(value, scope_review_sha256=PIN)


@pytest.mark.parametrize("calendar,formations,lookback", [(dates("2021-01-01", 5), ("20210103",), 3),
    (dates("2021-01-01", 5), ("20210107",), 2), (dates("2021-01-01", 5), ("20210104", "20210103"), 2),
    (dates("2021-01-01", 5), ("20210103", "20210103"), 2), (dates("2021-01-01", 5), ("20210103",), True)])
def test_incomplete_or_ambiguous_cache_geometry_fails_closed(calendar, formations, lookback):
    with pytest.raises(ValueError): module.cache_geometry(calendar, formations, lookback)
