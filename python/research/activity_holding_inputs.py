"""Pure original-issue holding requirements and bounded typed-scan adaptation.

This seam consumes in-memory declarations only. It opens no source or database,
plans no cache intersection, and certifies neither source truth, price convention
nor event/no-event coverage. Successor/action requirements remain separate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import re
from types import MappingProxyType

from research.activity_failure_inputs import PANEL_END, PANEL_START, _progress, _typed
from research.activity_inputs import _amount
from research.activity_partition_selection import PartitionSelection
from research.activity_preflight import digest, encoded
from research.activity_replay import DecimalSeries
from research.activity_timing import SessionLagPolicy


_FIELDS = ("open", "high", "low", "close", "volume", "turnover")
_FINGERPRINT_SCOPE = (
    "typed consumed requested rows including absence, panel, complete requested-code "
    "progress, calendar and read scope hash"
)


def _require(condition: bool, message: str) -> None:
    """Reject a malformed declaration without repairing its scope or values."""
    if not condition:
        raise ValueError(message)


def _sha(value: object) -> None:
    """Require an explicit lowercase SHA-256 reference, not its source bytes."""
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
             "holding input pin must be a lowercase SHA-256")


def _calendar(calendar: tuple[date, ...]) -> None:
    """Require the immutable, complete caller-supplied trading calendar."""
    _require(type(calendar) is tuple, "holding calendar must be an immutable tuple")
    SessionLagPolicy._calendar(calendar)


def _iso(value: object) -> date:
    """Parse canonical ISO date text without accepting compact date aliases."""
    _require(type(value) is str, "holding scope dates must be ISO text")
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid holding scope ISO date") from exc
    _require(day.isoformat() == value, "holding scope dates must be canonical ISO text")
    return day


def _scope(value: Mapping[str, Sequence[str]], calendar: tuple[date, ...]) -> dict[str, tuple[date, ...]]:
    """Validate exact issue/date scope; an empty cash-only declaration is legal."""
    _require(isinstance(value, Mapping), "holding scope must be a code/date mapping")
    result = {}
    positions = set(calendar)
    for code, values in value.items():
        _require(type(code) is str and re.fullmatch(r"[0-9A-Z]{6}", code) is not None,
                 "holding scope needs exact six-character issue codes")
        _require(type(values) in (tuple, list) and bool(values), "holding code dates must be nonempty")
        days = tuple(_iso(item) for item in values)
        _require(days == tuple(sorted(set(days))) and set(days) <= positions,
                 "holding code dates must be unique ordered calendar sessions")
        result[code] = days
    return dict(sorted(result.items()))


def holding_requirements(
    selections_d1: tuple[PartitionSelection, ...],
    selections_d2: tuple[PartitionSelection, ...],
    calendar: tuple[date, ...],
    params: Mapping,
    bj_requested_code_dates: Mapping[str, Sequence[str]],
) -> Mapping[str, tuple[str, ...]]:
    """Return possible original-issue entry-through-end requirements for both arms.

    Both complete paired schedules and their source partitions must agree. Each
    eligible signal contributes every calendar session from its policy entry
    through params.end inclusive, even after its scheduled due date. No targets,
    filled lots or quotes are needed. The union must fit BJ's fixed upper bound;
    no signal in either arm yields {}, without requiring a price snapshot.
    """
    _calendar(calendar)
    _require(isinstance(params, Mapping), "holding params must be explicit")
    end_day = _iso(params["end"])
    _require(end_day in calendar, "holding end must be a calendar session")
    end = calendar.index(end_day)
    indices = SessionLagPolicy(1, paired=True).formation_indices(
        calendar, lookback=params["lookback"], holding_sessions=params["holding_sessions"], end_index=end)
    expected = tuple(calendar[index] for index in indices)
    for lag, selections in ((1, selections_d1), (2, selections_d2)):
        _require(type(selections) is tuple and all(isinstance(row, PartitionSelection) for row in selections),
                 "each explicit D1/D2 arm requires immutable PartitionSelection declarations")
        _require(tuple(row.formation_on for row in selections) == expected,
                 "both arms must declare the complete ordered paired formation schedule")
        policy = SessionLagPolicy(lag, paired=True)
        for index, row in zip(indices, selections):
            _require(row.decision_on == policy.execution_on(calendar, index)
                     and row.selection_at == policy.selection_at(calendar, index),
                     "holding arm decision/cutoff disagrees with explicit D1/D2 timing")
    upper = _scope(bj_requested_code_dates, calendar)
    result: dict[str, set[date]] = {}
    for left, right in zip(selections_d1, selections_d2):
        _require((left.eligible_codes, left.source_population_count, left.source_partition_sha256)
                 == (right.eligible_codes, right.source_population_count, right.source_partition_sha256),
                 "paired arms must retain the same source partition and eligible signals")
        for row in (left, right):
            for code in row.eligible_codes:
                _require(type(code) is str and re.fullmatch(r"[0-9A-Z]{6}", code) is not None,
                         "holding signals need exact original issue codes")
                result.setdefault(code, set()).update(calendar[calendar.index(row.decision_on):end + 1])
    _require(all(code in upper and days <= set(upper[code]) for code, days in result.items()),
             "holding requirements exceed BJ's fixed original-issue upper bound")
    return MappingProxyType({code: tuple(day.isoformat() for day in sorted(days))
                             for code, days in sorted(result.items())})


@dataclass(frozen=True)
class HoldingObservation:
    """One requested row's immutable storage receipt and unresolved-value issues.

    typed_values=None means an explicitly absent requested row. provenance_json
    preserves the exact validated provenance as saved.encoded bytes; decoding it
    creates a caller-owned copy. Raw storage text is never replaced by Decimals.
    """

    code: str
    observation_date: date
    typed_values: tuple[tuple[str, str | None], ...] | None
    provenance_json: bytes
    issues: tuple[str, ...]


@dataclass(frozen=True)
class HoldingInputs:
    """Immutable quote projection with explicit request and snapshot coordinates.

    Only requested_coordinates were inspected. Both absent and unrequested panel
    cells have observed=False, but only absent_coordinates assert row absence;
    consumers must not claim observations outside requested_coordinates. Any
    unresolved observed row retains observed=True and the existing replay quote
    boundary rejects it. BI's activity pin is a separate reference, not an
    equality claim. Cash-only inputs have no quote pin/receipt/fingerprint.
    Nothing here supplies action coverage or a source/price certificate.
    """

    calendar: tuple[date, ...]
    activity_snapshot_sha256: str
    quote_snapshot_sha256: str | None
    read_scope_sha256: str
    snapshot_received_at: str | None
    fingerprint_json: bytes | None
    series: tuple[tuple[str, DecimalSeries], ...]
    requested_coordinates: tuple[tuple[str, date], ...]
    absent_coordinates: tuple[tuple[str, date], ...]
    unrequested_coordinates: tuple[tuple[str, date], ...]
    observations: tuple[HoldingObservation, ...]

    @property
    def panel(self) -> Mapping[str, DecimalSeries]:
        """Expose immutable aligned series without losing request sidecars."""
        return MappingProxyType(dict(self.series))


def _decode_typed(value: object) -> object:
    """Validate the reader's canonical SQLite storage encoding, without coercion."""
    _require(type(value) is list and len(value) == 2, "invalid typed scan storage pair")
    kind, text = value
    try:
        if kind == "null" and text is None:
            raw = None
        elif kind == "text" and type(text) is str:
            raw = text
        elif kind == "integer" and type(text) is str:
            raw = int(text)
        elif kind == "real" and type(text) is str:
            raw = float.fromhex(text)
        elif kind == "blob" and type(text) is str:
            raw = bytes.fromhex(text)
        else:
            raise ValueError("unknown typed scan storage")
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid typed scan storage encoding") from exc
    _require(_typed(raw) == value, "noncanonical typed scan storage encoding")
    return raw


def _project(raw: tuple) -> tuple[tuple, tuple[str, ...]]:
    """Reuse the Decimal loader's positive OHLC and flat frozen/locked rules.

    Unresolved storage/NULL/invalid volume or OHLC remains an observed row with
    unknown bar states, ensuring replay refuses it. Zero volume and turnover are
    valid; zero prices are unresolved. No REAL or integer is made into Decimal.
    """
    amounts, issues = [], []
    for field, value in zip(_FIELDS, raw[1:]):
        try:
            amount = _amount(value, positive=field in _FIELDS[:4])
        except ValueError:
            amount = None
        if amount is None:
            issues.append(("null:" if value is None else "invalid:") + field)
        amounts.append(amount)
    opening, high, low, close, volume, turnover = amounts
    if volume is not None and volume != volume.to_integral_value():
        issues.append("invalid:fractional_volume")
    if all(amount is not None for amount in amounts[:4]):
        if not low <= min(opening, close) <= max(opening, close) <= high:
            issues.append("invalid:ohlc_bounds")
    frozen = locked = None
    if not issues:
        flat = opening == high == low == close
        frozen, locked = flat and turnover == 0, flat and turnover > 0
    return (opening, close, turnover, frozen, locked, True), tuple(issues)


def _provenance(day: date, calendar: tuple[date, ...], receipt: str, sha: str) -> dict:
    """Reproduce the bounded reader's declaration; this is modeled availability."""
    return {
        "observation_date": day.isoformat(),
        "available_at": SessionLagPolicy(1).selection_at(calendar, calendar.index(day)).isoformat(),
        "retrieved_at": "unknown", "source": "stored KIS adjusted daily scan_bars",
        "is_final": None, "data_vintage": None, "evidence_level": "assumed",
        "evidence_level_scope": "modeled_availability_only", "source_public_available_at": None,
        "availability_policy": "assumed_next_trading_session_0830_Asia_Seoul",
        "snapshot_received_at": receipt,
        "snapshot_received_at_meaning": "local read snapshot receipt; original row retrieval unknown",
        "typed_snapshot_sha256": sha,
    }


def adapt_holding_scan(
    scan: dict | None,
    calendar: tuple[date, ...],
    requested_code_dates: Mapping[str, Sequence[str]],
    *,
    quote_snapshot_sha256: str | None,
    read_scope_sha256: str,
    activity_snapshot_sha256: str,
) -> HoldingInputs:
    """Validate an existing bounded load_scan return and project immutable quotes.

    The caller pins the exact typed quote snapshot and read scope separately
    from BI activity evidence. The encoded typed-scan-v1 fingerprint, every row,
    progress declaration and modeled provenance must agree; no DB is opened or
    scope reduced. Invalid numeric observations are preserved unresolved, not
    repaired or relabeled absent. Missing action/no-event evidence remains a
    separate obligation. With an empty scope, scan and quote pin must be None:
    a cash-only replay needs no invented quote snapshot.
    """
    _calendar(calendar)
    _sha(activity_snapshot_sha256)
    _sha(read_scope_sha256)
    scope = _scope(requested_code_dates, calendar)
    if not scope:
        _require(scan is None and quote_snapshot_sha256 is None,
                 "cash-only scope must not invent a quote snapshot")
        return HoldingInputs(calendar, activity_snapshot_sha256, None, read_scope_sha256,
                             None, None, (), (), (), (), ())
    _sha(quote_snapshot_sha256)
    keys = {"rows", "fingerprint", "typed_snapshot_sha256", "snapshot_received_at", "fingerprint_scope",
            "whole_database_hash", "BI_snapshot_equality_checked", "progress_counts_recomputed_from_prices"}
    _require(type(scan) is dict and set(scan) == keys, "exact bounded typed scan return required")
    _require(all(scan[key] is False for key in
                 ("whole_database_hash", "BI_snapshot_equality_checked", "progress_counts_recomputed_from_prices"))
             and scan["fingerprint_scope"] == _FINGERPRINT_SCOPE, "unsupported typed snapshot claims")
    fingerprint = scan["fingerprint"]
    _require(type(fingerprint) is dict and set(fingerprint) == {
        "schema", "calendar_dates", "read_scope_sha256", "intersection_code_dates", "panel", "progress", "rows"},
        "exact typed scan fingerprint required")
    expected_scope = {code: [day.isoformat() for day in days] for code, days in scope.items()}
    _require(fingerprint["schema"] == "activity-cached-price-parity-v1:typed-scan-v1"
             and fingerprint["calendar_dates"] == [day.strftime("%Y%m%d") for day in calendar]
             and fingerprint["read_scope_sha256"] == read_scope_sha256
             and fingerprint["intersection_code_dates"] == expected_scope,
             "typed scan calendar/read scope mismatch")
    fingerprint_json = encoded(fingerprint)
    _require(scan["typed_snapshot_sha256"] == quote_snapshot_sha256 == digest(fingerprint_json),
             "typed quote snapshot fingerprint/pin mismatch")
    receipt = scan["snapshot_received_at"]
    _require(type(receipt) is str, "snapshot receipt must be explicit aware ISO text")
    try:
        timestamp = datetime.fromisoformat(receipt)
    except ValueError as exc:
        raise ValueError("invalid snapshot receipt") from exc
    _require(timestamp.utcoffset() is not None and timestamp.isoformat() == receipt,
             "snapshot receipt must be canonical aware ISO text")
    _require(fingerprint["panel"] == [[["integer", "1"], ["text", "20190102"], ["text", "20260918"]]],
             "exact spent typed scan panel required")
    progress = fingerprint["progress"]
    _require(type(progress) is list and len(progress) == len(scope), "complete typed scan progress required")
    bounds = {}
    for item, code in zip(progress, scope):
        _require(type(item) is list and len(item) == 2 and item[0] == code
                 and type(item[1]) is list and len(item[1]) == 1
                 and type(item[1][0]) is list and len(item[1][0]) == 6,
                 "unique ordered requested-code progress required")
        bounds[code] = _progress(code, [tuple(_decode_typed(value) for value in item[1][0])])
    coordinates = tuple((code, day) for code, days in scope.items() for day in days)
    _require(all(PANEL_START <= day <= PANEL_END and calendar.index(day) + 1 < len(calendar)
                 for _, day in coordinates), "requested quotes outside bounded scan calendar")
    rows, typed_rows = scan["rows"], fingerprint["rows"]
    _require(type(rows) is dict and set(rows) == {(code, day.strftime("%Y%m%d")) for code, day in coordinates}
             and type(typed_rows) is list and len(typed_rows) == len(coordinates),
             "typed scan rows must exactly equal requested code/date scope")
    observations, absent, series = [], [], []
    projected = {}
    present_counts = {code: 0 for code in scope}
    for coordinate, typed_row in zip(coordinates, typed_rows):
        code, day = coordinate
        _require(type(typed_row) is list and len(typed_row) == 3 and typed_row[:2] == [code, day.isoformat()],
                 "unique ordered typed scan row coordinates required")
        row = rows[code, day.strftime("%Y%m%d")]
        _require(type(row) is dict and set(row) == {"values", "typed_values", "provenance"},
                 "exact bounded scan row fields required")
        _require(type(row["provenance"]) is dict
                 and row["provenance"] == _provenance(day, calendar, receipt, quote_snapshot_sha256),
                 "typed scan row provenance/pin mismatch")
        raw_types = typed_row[2]
        if raw_types == "absent":
            _require(row["typed_values"] is None and row["values"] is None,
                     "absent typed row cannot carry values")
            frozen_types, issues = None, ("absent_requested_row:cause_unknown",)
            projected[coordinate] = (None, None, None, None, None, False)
            absent.append(coordinate)
        else:
            _require(type(raw_types) is list and len(raw_types) == 7 and row["typed_values"] == raw_types,
                     "typed scan row storage mismatch")
            raw = tuple(_decode_typed(value) for value in raw_types)
            _require(raw[0] == day.strftime("%Y%m%d") and type(raw[0]) is str,
                     "typed scan observation date mismatch")
            expected_values = {field: value if value is None or type(value) is str
                               else {"sqlite_storage": _typed(value)} for field, value in zip(_FIELDS, raw[1:])}
            _require(type(row["values"]) is dict and row["values"] == expected_values,
                     "typed scan values disagree with raw storage")
            present_counts[code] += 1
            _require(bounds[code].first_date <= day <= bounds[code].last_date
                     and present_counts[code] <= bounds[code].bars, "typed scan row/progress disagreement")
            frozen_types = tuple(tuple(value) for value in raw_types)
            projected[coordinate], issues = _project(raw)
        observations.append(HoldingObservation(code, day, frozen_types, encoded(row["provenance"]), issues))
    unrequested = tuple((code, day) for code in scope for day in calendar if (code, day) not in projected)
    for code in scope:
        values = [projected.get((code, day), (None, None, None, None, None, False)) for day in calendar]
        series.append((code, DecimalSeries(*(tuple(value[index] for value in values) for index in range(6)))))
    return HoldingInputs(calendar, activity_snapshot_sha256, quote_snapshot_sha256, read_scope_sha256,
                         receipt, fingerprint_json, tuple(series), coordinates, tuple(absent), unrequested,
                         tuple(observations))
