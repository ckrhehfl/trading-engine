"""Fixed ED snapshot hooks for the existing ordinary stdio lifecycle.

Import and hook construction are source-only. The private entrypoint owns host
locations, deployment pins and the bridge call; it cannot provide executable
callbacks through configuration. Raw inputs are opened only by compute_verify,
which the bridge invokes after its durable STARTED and authenticated transfer.
No actual source is read to manufacture invented qualification identities.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


SPECIFICATION_PATH = "configs/research/discovery/intraday-ed-h1-common-comparison-v1.json"
SPECIFICATION_ID = "intraday-ed-h1-common-comparison-v1"
OUTPUT_FILENAME = "h1-snapshot.jsonl.gz"
FULL_GEOMETRY = {
    "sessions": 257, "targets_per_scenario": 5140, "union_coordinates": 5158,
    "cc_chunks": 5158, "cc_rows": 1002211, "cx_chunks": 5158, "cx_rows": 1002211,
    "cx_daily_rows": 2674, "filing_codes": 18, "filing_rows": 61, "complete_filing_codes": 14,
}
CANDIDATE_BOUNDS = {"S1": 5140, "S2": 5140}
INPUT_CONTRACTS = {"ED-H1-SNAPSHOT": wire.ED_INPUTS_CONTRACT,
    "ED-H1-RULES": wire.ED_SNAPSHOT_CONTRACT, "ED-H1-LIGHTGBM": wire.ED_SNAPSHOT_CONTRACT,
    "ED-H1-LIGHTGBM-ABLATION": wire.ED_SNAPSHOT_CONTRACT}
DYNAMIC_CHANNELS = ("target_minute_oc_return", "target_range_over_open", "target_close_location_payload",
    "target_zero_range_flag", "target_same_clock_share_volume_ratio", "observed_peer_minute_oc_return")
STATIC_NUMERIC_NAMES = ("target_30m_return", "peer_30m_return", "peer_last15_return", "peer_positive_30m_fraction",
    "target_minus_peer_30m_return", "last5_share_volume_ratio", "past20_sameclock_30m_return_volatility",
    "fraction_of_session_elapsed", "structural_stop_gap_from_trigger_close",
    "common_peer_count_over_original_peer_count", "past20_sameclock5_share_volume_median")
MINIMUM_PUBLIC_SOURCES = tuple(sorted((SPECIFICATION_PATH,
    "python/research/intraday_ed_comparison_unit.py", "python/tests/test_intraday_ed_comparison_unit.py",
    "python/research/intraday_ed_sources.py", "python/research/intraday_ed_snapshot.py",
    "python/research/intraday_ed_snapshot_verify.py", "python/research/intraday_ed_inputs.py",
    "python/research/intraday_ed_replay.py", "python/research/intraday_campaign_wsl_bridge.py",
    "python/research/intraday_campaign_wsl_transport.py")))
Checkpoint = Callable[[str], object]


def runtime_contract() -> dict:
    """A public minimum roster; the private deployment pins the complete closure."""
    return {"schema": "ED-H1-comparison-runtime-v1", "resource_profile": wire.ED_H1_600.id,
        "input_contracts": dict(INPUT_CONTRACTS), "snapshot_geometry": dict(FULL_GEOMETRY),
        "snapshot_candidate_bounds": dict(CANDIDATE_BOUNDS), "snapshot_output": OUTPUT_FILENAME,
        "snapshot_maximum_bytes": 512 * wire.MIB, "minimum_public_sources": list(MINIMUM_PUBLIC_SOURCES),
        "automatic_actual_retries": 0, "concurrent_units": 1,
        "invented_profile_is_actual_execution": False,
        "tiny_invented_profile_qualifies_full_geometry": False}


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        wire.require(key not in value, "duplicate registration key")
        value[key] = item
    return value


def contract(raw: bytes, *, actual: bool = False) -> dict:
    """Validate source declarations without opening a location in the document."""
    wire.require(type(raw) is bytes and 0 < len(raw) <= 16 * wire.MIB and type(actual) is bool,
                 "bounded registration bytes required")
    spec = json.loads(raw, object_pairs_hook=_pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite registration value")))
    wire.require(type(spec) is dict and spec["id"] == SPECIFICATION_ID and spec["campaign"] == "ED"
                 and spec["promotion_allowed"] is False, "ED registration identity/scope differs")
    population = spec["population"]
    wire.require(population["source_window"] == ["2025-09-04", "2026-09-23"]
                 and population["source_sessions"] == 257
                 and population["original_targets_per_timing_declared_in_existing_registration"] == 5140
                 and population["original_union_coordinates_declared_in_existing_registration"] == 5158
                 and population["original_archive_rows_declared_in_existing_registration"] == 1002211
                 and population["old_EA_AB_signal_mask_reused"] is False
                 and spec["source_geometry"] == FULL_GEOMETRY, "fixed original source geometry differs")
    codes = spec["filing_query_codes"]
    wire.require(type(codes) is list and len(codes) == 18 and codes == sorted(set(codes))
                 and all(type(code) is str and len(code) == 6 and code.isascii() and code.isdigit()
                         for code in codes), "fixed filing query roster differs")
    _registered_pins(spec)
    timing = spec["timing"]
    wire.require(wire.encode(timing["scenarios"]) == wire.encode({
        "S1": {"universe_available_session_lag": 1, "date_only_filing_available_session_lag": 1,
               "assumed_available_local_time": "08:30:00"},
        "S2": {"universe_available_session_lag": 2, "date_only_filing_available_session_lag": 2,
               "assumed_available_local_time": "08:30:00"}})
        and type(timing["completed_minute_available_after_end_minutes"]) is int
        and timing["completed_minute_available_after_end_minutes"] == 2
        and type(timing["entry_latency_minutes"]) is int
        and timing["entry_latency_minutes"] == 1, "fixed causal timing differs")
    tensor = spec["H1_common_support_and_tensor"]
    wire.require(tensor["schema_id"] == "ED-H1-common-30minute-observed-peer-v1"
        and tensor["tensor_shape"] == [30, 6] and tensor["minimum_common_peers"] == 5
        and tuple(item["name"] for item in tensor["dynamic_channels"]) == DYNAMIC_CHANNELS
        and tuple(tensor["static_numeric_order"]) == STATIC_NUMERIC_NAMES
        and tensor["static_categories"]["event_type_bits_order"] == [
            "NO_EVENT", "ORIGINAL_ANNUAL", "ORIGINAL_HALF_YEAR", "ORIGINAL_QUARTER"]
        and tensor["static_categories"]["event_age_one_hot_order"] == ["NO_EVENT_AGE",
            *[f"M{start}_{start + 29}" for start in range(30, 330, 30)]],
        "fixed common feature schema differs")
    training = spec["chronology_and_training"]
    wire.require(training["source_sessions"] == 257 and training["initial_train_sessions"] == 100
        and training["fold_lengths"] == [32, 32, 31, 31, 31]
        and training["fold_slices_train_start_train_end_test_start_test_end"] == [
            [0, 100, 100, 132], [0, 132, 132, 164], [0, 164, 164, 195],
            [0, 195, 195, 226], [0, 226, 226, 257]], "fixed chronological folds differ")
    wire.require(spec["label_and_common_replay_proposal"]["schema_id"] == "ED-causal-close-invalidation60-v1"
        and spec["cost_evidence_table"]["primary_continuity"]["total_bps"] == "33"
        and spec["cost_evidence_table"]["stress_continuity"]["total_bps"] == "43",
        "fixed replay/cost contract differs")
    execution = spec["execution_not_registered"]
    wire.require(execution["unit_ids"] == list(INPUT_CONTRACTS)
        and execution["input_contracts"] == INPUT_CONTRACTS
        and execution["resource_profile"] == wire.ED_H1_600.id
        and execution["operator_cpu_scope"] == "ED-H1-operator-2026-10-31"
        and type(spec["actual_runner_ready"]) is bool
        and type(spec["numbers_frozen_for_execution"]) is bool,
        "fixed execution declaration differs")
    resource = execution["operator_resource_decision"]
    wire.require(all(resource.get(name) == value for name, value in {
        "shared_cpu_seconds": 600, "worker_cpu_seconds": 480,
        "shared_address_space_bytes": 512 * wire.MIB, "worker_address_space_bytes": 352 * wire.MIB,
        "wall_seconds": 1200, "nice": 10, "concurrent_units": 1, "automatic_actual_retries": 0}.items()),
        "explicit ED resource amendment differs")
    if actual:
        wire.require(spec["actual_runner_ready"] is True and spec["numbers_frozen_for_execution"] is True
            and execution["source_only_at_this_stage"] is False
            and spec.get("public_runtime") == runtime_contract(), "actual ED runtime is not registered ready")
    return spec


def _registered_pins(spec: dict) -> tuple[bridge.InputPin, ...]:
    declared = spec["actual_source_inputs"]
    wire.require(type(declared) is list and len(declared) == 42
        and all(type(item) is dict and set(item) == {"name", "path", "sha256", "maximum"}
                for item in declared), "fixed source pin fields differ")
    by_name = {item["name"]: item for item in declared}
    wire.require(len(by_name) == len(declared) and set(by_name) == {name for name, _ in wire.ED_H1_INPUT_BOUNDS},
                 "fixed source pin roster differs")
    pins = tuple(bridge.InputPin(name, by_name[name]["path"], by_name[name]["sha256"], by_name[name]["maximum"])
                 for name, _ in wire.ED_H1_INPUT_BOUNDS)
    wire.validate_ed_input_manifest([pin.metadata(1) for pin in pins], input_contract=wire.ED_INPUTS_CONTRACT)
    identity = spec["input_identity"]
    for name, key, sha_key in (("CB", "original_monthly_universe", "sha256"),
        ("CC", "original_price_archive", "compressed_sha256"),
        ("CX", "original_volume_archive", "compressed_sha256"),
        ("DART-PUBLICATION", "adopted_disclosure_publication", "sha256")):
        wire.require((by_name[name]["path"], by_name[name]["sha256"]) ==
                     (identity[key]["path"], identity[key][sha_key]), "original source declaration differs")
    for key in ("original_price_archive", "original_volume_archive"):
        wire.require(wire.valid_sha(identity[key]["decoded_stream_sha256"]), "decoded source declaration differs")
    return pins


def decoded_stream_pin(cc_sha256: str, cx_sha256: str) -> str:
    wire.require(wire.valid_sha(cc_sha256) and wire.valid_sha(cx_sha256), "two decoded source pins required")
    return wire.digest(wire.encode({"CC": cc_sha256, "CX": cx_sha256}))


def invented_geometry(sessions: int) -> dict:
    wire.require(type(sessions) is int and 1 <= sessions <= 257, "invented session geometry differs")
    chunks = 20 * sessions + min(sessions, 18)
    rows = FULL_GEOMETRY["cc_rows"] * chunks // FULL_GEOMETRY["cc_chunks"]
    return {**FULL_GEOMETRY, "sessions": sessions, "targets_per_scenario": 20 * sessions,
        "union_coordinates": chunks, "cc_chunks": chunks, "cc_rows": rows, "cx_chunks": chunks,
        "cx_rows": rows, "cx_daily_rows": FULL_GEOMETRY["cx_daily_rows"] * chunks // 5158}


@dataclass(frozen=True)
class SnapshotSourceContract:
    geometry: bytes
    query_codes: tuple[str, ...]
    cc_decoded_sha256: str
    cx_decoded_sha256: str


def snapshot_hooks(plan: bridge.UnitPlan, specification: bytes, *, invented_only: bool,
                   source_manifest_sha256: str, runtime_sha256: str) -> bridge.UnitHooks:
    """Close over reviewed immutable data, never config-supplied executable code."""
    wire.require(type(invented_only) is bool and wire.valid_sha(source_manifest_sha256)
                 and wire.valid_sha(runtime_sha256), "fixed hook execution identity differs")
    spec = contract(specification, actual=not invented_only)
    bridge.validate_plan(plan)
    identity = spec["input_identity"]
    source = SnapshotSourceContract(wire.encode(FULL_GEOMETRY), tuple(spec["filing_query_codes"]),
        identity["original_price_archive"]["decoded_stream_sha256"],
        identity["original_volume_archive"]["decoded_stream_sha256"])
    wire.require(plan.unit_id == "ED-H1-SNAPSHOT" and plan.input_contract == wire.ED_INPUTS_CONTRACT
        and plan.resource_profile == wire.ED_H1_600.id
        and plan.specification_path == SPECIFICATION_PATH and plan.input_pins == _registered_pins(spec)
        and plan.full_geometry == source.geometry and plan.candidate_bounds == wire.encode(CANDIDATE_BOUNDS)
        and plan.decoded_stream_sha256 == decoded_stream_pin(source.cc_decoded_sha256, source.cx_decoded_sha256)
        and plan.output_filename == OUTPUT_FILENAME and plan.output_max_bytes == 512 * wire.MIB
        and plan.actual_runner_ready == spec["actual_runner_ready"]
        and set(MINIMUM_PUBLIC_SOURCES) <= set(plan.source_names), "fixed snapshot plan differs")
    registration_sha256 = wire.digest(specification)

    def load_plan(config: dict) -> bridge.UnitPlan:
        wire.require(config.get("invented_only") is invented_only
            and config["source_hashes"].get(SPECIFICATION_PATH) == registration_sha256
            and wire.digest(wire.encode(config["source_hashes"])) == source_manifest_sha256
            and config["local_identity"]["runtime_sha256"] == runtime_sha256,
            "fixed snapshot hook/source/runtime configuration differs")
        contract(specification, actual=not config["invented_only"])
        return plan

    def make_invented(directory: Path, sessions: int, checkpoint: Checkpoint) -> bridge.InputEnvelope:
        wire.require(invented_only, "invented generation unavailable to actual hook")
        return _make_invented(directory, sessions, source.query_codes, checkpoint)

    def compute_verify(directory: Path, envelope: bridge.InputEnvelope, checkpoint: Checkpoint) -> bridge.OutputReceipt:
        return _compute_snapshot(directory, envelope, plan, source, invented_only, {
            "registration_sha256": registration_sha256, "source_sha256": source_manifest_sha256,
            "runtime_sha256": runtime_sha256, "input_manifest_sha256": wire.digest(wire.encode(
                [item.metadata() for item in envelope.manifest])), "caller": "ED-H1-SNAPSHOT"}, checkpoint)

    return bridge.UnitHooks(load_plan, make_invented, compute_verify)


def _decoded_digest(path: Path, maximum: int, checkpoint: Checkpoint) -> str:
    """Invented bytes only; actual pins always come from the frozen registration."""
    digest, size = hashlib.sha256(), 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as raw, gzip.GzipFile(fileobj=raw, mode="rb") as decoded:
        while block := decoded.read(wire.BLOCK):
            checkpoint("invented_decoded_pin")
            size += len(block)
            wire.require(size <= maximum, "invented decoded archive exceeded")
            digest.update(block)
    return digest.hexdigest()


class _BoundedSink:
    def __init__(self, stream, maximum: int) -> None:
        self.stream, self.maximum, self.size = stream, maximum, 0

    def write(self, raw: bytes) -> int:
        wire.require(self.size + len(raw) <= self.maximum, "snapshot compressed output exceeded")
        wire.write_all(self.stream, raw)
        self.size += len(raw)
        return len(raw)

    def flush(self) -> None:
        self.stream.flush()


def _compute_snapshot(directory: Path, envelope: bridge.InputEnvelope, plan: bridge.UnitPlan,
                      source: SnapshotSourceContract, invented_only: bool, binding: dict,
                      checkpoint: Checkpoint) -> bridge.OutputReceipt:
    from research import intraday_ed_sources as sources
    from research import intraday_ed_snapshot as snapshot
    from research import intraday_ed_snapshot_verify as verifier

    wire.require(envelope.input_contract == wire.ED_INPUTS_CONTRACT, "snapshot raw input contract differs")
    geometry = bridge.canonical(envelope.geometry)
    if invented_only:
        wire.require(geometry == invented_geometry(geometry["sessions"]), "invented raw geometry differs")
    else:
        bridge._actual_envelope(envelope, plan)
    paths = sources.received_inputs(directory, [item.metadata() for item in envelope.manifest], checkpoint)
    manifest = {item.name: item for item in envelope.manifest}
    universe = sources.load_universe(bridge.bounded_read(paths["CB"], manifest["CB"].maximum),
                                     manifest["CB"].sha256, expected_sessions=geometry["sessions"])
    coordinates = {(row.session, row.symbol) for row in universe.targets}
    wire.require(all(len(universe.coordinates(scenario)) == geometry["targets_per_scenario"]
                     for scenario in ("S1", "S2")) and len(coordinates) == geometry["union_coordinates"],
                 "original universe coordinate geometry differs")
    cc_decoded, cx_decoded = source.cc_decoded_sha256, source.cx_decoded_sha256
    if invented_only:
        cc_decoded = _decoded_digest(paths["CC"], sources.MAX_STREAM, checkpoint)
        cx_decoded = _decoded_digest(paths["CX"], sources.MAX_STREAM, checkpoint)
    wire.require(envelope.stream_sha256 == decoded_stream_pin(cc_decoded, cx_decoded),
                 "combined decoded stream identity differs")
    archives = {kind: (paths[kind], sources.RawArchivePin(kind, manifest[kind].sha256, decoded,
        geometry[kind.lower() + "_chunks"], geometry[kind.lower() + "_rows"],
        0 if kind == "CC" else geometry["cx_daily_rows"]))
        for kind, decoded in (("CC", cc_decoded), ("CX", cx_decoded))}
    cache = directory / "raw-byte-chunks.sqlite"
    cached = sources.ByteChunkIndex.build(cache, archives, checkpoint)
    filing_files = {name: bridge.bounded_read(path, manifest[name].maximum)
                    for name, path in paths.items() if name.startswith("DART-")}
    filing_source = sources.load_filing_source(filing_files,
        {name: manifest[name].sha256 for name in filing_files}, expected_query_codes=source.query_codes,
        expected_complete_codes=geometry["complete_filing_codes"], expected_rows=geometry["filing_rows"])
    output = directory / OUTPUT_FILENAME
    wire.safe_path(output)
    with sources.audit_index(cache, archives, checkpoint, expected_cache_sha256=cached["sha256"]) as reader:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            digest = snapshot.write_snapshot(snapshot.snapshot_records(universe, reader, filing_source,
                binding=binding, checkpoint=checkpoint), _BoundedSink(stream, plan.output_max_bytes), checkpoint,
                validate_types=False)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            verified = verifier.verify_h1_snapshot(snapshot.read_snapshot(stream,
                expected_sha256=digest.compressed_sha256, expected_decoded_sha256=digest.decoded_sha256,
                checkpoint=checkpoint, validate_types=False), universe=universe, filing_source=filing_source, raw_reader=reader,
                binding=binding, checkpoint=checkpoint, raw_filing_files=filing_files)
        wire.require(verified.get("verified") is True and verified["record_counts"] == {
            "session": geometry["sessions"], "coordinate": 2 * geometry["targets_per_scenario"]}
            and verified.get("all_original_coordinates_verified") is True
            and verified.get("sampled_scientific_rows") is False, "complete independent snapshot verification required")
        audit = dict(reader.receipt)
    wire.require(wire.file_digest(output, plan.output_max_bytes, checkpoint) ==
                 (digest.compressed_sha256, digest.compressed_bytes), "stored snapshot output differs")
    descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        os.fchmod(stream.fileno(), 0o400)
        os.fsync(stream.fileno())
    wire.fsync_directory(directory)
    independent = {**verified, "raw_byte_audit": audit, "decoded_sha256": digest.decoded_sha256,
        "compressed_bytes": digest.compressed_bytes, "decoded_bytes": digest.decoded_bytes,
        "records": digest.records, "input_manifest_sha256": binding["input_manifest_sha256"],
        "invented_only": invented_only, "full_geometry": geometry == FULL_GEOMETRY,
        "qualifies_actual_by_itself": False}
    return bridge.OutputReceipt(digest.compressed_sha256, digest.compressed_bytes, envelope.geometry,
        wire.encode(independent), wire.encode({scenario: len(universe.coordinates(scenario)) for scenario in ("S1", "S2")}))


def _invented_calendar(sessions: int) -> tuple[str, ...]:
    start, end, days = date(2025, 9, 4), date(2026, 9, 23), []
    while start <= end:
        if start.weekday() < 5:
            days.append(start.isoformat())
        start += timedelta(days=1)
    full = (*days[:256], days[-1])
    return tuple(full[:sessions])


def _invented_universe(days: tuple[str, ...], codes: tuple[str, ...], geometry: dict) -> tuple[dict, tuple]:
    symbols = tuple("KRX:" + code for code in (*codes, "900001", "900002", "900003"))
    union = tuple((day, symbol) for index, day in enumerate(days)
                  for symbol in sorted(symbols[:20] + ((symbols[20],) if index < 18 else ())))
    base, extra = divmod(geometry["cc_rows"], len(union))
    rows = {coordinate: base + (index < extra) for index, coordinate in enumerate(union)}
    variants = {}
    for name in ("D+1", "D+2"):
        variants[name] = {"days": [{"session": day, "available_at": day + "T08:30:00+09:00",
            "selected_at": day + "T08:30:00+09:00", "targets": [{"symbol": symbol, "rank": rank,
                "stored_row_count": rows[day, symbol], "state": "stored_rows_completeness_unverified"}
                for rank, symbol in enumerate(symbols[:19] +
                    ((symbols[20],) if name == "D+2" and index < 18 else (symbols[19],)), 1)]}
            for index, day in enumerate(days)]}
    value = {"study_id": "intraday-universe-sources-v1", "invented_only": True,
        "calendar": {"rows": [{"date": day} for day in days]}, "variants": variants}
    value.update({key: False for key in ("historical_eligibility_certified", "liquidity_certified",
        "historical_revision_status_certified", "calendar_completeness_certified",
        "historical_api_availability_proven", "performance_certified", "promotion_allowed")})
    return value, tuple((day, symbol, rows[day, symbol]) for day, symbol in union)


def _invented_chunk(kind: str, day: str, symbol: str, row_count: int, day_index: int,
                    symbol_index: int, daily: bool) -> dict:
    from research import intraday_pattern_calibration as clock
    opening = clock.session_open(day)
    stamp = int(opening.timestamp()) * 1000
    base, sign = 1000 + symbol_index * 100, 1 if (day_index + symbol_index) % 2 else -1
    rows = []
    for offset in range(row_count):
        row = {"open_time_ms": stamp + offset * 60000, "fetched_at": "2026-10-31T00:00:00Z"}
        if kind == "CC":
            op = base if offset < 25 else base + 2 if offset < 35 else base + 3 + sign * ((offset - 35) // 10)
            close = op if offset < 35 else op + sign
            row.update(symbol=symbol, interval="1m", open=str(op), high=str(max(op, close) + 1),
                       low=str(min(op, close) - 1), close=str(close))
        else:
            row.update(volume="0" if offset == 120 else str(10 + day_index // 7), quote_volume=None)
        rows.append(row)
    chunk = {"symbol": symbol, "session": day, "observation_date": day, "available_at": None,
        "retrieved_at": "2026-10-31T00:00:00Z", "source": "collector klines", "is_final": None,
        "evidence_level": "assumed", "rows": rows}
    if kind == "CX":
        chunk.update(daily_rows=[{"open_time_ms": stamp, "fetched_at": "2026-10-31T00:00:00Z",
            "volume": "100000", "quote_volume": None}] if daily else [],
            daily_retrieved_at="2026-10-31T00:00:00Z")
    return chunk


def _invented_filings(days: tuple[str, ...], codes: tuple[str, ...]) -> dict[str, bytes]:
    from research import intraday_ed_disclosures as dart
    raw_files, listings, origin = {}, [], {"artifacts": {}}
    for index, code in enumerate(codes, 1):
        count = 5 if index <= 5 else 4 if index <= 14 else 0
        rows = []
        dated = sorted(((days[(index * 11 + serial * 47) % len(days)], serial)
                        for serial in range(1, count + 1)), reverse=True)
        for number, (day, serial) in enumerate(dated, 1):
            receipt = day.replace("-", "") + f"{index * 100 + serial:06d}"
            title = ("사업보고서", "반기보고서", "분기보고서")[(index + serial) % 3]
            annotation = "[정정]" if index == serial == 1 else ""
            rows.append(f'<tr><td>{number}</td><td><a onclick="openCorpInfoNew(\'00000001\', \'winCorpInfo\', '
                f'\'/dsae001/selectPopup.ax\');">발명회사</a></td><td><a id="r_{receipt}" '
                f'href="/dsaf001/main.do?rcpNo={receipt}" '
                f'onclick="openReportViewer(\'{receipt}\',\'\'); return false;">{title} (2024.12)</a>{annotation}'
                f'</td><td>발명회사</td><td>{day.replace("-", ".")}</td><td></td></tr>')
        raw = ((f'<div class="pageInfo">[1/1] [총 {count}건]</div><table><tbody id="tbody">'
                + "".join(rows) + "</tbody></table>").encode() if count else b"<p>invented unknown query</p>")
        metadata = {"received_at": "2026-10-31T00:00:00Z", "status": 200, "url": dart.SOURCE,
            "bytes": len(raw), "received_bytes": len(raw), "sha256": wire.digest(raw)}
        raw_files[f"DART-R{index:03d}"] = raw
        raw_files[f"DART-M{index:03d}"] = wire.encode(metadata)
        for kind, suffix in (("R", "-raw.html"), ("M", "-response.json")):
            origin["artifacts"][f"attempt-{index:03d}" + suffix] = wire.digest(raw_files[f"DART-{kind}{index:03d}"])
        page_rows = []
        if count:
            page = dart.parse_listing_page(raw, dart.ListingRequest(code, dart.WINDOW_START, dart.WINDOW_END, 1))
            page_rows = [asdict(row) | {"observation_date": row.observation_date.isoformat()} for row in page.rows]
        listings.append({"query_code": code, "state": "complete" if count else "unknown", "rows": page_rows})
    summary = {"wire_attempts": 18, "complete_codes": 14, "unknown_codes": 4, "complete_listing_rows": 61}
    report = {"schema": "ed-disclosure-acquisition-v1", "codes": list(codes), "source": dart.SOURCE,
        "start_date": dart.WINDOW_START.isoformat(), "end_date": dart.WINDOW_END.isoformat(),
        "state": "complete", "failure": None, "attempts": [f"attempt-{index:03d}" for index in range(1, 19)],
        "listings": listings, "summary": summary}
    raw_files["DART-REPORT"] = wire.encode(report)
    origin["artifacts"]["report.json"] = wire.digest(raw_files["DART-REPORT"])
    raw_files["DART-ORIGIN-MANIFEST"] = wire.encode(origin)
    raw_files["DART-PUBLICATION"] = wire.encode({"status": "completed", "verification_complete": True,
        "automatic_retries": 0, "artifacts": {"origin-manifest.json": wire.digest(raw_files["DART-ORIGIN-MANIFEST"])},
        "verified_support": {"schema": report["schema"], "verified": True, "state": "complete", **summary}})
    return raw_files


def _make_invented(directory: Path, sessions: int, codes: tuple[str, ...],
                   checkpoint: Checkpoint) -> bridge.InputEnvelope:
    geometry, days = invented_geometry(sessions), _invented_calendar(sessions)
    universe, coordinates = _invented_universe(days, codes, geometry)
    wire.exclusive_bytes(directory / "CB", wire.encode(universe))
    decoded_pins = {}
    by_day = {day: index for index, day in enumerate(days)}
    by_symbol = {symbol: index for index, symbol in enumerate(sorted({symbol for _, symbol, _ in coordinates}))}
    for kind in ("CC", "CX"):
        descriptor = os.open(directory / kind, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        decoded = hashlib.sha256()
        with os.fdopen(descriptor, "wb") as stream:
            with gzip.GzipFile(filename="", fileobj=stream, mode="wb", compresslevel=1, mtime=0) as compressed:
                for index, (day, symbol, row_count) in enumerate(coordinates):
                    checkpoint("invented_raw_chunk")
                    raw = wire.encode(_invented_chunk(kind, day, symbol, row_count, by_day[day], by_symbol[symbol],
                        kind == "CX" and index < geometry["cx_daily_rows"]))
                    compressed.write(raw)
                    decoded.update(raw)
            stream.flush()
            os.fsync(stream.fileno())
        decoded_pins[kind] = decoded.hexdigest()
    for name, raw in _invented_filings(days, codes).items():
        wire.exclusive_bytes(directory / name, raw)
    manifest = []
    for name, maximum in wire.ED_H1_INPUT_BOUNDS:
        pin, size = wire.file_digest(directory / name, maximum, checkpoint)
        manifest.append(bridge.InputFile(name, str(directory / name), pin, maximum, size))
    envelope = bridge.InputEnvelope(tuple(manifest), decoded_stream_pin(decoded_pins["CC"], decoded_pins["CX"]),
                                    wire.encode(geometry), wire.ED_INPUTS_CONTRACT)
    envelope.payload()
    return envelope
