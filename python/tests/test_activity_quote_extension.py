"""Composite quote checks use fabricated in-memory scan evidence exclusively."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
import sqlite3

import pytest

from research.activity_failure_inputs import _typed
from research.activity_holding_inputs import _FINGERPRINT_SCOPE, _provenance, adapt_holding_scan
from research.activity_preflight import digest, encoded, strict_json
from research.activity_quote_extension import extend_holding_quotes
from research.activity_replay import _quote


A, B = "000001", "000002"
DAYS = tuple(date(2021, 5, 24) + timedelta(days=index) for index in range(8))
ACTIVITY = "a" * 64


def component(scope, pin, *, receipt="2026-10-10T00:00:00+00:00", changes=None):
    """Build independently pinned reader-shaped synthetic rows without a DB."""
    typed_rows, rows = [], {}
    for code, days in sorted(scope.items()):
        for day in days:
            raw = (day.strftime("%Y%m%d"), "100.000", "101", "99", "100.000", "100", "10")
            raw = (changes or {}).get((code, day), raw)
            typed_rows.append([code, day.isoformat(), "absent" if raw is None else [_typed(value) for value in raw]])
    fingerprint = {"schema": "activity-cached-price-parity-v1:typed-scan-v1",
        "calendar_dates": [day.strftime("%Y%m%d") for day in DAYS], "read_scope_sha256": pin,
        "intersection_code_dates": {code: [day.isoformat() for day in days] for code, days in sorted(scope.items())},
        "panel": [[["integer", "1"], ["text", "20190102"], ["text", "20260918"]]],
        "progress": [[code, [[_typed(value) for value in (
            "20190102", "20260918", len(DAYS), 0, "done", "2026-10-09T00:00:00+00:00")]]]
                     for code in sorted(scope)], "rows": typed_rows}
    sha = digest(encoded(fingerprint))
    for code, text, typed in typed_rows:
        day = date.fromisoformat(text)
        values = None if typed == "absent" else {field: value if kind in ("null", "text")
            else {"sqlite_storage": [kind, value]} for field, (kind, value) in zip(
                ("open", "high", "low", "close", "volume", "turnover"), typed[1:])}
        rows[code, day.strftime("%Y%m%d")] = {"values": values,
            "typed_values": None if typed == "absent" else typed, "provenance": _provenance(day, DAYS, receipt, sha)}
    scan = {"rows": rows, "fingerprint": fingerprint, "typed_snapshot_sha256": sha,
        "snapshot_received_at": receipt, "fingerprint_scope": _FINGERPRINT_SCOPE,
        "whole_database_hash": False, "BI_snapshot_equality_checked": False,
        "progress_counts_recomputed_from_prices": False}
    return adapt_holding_scan(scan, DAYS, fingerprint["intersection_code_dates"],
        quote_snapshot_sha256=sha, read_scope_sha256=pin, activity_snapshot_sha256=ACTIVITY)


def pair():
    """Keep absent, frozen and invalid observed originals alongside a new issue."""
    changes = {(A, DAYS[1]): None,
        (A, DAYS[2]): (DAYS[2].strftime("%Y%m%d"), *(["100"] * 4), "0", "0"),
        (A, DAYS[3]): (DAYS[3].strftime("%Y%m%d"), "100", "101", "99", 100, "100", "10")}
    return component({A: DAYS[:4]}, "b" * 64, changes=changes), component(
        {A: (DAYS[4],), B: DAYS[2:6]}, "c" * 64, receipt="2026-10-10T01:00:00+00:00")


def test_composition_preserves_exact_original_and_separate_lineage_without_io(monkeypatch):
    original, extension = pair()
    for owner, name in ((Path, "open"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, lambda *args, **kwargs: pytest.fail("unexpected IO"))
    result = extend_holding_quotes(original, extension)
    assert result.original is original and result.extension is extension
    assert result.quote_snapshot_sha256 == digest(result.fingerprint_json)
    assert result.quote_snapshot_sha256 not in (original.quote_snapshot_sha256, extension.quote_snapshot_sha256)
    assert extend_holding_quotes(original, extension) == result
    artifact = strict_json(result.fingerprint_json)
    assert artifact["schema"] == "activity-composite-quote-extension-v1"
    assert artifact["atomic_source_vintage_certified"] is False
    for name, source in (("original", original), ("extension", extension)):
        assert artifact[name]["fingerprint_sha256"] == digest(source.fingerprint_json)
        assert getattr(result, name).fingerprint_json is source.fingerprint_json
        assert artifact[name]["snapshot_received_at"] == source.snapshot_received_at
        assert all(any(row is saved for row in result.observations) for saved in source.observations)
    assert result.panel[A].closes[0].as_tuple() == D("100.000").as_tuple()
    assert result.absent_coordinates == original.absent_coordinates
    assert result.panel[A].frozen[2] is True and result.panel[A].observed[3] is True
    assert result.panel[A].closes[3] is None
    with pytest.raises(ValueError, match="unresolved observed"):
        _quote(result.panel[A], 3)
    assert (A, DAYS[4]) in result.requested_coordinates
    assert (A, DAYS[5]) in result.unrequested_coordinates
    assert (A, DAYS[1]) not in result.unrequested_coordinates
    assert result.panel[B].observed[0] is False
    with pytest.raises(TypeError): result.panel[A] = result.panel[A]
    with pytest.raises(FrozenInstanceError): result.quote_snapshot_sha256 = "e" * 64


@pytest.mark.parametrize("day", DAYS[:4])
def test_original_requested_cells_including_absence_cannot_be_replaced(day):
    original, _ = pair()
    extension = component({A: (day,)}, "c" * 64)
    with pytest.raises(ValueError, match="overlaps"):
        extend_holding_quotes(original, extension)


@pytest.mark.parametrize("mutation", ["calendar", "activity", "hash", "scope", "rows", "provenance",
    "typed", "issues", "absent", "unrequested", "series", "decimal_scale", "coordinate", "fingerprint"])
def test_forged_components_and_identity_masquerades_are_rejected(mutation):
    original, extension = pair()
    if mutation == "calendar": extension = replace(extension, calendar=DAYS[:-1])
    elif mutation == "activity": extension = replace(extension, activity_snapshot_sha256="d" * 64)
    elif mutation == "hash": extension = replace(extension, quote_snapshot_sha256=original.quote_snapshot_sha256)
    elif mutation == "scope": extension = replace(extension, read_scope_sha256=original.read_scope_sha256)
    elif mutation in ("rows", "provenance", "typed", "issues"):
        row = original.observations[0]
        if mutation == "provenance": row = replace(row, provenance_json=encoded({"source": "invented"}))
        elif mutation == "typed": row = replace(row, typed_values=tuple(reversed(row.typed_values)))
        elif mutation == "issues": row = replace(row, issues=("invented",))
        original = replace(original, observations=(row, *original.observations[1:]) if mutation != "rows" else ())
    elif mutation == "absent": original = replace(original, absent_coordinates=())
    elif mutation == "unrequested": original = replace(original, unrequested_coordinates=())
    elif mutation in ("series", "decimal_scale"):
        series = original.panel[A]
        series = replace(series, closes=(D("101") if mutation == "series" else D("100"), *series.closes[1:]))
        original = replace(original, series=((A, series),))
    elif mutation == "coordinate": original = replace(original, requested_coordinates=original.requested_coordinates[:-1])
    elif mutation == "fingerprint": original = replace(original, fingerprint_json=b"{}\n")
    with pytest.raises(ValueError): extend_holding_quotes(original, extension)


def test_different_receipts_change_composite_identity_and_empty_extension_is_refused():
    original, extension = pair()
    later = component({A: (DAYS[4],), B: DAYS[2:6]}, "c" * 64, receipt="2026-10-10T02:00:00+00:00")
    assert later.quote_snapshot_sha256 == extension.quote_snapshot_sha256
    assert extend_holding_quotes(original, later).quote_snapshot_sha256 != extend_holding_quotes(original, extension).quote_snapshot_sha256
    empty = adapt_holding_scan(None, DAYS, {}, quote_snapshot_sha256=None,
        read_scope_sha256="d" * 64, activity_snapshot_sha256=ACTIVITY)
    with pytest.raises(ValueError, match="must add"):
        extend_holding_quotes(original, empty)


@pytest.mark.parametrize("value", [100, 100.0, b"100", None, "100.123456789012345678901234567890"])
def test_extension_preserves_all_storage_types_and_long_decimals(value):
    original, _ = pair()
    raw = (DAYS[4].strftime("%Y%m%d"), "100", "101", "99", value, "100", "10")
    extension = component({B: (DAYS[4],)}, "c" * 64, changes={(B, DAYS[4]): raw})
    result = extend_holding_quotes(original, extension)
    assert result.observations[-1] is extension.observations[0]
    assert result.panel[B] == extension.panel[B]
    if type(value) is str:
        assert result.panel[B].closes[4].as_tuple() == D(value).as_tuple()
    else:
        assert result.panel[B].observed[4] is True and result.panel[B].closes[4] is None
