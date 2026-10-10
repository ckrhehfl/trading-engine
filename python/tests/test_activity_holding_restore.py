"""Audit restoration uses actual producer code over temporary synthetic SQLite."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from research import activity_holding_preflight as producer
from research import activity_holding_restore as module
from research import activity_preflight as saved
from research import activity_price_parity as prices
from test_activity_holding_preflight import fixture


def audit_case(tmp_path, monkeypatch, *, cash=False, change=None):
    """Capture the real adapter input and the producer's retained JSON audit."""
    _, output, _, spec, store, _, calendar = fixture(tmp_path, monkeypatch, cash=cash)
    if change:
        field, value = change
        assert field in ("open", "high", "low", "close", "volume", "turnover")
        with sqlite3.connect(Path(spec["inputs"]["scan_database"]["path"])) as connection:
            connection.execute(f"UPDATE scan_bars SET {field}=? WHERE bsop_date=?", (value, calendar[65].strftime("%Y%m%d")))
    output.mkdir(mode=0o700)
    captures = []
    adapt = producer.adapt_holding_scan

    def capture(*args, **kwargs):
        """Retain the genuine scan return and adapter result for exact comparison."""
        result = adapt(*args, **kwargs)
        captures.append((deepcopy(args[0]), result))
        return result

    monkeypatch.setattr(producer, "adapt_holding_scan", capture)
    result = producer.evaluate_inputs(spec, saved.PinnedInputs(store.__getitem__), output)
    audit = saved.strict_json((output / "holding-input-audit.json").read_bytes())
    scope = saved.strict_json((output / "read-scope.json").read_bytes())
    pins = {key: result[key] for key in ("quote_snapshot_sha256", "activity_snapshot_sha256", "read_scope_sha256")}
    return audit, scope, pins, captures[0]


@pytest.mark.parametrize("cash", [False, True], ids=["requested-quotes", "empty-union"])
def test_real_producer_audit_restores_exact_adapter_without_io(tmp_path, monkeypatch, cash):
    """Preserve every typed value/provenance/state and requested panel boundary."""
    audit, scope, pins, (_, original) = audit_case(tmp_path, monkeypatch, cash=cash)
    before = deepcopy((audit, scope))
    monkeypatch.setattr(prices, "load_scan", lambda *a, **kw: pytest.fail("restoration reread quotes"))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("restoration opened database"))
    monkeypatch.setattr(Path, "open", lambda *a, **kw: pytest.fail("restoration opened a file"))
    restored = module.restore_holding_inputs(audit, scope, **pins)
    assert restored == original
    assert (audit, scope) == before
    assert restored.activity_snapshot_sha256 == "3" * 64 != restored.quote_snapshot_sha256
    if cash:
        assert restored.quote_snapshot_sha256 is restored.snapshot_received_at is restored.fingerprint_json is None
        assert restored.observations == restored.series == restored.requested_coordinates == ()
    else:
        assert len(restored.requested_coordinates) == 69
        assert restored.absent_coordinates == (("000001", restored.calendar[63]),)
        assert ("000001", restored.calendar[0]) in restored.unrequested_coordinates
        assert restored.panel["000001"].frozen[61] is True
        assert restored.panel["000001"].locked[62] is True
        assert restored.panel["000001"].observed[64] is True
        assert restored.panel["000001"].closes[64] is None
        assert restored.panel["000001"].observed[60] is restored.panel["000001"].observed[130] is False
        assert restored.observations[2].typed_values is None
        assert restored.observations[3].typed_values[4] == ("null", None)
        assert audit["summary"]["state_counts"] == {
            "absent_requested": 1, "frozen": 1, "locked": 1, "observed": 65, "unresolved_observed": 1}
        audit["fingerprint"]["rows"][0][2][1][1] = "caller mutation"
        assert restored.fingerprint_json == original.fingerprint_json
        assert restored.observations == original.observations


@pytest.mark.parametrize("field,value,issue", [
    ("close", "100.123456789012345678901234567890", None),
    ("turnover", "100000000000000000000000000.0000000001", None),
    ("open", 100, "invalid:open"), ("close", 100.0, "invalid:close"),
    ("high", b"101", "invalid:high"), ("low", None, "null:low"),
    ("turnover", "NaN", "invalid:turnover"), ("volume", "1.5", "invalid:fractional_volume"),
])
def test_lossless_typed_storage_and_unresolved_values_survive_restore(tmp_path, monkeypatch, field, value, issue):
    """Never coerce INTEGER/REAL/BLOB into TEXT or round long Decimal strings."""
    audit, scope, pins, (_, original) = audit_case(tmp_path, monkeypatch, change=(field, value))
    restored = module.restore_holding_inputs(audit, scope, **pins)
    assert restored == original
    observation = restored.observations[4]
    if issue:
        assert issue in observation.issues and restored.panel["000001"].observed[65] is True
    elif field == "close":
        assert restored.panel["000001"].closes[65].as_tuple() == Decimal(value).as_tuple()
    else:
        assert restored.panel["000001"].turnover[65].as_tuple() == Decimal(value).as_tuple()


@pytest.mark.parametrize("corruption", [
    "quote_pin", "activity_pin", "scope_pin", "scope_dates", "calendar", "audit_scope", "audit_activity",
    "audit_quote", "receipt", "metadata_receipt", "metadata_hash", "metadata_flag", "metadata_extra",
    "missing_row", "extra_row", "duplicate_row", "row_order", "row_date", "row_code", "state", "issues",
    "typed_value", "typed_encoding", "typed_date", "typed_identity", "provenance", "fingerprint", "progress",
    "absent", "unrequested", "summary", "summary_bool", "audit_extra",
])
def test_conflicting_scope_rows_receipts_storage_and_projection_are_rejected(tmp_path, monkeypatch, corruption):
    """Reject disagreement without weakening existing typed-scan adapter checks."""
    audit, scope, pins, _ = audit_case(tmp_path, monkeypatch)
    row = audit["rows"][0]
    if corruption in {"quote_pin", "activity_pin", "scope_pin"}:
        pins[{"quote_pin": "quote_snapshot_sha256", "activity_pin": "activity_snapshot_sha256",
              "scope_pin": "read_scope_sha256"}[corruption]] = "e" * 64
    elif corruption == "scope_dates": scope["requested_code_dates"]["000001"].pop()
    elif corruption == "calendar": scope["calendar_dates"][0] = "2020-01-01"
    elif corruption == "audit_scope": audit["requested_code_dates"]["000001"].pop()
    elif corruption == "audit_activity": audit["activity_snapshot_sha256"] = "e" * 64
    elif corruption == "audit_quote": audit["quote_snapshot_sha256"] = "e" * 64
    elif corruption == "receipt": audit["snapshot_received_at"] = "2026-10-09T00:00:00"
    elif corruption == "metadata_receipt": audit["scan_metadata"]["snapshot_received_at"] = "2026-10-09T00:00:00+00:00"
    elif corruption == "metadata_hash": audit["scan_metadata"]["typed_snapshot_sha256"] = "e" * 64
    elif corruption == "metadata_flag": audit["scan_metadata"]["whole_database_hash"] = True
    elif corruption == "metadata_extra": audit["scan_metadata"]["rows"] = {}
    elif corruption == "missing_row": audit["rows"].pop()
    elif corruption == "extra_row": audit["rows"].append({**deepcopy(row), "code": "000006"})
    elif corruption == "duplicate_row": audit["rows"].append(deepcopy(row))
    elif corruption == "row_order": audit["rows"].reverse()
    elif corruption == "row_date": row["observation_date"] = "20200302"
    elif corruption == "row_code": row["code"] = 1
    elif corruption == "state": row["state"] = "observed"
    elif corruption == "issues": row["issues"] = ["invented"]
    elif corruption == "typed_value": row["typed_values"][1][1] = "101"
    elif corruption == "typed_encoding": row["typed_values"][1] = ["integer", "0100"]
    elif corruption == "typed_date": row["typed_values"][0][1] = "20200303"
    elif corruption == "typed_identity": row["typed_values"][1] = ["integer", "100"]
    elif corruption == "provenance": row["provenance"]["retrieved_at"] = "2026-10-09T00:00:00+00:00"
    elif corruption == "fingerprint": audit["fingerprint"]["rows"][0][2][1][1] = "101"
    elif corruption == "progress": audit["fingerprint"]["progress"][0][1][0][4][1] = "failed"
    elif corruption == "absent": audit["absent_coordinates"] = []
    elif corruption == "unrequested": audit["unrequested_coordinates"] = []
    elif corruption == "summary": audit["summary"]["state_counts"]["frozen"] = 0
    elif corruption == "summary_bool": audit["summary"]["state_counts"]["frozen"] = True
    elif corruption == "audit_extra": audit["certified"] = True
    with pytest.raises(ValueError):
        module.restore_holding_inputs(audit, scope, **pins)


@pytest.mark.parametrize("corruption", ["hash", "receipt", "metadata", "fingerprint", "rows", "sidecar", "summary"])
def test_cash_only_cannot_invent_quote_evidence(tmp_path, monkeypatch, corruption):
    """An empty requested union restores the adapter's None scan, never a hash."""
    audit, scope, pins, _ = audit_case(tmp_path, monkeypatch, cash=True)
    if corruption == "hash": audit["quote_snapshot_sha256"] = pins["quote_snapshot_sha256"] = "e" * 64
    elif corruption == "receipt": audit["snapshot_received_at"] = "2026-10-09T00:00:00+00:00"
    elif corruption == "metadata": audit["scan_metadata"] = {}
    elif corruption == "fingerprint": audit["fingerprint"] = {}
    elif corruption == "rows": audit["rows"] = [{}]
    elif corruption == "sidecar": audit["absent_coordinates"] = [["000001", "2020-03-02"]]
    elif corruption == "summary": audit["summary"]["requested_code_dates"] = 1
    with pytest.raises(ValueError):
        module.restore_holding_inputs(audit, scope, **pins)


def test_repinned_fingerprint_still_requires_matching_retained_row_types(tmp_path, monkeypatch):
    """A newly pinned malformed audit cannot discard INTEGER storage identity."""
    audit, scope, pins, _ = audit_case(tmp_path, monkeypatch)
    audit["fingerprint"]["rows"][0][2][1] = ["integer", "100"]
    quote = saved.digest(saved.encoded(audit["fingerprint"]))
    pins["quote_snapshot_sha256"] = audit["quote_snapshot_sha256"] = quote
    audit["scan_metadata"]["typed_snapshot_sha256"] = quote
    for row in audit["rows"]:
        row["provenance"]["typed_snapshot_sha256"] = quote
    with pytest.raises(ValueError, match="storage mismatch"):
        module.restore_holding_inputs(audit, scope, **pins)
