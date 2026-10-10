"""Task BL restores BI receipts from temporary synthetic reader fixtures only."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal as D

import pytest

from research import activity_failure_inputs as reader
from research.activity_failure_inputs import load_activity_failure_inputs, restore_activity_failure
from test_activity_failure_inputs import A, B, DAYS, database, mutate


@pytest.fixture
def receipt(tmp_path):
    path = database(tmp_path)
    exact = "100000000000000000000000000000001.500"
    mutate(path, "UPDATE scan_bars SET turnover=? WHERE code=? AND bsop_date=?",
           (exact, A, DAYS[0].strftime("%Y%m%d")))
    for index, column, value in ((1, "turnover", "-0"), (2, "high", None),
                                 (3, "turnover", 1.0), (5, "high", "99"),
                                 (6, "high", "99"), (6, "turnover", None),
                                 (7, "high", "101"), (7, "turnover", "0")):
        mutate(path, f"UPDATE scan_bars SET {column}=? WHERE code=? AND bsop_date=?",
               (value, A, DAYS[index].strftime("%Y%m%d")))
    mutate(path, "DELETE FROM scan_bars WHERE code=? AND bsop_date=?",
           (A, DAYS[4].strftime("%Y%m%d")))
    scope = {B: DAYS[:2], A: DAYS[:8]}
    original = load_activity_failure_inputs(path, DAYS, scope)
    return original, original.snapshot.to_dict(), scope


def test_roundtrip_is_exact_immutable_and_does_not_read_or_rehash(receipt, monkeypatch):
    original, saved, scope = receipt

    def refuse(*args, **kwargs):
        pytest.fail("pure restore attempted a source read or raw snapshot hash recomputation")

    monkeypatch.setattr(reader.sqlite3, "connect", refuse)
    monkeypatch.setattr(reader.hashlib, "sha256", refuse)
    restored = restore_activity_failure(saved, DAYS, scope)
    assert restored.snapshot.to_dict() == saved
    assert restored.snapshot == original.snapshot
    assert restored.observations == original.observations
    assert restored.observations[A][DAYS[0]].turnover == D(saved["row_provenance"][0]["turnover"])
    assert restored.observations[A][DAYS[0]].turnover.as_tuple().exponent == -3
    assert [row.state for row in restored.observations[A].values()] == [
        "observed", "frozen", "null", "invalid", "missing", "invalid", "invalid", "observed",
    ]
    with pytest.raises(TypeError):
        restored.observations[A][DAYS[0]] = None
    with pytest.raises(TypeError):
        restored.observations[A] = {}
    with pytest.raises(FrozenInstanceError):
        restored.snapshot.dataset_sha256 = "f" * 64
    saved["row_provenance"][0]["turnover"] = "0"
    saved["row_states"][2]["issues"].append("null:low")
    assert restored.snapshot == original.snapshot


def replace(saved, path, value):
    target = saved
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize("path,value", [
    (("schema",), "task-bi-activity-failure-snapshot-v2"),
    (("dataset_sha256",), "A" * 64), (("dataset_sha256",), "a" * 63),
    (("fingerprint_scope",), "whole database"), (("whole_database_hash",), 0),
    (("original_source_vintage_certified",), True),
    (("progress_counts_recomputed_from_prices",), True),
    (("requested_rows_checked_against_progress",), False),
    (("snapshot_received_at",), "2026-10-10T01:00:00"),
    (("snapshot_received_at",), "2026-10-10T01:00:00+09:00"),
    (("snapshot_received_at",), "2026-10-10T01:00:00Z"),
    (("snapshot_received_at",), None), (("snapshot_received_at",), "invalid"),
    (("snapshot_received_at_meaning",), "original source retrieval"),
    (("read_columns",), ["bsop_date", "turnover"]),
    (("panel", "id"), True), (("panel", "start"), "2019-01-01"),
    (("panel", "end"), "2026-09-19"),
    (("read_scope", 0, "dates", 0), "20190102"),
    (("read_scope", 0, "dates", 0), "2019-02-30"),
    (("read_scope", 0, "code"), B),
    (("progress", 0, "code"), B), (("progress", 0, "status"), "failed"),
    (("progress", 0, "bars"), True), (("progress", 0, "bars"), 1.0),
    (("progress", 0, "bars"), 1), (("progress", 0, "frozen"), -1),
    (("progress", 0, "first_date"), "2019-01-03"),
    (("progress", 0, "last_date"), "2026-09-19"),
    (("progress", 0, "fetched_at"), "2026-10-10T01:00:00"),
    (("progress", 0, "fetched_at_meaning"), "row retrieval"),
    (("fetched_row_count",), 10), (("missing_row_count",), 0),
    (("missing_row_count",), True),
    (("row_states", 0, "code"), B), (("row_provenance", 0, "code"), B),
    (("row_states", 0, "observation_date"), "2019-01-03"),
    (("row_provenance", 0, "observation_date"), "2019-01-03"),
    (("row_provenance", 0, "state"), "frozen"),
    (("row_provenance", 0, "issues"), ["invalid:turnover"]),
    (("row_provenance", 0, "turnover"), 10),
    (("row_provenance", 0, "turnover"), 10.0),
    (("row_provenance", 0, "turnover"), D("10")),
    (("row_provenance", 0, "turnover"), "NaN"),
    (("row_provenance", 0, "turnover"), "Infinity"),
    (("row_provenance", 0, "turnover"), "-1"),
    (("row_provenance", 0, "turnover"), " 10"),
    (("row_provenance", 0, "turnover"), "01"),
    (("row_provenance", 0, "turnover"), "1e2"),
    (("row_provenance", 0, "turnover"), "1e100000000000000000000"),
    (("row_provenance", 1, "turnover"), "1"),
    (("row_provenance", 2, "turnover"), "1"),
    (("row_provenance", 0, "available_at"), "2019-01-04T08:30:00+09:00"),
    (("row_provenance", 0, "available_at"), "2019-01-02T23:30:00+00:00"),
    (("row_provenance", 0, "retrieved_at"), "2026-10-10T00:00:00+00:00"),
    (("row_provenance", 0, "source"), "later BK snapshot"),
    (("row_provenance", 0, "is_final"), True),
    (("row_provenance", 0, "data_vintage"), "2026-10-10"),
    (("row_provenance", 0, "evidence_level"), "confirmed"),
    (("row_provenance", 0, "source_public_available_at"), "2019-01-03T08:30:00+09:00"),
    (("row_provenance", 0, "availability_policy"), "task-bi-next-calendar-day"),
    (("row_provenance", 0, "evidence_reference"), "Task BI registered snapshot sha256:" + "f" * 64),
    (("row_provenance", 0, "snapshot_sha256"), "f" * 64),
])
def test_corrupt_schema_scope_amounts_progress_and_provenance_fail(receipt, path, value):
    _, saved, scope = receipt
    replace(saved, path, value)
    with pytest.raises(ValueError):
        restore_activity_failure(saved, DAYS, scope)


@pytest.mark.parametrize("field", ["read_scope", "progress", "row_states", "row_provenance"])
@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "reorder", "wrong_container"])
def test_saved_lists_must_preserve_the_complete_unique_canonical_join(receipt, field, change):
    _, saved, scope = receipt
    rows = saved[field]
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows[-1] = deepcopy(rows[0])
    elif change == "extra":
        rows.append(deepcopy(rows[0]))
    elif change == "reorder":
        rows.reverse()
    else:
        saved[field] = tuple(rows)
    with pytest.raises(ValueError):
        restore_activity_failure(saved, DAYS, scope)


@pytest.mark.parametrize("path", [(), ("panel",), ("read_scope", 0), ("progress", 0),
                                  ("row_states", 0), ("row_provenance", 0)])
@pytest.mark.parametrize("change", ["extra", "missing"])
def test_unknown_or_omitted_record_fields_are_rejected(receipt, path, change):
    _, saved, scope = receipt
    target = saved
    for key in path:
        target = target[key]
    if change == "extra":
        target["invented"] = None
    else:
        target.pop(next(iter(target)))
    with pytest.raises(ValueError):
        restore_activity_failure(saved, DAYS, scope)


@pytest.mark.parametrize("index,state,issues", [
    (0, "observed", ["null:high"]), (0, "unknown", []),
    (1, "frozen", ["invalid:turnover"]),
    (2, "null", []), (2, "null", ["invalid:high"]),
    (2, "null", ["null:high", "null:high"]),
    (2, "null", ["null:turnover", "null:high"]),
    (2, "null", ["null:volume"]),
    (3, "invalid", ["null:turnover"]),
    (3, "invalid", ["null:high", "invalid:high"]),
    (4, "missing", ["absent_requested_row:halt"]),
    (5, "invalid", ["invalid:high", "invalid:ohlc_bounds"]),
])
def test_matching_but_impossible_state_and_issue_pairs_are_rejected(receipt, index, state, issues):
    _, saved, scope = receipt
    for field in ("row_states", "row_provenance"):
        saved[field][index]["state"] = state
        saved[field][index]["issues"] = issues
    with pytest.raises(ValueError):
        restore_activity_failure(saved, DAYS, scope)


def test_external_scope_and_calendar_are_required_and_cannot_be_rebound(receipt):
    _, saved, scope = receipt
    for calendar, requested in ((DAYS, {A: DAYS[:2]}), (DAYS, {}),
                                (list(DAYS), scope), (DAYS[::2], scope)):
        with pytest.raises(ValueError):
            restore_activity_failure(saved, calendar, requested)
    # Even a valid altered calendar cannot move the receipt's modeled next session.
    calendar = (*DAYS[:8], date(2019, 1, 10), *DAYS[8:])
    with pytest.raises(ValueError):
        restore_activity_failure(saved, calendar, scope)
