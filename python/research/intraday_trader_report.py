"""EE-only canonical price-path report over caller-owned streams.

No paths, source authentication, model, account or transport live here. Saved
verification trusts the existing raw parser (_session_observations) and closed
scalar codec, but never a producer detector, path or inline scientific receipt.
Consume read_report through EOF before adopting its external compressed pins.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
import gzip
import hashlib
from re import fullmatch
from typing import BinaryIO
import zlib

from research import intraday_ed_snapshot as codec
from research import intraday_ed_sources as sources
from research import intraday_trader_inputs as original
from research import intraday_trader_reference as reference


SCHEMA = "EE-original-population-price-paths-v1"
POLICIES = original.SCENARIOS
BINDING_KEYS = frozenset({"registration_sha256", "scenario_specification_sha256",
    "source_sha256", "runtime_sha256", "input_manifest_sha256", "caller"})
MAX_COMPRESSED = 536870912
MAX_DECODED = 1073741824
MAX_LINE = 16777216
GEOMETRY_MAXIMUM = {"sessions": 257, "targets_per_scenario": 5140,
    "union_coordinates": 5158, "cc_chunks": 5158, "cc_rows": 1002211,
    "cx_chunks": 5158, "cx_rows": 1002211, "cx_daily_rows": 2674}
SCOPE = "price-path exploration only; no C content, automatic reentry, account, integer-share or actual-fill certification"
ARITHMETIC = "Fraction per path; Decimal descriptive aggregates precision50 ROUND_HALF_EVEN; no account PnL"
WEEKLY_ENTRY_COUNTING = {
    "weekly_entry_counts": "confirmed modeled entry: traded/unknown path with observed entry_open after entry gates; no refusal",
    "weekly_entry_attempt_counts": "path entry_at records an attempted OPEN lookup, including declined gates and unknown OPEN; no refusal",
    "weekly_entry_unknown_counts": "unknown path with attempted entry_at and absent entry_open; entry remains unconfirmed",
    "weekly_entry_refusal_counts": "path refusal with unavailable entry evidence, grouped by original session week; no confirmed entry claim",
}
ReportRefusal = codec.SnapshotRefusal


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise ReportRefusal(reason)


def _pin(value: object) -> bool:
    return type(value) is str and fullmatch(r"[0-9a-f]{64}", value) is not None


def _binding(value: dict) -> dict:
    _need(type(value) is dict and set(value) == BINDING_KEYS, "report_binding_keys_differ")
    _need(all(_pin(value[key]) for key in BINDING_KEYS - {"caller"})
          and type(value["caller"]) is str and 0 < len(value["caller"]) <= 256
          and value["caller"].strip() == value["caller"], "invalid_report_binding")
    return dict(value)


def _metadata(raw: bytes) -> None:
    _need(type(raw) is bytes and codec.canonical(codec._json(raw)) == raw,
          "report_metadata_must_be_canonical_json_bytes")


def _population(universe: sources.UniverseCalendar, geometry: dict) -> dict:
    groups = original._groups(universe)
    for target in universe.targets:
        _metadata(target.metadata)
    _need(type(geometry) is dict and set(geometry) == set(GEOMETRY_MAXIMUM),
          "report_geometry_keys_differ")
    _need(all(type(geometry[key]) is int and 0 <= geometry[key] <= maximum
              for key, maximum in GEOMETRY_MAXIMUM.items()), "report_geometry_bound")
    counts = {clock: sum(target.scenario == clock for target in universe.targets)
              for clock in ("S1", "S2")}
    _need(geometry["sessions"] == len(universe.sessions)
          and counts["S1"] == counts["S2"] == geometry["targets_per_scenario"]
          and geometry["union_coordinates"] == len(groups)
          and geometry["cc_chunks"] == geometry["cx_chunks"] == len(groups),
          "report_original_geometry_differs")
    return groups


def _header(universe: sources.UniverseCalendar, binding: dict, geometry: dict,
            invented_only: bool) -> dict:
    _population(universe, geometry)
    _need(type(invented_only) is bool, "report_invented_flag_required")
    return {"kind": "header", "schema": SCHEMA, "binding": _binding(binding),
        "geometry": dict(geometry), "invented_only": invented_only,
        "policies": list(POLICIES), "scope": SCOPE, "statistic_arithmetic": ARITHMETIC,
        "weekly_entry_counting": dict(WEEKLY_ENTRY_COUNTING), "calendar": codec.encode(universe)}


def _decode_record(record: dict) -> original.OriginalSessionRecord:
    _need(type(record) is dict and set(record) == {"kind", "payload"}
          and record["kind"] == "session", "report_session_fields_differ")
    result = codec._decode(original.OriginalSessionRecord, record["payload"])
    _need(codec.canonical(codec.encode(result)) == codec.canonical(record["payload"]),
          "report_typed_roundtrip_differs")
    _metadata(result.result.source_metadata)
    for target in result.targets:
        _metadata(target.metadata)
    _need(tuple(item.scenario_id for item in result.result.results) == POLICIES,
          "report_policy_order_differs")
    _need(result.mathematical_verification.scenarios == POLICIES
          and result.mathematical_verification.compared_policy_count == len(POLICIES)
          and result.mathematical_verification.claim == reference.CLAIM,
          "report_inline_receipt_contract_differs")
    for item in result.result.results:
        candidate = item.detection.candidate
        _need(item.detection.attempt_number == 1
              and (candidate is None or (candidate.attempt_number == 1
                   and candidate.previous_terminal is None and candidate.profit_facts is None)),
              "report_first_attempt_only")
    return result


def _week(day: str) -> str:
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year:04d}-W{week:02d}"


def _reason(counts: dict, reason: str) -> None:
    _need(type(reason) is str and 0 < len(reason) <= 256, "report_reason_bound")
    counts[reason] = counts.get(reason, 0) + 1
    _need(len(counts) <= 128, "report_reason_count_bound")


class _Summary:
    """A finite count table and descriptive labels, never a market panel."""
    def __init__(self, sessions: tuple[str, ...]):
        weeks = sorted({_week(day) for day in sessions})
        self.rows = {clock: {policy: {"original_targets": 0,
            "detection_states": dict.fromkeys(("candidate", "known_no_candidate", "unknown", "invalid"), 0),
            "detection_reasons": {}, "eligible": dict.fromkeys(("true", "false", "unknown"), 0),
            "path_states": dict.fromkeys(("none", "traded", "declined", "unknown", "refusal"), 0),
            "path_reasons": {}, **{name: dict.fromkeys(weeks, 0) for name in WEEKLY_ENTRY_COUNTING},
            "returns": {name: {"count": 0, "sum": Decimal(0)}
                        for name in ("gross", "net33", "net43")}}
            for policy in POLICIES} for clock in ("S1", "S2")}

    def add(self, record: original.OriginalSessionRecord) -> None:
        for target in record.targets:
            for policy in record.result.results:
                row = self.rows[target.scenario][policy.scenario_id]
                detection, path = policy.detection, policy.path
                _need(detection.state in row["detection_states"], "report_detection_state_differs")
                row["original_targets"] += 1
                row["detection_states"][detection.state] += 1
                _reason(row["detection_reasons"], detection.reason)
                if detection.candidate is not None:
                    eligibility = detection.candidate.eligible
                    _need(eligibility is None or type(eligibility) is bool, "report_eligibility_differs")
                    row["eligible"]["unknown" if eligibility is None else str(eligibility).lower()] += 1
                _need(policy.path_refusal is None or path is None, "report_path_and_refusal_conflict")
                state = "refusal" if policy.path_refusal is not None else "none" if path is None else path.status
                _need(state in row["path_states"], "report_path_state_differs")
                row["path_states"][state] += 1
                if policy.path_refusal is not None:
                    _reason(row["path_reasons"], policy.path_refusal)
                    row["weekly_entry_refusal_counts"][_week(target.session)] += 1
                elif path is not None:
                    _reason(row["path_reasons"], path.reason)
                    if path.entry_at is not None:
                        week = _week(path.entry_at.date().isoformat())
                        _need(week in row["weekly_entry_counts"], "report_entry_outside_calendar")
                        row["weekly_entry_attempt_counts"][week] += 1
                        if state in ("traded", "unknown") and path.entry_open is not None:
                            row["weekly_entry_counts"][week] += 1
                        elif state == "unknown" and path.entry_open is None:
                            row["weekly_entry_unknown_counts"][week] += 1
                    for name, aggregate in row["returns"].items():
                        value = getattr(path, name)
                        _need((state == "traded" and type(value) is Fraction)
                              or (state != "traded" and value is None), "report_numeric_path_contract_differs")
                        if state == "traded":
                            with localcontext() as context:
                                context.prec, context.rounding = 50, ROUND_HALF_EVEN
                                aggregate["sum"] += Decimal(value.numerator) / Decimal(value.denominator)
                            aggregate["count"] += 1

    def finish(self) -> dict:
        result = {}
        for clock, policies in self.rows.items():
            result[clock] = {}
            for policy, row in policies.items():
                result[clock][policy] = {**row, "returns": {}}
                for name, aggregate in row["returns"].items():
                    count, total = aggregate["count"], aggregate["sum"]
                    with localcontext() as context:
                        context.prec, context.rounding = 50, ROUND_HALF_EVEN
                        mean = str(total / count) if count else None
                    result[clock][policy]["returns"][name] = {
                        "count": count, "sum": str(total) if count else None, "mean": mean}
        return result


class _Order:
    """EE framing, original coordinates and footer; no ED snapshot schema."""
    def __init__(self, *, summarize: bool = True):
        self.header = None
        self.pending = iter(())
        self.summary = None
        self.prefix = hashlib.sha256()
        self.counts = {"session": 0, "targets": {"S1": 0, "S2": 0}}
        self.done = False
        self.summarize = summarize

    def accept(self, record: dict, raw: bytes) -> original.OriginalSessionRecord | None:
        _need(type(record) is dict and not self.done, "report_record_after_footer")
        kind = record.get("kind")
        decoded = None
        if self.header is None:
            _need(kind == "header" and "calendar" in record, "report_header_required")
            universe = codec._decode(sources.UniverseCalendar, record["calendar"])
            expected = _header(universe, record.get("binding"), record.get("geometry"), record.get("invented_only"))
            _need(codec.canonical(record) == codec.canonical(expected), "report_header_differs")
            groups = _population(universe, record["geometry"])
            self.pending = iter((key, tuple(groups[key])) for key in sorted(groups))
            self.summary = _Summary(universe.sessions)
            self.header = record
        elif kind == "session":
            expected = next(self.pending, None)
            _need(expected is not None, "report_extra_session")
            decoded = _decode_record(record)
            key, targets = expected
            _need(decoded.targets == targets
                  and (decoded.result.opening.date().isoformat(), decoded.result.symbol) == key,
                  "report_original_coordinate_order_differs")
            if self.summarize:
                self.summary.add(decoded)
            self.counts["session"] += 1
            for target in decoded.targets:
                self.counts["targets"][target.scenario] += 1
        elif kind == "footer":
            _need(next(self.pending, None) is None, "report_missing_session")
            expected = self.footer()
            if not self.summarize:
                # The saved verifier accumulates statistics only after the raw
                # mathematical comparison, then checks this field itself.
                expected["summary"] = record.get("summary")
            _need(codec.canonical(record) == codec.canonical(expected), "report_footer_differs")
            self.done = True
            return None
        else:
            raise ReportRefusal("report_record_kind_differs")
        self.prefix.update(raw)
        return decoded

    def footer(self) -> dict:
        return {"kind": "footer", "record_counts": self.counts,
                "summary": self.summary.finish(), "decoded_prefix_sha256": self.prefix.hexdigest()}


def report_records(universe: sources.UniverseCalendar, reader: original.AuditedChunkReader, *,
                   binding: dict, geometry: dict, invented_only: bool,
                   checkpoint: Callable[[str], object]) -> Iterator[dict]:
    """Lossless original first-attempt A/B records, including missing targets."""
    _need(callable(checkpoint), "report_checkpoint_required")
    order = _Order()
    header = _header(universe, binding, geometry, invented_only)
    order.accept(header, codec.canonical(header) + b"\n")
    yield header
    for value in original.iter_original_sessions(universe, reader, checkpoint):
        _metadata(value.result.source_metadata)
        record = {"kind": "session", "payload": codec.encode(value)}
        order.accept(record, codec.canonical(record) + b"\n")
        yield record
    footer = order.footer()
    order.accept(footer, codec.canonical(footer) + b"\n")
    yield footer


@dataclass(frozen=True)
class ReportDigest:
    compressed_sha256: str
    decoded_sha256: str
    compressed_bytes: int
    decoded_bytes: int
    records: int


class _Sink:
    def __init__(self, stream: BinaryIO):
        self.stream, self.digest, self.size = stream, hashlib.sha256(), 0

    def write(self, raw: bytes) -> int:
        _need(type(raw) is bytes and self.size + len(raw) <= MAX_COMPRESSED, "report_compressed_limit")
        wrote = self.stream.write(raw)
        _need(wrote == len(raw), "report_short_write")
        self.digest.update(raw)
        self.size += len(raw)
        return wrote


def write_report(records: Iterator[dict], sink: BinaryIO,
                 checkpoint: Callable[[str], object]) -> ReportDigest:
    _need(callable(checkpoint), "report_checkpoint_required")
    hashed, decoded, size, count, order = _Sink(sink), hashlib.sha256(), 0, 0, _Order()
    with gzip.GzipFile(filename="", fileobj=hashed, mode="wb", compresslevel=1, mtime=0) as compressed:
        for record in records:
            raw = codec.canonical(record) + b"\n"
            _need(len(raw) <= MAX_LINE and size + len(raw) <= MAX_DECODED, "report_decoded_limit")
            order.accept(record, raw)
            compressed.write(raw)
            decoded.update(raw)
            size, count = size + len(raw), count + 1
            checkpoint("ee_report_write")
    _need(order.done, "report_footer_missing")
    return ReportDigest(hashed.digest.hexdigest(), decoded.hexdigest(), hashed.size, size, count)


def read_report(source: BinaryIO, expected_sha256: str, expected_decoded_sha256: str,
                checkpoint: Callable[[str], object]) -> Iterator[dict]:
    """Bounded single-member gzip JSONL; records remain provisional until EOF."""
    _need(_pin(expected_sha256) and _pin(expected_decoded_sha256), "report_external_pins_required")
    _need(callable(checkpoint), "report_checkpoint_required")
    packed, decoded = hashlib.sha256(), hashlib.sha256()
    packed_size, size, pending, header = 0, 0, b"", b""
    inflater, order = zlib.decompressobj(31), _Order()
    while True:
        chunk = source.read(65536)
        _need(type(chunk) is bytes, "report_binary_source_required")
        if not chunk:
            break
        packed_size += len(chunk)
        _need(packed_size <= MAX_COMPRESSED, "report_compressed_limit")
        packed.update(chunk)
        if len(header) < 10:
            # GzipFile's deterministic empty-name, mtime0, level1 header.
            header += chunk[:10 - len(header)]
            _need(b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x04\xff".startswith(header),
                  "report_gzip_header_differs")
        _need(not inflater.eof, "report_extra_gzip_content")
        while chunk:
            try:
                part = inflater.decompress(chunk, 65536)
            except zlib.error as error:
                raise ReportRefusal("invalid_report_gzip") from error
            _need(not inflater.unused_data, "report_extra_gzip_content")
            chunk = inflater.unconsumed_tail
            size += len(part)
            _need(size <= MAX_DECODED, "report_decoded_limit")
            decoded.update(part)
            pending += part
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                raw = line + b"\n"
                _need(len(raw) <= MAX_LINE, "report_line_limit")
                record = codec._json(raw)
                _need(codec.canonical(record) + b"\n" == raw, "noncanonical_report_jsonl")
                order.accept(record, raw)
                checkpoint("ee_report_read")
                yield record
            _need(len(pending) < MAX_LINE, "report_line_limit")
    _need(inflater.eof and not pending and order.done, "report_truncated_or_footer_missing")
    _need(packed.hexdigest() == expected_sha256, "report_compressed_pin_differs")
    _need(decoded.hexdigest() == expected_decoded_sha256, "report_decoded_pin_differs")


def verify_report(records: Iterator[dict], *, universe: sources.UniverseCalendar,
                  raw_reader: original.AuditedChunkReader, binding: dict, geometry: dict,
                  invented_only: bool, checkpoint: Callable[[str], object]) -> dict:
    """Independently verify every saved session from the original raw chunks.

    Raw/parser integrity is the caller's authenticated boundary. session_inputs
    supplies expected source metadata only; full_reference_session_inputs and
    the independent reference reconstruct all scientific results. Both decoders
    share _session_observations, an explicitly trusted parsing boundary.
    Supplied records are exhausted; compressed pins require read_report as input.
    """
    expected_header = _header(universe, binding, geometry, invented_only)
    _need(callable(checkpoint) and callable(getattr(raw_reader, "load_raw_chunk", None))
          and callable(getattr(raw_reader, "assert_unchanged", None)), "report_reader_contract_required")
    order, independent = _Order(summarize=False), _Summary(universe.sessions)
    footer = None
    raw_reader.assert_unchanged()
    try:
        for index, record in enumerate(records):
            if index == 0:
                _need(codec.canonical(record) == codec.canonical(expected_header),
                      "report_expected_header_differs")
            value = order.accept(record, codec.canonical(record) + b"\n")
            if value is not None:
                checkpoint("ee_report_verify_before")
                raw_reader.assert_unchanged()
                try:
                    symbol, day = value.result.symbol, value.result.opening.date().isoformat()
                    price = raw_reader.load_raw_chunk("CC", symbol, day)
                    volume = raw_reader.load_raw_chunk("CX", symbol, day)
                    metadata = sources.session_inputs(symbol, day, price, volume).metadata
                    _metadata(metadata)
                    session = sources.full_reference_session_inputs(symbol, day, price, volume)
                    receipt = reference.verify_session_result(value.result, session,
                        scenarios=POLICIES, source_metadata=metadata)
                    _need(value.mathematical_verification == receipt, "report_inline_receipt_differs")
                    del price, volume, session, metadata
                finally:
                    raw_reader.assert_unchanged()
                    checkpoint("ee_report_verify_after")
                independent.add(value)
            elif record.get("kind") == "footer":
                footer = record
        _need(order.done and footer is not None, "report_footer_missing")
        summary = independent.finish()
        _need(codec.canonical(summary) == codec.canonical(footer["summary"]),
              "report_independent_summary_differs")
    finally:
        raw_reader.assert_unchanged()
    return {"verified": True, "schema": SCHEMA, "record_counts": order.counts,
        "candidate_counts": {clock: {policy: row["eligible"]["true"] for policy, row in policies.items()}
                             for clock, policies in summary.items()}, "summary": summary,
        "record_stream_exhausted": True,
        "claim": "all supplied original targets and first-attempt price-path mathematics; compressed pins/EOF require exhausted read_report; no archive/account/fill certification",
        "trusted_boundary": "existing closed scalar codec and shared source _session_observations parser"}
