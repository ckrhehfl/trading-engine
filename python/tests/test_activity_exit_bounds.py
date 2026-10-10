"""Provisional exit proofs use in-memory synthetic BL declarations/typed rows."""

import builtins
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import socket
import sqlite3
import subprocess
from types import MappingProxyType

import pytest

from research import activity_exit_bounds as module
from research.activity_failure_inputs import _typed
from research.activity_holding_inputs import _FINGERPRINT_SCOPE, _provenance, adapt_holding_scan, holding_requirements
from research.activity_partition_selection import PartitionSelection
from research.activity_preflight import digest, encoded
from research.activity_timing import SessionLagPolicy


A, B = "000001", "000002"
ISINS = {A: "KR7000001000", B: "KR7000002000"}
ACTIVITY, SCOPE, RECEIPT = "a" * 64, "b" * 64, "2026-10-10T00:00:00+00:00"
PINS = {"bl_result": "c" * 64, "bm_holding_input_audit": "d" * 64}


def fixture(*, changes=None, signals=None, cash=False):
    """Build a genuine adapter input without a file, database or external source.

    The toy calendar omits weekends only; it makes no real holiday-calendar claim.
    Three formations are 126 sessions apart, using the unchanged study horizon.
    """
    days = []
    day = date(2020, 1, 1)
    while len(days) < 423:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    calendar = tuple(days)
    params = {"lookback": 2, "holding_sessions": 126, "end": calendar[420].isoformat(),
              "threshold": Decimal("3.0"), "slots": 20, "seed": 20261005,
              "commission_bps_per_side": Decimal("1.77"), "slippage_bps_per_side": Decimal("5.0")}
    indices = (2, 128, 254)
    signals = signals if signals is not None else {index: (A,) for index in indices}
    arms = []
    for lag in (1, 2):
        policy = SessionLagPolicy(lag, paired=True)
        arms.append(tuple(PartitionSelection(calendar[index], policy.execution_on(calendar, index),
            policy.selection_at(calendar, index), () if cash else signals.get(index, ()), 2, "e" * 64)
            for index in indices))
    d1, d2 = arms
    upper = {code: [day.isoformat() for day in calendar[:421]] for code in (A, B)}
    original = holding_requirements(d1, d2, calendar, params, upper)
    original = {code: list(values) for code, values in original.items()}
    identities = {code: ISINS[code] for code in original}
    if not original:
        inputs = adapt_holding_scan(None, calendar, {}, quote_snapshot_sha256=None,
                                    activity_snapshot_sha256=ACTIVITY, read_scope_sha256=SCOPE)
        return d1, d2, inputs, params, original, identities
    changes = changes or {}
    fingerprint_rows, raw_rows = [], {}
    positions = {day.isoformat(): index for index, day in enumerate(calendar)}
    for code, requested in original.items():
        for text in requested:
            state = changes.get((code, positions[text]), "observed")
            compact = date.fromisoformat(text).strftime("%Y%m%d")
            values = None
            if state != "absent":
                raw = [compact, "100", "102", "99", "101", "100", "10"]
                if state in ("frozen", "locked"):
                    raw = [compact, "100", "100", "100", "100", "0", "0" if state == "frozen" else "10"]
                elif state == "unresolved":
                    raw[1] = None
                elif state == "zero_open":
                    raw[1] = "0"
                elif state == "zero_turnover":
                    raw[-1] = "0"
                elif state == "invalid_volume":
                    raw[-2] = "1.5"
                values = [_typed(item) for item in raw]
                raw_rows[code, compact] = {"values": dict(zip(
                    ("open", "high", "low", "close", "volume", "turnover"), raw[1:])),
                    "typed_values": values}
            else:
                raw_rows[code, compact] = {"values": None, "typed_values": None}
            fingerprint_rows.append([code, text, values if values is not None else "absent"])
    fingerprint = {
        "schema": "activity-cached-price-parity-v1:typed-scan-v1",
        "calendar_dates": [day.strftime("%Y%m%d") for day in calendar], "read_scope_sha256": SCOPE,
        "intersection_code_dates": original,
        "panel": [[_typed(1), _typed("20190102"), _typed("20260918")]],
        "progress": [[code, [[_typed(item) for item in
            ("20190102", "20260918", len(calendar), 0, "done", RECEIPT)]]] for code in original],
        "rows": fingerprint_rows,
    }
    quote_sha = digest(encoded(fingerprint))
    for (code, compact), row in raw_rows.items():
        row["provenance"] = _provenance(date.fromisoformat(compact), calendar, RECEIPT, quote_sha)
    scan = {"rows": raw_rows, "fingerprint": fingerprint, "typed_snapshot_sha256": quote_sha,
            "snapshot_received_at": RECEIPT, "fingerprint_scope": _FINGERPRINT_SCOPE,
            "whole_database_hash": False, "BI_snapshot_equality_checked": False,
            "progress_counts_recomputed_from_prices": False}
    inputs = adapt_holding_scan(scan, calendar, original, quote_snapshot_sha256=quote_sha,
                                activity_snapshot_sha256=ACTIVITY, read_scope_sha256=SCOPE)
    return d1, d2, inputs, params, original, identities


def plan(case):
    """Keep the public planner's caller-supplied package pins explicit."""
    return module.provisional_exit_bounds(*case, input_sha256=PINS)


def resign(value):
    """An attacker can rehash its fabricated child but cannot change the package."""
    value["child_scope_sha256"] = digest(encoded({key: item for key, item in value.items()
                                                if key != "child_scope_sha256"}))


def test_exact_due_each_arm_entry_and_original_snapshot_are_preserved():
    case = fixture()
    before = deepcopy(case)
    result = plan(case)
    assert case == before
    calendar = case[2].calendar
    assert len(result["entries"]) == 6
    for row, entry in zip(result["entries"], (3, 4, 129, 130, 255, 256)):
        assert row["entry_on"] == calendar[entry].isoformat()
        assert row["due_on"] == row["candidate_on"] == row["end_on"] == calendar[entry + 126].isoformat()
        assert row["candidate_state"] == "observed" and row["state"] == "provisional_candidate"
        assert row["original_end_on"] == calendar[420].isoformat()
    assert result["original_required_code_dates"] == case[4]
    assert result["quote_snapshot_sha256"] == case[2].quote_snapshot_sha256 != ACTIVITY
    assert result["original_read_scope_sha256"] == SCOPE and result["input_sha256"] == PINS
    assert result["parameters"]["commission_bps_per_side"] == "1.77"
    assert result["returns_computed"] is result["no_event_coverage_certified"] is result["actual_exits_certified"] is False
    assert result["status"] == "provisional"
    expected = deepcopy(result)
    resign(expected)
    assert expected == result
    assert module.validate_exit_bounds(result, *case, input_sha256=PINS) == result


def test_first_resolved_nonfrozen_open_skips_absence_invalid_and_frozen_but_allows_locked():
    case = fixture(changes={(A, 129): "frozen", (A, 130): "absent",
                            (A, 131): "unresolved", (A, 132): "locked"})
    result = plan(case)
    one, two = result["entries"][:2]
    assert one["due_state"] == "frozen" and two["due_state"] == "absent_requested"
    assert one["candidate_on"] == two["candidate_on"] == case[2].calendar[132].isoformat()
    assert one["candidate_state"] == two["candidate_state"] == "locked"


@pytest.mark.parametrize("state", ["absent", "frozen", "unresolved", "zero_open", "invalid_volume"])
def test_no_usable_open_keeps_original_evaluation_end_without_inventing_exit(state):
    case = fixture(changes={(A, index): state for index in range(129, 421)})
    result = plan(case)
    assert all(row["candidate_on"] is row["candidate_state"] is None for row in result["entries"])
    assert all(row["end_on"] == case[3]["end"] and row["state"] == "no_usable_opening"
               for row in result["entries"])
    assert result["child_code_dates"] == result["original_required_code_dates"]


def test_opening_on_evaluation_end_is_candidate_and_nonflat_zero_turnover_is_allowed():
    changes = {(A, index): "frozen" for index in range(129, 420)}
    changes[A, 420] = "zero_turnover"
    case = fixture(changes=changes)
    result = plan(case)
    assert all(row["candidate_on"] == case[3]["end"] and row["candidate_state"] == "observed"
               for row in result["entries"])


@pytest.mark.parametrize("state", ["absent", "frozen", "unresolved"])
def test_unusable_early_entry_does_not_remove_it_or_later_reentry(state):
    case = fixture(changes={(A, 3): state, (A, 4): state})
    result = plan(case)
    assert len(result["entries"]) == 6
    assert result["entries"][2]["entry_on"] == case[2].calendar[129].isoformat()
    assert result["entries"][-1]["end_on"] == case[2].calendar[382].isoformat()
    assert result["child_code_dates"][A][-1] == case[2].calendar[382].isoformat()


def test_calendar_day_windows_include_weekends_but_leave_unrelated_reentry_gaps():
    case = fixture(signals={2: (A, B), 254: (A,)})
    result = plan(case)
    calendar = case[2].calendar
    assert len(result["entries"]) == 6
    assert result["child_windows"] == [
        {"code": A, "isin": ISINS[A], "start": calendar[3].isoformat(), "end": calendar[130].isoformat()},
        {"code": A, "isin": ISINS[A], "start": calendar[255].isoformat(), "end": calendar[382].isoformat()},
        {"code": B, "isin": ISINS[B], "start": calendar[3].isoformat(), "end": calendar[130].isoformat()},
    ]
    assert calendar[200].isoformat() not in result["child_code_dates"][A]
    assert (calendar[130] - calendar[3]).days > len(result["child_code_dates"][B])


def test_empty_union_has_no_entries_windows_or_invented_quote_hash():
    case = fixture(cash=True)
    result = plan(case)
    assert result["entries"] == result["child_windows"] == []
    assert result["child_code_dates"] == result["original_required_code_dates"] == result["issue_isins"] == {}
    assert result["quote_snapshot_sha256"] is None
    assert module.validate_exit_bounds(result, *case, input_sha256=PINS) == result


def test_immutable_caller_scope_and_pins_produce_the_same_child_proof():
    case = list(fixture())
    expected = plan(case)
    case[3] = MappingProxyType(case[3])
    case[4] = MappingProxyType({code: tuple(days) for code, days in case[4].items()})
    case[5] = MappingProxyType(case[5])
    assert module.provisional_exit_bounds(*case, input_sha256=MappingProxyType(PINS)) == expected


@pytest.mark.parametrize("corruption", [
    "missing_entry", "duplicate_entry", "order", "arm", "isin", "due", "candidate", "candidate_state",
    "entry_state", "end", "status", "claim", "hash", "quote_pin", "scope_pin", "input_pin", "parameters",
    "parameter_hash", "calendar", "calendar_hash", "original_scope", "original_hash", "identity",
    "child_date", "child_window", "extra",
])
def test_fabricated_child_rows_scope_and_pins_rejected_even_after_rehash(corruption):
    case = fixture()
    value = plan(case)
    if corruption == "missing_entry": value["entries"].pop()
    elif corruption == "duplicate_entry": value["entries"].append(deepcopy(value["entries"][0]))
    elif corruption == "order": value["entries"].reverse()
    elif corruption == "arm": value["entries"][0]["arm"] = "D2"
    elif corruption == "isin": value["entries"][0]["isin"] = ISINS[B]
    elif corruption == "due": value["entries"][0]["due_on"] = case[2].calendar[128].isoformat()
    elif corruption == "candidate": value["entries"][0]["candidate_on"] = case[2].calendar[128].isoformat()
    elif corruption == "candidate_state": value["entries"][0]["candidate_state"] = "locked"
    elif corruption == "entry_state": value["entries"][0]["entry_state"] = "absent_requested"
    elif corruption == "end": value["entries"][0]["end_on"] = case[3]["end"]
    elif corruption == "status": value["status"] = "verified"
    elif corruption == "claim": value["no_event_coverage_certified"] = True
    elif corruption == "hash": value["child_scope_sha256"] = "f" * 64
    elif corruption == "quote_pin": value["quote_snapshot_sha256"] = "f" * 64
    elif corruption == "scope_pin": value["original_read_scope_sha256"] = "f" * 64
    elif corruption == "input_pin": value["input_sha256"]["bl_result"] = "f" * 64
    elif corruption == "parameters": value["parameters"]["seed"] += 1
    elif corruption == "parameter_hash": value["parameters_sha256"] = "f" * 64
    elif corruption == "calendar": value["calendar_dates"].pop()
    elif corruption == "calendar_hash": value["calendar_sha256"] = "f" * 64
    elif corruption == "original_scope": value["original_required_code_dates"][A].pop()
    elif corruption == "original_hash": value["original_requirements_sha256"] = "f" * 64
    elif corruption == "identity": value["issue_isins"][A] = ISINS[B]
    elif corruption == "child_date": value["child_code_dates"][A].pop()
    elif corruption == "child_window": value["child_windows"][0]["end"] = case[3]["end"]
    elif corruption == "extra": value["returns"] = []
    if corruption != "hash":
        resign(value)
    with pytest.raises(ValueError, match="exit proof"):
        module.validate_exit_bounds(value, *case, input_sha256=PINS)


@pytest.mark.parametrize("corruption", [
    "missing_arm", "swapped_arms", "partition", "truncated_original", "extra_original", "missing_isin",
    "extra_isin", "invalid_isin", "coordinates", "panel", "fingerprint", "empty_quote", "holding_bool",
])
def test_inconsistent_original_package_declarations_are_rejected(corruption):
    case = list(fixture(cash=corruption == "empty_quote"))
    d1, d2, inputs, params, original, identities = case
    if corruption == "missing_arm": case[0] = d1[:-1]
    elif corruption == "swapped_arms": case[0], case[1] = d2, d1
    elif corruption == "partition": case[1] = (replace(d2[0], source_partition_sha256="f" * 64), *d2[1:])
    elif corruption == "truncated_original": original[A].pop()
    elif corruption == "extra_original": original[A].insert(0, inputs.calendar[2].isoformat())
    elif corruption == "missing_isin": identities.pop(A)
    elif corruption == "extra_isin": identities[B] = ISINS[B]
    elif corruption == "invalid_isin": identities[A] = "unknown"
    elif corruption == "coordinates": case[2] = replace(inputs, requested_coordinates=inputs.requested_coordinates[:-1])
    elif corruption == "panel": case[2] = replace(inputs, series=())
    elif corruption == "fingerprint": case[2] = replace(inputs, fingerprint_json=b"changed")
    elif corruption == "empty_quote": case[2] = replace(inputs, quote_snapshot_sha256="f" * 64)
    elif corruption == "holding_bool": params["holding_sessions"] = True
    with pytest.raises(ValueError):
        plan(case)


def test_planner_and_validator_cannot_open_files_database_network_or_run_books(monkeypatch):
    case = fixture()

    def forbidden(*args, **kwargs):
        pytest.fail("pure exit planner attempted external IO or accounting")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    from research import activity_replay
    monkeypatch.setattr(activity_replay, "replay_synthetic", forbidden)
    value = plan(case)
    assert module.validate_exit_bounds(value, *case, input_sha256=PINS) == value
