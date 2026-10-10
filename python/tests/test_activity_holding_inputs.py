"""Pure holding seams with synthetic declarations and temporary SQLite only."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal as D
import json
import sqlite3

import pytest

from research.activity_holding_inputs import adapt_holding_scan, holding_requirements
from research.activity_partition_selection import selections_from_bl
from research.activity_preflight import PinnedInputs, digest, encoded
from research.activity_price_parity import load_scan
from research.activity_replay import _quote, replay_synthetic
from research.activity_timing import SessionLagPolicy
from test_activity_failure_inputs import A, B, DAYS, database, mutate
from test_activity_partition_selection import package


ACTIVITY_PIN, SCOPE_PIN = "b" * 64, "c" * 64


def declarations():
    """Use the real BL producer-shaped synthetic selection factory for both arms."""
    result, population, normal, calendar, params = package()
    calendar = (*calendar, calendar[-1] + timedelta(days=1), calendar[-1] + timedelta(days=2))
    params = {**params, "slots": 1, "seed": 20261005,
              "commission_bps_per_side": D("1.77"), "slippage_bps_per_side": D(5)}
    return (selections_from_bl(result, population, normal, calendar, params, "D1"),
            selections_from_bl(result, population, normal, calendar, params, "D2"), calendar, params)


def upper_bound(calendar):
    """Declare an intentionally broader fixed original-issue upper bound."""
    return {code: tuple(day.isoformat() for day in calendar[:-2]) for code in (A, B)}


def scan_case(tmp_path, *, change=None):
    """Exercise only the existing bounded reader over a temporary synthetic DB."""
    path = database(tmp_path)
    mutate(path, "UPDATE scan_bars SET volume='100'")
    if change is not None:
        field, value = change
        assert field in ("open", "high", "low", "close", "volume", "turnover")
        mutate(path, f"UPDATE scan_bars SET {field}=? WHERE code=? AND bsop_date=?",
               (value, A, DAYS[0].strftime("%Y%m%d")))
    requested = {A: tuple(day.isoformat() for day in DAYS[:3])}
    plan = {"calendar_dates": [day.strftime("%Y%m%d") for day in DAYS],
            "intersection_code_dates": {code: list(days) for code, days in requested.items()}}
    scan = load_scan(path, plan, PinnedInputs(), SCOPE_PIN)
    return path, requested, scan


def adapt(scan, requested, calendar=DAYS):
    """Supply explicit, distinct activity and typed quote snapshot references."""
    return adapt_holding_scan(scan, calendar, requested,
                              quote_snapshot_sha256=scan["typed_snapshot_sha256"],
                              read_scope_sha256=SCOPE_PIN, activity_snapshot_sha256=ACTIVITY_PIN)


def resign(scan):
    """Simulate a separately pinned malformed receipt to test more than hashing."""
    sha = digest(encoded(scan["fingerprint"]))
    scan["typed_snapshot_sha256"] = sha
    for row in scan["rows"].values():
        row["provenance"]["typed_snapshot_sha256"] = sha


def test_requirements_cover_both_arm_entries_through_end_not_scheduled_exit():
    """Keep every possible holding session through evaluation end for both arms."""
    d1, d2, calendar, params = declarations()
    result = holding_requirements(d1, d2, calendar, params, upper_bound(calendar))
    assert dict(result) == {A: tuple(day.isoformat() for day in calendar[61:130])}
    assert calendar[91].isoformat() in result[A] and calendar[129].isoformat() in result[A]
    assert calendar[60].isoformat() not in result[A] and calendar[130].isoformat() not in result[A]
    assert d1[1].eligible_codes == d2[1].eligible_codes == ()
    with pytest.raises(TypeError):
        result[A] = ()


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "order", "swapped_arms", "cutoff",
                                         "partition", "eligible", "BJ_missing", "BJ_due_only", "BJ_dates"])
def test_incomplete_or_inconsistent_schedule_scope_is_rejected(corruption):
    """Reject incomplete paired declarations or requirements outside the BJ bound."""
    d1, d2, calendar, params = declarations()
    upper = upper_bound(calendar)
    if corruption == "missing": d1 = d1[:-1]
    elif corruption == "duplicate": d2 = (*d2, d2[-1])
    elif corruption == "order": d1 = tuple(reversed(d1))
    elif corruption == "swapped_arms": d1, d2 = d2, d1
    elif corruption == "cutoff": d2 = (replace(d2[0], selection_at=d2[0].selection_at + timedelta(minutes=1)), *d2[1:])
    elif corruption == "partition": d2 = (replace(d2[0], source_partition_sha256="d" * 64), *d2[1:])
    elif corruption == "eligible": d2 = (replace(d2[0], eligible_codes=()), *d2[1:])
    elif corruption == "BJ_missing": upper.pop(A)
    elif corruption == "BJ_due_only": upper[A] = tuple(day.isoformat() for day in calendar[61:92])
    elif corruption == "BJ_dates": upper[A] = tuple(reversed(upper[A]))
    with pytest.raises(ValueError):
        holding_requirements(d1, d2, calendar, params, upper)


def test_no_signals_need_no_quote_read_or_invented_snapshot():
    """Allow empty signals without constructing or reading a quote snapshot."""
    d1, d2, calendar, params = declarations()
    d1 = tuple(replace(row, eligible_codes=()) for row in d1)
    d2 = tuple(replace(row, eligible_codes=()) for row in d2)
    requirements = holding_requirements(d1, d2, calendar, params, {})
    assert dict(requirements) == {}
    inputs = adapt_holding_scan(None, calendar, requirements, quote_snapshot_sha256=None,
                               read_scope_sha256=SCOPE_PIN, activity_snapshot_sha256=ACTIVITY_PIN)
    assert dict(inputs.panel) == {} and inputs.observations == inputs.requested_coordinates == ()
    assert inputs.quote_snapshot_sha256 is inputs.fingerprint_json is inputs.snapshot_received_at is None
    with pytest.raises(ValueError, match="cash-only"):
        adapt_holding_scan(None, calendar, {}, quote_snapshot_sha256="a" * 64,
                           read_scope_sha256=SCOPE_PIN, activity_snapshot_sha256=ACTIVITY_PIN)


def test_lossless_projection_preserves_storage_receipt_and_is_immutable(tmp_path):
    """Preserve exact Decimal text and freeze receipts independently of caller mutation."""
    value = "100.123456789012345678901234567890"
    path, requested, _ = scan_case(tmp_path)
    mutate(path, "UPDATE scan_bars SET high='101',low='99',close=?,turnover=? WHERE code=? AND bsop_date=?",
           (value, "100000000000000000000000000.0000000001", A, "20190102"))
    plan = {"calendar_dates": [day.strftime("%Y%m%d") for day in DAYS],
            "intersection_code_dates": {A: list(requested[A])}}
    scan = load_scan(path, plan, PinnedInputs(), SCOPE_PIN)
    original = deepcopy(scan)
    inputs = adapt(scan, requested)
    assert scan == original
    assert inputs.panel[A].closes[0].as_tuple() == D(value).as_tuple()
    assert inputs.panel[A].turnover[0].as_tuple() == D("100000000000000000000000000.0000000001").as_tuple()
    assert inputs.activity_snapshot_sha256 == ACTIVITY_PIN != inputs.quote_snapshot_sha256
    assert inputs.fingerprint_json == encoded(scan["fingerprint"])
    assert digest(inputs.fingerprint_json) == inputs.quote_snapshot_sha256
    observation = inputs.observations[0]
    assert observation.typed_values[4] == ("text", value)
    assert json.loads(observation.provenance_json) == scan["rows"][A, "20190102"]["provenance"]
    scan["fingerprint"]["rows"][0][2][4][1] = "mutated after projection"
    assert observation.typed_values[4][1] == value
    assert inputs.fingerprint_json == encoded(original["fingerprint"])
    with pytest.raises(FrozenInstanceError):
        inputs.quote_snapshot_sha256 = "e" * 64
    with pytest.raises(TypeError):
        inputs.panel[A] = inputs.panel[A]


@pytest.mark.parametrize("field,value,issue", [
    ("open", 100, "invalid:open"), ("close", 100.0, "invalid:close"),
    ("high", b"100", "invalid:high"), ("low", None, "null:low"),
    ("turnover", "NaN", "invalid:turnover"), ("open", "0", "invalid:open"),
    ("close", " 100", "invalid:close"), ("volume", "1.5", "invalid:fractional_volume"),
    ("high", "99", "invalid:ohlc_bounds"), ("volume", None, "null:volume"),
    ("turnover", 0, "invalid:turnover"), ("volume", "Infinity", "invalid:volume"),
])
def test_unresolved_rows_preserve_observed_presence_and_replay_refuses(tmp_path, field, value, issue):
    """Retain invalid observed storage and refuse its use as an execution quote."""
    _, requested, scan = scan_case(tmp_path, change=(field, value))
    inputs = adapt(scan, requested)
    assert inputs.panel[A].observed[0] is True and inputs.absent_coordinates == ()
    assert issue in inputs.observations[0].issues
    assert inputs.observations[0].typed_values is not None
    with pytest.raises(ValueError, match="unresolved observed"):
        _quote(inputs.panel[A], 0)


@pytest.mark.parametrize("turnover,frozen,locked", [("0", True, False), ("10", False, True)])
def test_zero_volume_and_turnover_use_existing_flat_bar_states(tmp_path, turnover, frozen, locked):
    """Interpret genuine zero volume and turnover with existing flat-bar rules."""
    path, requested, _ = scan_case(tmp_path)
    mutate(path, "UPDATE scan_bars SET volume='0',turnover=? WHERE code=?", (turnover, A))
    scan = load_scan(path, {"calendar_dates": [day.strftime("%Y%m%d") for day in DAYS],
                            "intersection_code_dates": {A: list(requested[A])}}, PinnedInputs(), SCOPE_PIN)
    inputs = adapt(scan, requested)
    assert _quote(inputs.panel[A], 0) == (D(100), D(100), frozen, locked)
    assert inputs.observations[0].issues == () and inputs.panel[A].turnover[0] == D(turnover)


def test_explicit_absence_is_distinct_from_unrequested_calendar_cells(tmp_path):
    """Distinguish an inspected absent row from calendar cells outside the request."""
    path, requested, _ = scan_case(tmp_path)
    mutate(path, "DELETE FROM scan_bars WHERE code=? AND bsop_date='20190103'", (A,))
    scan = load_scan(path, {"calendar_dates": [day.strftime("%Y%m%d") for day in DAYS],
                            "intersection_code_dates": {A: list(requested[A])}}, PinnedInputs(), SCOPE_PIN)
    inputs = adapt(scan, requested)
    assert inputs.absent_coordinates == ((A, DAYS[1]),)
    assert (A, DAYS[1]) not in inputs.unrequested_coordinates
    assert (A, DAYS[3]) in inputs.unrequested_coordinates
    assert inputs.panel[A].observed[1] is inputs.panel[A].observed[3] is False
    assert inputs.observations[1].typed_values is None
    assert inputs.observations[1].issues == ("absent_requested_row:cause_unknown",)
    assert len(inputs.observations) == len(inputs.requested_coordinates) == 3
    assert _quote(inputs.panel[A], 1) == (None, None, False, False)


@pytest.mark.parametrize("corruption", ["hash", "scope_pin", "calendar", "extra_row", "missing_row",
                                         "duplicate_typed", "date", "values", "typed_values", "provenance",
                                         "receipt", "flag", "progress", "panel", "encoding"])
def test_corrupt_scope_storage_provenance_and_metadata_rejected(tmp_path, corruption):
    """Reject corrupted typed receipts even when their fingerprint is repinned."""
    _, requested, scan = scan_case(tmp_path)
    row = scan["rows"][A, "20190102"]
    if corruption == "hash": scan["fingerprint"]["rows"][0][2][1][1] = "101"
    elif corruption == "scope_pin": scan["fingerprint"]["read_scope_sha256"] = "d" * 64
    elif corruption == "calendar": scan["fingerprint"]["calendar_dates"].pop()
    elif corruption == "extra_row": scan["rows"][B, "20190102"] = deepcopy(row)
    elif corruption == "missing_row": scan["rows"].pop((A, "20190102"))
    elif corruption == "duplicate_typed": scan["fingerprint"]["rows"][1] = deepcopy(scan["fingerprint"]["rows"][0])
    elif corruption == "date":
        scan["fingerprint"]["rows"][0][2][0][1] = "20190103"
        row["typed_values"][0][1] = "20190103"
    elif corruption == "values": row["values"]["open"] = "101"
    elif corruption == "typed_values": row["typed_values"][1][1] = "101"
    elif corruption == "provenance": row["provenance"]["retrieved_at"] = "2026-10-09T00:00:00+00:00"
    elif corruption == "receipt": scan["snapshot_received_at"] = "2026-10-09T00:00:00"
    elif corruption == "flag": scan["whole_database_hash"] = True
    elif corruption == "progress": scan["fingerprint"]["progress"][0][1][0][4][1] = "failed"
    elif corruption == "panel": scan["fingerprint"]["panel"][0][2][1] = "20260917"
    elif corruption == "encoding":
        scan["fingerprint"]["rows"][0][2][1] = ["integer", "0100"]
        row["typed_values"][1] = ["integer", "0100"]
    if corruption != "hash": resign(scan)
    with pytest.raises(ValueError):
        adapt(scan, requested)


def test_external_quote_activity_and_scope_pins_are_explicit(tmp_path):
    """Require explicit valid activity references and matching quote and scope pins."""
    _, requested, scan = scan_case(tmp_path)
    for overrides in ({"quote_snapshot_sha256": "e" * 64}, {"read_scope_sha256": "e" * 64},
                      {"activity_snapshot_sha256": "unknown"}):
        kwargs = {"quote_snapshot_sha256": scan["typed_snapshot_sha256"], "read_scope_sha256": SCOPE_PIN,
                  "activity_snapshot_sha256": ACTIVITY_PIN, **overrides}
        with pytest.raises(ValueError):
            adapt_holding_scan(scan, DAYS, requested, **kwargs)
    with pytest.raises(ValueError, match="scope"):
        adapt(scan, {A: requested[A][:-1]})


@pytest.mark.parametrize("lag", [1, 2])
def test_full_bounded_synthetic_reader_adapter_selection_book_route(tmp_path, lag):
    """Connect fabricated bounded quotes to paired selection and existing accounting."""
    d1, d2, calendar, params = declarations()
    requested = holding_requirements(d1, d2, calendar, params, upper_bound(calendar))
    # This temporary database contains only fabricated rows for the declared union.
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM scan_bars")
        connection.execute("DELETE FROM scan_progress")
        for code, days in requested.items():
            connection.executemany("INSERT INTO scan_bars VALUES(?,?,?,?,?,?,?,?)",
                [(code, day.replace("-", ""), "100", "101", "99", "100", "100", "10") for day in days])
            connection.execute("INSERT INTO scan_progress VALUES(?,?,?,?,?,?,?)",
                (code, days[0].replace("-", ""), days[-1].replace("-", ""), len(days), 0,
                 "done", "2026-10-09T00:00:00+00:00"))
    plan = {"calendar_dates": [day.strftime("%Y%m%d") for day in calendar],
            "intersection_code_dates": {code: list(days) for code, days in requested.items()}}
    scan = load_scan(path, plan, PinnedInputs(), SCOPE_PIN)
    inputs = adapt(scan, requested, calendar)
    replayed = replay_synthetic(calendar, inputs.panel, params, d1 if lag == 1 else d2,
        dataset_sha256=inputs.quote_snapshot_sha256, timing_policy=SessionLagPolicy(lag, paired=True))
    assert replayed.selections == ((calendar[60], (A,)), (calendar[90], ()))
    assert replayed.closed_trades == 1 and replayed.books[-1].lots == ()
    assert len(inputs.requested_coordinates) == 69 and inputs.absent_coordinates == ()
    assert inputs.panel[A].observed[129] is True and inputs.panel[A].observed[130] is False
    assert all(0 < book.nav <= 1 for book in replayed.books)
