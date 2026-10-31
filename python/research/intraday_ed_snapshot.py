"""Streaming ED-H1 common observations over authenticated caller inputs.

This module opens no source path, logs no trial and runs no model. The caller
supplies the original calendar, an independently audited unchanged-byte cache,
filing evidence and immutable execution bindings. Daily archived observations
are shared across both timing scenarios. Only one target's twenty reference
sessions are decoded at a time; no whole-period Minute panel is retained.

Snapshot readers yield provisional records: adoption requires consuming through
the footer AND gzip EOF so both the decoded and compressed pins are checked.
Scientific independent verification remains a separate caller obligation.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from collections import OrderedDict
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from functools import lru_cache
import gzip
import hashlib
import json
import math
import re
import types
from typing import BinaryIO, Literal, Union, get_args, get_origin, get_type_hints

from research import intraday_ed_inputs as inputs
from research import intraday_ed_replay as replay
from research import intraday_ed_sources as sources
from research import intraday_pattern_calibration as clock


SCHEMA = "ED-H1-common-snapshot-v1"
MAX_LINE = 16 * 1024 * 1024
MAX_STREAM = 1024 * 1024 * 1024
SCENARIOS = ("S1", "S2")
BINDING_KEYS = frozenset({"registration_sha256", "source_sha256", "runtime_sha256",
                          "input_manifest_sha256", "caller"})
ASSUMPTIONS = (
    "original_dated_population_including_unavailable_coordinates",
    "S1_S2_joint_universe_and_date_only_filing_clock_sensitivity_not_independent_samples",
    "assumed_session_schedule_not_session_auction_halt_or_VI_certification",
    "completed_minute_end_plus2_assumed_not_historical_publication_proof",
    "current_DART_query_not_historical_completeness_or_issuer_certification",
    "stored_vendor_price_and_volume_units_and_adjustment_basis_uncertified",
    "features_selected_before_future_unit_label_or_holding_path_support",
    *replay.ASSUMPTIONS,
)


class SnapshotRefusal(ValueError):
    """Invalid framing, identities or contracts must not become missingness."""


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise SnapshotRefusal(reason)


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def encode(value: object) -> object:
    """Public lossless wire projection; metadata bytes are canonical JSON."""
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, datetime):
        _need(value.utcoffset() is not None, "naive_snapshot_clock")
        return value.isoformat()
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, Decimal):
        _need(value.is_finite(), "nonfinite_snapshot_decimal")
        return {"decimal": str(value)}
    if isinstance(value, Fraction):
        return {"fraction": [value.numerator, value.denominator]}
    if type(value) is float:
        _need(math.isfinite(value), "nonfinite_snapshot_float64")
        return {"float64": value.hex()}
    if type(value) is bytes:
        return _json(value)
    if type(value) is inputs.SessionInputs:
        return {"symbol": value.symbol, "opening": encode(value.opening), "closing": encode(value.closing),
                "minutes": [encode(value.minutes[at]) for at in sorted(value.minutes)]}
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: encode(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        _need(all(type(key) is str for key in value), "nonstring_snapshot_key")
        return {key: encode(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [encode(item) for item in value]
    raise SnapshotRefusal("unsupported_snapshot_scalar")


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, "duplicate_snapshot_json_key")
        result[key] = value
    return result


def _json(raw: bytes) -> object:
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _value: (_ for _ in ()).throw(SnapshotRefusal("nonfinite_json")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotRefusal("invalid_snapshot_json") from error


@lru_cache(maxsize=64)
def _hints(annotation: type) -> dict:
    return get_type_hints(annotation)


def _decode(annotation: object, value: object) -> object:
    """Closed typed decoder; no executable class names are loaded from JSON."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        for choice in args:
            try:
                return _decode(choice, value)
            except (SnapshotRefusal, ValueError, TypeError):
                pass
        raise SnapshotRefusal("snapshot_union_type_differs")
    if origin is Literal:
        _need(value in args and any(type(value) is type(item) for item in args), "snapshot_literal_differs")
        return value
    if annotation is type(None):
        _need(value is None, "snapshot_null_required")
        return None
    if annotation in (str, int, bool):
        _need(type(value) is annotation, "snapshot_scalar_type_differs")
        return value
    if annotation in (datetime, date):
        _need(type(value) is str, "snapshot_clock_text_required")
        result = annotation.fromisoformat(value)
        _need(result.isoformat() == value and (annotation is date or result.utcoffset() is not None),
              "noncanonical_snapshot_clock")
        return result
    if annotation in (Fraction, Decimal, float):
        name = {Fraction: "fraction", Decimal: "decimal", float: "float64"}[annotation]
        _need(type(value) is dict and set(value) == {name}, "snapshot_exact_scalar_tag_differs")
        payload = value[name]
        if annotation is Fraction:
            _need(type(payload) is list and len(payload) == 2 and all(type(x) is int for x in payload)
                  and payload[1] > 0, "invalid_snapshot_fraction")
            result = Fraction(*payload)
        elif annotation is Decimal:
            _need(type(payload) is str, "snapshot_decimal_text_required")
            try:
                result = Decimal(payload)
            except InvalidOperation as error:
                raise SnapshotRefusal("invalid_snapshot_decimal") from error
        else:
            _need(type(payload) is str, "snapshot_float64_text_required")
            result = float.fromhex(payload)
        _need(encode(result) == value, "noncanonical_snapshot_exact_scalar")
        return result
    if annotation is bytes:
        return canonical(value)
    if origin is tuple:
        _need(type(value) is list, "snapshot_array_required")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], item) for item in value)
        _need(len(value) == len(args), "snapshot_tuple_length_differs")
        return tuple(_decode(kind, item) for kind, item in zip(args, value, strict=True))
    if annotation is inputs.SessionInputs:
        _need(type(value) is dict and set(value) == {"symbol", "opening", "closing", "minutes"}
              and type(value["minutes"]) is list, "snapshot_session_fields_differ")
        rows = tuple(_decode(inputs.Minute, item) for item in value["minutes"])
        _need(tuple(row.start_at for row in rows) == tuple(sorted({row.start_at for row in rows})),
              "snapshot_minute_order_differs")
        session = inputs.SessionInputs(_decode(str, value["symbol"]), _decode(datetime, value["opening"]),
                                       _decode(datetime, value["closing"]), {row.start_at: row for row in rows})
        _need(all(row.symbol == session.symbol and session.opening <= row.start_at < session.closing
                  for row in rows), "snapshot_minute_session_differs")
        return session
    if isinstance(annotation, type) and is_dataclass(annotation):
        _need(type(value) is dict and set(value) == {field.name for field in fields(annotation)},
              "snapshot_dataclass_fields_differ")
        hints = _hints(annotation)
        return annotation(**{field.name: _decode(hints[field.name], value[field.name]) for field in fields(annotation)})
    raise SnapshotRefusal("unsupported_snapshot_decode_type")


@dataclass(frozen=True)
class SnapshotCoordinate:
    coordinate: sources.TargetCoordinate
    opening_context: sources.OpeningContextRecord
    original_peers: tuple[str, ...]
    expected_prior_openings: tuple[datetime, ...]
    parent_scan: inputs.ParentScan
    window_inputs: inputs.H1WindowInputs | None
    features: tuple[float, ...] | None
    common_support: bool
    feature_reason: str | None
    candidate: replay.EDCandidate | None
    label: replay.EDUnitLabel | None
    actions: Mapping[str, replay.EDAction]


def decode_coordinate(record: dict) -> SnapshotCoordinate:
    _need(type(record) is dict and record.get("kind") == "coordinate", "coordinate_record_required")
    payload = {key: value for key, value in record.items() if key != "kind"}
    _need(type(payload.get("actions")) is dict and set(payload["actions"]) == {"rule", "without_filing"},
          "snapshot_action_roster_differs")
    actions = {name: _decode(replay.EDAction, action) for name, action in payload.pop("actions").items()}
    expected = {field.name for field in fields(SnapshotCoordinate)} - {"actions"}
    _need(set(payload) == expected, "snapshot_coordinate_fields_differ")
    hints = _hints(SnapshotCoordinate)
    value = SnapshotCoordinate(**{key: _decode(hints[key], item) for key, item in payload.items()}, actions=actions)
    _need(value.common_support == (value.features is not None)
          and (value.features is None or len(value.features) == len(inputs.FEATURE_NAMES)),
          "snapshot_feature_support_differs")
    return value


def decode_session(record: dict) -> tuple[sources.SessionSourceView, ...]:
    _need(type(record) is dict and set(record) == {"kind", "session", "opening", "closing", "sources"}
          and record["kind"] == "session" and type(record["sources"]) is list, "session_record_required")
    day = _decode(str, record["session"])
    opening, closing = clock.session_open(day), clock.session_close(day)
    _need(record["opening"] == opening.isoformat() and record["closing"] == closing.isoformat(),
          "snapshot_session_schedule_differs")
    views = tuple(_decode(sources.SessionSourceView, item) for item in record["sources"])
    _need(tuple(view.session.symbol for view in views) == tuple(sorted({view.session.symbol for view in views}))
          and all(view.session.opening == opening and view.session.closing == closing for view in views),
          "snapshot_daily_sources_differ")
    return views


def _binding(value: Mapping) -> dict:
    _need(isinstance(value, Mapping) and set(value) == BINDING_KEYS and value["caller"] == "ED-H1-SNAPSHOT",
          "snapshot_binding_fields_differ")
    _need(all(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{64}", value[key])
              for key in BINDING_KEYS - {"caller"}), "snapshot_binding_pin_differs")
    return dict(value)


def _calendar(universe: sources.UniverseCalendar) -> None:
    _need(type(universe) is sources.UniverseCalendar and bool(universe.sessions)
          and universe.sessions == tuple(sorted(set(universe.sessions))), "snapshot_original_calendar_differs")
    identities = [(row.session, row.symbol, row.scenario) for row in universe.targets]
    _need(len(set(identities)) == len(identities) and all(row.scenario in SCENARIOS and row.session in universe.sessions
          for row in universe.targets), "snapshot_original_coordinate_differs")
    for row in universe.targets:
        _need((row.stored_row_count is None and row.state == "no_stored_rows_unknown_reason") or
              (type(row.stored_row_count) is int and row.stored_row_count > 0
               and row.state == "stored_rows_completeness_unverified"), "snapshot_original_availability_differs")


def _source(reader: sources.AuditedChunkReader, symbol: str, day: str, *,
            requested_starts: tuple[datetime, ...] | None = None) -> sources.SessionSourceView:
    arguments = {} if requested_starts is None else {"requested_starts": requested_starts}
    return sources.session_inputs(symbol, day, reader.load_raw_chunk("CC", symbol, day),
                                  reader.load_raw_chunk("CX", symbol, day), **arguments)


def _parent_id(symbol: str, day: str) -> str:
    return f"ED-H1:{day}:{symbol}"


class _References(Mapping):
    """Lazy target-local reference panel, shared only across S1/S2."""
    def __init__(self, reader: sources.AuditedChunkReader, symbol: str, days: tuple[str, ...], shared):
        self.reader, self.symbol = reader, symbol
        self.days = {clock.session_open(day): day for day in days}
        self.loaded = {}
        self.offset = None
        self.shared = shared

    def for_window(self, offset: int) -> None:
        if self.offset != offset:
            self.loaded.clear()
            self.offset = offset

    def __iter__(self):
        return iter(self.days)

    def __len__(self):
        return len(self.days)

    def __getitem__(self, opening):
        day = self.days[opening]
        if opening not in self.loaded:
            _need(self.offset is not None, "reference_window_not_bound")
            key = self.symbol, day, self.offset
            if key not in self.shared:
                requested = tuple(opening + (self.offset + index) * inputs.MINUTE for index in range(30))
                self.shared[key] = _source(self.reader, self.symbol, day, requested_starts=requested).session
                while len(self.shared) > 440:
                    self.shared.popitem(last=False)
            else:
                self.shared.move_to_end(key)
            self.loaded[opening] = self.shared[key]
        return self.loaded[opening]


def _coordinate(row: sources.TargetCoordinate, context: sources.OpeningContextRecord, *,
                current: inputs.SessionInputs, peers: Mapping[str, inputs.SessionInputs | None],
                expected: tuple[datetime, ...], references: Mapping[datetime, inputs.SessionInputs | None]) -> SnapshotCoordinate:
    original_peers = tuple(sorted(peers))
    final_decision = current.closing - 78 * inputs.MINUTE
    if row.stored_row_count is None:
        scan = inputs.ParentScan("parent_identity_unknown", issues=(
            inputs.Issue(row.symbol, current.opening, "original_coordinate_unavailable"),))
    else:
        scan = inputs.first_h1_parent(current, through=final_decision)
    built, features, candidate, label = None, None, None, None
    if scan.parent is not None:
        candidate = replay.candidate_for_parent(scan.parent, closing=current.closing,
                                                parent_id=_parent_id(row.symbol, row.session))
        # Feature/support admission precedes, and never consults, label replay.
        if len(expected) == 20:
            if isinstance(references, _References):
                references.for_window(int((scan.parent.trigger.end_at - current.opening) // inputs.MINUTE) - 30)
            built = inputs.build_h1_window_inputs(current, scan.parent, original_peers=original_peers,
                peers=peers, expected=expected, references=references, opening_context=context.context)
            features = inputs.flatten_h1_features(built)
            feature_reason = None if features is not None else "required_predecision_input_unknown"
        else:
            feature_reason = "insufficient_expected_prior_sessions"
        label = replay.ed_unit_label(candidate, {(row.symbol, at): value for at, value in current.minutes.items()})
        actions = {}
        for name, without in (("rule", False), ("without_filing", True)):
            decision = "unknown" if built is None else inputs.h1_rule_decision(built, without_filing=without)
            status = {"accepted": "accept", "declined": "decline", "unknown": "unknown"}[decision]
            actions[name] = replay.EDAction(candidate.decision_at, status,
                "common_support_unknown" if status == "unknown" else f"fixed_{name}_{status}", candidate)
    else:
        feature_reason = scan.status
        status = "decline" if scan.status == "known_no_parent" else "unknown"
        # Unknown first identity can affect the first eligible decision. Known
        # absence is established only after the final E<=close-80 and E+2.
        earliest_unknown = min((issue.minute for issue in scan.issues), default=current.opening)
        unknown_grid = current.opening + (
            ((earliest_unknown - current.opening) // (5 * inputs.MINUTE) + 1) * 5 + 2) * inputs.MINUTE
        at = final_decision if status == "decline" else max(current.opening + 32 * inputs.MINUTE, unknown_grid)
        actions = {name: replay.EDAction(at, status, scan.status, symbol=row.symbol,
                    parent_id=_parent_id(row.symbol, row.session)) for name in ("rule", "without_filing")}
    return SnapshotCoordinate(row, context, original_peers, expected, scan, built, features,
                              features is not None, feature_reason, candidate, label, actions)


def snapshot_records(universe: sources.UniverseCalendar, reader: sources.AuditedChunkReader,
                     filing_source: sources.FilingSource, *, binding: Mapping,
                     checkpoint: Callable[[str], object]) -> Iterator[dict]:
    """Produce every original coordinate without shrinking to observed support."""
    _calendar(universe)
    bound = _binding(binding)
    _need(isinstance(reader, sources.AuditedChunkReader) and reader.receipt.get("verified") is True
          and reader.receipt.get("schema") == "ED-raw-byte-cache-audit-v1"
          and reader.receipt.get("full_eof_verified") is True, "independently_audited_byte_reader_required")
    contexts = {(scenario, item.context.opening.date().isoformat(), item.context.symbol): item
                for scenario in SCENARIOS for item in sources.build_opening_contexts(universe, filing_source, scenario)}
    declared = tuple(sorted(universe.targets, key=lambda item: (item.session, item.symbol, item.scenario)))
    prefix = hashlib.sha256()
    header = {"kind": "header", "schema": SCHEMA, "binding": bound,
              "calendar": {"sessions": list(universe.sessions), "targets": encode(declared), "raw_sha256": universe.raw_sha256},
              "feature_schema": inputs.SCHEMA_ID, "feature_names": list(inputs.FEATURE_NAMES),
              "source_audit": encode(reader.receipt), "filing_source": encode(filing_source),
              "assumptions": list(ASSUMPTIONS)}
    prefix.update(canonical(header) + b"\n")
    yield header
    reference_cache = OrderedDict()
    counts = {"session": 0, "coordinate": 0}
    for index, day in enumerate(universe.sessions):
        checkpoint("snapshot_session")
        day_rows = tuple(item for item in declared if item.session == day)
        symbols = tuple(sorted({item.symbol for item in day_rows}))
        views = {name: _source(reader, name, day) for name in symbols}
        day_record = {"kind": "session", "session": day, "opening": clock.session_open(day).isoformat(),
                      "closing": clock.session_close(day).isoformat(), "sources": [encode(views[name]) for name in symbols]}
        prefix.update(canonical(day_record) + b"\n")
        counts["session"] += 1
        yield day_record
        expected_days = universe.sessions[max(0, index - 20):index]
        expected = tuple(clock.session_open(prior) for prior in expected_days)
        scenario_rows = {(item.scenario, item.symbol): item for item in day_rows}
        for symbol in symbols:
            # References shared by this target's S1/S2 rows, then released.
            references = _References(reader, symbol, expected_days, reference_cache)
            for row in (item for item in day_rows if item.symbol == symbol):
                peers = {name: views[name].session if other.stored_row_count is not None else None
                         for (scenario, name), other in scenario_rows.items()
                         if scenario == row.scenario and name != symbol}
                value = _coordinate(row, contexts[(row.scenario, day, symbol)], current=views[symbol].session,
                                    peers=peers, expected=expected, references=references)
                record = {"kind": "coordinate", **encode(value)}
                prefix.update(canonical(record) + b"\n")
                counts["coordinate"] += 1
                checkpoint("snapshot_coordinate")
                yield record
    yield {"kind": "footer", "record_counts": counts, "decoded_prefix_sha256": prefix.hexdigest()}


@dataclass(frozen=True)
class SnapshotDigest:
    compressed_sha256: str
    decoded_sha256: str
    compressed_bytes: int
    decoded_bytes: int
    records: int


class _HashedIO:
    def __init__(self, stream: BinaryIO):
        self.stream, self.digest, self.size = stream, hashlib.sha256(), 0

    def write(self, raw: bytes) -> int:
        _need(type(raw) is bytes and self.size + len(raw) <= MAX_STREAM, "snapshot_compressed_limit")
        wrote = self.stream.write(raw)
        _need(wrote == len(raw), "snapshot_short_write")
        self.digest.update(raw)
        self.size += len(raw)
        return wrote

    def read(self, size: int = -1) -> bytes:
        raw = self.stream.read(size)
        _need(type(raw) is bytes and self.size + len(raw) <= MAX_STREAM, "snapshot_compressed_limit")
        self.digest.update(raw)
        self.size += len(raw)
        return raw

    def flush(self) -> None:
        self.stream.flush()


class _Order:
    """Bounded schema/order/denominator check shared by reader and writer."""
    def __init__(self, *, validate_types: bool = True):
        _need(type(validate_types) is bool, "boolean_type_validation_required")
        self.validate_types = validate_types
        self.header = None
        self.day_index = -1
        self.pending = []
        self.counts = {"session": 0, "coordinate": 0}
        self.prefix = hashlib.sha256()
        self.done = False
        self.targets_by_day = {}

    def accept(self, record: dict, raw: bytes) -> None:
        _need(type(record) is dict and not self.done, "snapshot_record_after_footer")
        kind = record.get("kind")
        if self.header is None:
            _need(kind == "header" and set(record) == {"kind", "schema", "binding", "calendar", "feature_schema",
                  "feature_names", "source_audit", "filing_source", "assumptions"}, "snapshot_header_required")
            _need(record["schema"] == SCHEMA and record["feature_schema"] == inputs.SCHEMA_ID
                  and record["feature_names"] == list(inputs.FEATURE_NAMES)
                  and record["assumptions"] == list(ASSUMPTIONS), "snapshot_schema_differs")
            _binding(record["binding"])
            calendar = record["calendar"]
            _need(type(calendar) is dict and set(calendar) == {"sessions", "targets", "raw_sha256"},
                  "snapshot_calendar_fields_differ")
            universe = sources.UniverseCalendar(_decode(tuple[str, ...], calendar["sessions"]),
                        _decode(tuple[sources.TargetCoordinate, ...], calendar["targets"]),
                        _decode(str, calendar["raw_sha256"]))
            _calendar(universe)
            _need(calendar["targets"] == encode(tuple(sorted(universe.targets,
                  key=lambda row: (row.session, row.symbol, row.scenario)))), "snapshot_target_order_differs")
            _decode(sources.FilingSource, record["filing_source"])
            for row in calendar["targets"]:
                self.targets_by_day.setdefault(row["session"], []).append(row)
            self.header = record
        elif kind == "session":
            _need(not self.pending, "snapshot_missing_coordinate")
            self.day_index += 1
            sessions = self.header["calendar"]["sessions"]
            _need(self.day_index < len(sessions) and record.get("session") == sessions[self.day_index],
                  "snapshot_day_order_differs")
            self.pending = list(self.targets_by_day.get(record["session"], ()))
            if self.validate_types:
                names = [view.session.symbol for view in decode_session(record)]
            else:
                _need(set(record) == {"kind", "session", "opening", "closing", "sources"}
                      and type(record["sources"]) is list
                      and all(type(view) is dict and set(view) == {"session", "metadata"}
                              and type(view["session"]) is dict
                              and set(view["session"]) == {"symbol", "opening", "closing", "minutes"}
                              and view["session"]["opening"] == record["opening"]
                              and view["session"]["closing"] == record["closing"]
                              for view in record["sources"]), "snapshot_daily_structure_differs")
                names = [view["session"]["symbol"] for view in record["sources"]]
            _need(names == sorted({row["symbol"] for row in self.pending}),
                  "snapshot_daily_population_differs")
            self.counts["session"] += 1
        elif kind == "coordinate":
            _need(bool(self.pending) and record.get("coordinate") == self.pending[0], "snapshot_coordinate_order_differs")
            if self.validate_types:
                decode_coordinate(record)
            else:
                _need(set(record) == {"kind", *(field.name for field in fields(SnapshotCoordinate))}
                      and type(record["actions"]) is dict
                      and set(record["actions"]) == {"rule", "without_filing"},
                      "snapshot_coordinate_structure_differs")
            self.pending.pop(0)
            self.counts["coordinate"] += 1
        elif kind == "footer":
            _need(set(record) == {"kind", "record_counts", "decoded_prefix_sha256"} and not self.pending
                  and self.day_index + 1 == len(self.header["calendar"]["sessions"])
                  and self.counts == record["record_counts"]
                  and record["decoded_prefix_sha256"] == self.prefix.hexdigest(), "snapshot_footer_differs")
            self.done = True
            return
        else:
            raise SnapshotRefusal("snapshot_record_kind_differs")
        self.prefix.update(raw)


def write_snapshot(records: Iterator[dict], sink: BinaryIO,
                   checkpoint: Callable[[str], object], *,
                   validate_types: bool = True) -> SnapshotDigest:
    """Write deterministic gzip JSONL to the caller-owned newly created sink.

    The registered worker skips redundant type materialization of its own
    encoded records only when full producer-independent raw reconstruction
    follows before adoption. Structure, order, denominator and digests always
    remain checked. Skipping type materialization alone is never verification.
    """
    hashed, decoded, size, count, order = _HashedIO(sink), hashlib.sha256(), 0, 0, _Order(validate_types=validate_types)
    with gzip.GzipFile(filename="", fileobj=hashed, mode="wb", compresslevel=1, mtime=0) as compressed:
        for record in records:
            raw = canonical(record) + b"\n"
            _need(len(raw) <= MAX_LINE and size + len(raw) <= MAX_STREAM, "snapshot_decoded_limit")
            order.accept(record, raw)
            compressed.write(raw)
            decoded.update(raw)
            size, count = size + len(raw), count + 1
            checkpoint("snapshot_write")
    _need(order.done, "snapshot_footer_missing")
    return SnapshotDigest(hashed.digest.hexdigest(), decoded.hexdigest(), hashed.size, size, count)


def read_snapshot(source: BinaryIO, *, expected_sha256: str,
                  expected_decoded_sha256: str | None = None,
                  checkpoint: Callable[[str], object], validate_types: bool = True) -> Iterator[dict]:
    """Strict streaming reader; full iteration checks gzip EOF and external pins."""
    _need(type(expected_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
          and (expected_decoded_sha256 is None or type(expected_decoded_sha256) is str
               and re.fullmatch(r"[0-9a-f]{64}", expected_decoded_sha256)), "snapshot_external_pin_required")
    hashed, decoded, size, order = _HashedIO(source), hashlib.sha256(), 0, _Order(validate_types=validate_types)
    with gzip.GzipFile(fileobj=hashed, mode="rb") as compressed:
        while True:
            raw = compressed.readline(MAX_LINE + 1)
            if not raw:
                break
            _need(raw.endswith(b"\n") and len(raw) <= MAX_LINE and size + len(raw) <= MAX_STREAM,
                  "snapshot_line_or_stream_limit")
            record = _json(raw)
            _need(canonical(record) + b"\n" == raw, "noncanonical_snapshot_jsonl")
            order.accept(record, raw)
            decoded.update(raw)
            size += len(raw)
            checkpoint("snapshot_read")
            yield record
    _need(order.done, "snapshot_footer_missing")
    _need(hashed.digest.hexdigest() == expected_sha256, "snapshot_compressed_pin_differs")
    _need(expected_decoded_sha256 is None or decoded.hexdigest() == expected_decoded_sha256,
          "snapshot_decoded_pin_differs")
