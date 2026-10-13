"""Immutable raw LG/LX episode projection over retained full BM/BP lineage.

Raw rows have their own artifact and unit plan, never SQLite typed storage.
This pure view performs no IO or replay. Consumers must call
require_raw_coordinate before using affected-code prices. The existing replay
enforces episode boundaries only when supplied the explicit raw_quote_view;
passing the projected panel alone does not establish that boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from decimal import Decimal
import json
from types import MappingProxyType

from research.activity_accounting import PriceBasis
from research.activity_preflight import digest, encoded, require, strict_json, _pin
from research.activity_quote_extension import CompositeHoldingInputs, _same, extend_holding_quotes
from research.activity_raw_episode import RawQuoteObservation, TARGET_CODES, validate_raw_observation
from research.activity_replay import DecimalSeries

SCHEMA = "activity-raw-episode-quote-view-v1"
PLAN_SCHEMA = "activity-raw-episode-unit-plan-v1"
ARTIFACT_SCHEMA = "activity-raw-episode-quotes-v1"
UNITS = dict(price_unit="KRW_per_raw_share", volume_unit="raw_shares", turnover_unit="KRW",
             quotation_basis="KRX_unadjusted_raw")
NAMES = ("opens", "closes", "turnover", "frozen", "locked", "observed")


def _value(value):
    """Canonical source representation without float conversion or type labels."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if is_dataclass(value):
        return {field.name: _value(getattr(value, field.name)) for field in fields(value)}
    if type(value) in (tuple, list):
        return [_value(item) for item in value]
    if type(value) is dict:
        return {key: _value(item) for key, item in value.items()}
    require(value is None or type(value) in (str, int, bool), "raw artifact cannot contain floats")
    return value


def calendar_sha256(calendar: tuple[date, ...]) -> str:
    return digest(json.dumps([str(day) for day in calendar], separators=(",", ":")).encode())


def encode_raw_artifact(base: CompositeHoldingInputs, observations: tuple[RawQuoteObservation, ...],
                        unit_plan_sha256: str) -> bytes:
    """Encode declared rows; acquisition/body-pin verification remains separate."""
    return encoded(dict(schema=ARTIFACT_SCHEMA, activity_snapshot_sha256=base.activity_snapshot_sha256,
        base_quote_snapshot_sha256=base.quote_snapshot_sha256, calendar_sha256=calendar_sha256(base.calendar),
        unit_plan_sha256=unit_plan_sha256, rows=_value(observations)))


@dataclass(frozen=True)
class RawEpisodeQuoteInputs:
    """Separate execution projection; the complete original base stays verbatim."""

    base: CompositeHoldingInputs
    calendar: tuple[date, ...]
    activity_snapshot_sha256: str
    quote_snapshot_sha256: str
    fingerprint_json: bytes
    source_artifact_sha256: str
    source_artifact_json: bytes
    unit_plan_sha256: str
    unit_plan_json: bytes
    raw_observations: tuple[RawQuoteObservation, ...]
    series: tuple[tuple[str, DecimalSeries], ...]
    requested_coordinates: tuple[tuple[str, date], ...]
    absent_coordinates: tuple[tuple[str, date], ...]
    unresolved_coordinates: tuple[tuple[str, date], ...]
    no_trade_coordinates: tuple[tuple[str, date], ...]
    unrequested_coordinates: tuple[tuple[str, date], ...]
    summary_json: bytes

    @property
    def original(self):
        return self.base.original

    @property
    def extension(self):
        return self.base.extension

    @property
    def base_requested_coordinates(self):
        return self.base.requested_coordinates

    @property
    def base_absent_coordinates(self):
        return self.base.absent_coordinates

    @property
    def panel(self):
        return MappingProxyType(dict(self.series))

    @property
    def summary(self):
        return strict_json(self.summary_json)


def build_raw_quote_view(base: CompositeHoldingInputs, observations: tuple[RawQuoteObservation, ...], *,
                         source_artifact: bytes, source_artifact_sha256: str,
                         unit_plan: bytes, unit_plan_sha256: str) -> RawEpisodeQuoteInputs:
    """Validate exact raw scope and source bytes, retaining full original evidence."""
    require(type(base) is CompositeHoldingInputs and _same(base, extend_holding_quotes(base.original, base.extension)),
            "validated full original/BP composite required")
    for raw, pin in ((source_artifact, source_artifact_sha256), (unit_plan, unit_plan_sha256)):
        _pin(pin)
        require(type(raw) is bytes and digest(raw) == pin, "raw artifact/unit plan byte pin mismatch")
    plan = strict_json(unit_plan)
    require(type(plan) is dict and set(plan) == {"schema", "activity_snapshot_sha256", "base_quote_snapshot_sha256",
        "calendar_sha256", "episodes", *UNITS} and plan["schema"] == PLAN_SCHEMA
        and encoded(plan) == unit_plan and plan["activity_snapshot_sha256"] == base.activity_snapshot_sha256
        and plan["base_quote_snapshot_sha256"] == base.quote_snapshot_sha256
        and plan["calendar_sha256"] == calendar_sha256(base.calendar)
        and all(plan[name] == value for name, value in UNITS.items()), "exact raw unit/base/calendar plan required")
    episodes = plan["episodes"]
    require(type(episodes) is list and len(episodes) == len(TARGET_CODES), "exact LG/LX episodes required")
    scope, seen = set(), []
    for row in episodes:
        require(type(row) is dict and set(row) == {"code", "start", "end"} and row["code"] in TARGET_CODES,
                "exact raw episode interval required")
        start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
        require(str(start) == row["start"] and str(end) == row["end"] and start in base.calendar and end in base.calendar
                and start <= end, "raw episode endpoints must be ordered calendar sessions")
        seen.append(row["code"])
        scope.update((row["code"], day) for day in base.calendar if start <= day <= end)
    require(seen == sorted(TARGET_CODES), "unique ordered LG/LX episode intervals required")
    require(type(observations) is tuple and all(type(row) is RawQuoteObservation for row in observations),
            "immutable raw observations required")
    coordinates = []
    for row in observations:
        validate_raw_observation(row)
        coordinates.append((row.code, date.fromisoformat(row.requested_bas_dd)))
    require(tuple(coordinates) == tuple(sorted(scope)), "raw rows must exactly match unique ordered request scope")
    require(source_artifact == encode_raw_artifact(base, observations, unit_plan_sha256),
            "raw artifact rows/base/unit-plan lineage mismatch")
    raw_rows = dict(zip(coordinates, observations))
    base_panel = base.panel
    codes = sorted(set(base_panel) | TARGET_CODES)
    series, absent, unresolved, no_trade = [], [], [], []
    requested = set(key for key in base.requested_coordinates if key[0] not in TARGET_CODES) | scope
    for key, row in raw_rows.items():
        if row.issues == ("target_absent_cause_unknown",):
            absent.append(key)
        elif row.status != "observed":
            unresolved.append(key)
        elif row.state == "observed_no_trade_zero_prices":
            no_trade.append(key)
    absent.extend(key for key in base.absent_coordinates if key[0] not in TARGET_CODES)
    for code in codes:
        if code not in TARGET_CODES:
            series.append((code, base_panel[code]))
            continue
        columns = [[] for _ in NAMES]
        for day in base.calendar:
            row = raw_rows.get((code, day))
            if row is None or row.state == "observed_no_trade_zero_prices" or row.issues == ("target_absent_cause_unknown",):
                # DecimalSeries forbids values, even frozen=True, when absent.
                # Exact observed-zero source evidence stays in raw_observations.
                values = (None, None, None, None, None, False)
            elif row.status != "observed":
                values = (None, None, None, None, None, True)
            else:
                values = (row.open, row.close, row.turnover, row.state == "frozen", row.state == "locked", True)
            for column, value in zip(columns, values):
                column.append(value)
        series.append((code, DecimalSeries(*(tuple(column) for column in columns))))
    unrequested = tuple((code, day) for code in codes for day in base.calendar if (code, day) not in requested)
    identity = encoded(dict(schema=SCHEMA, activity_snapshot_sha256=base.activity_snapshot_sha256,
        calendar_sha256=calendar_sha256(base.calendar), base_quote_snapshot_sha256=base.quote_snapshot_sha256,
        base_lineage=strict_json(base.fingerprint_json), source_artifact_sha256=source_artifact_sha256,
        unit_plan_sha256=unit_plan_sha256, source_unit_plan=plan, **UNITS,
        original_requested_absences_preserved=True, atomic_source_vintage_certified=False,
        price_basis_certified=False, unit_bridge_certified=False))
    quote_sha = digest(identity)
    require(quote_sha not in {base.quote_snapshot_sha256, base.original.quote_snapshot_sha256,
        base.extension.quote_snapshot_sha256, source_artifact_sha256, unit_plan_sha256}, "raw view needs a separate identity")
    summary = encoded(dict(schema=SCHEMA + ":summary-v1", raw_requested_code_dates=len(scope),
        raw_absent_code_dates=len([key for key in absent if key[0] in TARGET_CODES]),
        raw_unresolved_code_dates=len(unresolved), raw_no_trade_code_dates=len(no_trade),
        source_artifact_sha256=source_artifact_sha256, unit_plan_sha256=unit_plan_sha256,
        base_quote_snapshot_sha256=base.quote_snapshot_sha256, quote_snapshot_sha256=quote_sha,
        original_requested_absences_preserved=True, episode_end_is_actual_exit=False,
        original_end_obligation_discharged=False, legacy_replay_boundary_guard_integrated=False,
        source_truth_certified=False, historical_vintage_certified=False, unit_bridge_certified=False,
        returns_computed=False, books_computed=False))
    return RawEpisodeQuoteInputs(base, base.calendar, base.activity_snapshot_sha256, quote_sha, identity,
        source_artifact_sha256, source_artifact, unit_plan_sha256, unit_plan, observations, tuple(series),
        tuple(sorted(requested)), tuple(sorted(absent)), tuple(sorted(unresolved)), tuple(sorted(no_trade)), unrequested, summary)


def validate_raw_quote_view(view: RawEpisodeQuoteInputs) -> RawEpisodeQuoteInputs:
    """Refreeze every projection instead of trusting a replaced frozen dataclass."""
    require(type(view) is RawEpisodeQuoteInputs, "explicit raw quote view required")
    fresh = build_raw_quote_view(view.base, view.raw_observations, source_artifact=view.source_artifact_json,
        source_artifact_sha256=view.source_artifact_sha256, unit_plan=view.unit_plan_json, unit_plan_sha256=view.unit_plan_sha256)
    require(_same(view, fresh), "raw view disagrees with retained artifact/unit plan")
    return fresh


def restore_raw_quote_view(base: CompositeHoldingInputs, source_artifact: bytes, source_artifact_sha256: str,
                           unit_plan: bytes, unit_plan_sha256: str) -> RawEpisodeQuoteInputs:
    """Restore only the versioned raw representation, never KIS typed storage."""
    artifact = strict_json(source_artifact)
    require(type(artifact) is dict and set(artifact) == {"schema", "activity_snapshot_sha256",
        "base_quote_snapshot_sha256", "calendar_sha256", "unit_plan_sha256", "rows"}
        and artifact["schema"] == ARTIFACT_SCHEMA and encoded(artifact) == source_artifact
        and type(artifact["rows"]) is list, "canonical raw artifact required")
    names = {field.name for field in fields(RawQuoteObservation)}
    observations = []
    for raw in artifact["rows"]:
        require(type(raw) is dict and set(raw) == names, "exact raw observation fields required")
        row = dict(raw)
        for name in ("open", "high", "low", "close", "volume", "turnover"):
            require(row[name] is None or type(row[name]) is str, "raw Decimal source must be text")
            row[name] = None if row[name] is None else Decimal(row[name])
        for name in ("issues", "row_positions", "original_texts", "canonical_decimal_strings"):
            require(type(row[name]) is list, "raw observation sequences must be explicit arrays")
            if name in ("original_texts", "canonical_decimal_strings"):
                require(all(type(pair) is list for pair in row[name]), "raw field pairs must be explicit arrays")
                row[name] = tuple(tuple(pair) for pair in row[name])
            else:
                row[name] = tuple(row[name])
        observations.append(validate_raw_observation(RawQuoteObservation(**row)))
    return build_raw_quote_view(base, tuple(observations), source_artifact=source_artifact,
        source_artifact_sha256=source_artifact_sha256, unit_plan=unit_plan, unit_plan_sha256=unit_plan_sha256)


def require_raw_coordinate(view: RawEpisodeQuoteInputs, code: str, session: date) -> RawQuoteObservation | None:
    """Mandatory raw-aware buy/sale/mark boundary; other codes retain base units."""
    require(type(session) is date, "explicit raw view/session required")
    view = validate_raw_quote_view(view)
    if code not in TARGET_CODES:
        return None
    row = next((row for row in view.raw_observations if row.code == code and row.requested_bas_dd == session.strftime("%Y%m%d")), None)
    require(row is not None, "affected lot crossed the registered raw episode boundary")
    require(row.status == "observed" or row.issues == ("target_absent_cause_unknown",), "unresolved raw observation")
    return row


def raw_price_basis(view: RawEpisodeQuoteInputs, code: str, session: date) -> PriceBasis:
    """Raw/raw factor one by declared units, never a vendor adjustment certificate."""
    row = require_raw_coordinate(view, code, session)
    require(row is not None and row.state in ("observed", "locked") and row.close is not None,
            "raw basis requires a resolved positive nonfrozen raw close")
    return PriceBasis(code, session, row.close, row.close, view.quote_snapshot_sha256,
        "declared_raw_share_unit;raw_artifact:" + view.source_artifact_sha256 + ";unit_plan:" + view.unit_plan_sha256)
