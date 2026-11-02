"""EE hooks on the existing all-local stdio lifecycle; no work at import.

Hook construction uses immutable source declarations only. Science, raw-source
and gzip imports are deferred until the worker's post-STARTED callback. The
actual branch re-audits the existing sealed byte index and never rebuilds it.
Readiness, release and whole-path qualification remain separate obligations.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Callable

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire


SPECIFICATION_PATH = bridge.EE_SPECIFICATION_PATH
SCENARIO_PATH = "configs/research/discovery/intraday-trader-scenarios-v1.json"
SPECIFICATION_ID = "intraday-trader-local-input-v1"
OUTPUT_FILENAME = "trader-price-paths.jsonl.gz"
POLICIES = ("EE-A", "EE-B", "EE-A-WITHOUT-15M-CONTEXT", "EE-B-EARLY-RECLAIM")
FULL_GEOMETRY = {"sessions":257,"targets_per_scenario":5140,"union_coordinates":5158,
    "cc_chunks":5158,"cc_rows":1002211,"cx_chunks":5158,"cx_rows":1002211,"cx_daily_rows":2674}
CANDIDATE_BOUNDS = {clock: {policy:5140 for policy in POLICIES} for clock in ("S1", "S2")}
MINIMUM_PUBLIC_SOURCES = tuple(sorted((SPECIFICATION_PATH, SCENARIO_PATH,
    "python/research/intraday_trader_unit.py", "python/tests/test_intraday_trader_unit.py",
    "python/research/intraday_trader_report.py", "python/tests/test_intraday_trader_report.py",
    "python/research/intraday_trader_inputs.py", "python/research/intraday_trader_session.py",
    "python/research/intraday_trader_reference.py", "python/research/intraday_trader_scenarios.py",
    "python/research/intraday_trader_paths.py", "python/research/intraday_ed_inputs.py",
    "python/research/intraday_ed_sources.py", "python/research/intraday_ed_snapshot.py",
    "python/research/intraday_ed_comparison_unit.py", "python/research/intraday_campaign_wsl_bridge.py",
    "python/research/intraday_campaign_wsl_transport.py")))
Checkpoint = Callable[[str], object]


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        wire.require(key not in result, "duplicate EE registration key")
        result[key] = value
    return result


def _pins(spec: dict) -> tuple[bridge.InputPin, ...]:
    rows = spec["actual_source_preparation"]["local_received_sources"]
    wire.require(type(rows) is list and len(rows) == 3
        and all(type(row) is dict and set(row) == {"name", "path", "sha256", "maximum"}
                for row in rows), "EE fixed source fields differ")
    pins = tuple(bridge.InputPin(row["name"], row["path"], row["sha256"], row["maximum"]) for row in rows)
    wire.validate_registered_input_manifest([pin.metadata(1) for pin in pins], input_contract=wire.EE_INPUT_CONTRACT)
    return pins


def decoded_stream_pin(cc: str, cx: str) -> str:
    wire.require(wire.valid_sha(cc) and wire.valid_sha(cx), "EE decoded archive pins required")
    return wire.digest(wire.encode({"CC":cc,"CX":cx}))


def contract(raw: bytes, scenario: bytes, *, actual: bool = False) -> dict:
    wire.require(type(raw) is bytes and 0 < len(raw) <= wire.MIB and type(scenario) is bytes
        and 0 < len(scenario) <= wire.MIB and type(actual) is bool, "bounded EE source bytes required")
    spec = json.loads(raw, object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite EE registration")))
    wire.require(type(spec) is dict and spec["id"] == SPECIFICATION_ID and spec["campaign"] == "EE"
        and type(spec["actual_runner_ready"]) is bool
        and type(spec["actual_execution_authorized_by_this_file"]) is bool,
        "EE registration identity/readiness differs")
    wire.require(spec["scenario_specification_sha256"] == wire.digest(scenario), "EE scenario bytes differ")
    execution = spec["lifecycle_preparation"]
    wire.require(execution["unit_id"] == bridge.EE_UNIT_ID
        and execution["specification_path"] == SPECIFICATION_PATH
        and execution["input_contract"] == wire.EE_INPUT_CONTRACT
        and execution["resource_profile"] == wire.ORDINARY300.id
        and wire.encode(execution["input_roster"]) == wire.encode([list(row) for row in wire.EE_INPUT_BOUNDS])
        and execution["local_coordinator"] is True and execution["host_identity"] == bridge.LOCAL_HOST
        and execution["ssh_argv"] == [] and execution["remote_prefix"] == [], "EE fixed local execution differs")
    source = spec["actual_source_preparation"]
    wire.require(source["window"] == ["2025-09-04", "2026-09-23"]
        and wire.encode(source["geometry"]) == wire.encode(FULL_GEOMETRY), "EE fixed original geometry differs")
    _pins(spec)
    wire.require(set(source["decoded_archives"]) == {"CC", "CX"}
        and all(wire.valid_sha(pin) for pin in source["decoded_archives"].values()), "EE decoded archive roster differs")
    cached = source["local_raw_cache"]
    wire.require(type(cached["path"]) is str and Path(cached["path"]).is_absolute()
        and ".." not in Path(cached["path"]).parts and Path(cached["path"]).name == "raw-byte-chunks.sqlite"
        and not cached["path"].startswith("/mnt/") and wire.valid_sha(cached["sha256"]), "EE fixed native raw cache differs")
    output = spec["stored_output_preparation"]
    wire.require(output["schema"] == "EE-original-population-price-paths-v1"
        and output["filename"] == OUTPUT_FILENAME and type(output["maximum_compressed_bytes"]) is int
        and output["maximum_compressed_bytes"] == 512 * wire.MIB
        and type(output["maximum_decoded_bytes"]) is int and output["maximum_decoded_bytes"] == 1024 * wire.MIB
        and type(output["maximum_line_bytes"]) is int and output["maximum_line_bytes"] == 16 * wire.MIB
        and output["binding_keys"] == ["registration_sha256","scenario_specification_sha256","source_sha256",
            "runtime_sha256","input_manifest_sha256","caller"]
        and type(spec["actual_market_accesses_in_preparation"]) is int
        and spec["actual_market_accesses_in_preparation"] == 0
        and spec["live_or_paper_order_capable"] is False,
        "EE fixed output/scope differs")
    if actual:
        wire.require(spec["actual_runner_ready"] and spec["actual_execution_authorized_by_this_file"],
                     "actual EE source not registered ready")
    return spec


def invented_geometry(sessions: int) -> dict:
    wire.require(type(sessions) is int and 1 <= sessions <= 257, "EE invented session bound differs")
    chunks = 20 * sessions + min(sessions, 18)
    rows = FULL_GEOMETRY["cc_rows"] * chunks // FULL_GEOMETRY["cc_chunks"]
    return {**FULL_GEOMETRY, "sessions":sessions,"targets_per_scenario":20*sessions,
        "union_coordinates":chunks,"cc_chunks":chunks,"cc_rows":rows,"cx_chunks":chunks,"cx_rows":rows,
        "cx_daily_rows":FULL_GEOMETRY["cx_daily_rows"] * chunks // FULL_GEOMETRY["cx_chunks"]}


def primary_hooks(plan: bridge.UnitPlan, specification: bytes, scenario: bytes, *, invented_only: bool,
                  source_manifest_sha256: str, runtime_sha256: str) -> bridge.UnitHooks:
    """Close over source data only; execution config cannot supply callbacks."""
    wire.require(type(invented_only) is bool and wire.valid_sha(source_manifest_sha256)
        and wire.valid_sha(runtime_sha256), "EE immutable hook identity required")
    spec = contract(specification, scenario, actual=not invented_only)
    source = spec["actual_source_preparation"]
    wire.require(type(plan) is bridge.UnitPlan and plan.unit_id == bridge.EE_UNIT_ID
        and plan.specification_path == SPECIFICATION_PATH and plan.input_contract == wire.EE_INPUT_CONTRACT
        and plan.resource_profile == wire.ORDINARY300.id and plan.local_coordinator is True
        and plan.local_execution_root is not None and plan.local_worktree == plan.remote_worktree
        and plan.ssh_argv == plan.remote_prefix == () and plan.recovery_contract is None
        and plan.input_pins == _pins(spec)
        and plan.decoded_stream_sha256 == decoded_stream_pin(**{key.lower():value
            for key, value in source["decoded_archives"].items()})
        and plan.full_geometry == wire.encode(FULL_GEOMETRY)
        and plan.candidate_bounds == wire.encode(CANDIDATE_BOUNDS)
        and plan.output_filename == OUTPUT_FILENAME and plan.output_max_bytes == 512 * wire.MIB
        and plan.actual_runner_ready is spec["actual_runner_ready"]
        and set(MINIMUM_PUBLIC_SOURCES) <= set(plan.source_names), "EE fixed primary plan differs")
    registration_pin, scenario_pin = wire.digest(specification), wire.digest(scenario)

    def load_plan(config: dict) -> bridge.UnitPlan:
        wire.require(invented_only or (type(config.get("attempt")) is str and config["attempt"] == "actual1"
            and type(config.get("sessions")) is int and config["sessions"] == FULL_GEOMETRY["sessions"]),
            "EE actual requires fixed actual1 and full257 sessions")
        wire.require(config.get("invented_only") is invented_only
            and config["source_hashes"].get(SPECIFICATION_PATH) == registration_pin
            and config["source_hashes"].get(SCENARIO_PATH) == scenario_pin
            and wire.digest(wire.encode(config["source_hashes"])) == source_manifest_sha256
            and config["local_identity"]["runtime_sha256"] == runtime_sha256,
            "EE fixed hook/source/runtime differs")
        return plan

    def make_invented(directory: Path, sessions: int, checkpoint: Checkpoint) -> bridge.InputEnvelope:
        wire.require(invented_only, "invented EE generation unavailable to actual hook")
        return _make_invented(directory, sessions, checkpoint)

    def compute_verify(directory: Path, envelope: bridge.InputEnvelope, checkpoint: Checkpoint) -> bridge.OutputReceipt:
        binding = {"registration_sha256":registration_pin,"scenario_specification_sha256":scenario_pin,
            "source_sha256":source_manifest_sha256,"runtime_sha256":runtime_sha256,
            "input_manifest_sha256":wire.digest(wire.encode([item.metadata() for item in envelope.manifest])),
            "caller":bridge.EE_UNIT_ID}
        return _compute(directory, envelope, plan, source, invented_only, binding, checkpoint)

    return bridge.UnitHooks(load_plan, make_invented, compute_verify)


@contextmanager
def original_inputs(directory: Path, envelope: bridge.InputEnvelope, plan: bridge.UnitPlan,
                    source: dict, invented_only: bool, checkpoint: Checkpoint):
    """Same original-three byte/index authentication for both EE report shapes.

    This context is entered only in a post-STARTED worker callback. It never
    rebuilds the actual immutable index, and keeps the reader audit open across
    production and the separately decoded saved-result verification.
    """
    from research import intraday_ed_sources as sources
    from research import intraday_ed_comparison_unit as reuse
    wire.require(envelope.input_contract == wire.EE_INPUT_CONTRACT, "EE primary input contract differs")
    geometry = bridge.canonical(envelope.geometry)
    if invented_only:
        wire.require(geometry == invented_geometry(geometry["sessions"]), "EE invented geometry differs")
    else:
        bridge._actual_envelope(envelope, plan)
    wire.validate_registered_input_manifest([item.metadata() for item in envelope.manifest],
                                           input_contract=wire.EE_INPUT_CONTRACT)
    paths, manifest = {}, {item.name:item for item in envelope.manifest}
    for item in envelope.manifest:
        path = directory / "received" / item.name
        wire.require(wire.file_digest(path, item.maximum, checkpoint) == (item.sha256, item.size),
                     "EE received input differs")
        paths[item.name] = path
    universe = sources.load_universe(bridge.bounded_read(paths["CB"], manifest["CB"].maximum),
        manifest["CB"].sha256, expected_sessions=geometry["sessions"])
    union = {(target.session,target.symbol) for target in universe.targets}
    wire.require(len(union) == geometry["union_coordinates"] and all(len(universe.coordinates(clock))
        == geometry["targets_per_scenario"] for clock in ("S1", "S2")), "EE original population geometry differs")
    if not invented_only:
        wire.require((universe.sessions[0],universe.sessions[-1]) == tuple(source["window"]), "EE original window differs")
    decoded = dict(source["decoded_archives"])
    if invented_only:
        decoded = {kind:reuse._decoded_digest(paths[kind], sources.MAX_STREAM, checkpoint) for kind in ("CC", "CX")}
    wire.require(envelope.stream_sha256 == decoded_stream_pin(decoded["CC"],decoded["CX"]), "EE decoded envelope differs")
    archives = {kind:(paths[kind], sources.RawArchivePin(kind,manifest[kind].sha256,decoded[kind],
        geometry[kind.lower()+"_chunks"],geometry[kind.lower()+"_rows"],0 if kind == "CC" else geometry["cx_daily_rows"]))
        for kind in ("CC", "CX")}
    if invented_only:
        cache = directory / "raw-byte-chunks.sqlite"
        cached = sources.ByteChunkIndex.build(cache, archives, checkpoint)
        cache_pin = cached["sha256"]
    else:
        cache, cache_pin = Path(source["local_raw_cache"]["path"]), source["local_raw_cache"]["sha256"]
    with sources.audit_index(cache, archives, checkpoint, expected_cache_sha256=cache_pin) as reader:
        yield universe, reader


def _compute(directory: Path, envelope: bridge.InputEnvelope, plan: bridge.UnitPlan, source: dict,
             invented_only: bool, binding: dict, checkpoint: Checkpoint, *,
             profile: str = "primary") -> bridge.OutputReceipt:
    from research import intraday_ed_comparison_unit as reuse
    from research import intraday_trader_report as report

    scope = report.report_scope(profile)
    geometry = bridge.canonical(envelope.geometry)
    output = directory / OUTPUT_FILENAME
    wire.safe_path(output)
    with original_inputs(directory, envelope, plan, source, invented_only, checkpoint) as (universe, reader):
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            digest = report.write_report(report.report_records(universe,reader,binding=binding,geometry=geometry,
                invented_only=invented_only,checkpoint=checkpoint,profile=profile),
                reuse._BoundedSink(stream,plan.output_max_bytes),checkpoint,profile=profile)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            verified = report.verify_report(report.read_report(stream,expected_sha256=digest.compressed_sha256,
                expected_decoded_sha256=digest.decoded_sha256,checkpoint=checkpoint,profile=profile),universe=universe,
                raw_reader=reader,binding=binding,geometry=geometry,invented_only=invented_only,
                checkpoint=checkpoint,profile=profile)
        # This call consumes the concrete hash/EOF-checking read_report iterator,
        # rather than treating a plain iterable's exhaustion as gzip evidence.
        wire.require(verified.get("verified") is True and verified.get("record_stream_exhausted") is True,
                     "EE complete stored-result verification required")
        audit = dict(reader.receipt)
    wire.require(wire.file_digest(output,plan.output_max_bytes,checkpoint) ==
        (digest.compressed_sha256,digest.compressed_bytes), "EE stored report changed")
    descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor,"rb") as stream:
        os.fchmod(stream.fileno(),0o400)
        os.fsync(stream.fileno())
    wire.fsync_directory(directory)
    independent = {"verified":True,"schema":verified["schema"],"record_counts":verified["record_counts"],
        "full_eof_verified":True,"all_original_coordinates_verified":True,
        "summary_sha256":wire.digest(wire.encode(verified["summary"])),"raw_byte_audit":audit,
        "decoded_sha256":digest.decoded_sha256,"decoded_bytes":digest.decoded_bytes,
        "compressed_bytes":digest.compressed_bytes,"invented_only":invented_only,
        "input_manifest_sha256":binding["input_manifest_sha256"],"full_geometry":geometry == FULL_GEOMETRY,
        "qualifies_actual_by_itself":False,"scope":(
            "four first-attempt price-path policies; no C/reentry/account/fill certification"
            if profile == "primary" else scope)}
    return bridge.OutputReceipt(digest.compressed_sha256,digest.compressed_bytes,envelope.geometry,
        wire.encode(independent),wire.encode(verified["candidate_counts"]))


@lru_cache(maxsize=4096)
def _invented_prices(case: int, minute: int) -> tuple[str, ...]:
    initial = [(100,105,95,close) for close in (100,100,101,101,102,103)]
    a = initial + [(108,120,107,112),(112,122,111,115),(115,117,108,111),
                   (111,114,109,113),(113,116,110,115)]
    b = [(100,110,95,100)]*6 + [(100,101,90,93),(94,99,91,95),(95,100,92,96),
        (96,99,92,96),(96,100,92,97),(98,103,96,102)]
    block = minute // 5
    if case == 0:
        values = (100,105,95,100)
    elif case in (4,5):
        values = b[block] if block < len(b) else (100,103,98,101)
        if case == 5 and block == 9:
            values = (94,99,89,94)
    else:
        values = a[block] if block < len(a) else (110,111,109,110)
        if case == 3 and block < 6:
            values = (100,105,95,100)
        if case == 1 and minute >= 75:
            values = (123,125,122,123)
        if case == 2 and minute >= 65:
            values = (107,108,106,107)
    return tuple(map(str,values))


@lru_cache(maxsize=4096)
def _reentry_prices(case: int, minute: int) -> tuple[str, ...]:
    """Invented terminal/crossing cases only; existing profiles are unchanged."""
    if case in (0, 6):
        return _invented_prices(case, minute)
    a = [(100,105,95,close) for close in (100,100,101,101,102,103)] + [
        (108,120,107,112),(112,122,111,115),(115,117,108,111),(111,114,109,113),
        (113,116,110,115),(115,116,114,115),(123,131,114,123),(123,126,121,125),
        (125,126,120,122),(122,124,121,123),(123,127,122,126)]
    b = [(105,110,100,105)]*6 + [(105,112,90,95),(95,98,92,97),(97,99,93,98),
        (98,101,94,100),(100,103,95,102),(102,106,96,105),(105,106,104,105),
        (105,113,104,112),(105,106,95,98),(98,99,96,98),(98,100,97,99),
        (99,102,98,101),(101,104,99,103),(103,107,100,106)]
    overrides = {}
    if case in (4, 5):
        blocks, filler = b, (106,107,105,106)
        if case == 4:
            overrides = {65:(105,112,104,111),69:(112,113,111,112),
                         105:(106,107,93,94),109:(94,95,93,94)}
        else:
            blocks = b[:11] + [(102,103,95,102)]*3 + [(102,103,94,98)] + b[15:20]
    else:
        blocks, filler = a, (126,127,125,126)
        if case in (1, 7):
            overrides = {60:(115,131,114,130),64:(130,131,121,123),
                         90:(126,127,118,119),94:(118,119,117,118)}
        elif case == 2:
            blocks[10] = (113,116,107,115)
        elif case == 3:
            overrides = {58:(130,131,114,115)}
    values = overrides.get(minute, blocks[minute//5] if minute//5 < len(blocks) else filler)
    return tuple(map(str, values))


def _make_invented(directory: Path, sessions: int, checkpoint: Checkpoint, *,
                   profile: str = "primary") -> bridge.InputEnvelope:
    import gzip
    from research import intraday_ed_comparison_unit as reuse
    from research import intraday_pattern_calibration as clock
    wire.require(type(profile) is str and profile in {"primary", "mechanism1", "reentry"},
                 "closed EE invented profile required")
    geometry, days = invented_geometry(sessions), reuse._invented_calendar(sessions)
    codes = tuple(f"{number:06}" for number in range(1,19))
    universe, coordinates = reuse._invented_universe(days,codes,geometry)
    coordinates = list(coordinates)
    # Include an observed full no-event prefix and a short outcome while retaining exact totals.
    day,symbol,count = coordinates[0]
    extra = 300-count
    coordinates[0] = (day,symbol,300)
    day,symbol,count = coordinates[-1]
    wire.require(count > extra, "invented row redistribution bound")
    coordinates[-1] = (day,symbol,count-extra)
    counts = {(day,symbol):count for day,symbol,count in coordinates}
    # Only the new invented profile varies peer support/sign. One valid B
    # anchor retains its original structure on every session. Existing primary
    # fixture bytes and every actual-data path remain unchanged.
    day_modes = {day:index % 4 for index,day in enumerate(days)}
    anchors = {}
    if profile == "mechanism1":
        for index,(day,symbol,_) in enumerate(coordinates):
            if index % 8 == 4:
                anchors.setdefault(day,symbol)
        wire.require(set(anchors) == set(days), "invented peer anchor missing")
    for variant in universe["variants"].values():
        for day in variant["days"]:
            for target in day["targets"]:
                target["stored_row_count"] = counts[day["session"],target["symbol"]]
    wire.exclusive_bytes(directory/"CB",wire.encode(universe))
    decoded = {}
    for kind in ("CC","CX"):
        digest = hashlib.sha256()
        descriptor = os.open(directory/kind,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(descriptor,"wb") as stream:
            with gzip.GzipFile(filename="",fileobj=stream,mode="wb",compresslevel=1,mtime=0) as zipped:
                for index,(day,symbol,count) in enumerate(coordinates):
                    checkpoint("ee_invented_raw_chunk")
                    stamp, rows, case = int(clock.session_open(day).timestamp())*1000, [], index%8
                    for offset in range(count):
                        minute = offset + (case == 7 and offset >= 80)
                        row = {"open_time_ms":stamp+minute*60000,"fetched_at":"2026-11-02T00:00:00Z"}
                        if kind == "CC":
                            values = _reentry_prices(case,minute) if profile == "reentry" else _invented_prices(case,minute)
                            if (profile == "mechanism1" and symbol != anchors[day]
                                    and 45 <= minute < 60):
                                mode = day_modes[day]
                                if mode == 1:
                                    price = 120 - 2 * (minute-45)
                                    values = tuple(map(str,(price,price+1,price-1,price)))
                                elif mode == 2:
                                    values = ("100","101","99","100")
                            row.update(symbol=symbol,interval="1m",**dict(zip(("open","high","low","close"),values,strict=True)))
                            if case == 6 and offset == 0:
                                row["open"] = "invalid-invented-number"
                            if (profile == "mechanism1" and day_modes[day] == 3
                                    and symbol != anchors[day] and minute == 50):
                                row["open"] = "invalid-invented-peer-number"
                        else:
                            row.update(volume="0" if offset == 120 else "100",quote_volume=None)
                        rows.append(row)
                    chunk = {"symbol":symbol,"session":day,"observation_date":day,"available_at":None,
                        "retrieved_at":"2026-11-02T00:00:00Z","source":"collector klines","is_final":None,
                        "evidence_level":"assumed","rows":rows}
                    if kind == "CX":
                        chunk.update(daily_rows=[{"open_time_ms":stamp,"fetched_at":"2026-11-02T00:00:00Z",
                            "volume":"100000","quote_volume":None}] if index<geometry["cx_daily_rows"] else [],
                            daily_retrieved_at="2026-11-02T00:00:00Z")
                    raw = wire.encode(chunk)
                    zipped.write(raw)
                    digest.update(raw)
            stream.flush()
            os.fsync(stream.fileno())
        decoded[kind] = digest.hexdigest()
    entries = []
    for name,maximum in wire.EE_INPUT_BOUNDS:
        digest,size = wire.file_digest(directory/name,maximum,checkpoint)
        entries.append(bridge.InputFile(name,str(directory/name),digest,maximum,size))
    envelope = bridge.InputEnvelope(tuple(entries),decoded_stream_pin(decoded["CC"],decoded["CX"]),
        wire.encode(geometry),wire.EE_INPUT_CONTRACT)
    envelope.payload()
    return envelope
