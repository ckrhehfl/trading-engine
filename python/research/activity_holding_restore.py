"""Pure restoration of separately verified BM holding-price audit evidence.

The caller pins original bytes and verifies the complete package and independent
receipt separately. This seam opens no files or database and establishes only
the consistency of retained quote inputs, never event coverage or source truth.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from research.activity_holding_inputs import (
    HoldingInputs, _decode_typed, _iso, _require, _scope, _sha, adapt_holding_scan,
)
from research.activity_preflight import digest, encoded


_SCHEMA = "activity-holding-price-preflight-v1"
_FIELDS = ("open", "high", "low", "close", "volume", "turnover")
_METADATA = {
    "typed_snapshot_sha256", "snapshot_received_at", "fingerprint_scope", "whole_database_hash",
    "BI_snapshot_equality_checked", "progress_counts_recomputed_from_prices",
}


def restore_holding_inputs(
    audit: dict,
    read_scope: dict,
    *,
    quote_snapshot_sha256: str | None,
    activity_snapshot_sha256: str,
    read_scope_sha256: str,
) -> HoldingInputs:
    """Restore the existing immutable adapter result from retained audit records.

    Require the producer's canonical read-scope hash and separately supplied
    quote/activity identifiers. Reconstruct load_scan's exact in-memory shape,
    preserving TEXT/INTEGER/REAL/BLOB/NULL storage, absent requested rows and
    modeled row provenance, then reuse adapt_holding_scan's fingerprint, scope,
    progress and projection checks. No prices are reread or unresolved values
    repaired. Audit row states, issues and coordinate sidecars must agree with
    that projection. Empty scope requires absent metadata and no quote hash.

    This does not verify original package bytes, source lineage or the external
    independent receipt; those remain caller obligations before restoration.
    Signal summary validation likewise belongs to the complete package check.
    """
    _sha(activity_snapshot_sha256)
    _sha(read_scope_sha256)
    _require(type(read_scope) is dict and read_scope.get("schema") == _SCHEMA + ":read-scope-v1"
             and digest(encoded(read_scope)) == read_scope_sha256,
             "holding restoration read-scope schema/hash mismatch")
    _require(type(audit) is dict and set(audit) == {
        "schema", "summary", "read_scope_sha256", "activity_snapshot_sha256", "quote_snapshot_sha256",
        "snapshot_received_at", "fingerprint", "scan_metadata", "requested_code_dates",
        "absent_coordinates", "unrequested_coordinates", "rows"}, "exact holding audit fields required")
    _require(audit["schema"] == _SCHEMA + ":holding-input-audit-v1"
             and audit["read_scope_sha256"] == read_scope_sha256
             and audit["activity_snapshot_sha256"] == read_scope.get("activity_snapshot_sha256")
             == activity_snapshot_sha256 and audit["quote_snapshot_sha256"] == quote_snapshot_sha256,
             "holding restoration identifiers mismatch")
    raw_calendar = read_scope.get("calendar_dates")
    _require(type(raw_calendar) is list and all(type(day) is str for day in raw_calendar),
             "holding restoration calendar must be compact date text")
    calendar = tuple(date.fromisoformat(day) for day in raw_calendar)
    _require([day.strftime("%Y%m%d") for day in calendar] == raw_calendar,
             "holding restoration calendar must be canonical compact dates")
    requested = read_scope.get("requested_code_dates")
    scope = _scope(requested, calendar)
    _require(encoded(audit["requested_code_dates"]) == encoded(requested),
             "holding restoration requested scope mismatch")
    _require(type(audit["rows"]) is list, "holding audit rows must be a list")
    scan = None
    if scope:
        metadata = audit["scan_metadata"]
        _require(type(metadata) is dict and set(metadata) == _METADATA,
                 "exact retained scan metadata required")
        _require(metadata["snapshot_received_at"] == audit["snapshot_received_at"]
                 and metadata["typed_snapshot_sha256"] == quote_snapshot_sha256,
                 "holding audit snapshot receipt/metadata mismatch")
        rows = {}
        for row in audit["rows"]:
            _require(type(row) is dict and set(row) == {
                "code", "observation_date", "state", "issues", "typed_values", "provenance"},
                "exact retained holding row required")
            _require(type(row["code"]) is str, "holding row code must be text")
            key = row["code"], _iso(row["observation_date"]).strftime("%Y%m%d")
            _require(key not in rows, "duplicate retained holding row")
            typed = row["typed_values"]
            values = None
            if typed is not None:
                _require(type(typed) is list and len(typed) == 7, "seven retained typed fields required")
                raw = tuple(_decode_typed(value) for value in typed)
                # Keep non-TEXT SQLite storage explicit; it must remain unresolved.
                values = {field: value if value is None or type(value) is str
                          else {"sqlite_storage": pair} for field, value, pair in zip(_FIELDS, raw[1:], typed[1:])}
            rows[key] = {"values": values, "typed_values": typed, "provenance": row["provenance"]}
        scan = {**metadata, "fingerprint": audit["fingerprint"], "rows": rows}
    else:
        _require(all(audit[key] is None for key in (
            "fingerprint", "scan_metadata", "snapshot_received_at", "quote_snapshot_sha256"))
            and audit["rows"] == [], "cash-only audit must not invent quote evidence")
    restored = adapt_holding_scan(scan, calendar, requested, quote_snapshot_sha256=quote_snapshot_sha256,
        activity_snapshot_sha256=activity_snapshot_sha256, read_scope_sha256=read_scope_sha256)
    for field in ("absent_coordinates", "unrequested_coordinates"):
        expected = [[code, day.isoformat()] for code, day in getattr(restored, field)]
        _require(encoded(audit[field]) == encoded(expected), "holding audit coordinate sidecar mismatch")
    states, panel = Counter(), restored.panel
    positions = {day: index for index, day in enumerate(calendar)}
    for row, observation in zip(audit["rows"], restored.observations):
        series, index = panel[observation.code], positions[observation.observation_date]
        state = ("absent_requested" if observation.typed_values is None else "unresolved_observed" if observation.issues
                 else "frozen" if series.frozen[index] else "locked" if series.locked[index] else "observed")
        _require(row["code"] == observation.code and row["observation_date"] == observation.observation_date.isoformat()
                 and row["state"] == state and type(row["issues"]) is list
                 and row["issues"] == list(observation.issues), "holding audit row order/state/issues mismatch")
        states[state] += 1
    summary = audit["summary"]
    _require(type(summary) is dict, "holding audit summary must be a record")
    for key, value in (
        ("requested_codes", len(scope)), ("requested_code_dates", len(restored.requested_coordinates)),
        ("absent_requested_code_dates", len(restored.absent_coordinates)),
        ("unrequested_code_dates", len(restored.unrequested_coordinates)), ("state_counts", dict(sorted(states.items()))),
    ):
        _require(encoded(summary.get(key)) == encoded(value), "holding audit projection summary mismatch")
    return restored
