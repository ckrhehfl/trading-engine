"""Pure composite quotes with separate original and extension scan lineage.

The composite is a new content-addressed artifact, not a typed scan or one
atomic source vintage. Caller-pinned package/evidence verification, actual
read authorization and source/unit/event certification remain separate.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from research.activity_holding_inputs import (
    HoldingInputs, HoldingObservation, _decode_typed, _FINGERPRINT_SCOPE,
    _require, adapt_holding_scan,
)
from research.activity_preflight import digest, encoded, strict_json
from research.activity_replay import DecimalSeries


def _same(actual: object, expected: object) -> bool:
    """Compare exact immutable projection shapes, including Decimal exponents."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, Decimal):
        return actual.as_tuple() == expected.as_tuple()
    if is_dataclass(expected):
        return all(_same(getattr(actual, field.name), getattr(expected, field.name)) for field in fields(expected))
    if type(expected) is tuple:
        return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _validated(component: HoldingInputs) -> None:
    """Reapply the existing scan boundary instead of trusting a forged dataclass."""
    _require(type(component) is HoldingInputs, "quote components require original HoldingInputs")
    scan, requested = None, {}
    if component.fingerprint_json is not None:
        _require(type(component.fingerprint_json) is bytes, "component fingerprint must be immutable bytes")
        fingerprint = strict_json(component.fingerprint_json)
        _require(type(fingerprint) is dict and encoded(fingerprint) == component.fingerprint_json,
                 "component fingerprint must be canonical")
        requested = fingerprint.get("intersection_code_dates")
        _require(type(component.observations) is tuple, "component observations must be immutable")
        rows = {}
        for observation in component.observations:
            _require(type(observation) is HoldingObservation and type(observation.code) is str
                     and type(observation.observation_date) is date
                     and type(observation.provenance_json) is bytes,
                     "component observation receipt shape mismatch")
            key = observation.code, observation.observation_date.strftime("%Y%m%d")
            _require(key not in rows, "duplicate component observation")
            typed, values = observation.typed_values, None
            if typed is not None:
                _require(type(typed) is tuple and len(typed) == 7
                         and all(type(pair) is tuple and len(pair) == 2 and type(pair[0]) is str
                                 and (pair[1] is None or type(pair[1]) is str) for pair in typed),
                         "component typed storage must be immutable seven-field tuples")
                typed = [list(pair) for pair in typed]
                raw = tuple(_decode_typed(pair) for pair in typed)
                values = {field: value if value is None or type(value) is str
                          else {"sqlite_storage": pair} for field, value, pair in zip(
                              ("open", "high", "low", "close", "volume", "turnover"), raw[1:], typed[1:])}
            provenance = strict_json(observation.provenance_json)
            _require(encoded(provenance) == observation.provenance_json, "component provenance must be canonical")
            rows[key] = {"values": values, "typed_values": typed, "provenance": provenance}
        scan = {"rows": rows, "fingerprint": fingerprint,
                "typed_snapshot_sha256": component.quote_snapshot_sha256,
                "snapshot_received_at": component.snapshot_received_at,
                "fingerprint_scope": _FINGERPRINT_SCOPE, "whole_database_hash": False,
                "BI_snapshot_equality_checked": False, "progress_counts_recomputed_from_prices": False}
    restored = adapt_holding_scan(scan, component.calendar, requested,
        quote_snapshot_sha256=component.quote_snapshot_sha256,
        read_scope_sha256=component.read_scope_sha256,
        activity_snapshot_sha256=component.activity_snapshot_sha256)
    _require(_same(component, restored), "component projection disagrees with its retained typed evidence")


@dataclass(frozen=True)
class CompositeHoldingInputs:
    """One replay identity with verbatim, independently received component scans.

    original/extension retain their distinct quote hashes, read scopes, receipt
    times, typed fingerprints and row provenance. fingerprint_json explicitly
    describes this composition; it makes no atomic-vintage or source-truth claim.
    An original requested absence can never be filled by the extension.
    """

    original: HoldingInputs
    extension: HoldingInputs
    calendar: tuple[date, ...]
    activity_snapshot_sha256: str
    quote_snapshot_sha256: str
    fingerprint_json: bytes
    series: tuple[tuple[str, DecimalSeries], ...]
    requested_coordinates: tuple[tuple[str, date], ...]
    absent_coordinates: tuple[tuple[str, date], ...]
    unrequested_coordinates: tuple[tuple[str, date], ...]
    observations: tuple[HoldingObservation, ...]

    @property
    def panel(self) -> Mapping[str, DecimalSeries]:
        """Expose the aligned composite projection with explicit request sidecars."""
        return MappingProxyType(dict(self.series))


def extend_holding_quotes(original: HoldingInputs, extension: HoldingInputs) -> CompositeHoldingInputs:
    """Compose disjoint validated requests without changing either component.

    Same-code unrequested cells may be added, but even byte-identical overlap is
    rejected to retain unambiguous scan provenance. Recompute each input from its
    typed evidence first. The new hash covers both exact fingerprints and their
    independent receipt/scope identities, including modeled provenance links.
    This function performs no IO, quote repair, event decoding or replay.
    """
    _validated(original)
    _validated(extension)
    _require(original.calendar == extension.calendar
             and original.activity_snapshot_sha256 == extension.activity_snapshot_sha256,
             "quote extension calendar/activity identity mismatch")
    _require(bool(extension.requested_coordinates), "quote extension must add requested coordinates")
    _require(original.quote_snapshot_sha256 != extension.quote_snapshot_sha256
             and original.read_scope_sha256 != extension.read_scope_sha256,
             "quote extension must retain distinct scan and read-scope identities")
    _require(not set(original.requested_coordinates) & set(extension.requested_coordinates),
             "quote extension overlaps original requested coordinates")
    observations = tuple(sorted((*original.observations, *extension.observations),
                                key=lambda row: (row.code, row.observation_date)))
    requested = tuple((row.code, row.observation_date) for row in observations)
    absent = tuple(sorted((*original.absent_coordinates, *extension.absent_coordinates)))
    codes = sorted({code for code, _ in requested})
    sources = {}
    for component in (original, extension):
        panel = component.panel
        sources.update({coordinate: panel[coordinate[0]] for coordinate in component.requested_coordinates})
    series, unrequested = [], []
    names = ("opens", "closes", "turnover", "frozen", "locked", "observed")
    for code in codes:
        columns = [[] for _ in names]
        for index, day in enumerate(original.calendar):
            source = sources.get((code, day))
            if source is None:
                unrequested.append((code, day))
            for column, name in zip(columns, names):
                column.append(getattr(source, name)[index] if source is not None
                              else False if name == "observed" else None)
        series.append((code, DecimalSeries(*(tuple(column) for column in columns))))
    lineage = [{"quote_snapshot_sha256": component.quote_snapshot_sha256,
                "read_scope_sha256": component.read_scope_sha256,
                "snapshot_received_at": component.snapshot_received_at,
                "fingerprint_sha256": None if component.fingerprint_json is None else digest(component.fingerprint_json)}
               for component in (original, extension)]
    artifact = encoded({"schema": "activity-composite-quote-extension-v1",
                        "activity_snapshot_sha256": original.activity_snapshot_sha256,
                        "calendar_dates": [day.isoformat() for day in original.calendar],
                        "atomic_source_vintage_certified": False,
                        "original": lineage[0], "extension": lineage[1]})
    sha = digest(artifact)
    _require(sha not in {original.quote_snapshot_sha256, extension.quote_snapshot_sha256},
             "composite quote artifact must have its own identity")
    return CompositeHoldingInputs(original, extension, original.calendar,
        original.activity_snapshot_sha256, sha, artifact, tuple(series), requested,
        absent, tuple(unrequested), observations)
