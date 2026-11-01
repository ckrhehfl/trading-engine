"""Public-source contracts and tiny invented snapshot hooks; no actual inputs."""
import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_comparison_unit as unit
from research import intraday_ed_inputs as inputs
from research import intraday_ed_replay as replay
from research import intraday_ed_sources as sources
from research import intraday_ed_snapshot_verify as verifier


ROOT = Path(__file__).resolve().parents[2]


def specification():
    return (ROOT / unit.SPECIFICATION_PATH).read_bytes()


def plan_for(raw):
    spec = unit.contract(raw)
    names = tuple(sorted(set(unit.MINIMUM_PUBLIC_SOURCES) | {"python/research/experiment_log.py",
        "python/research/research_budget.py", "var/invented-ed-entry.py"}))
    local, remote, parent = Path("/invented-local/repo"), Path("/invented-remote/repo"), Path("/invented-private")
    pins = {item["name"]: item for item in spec["actual_source_inputs"]}
    identity = spec["input_identity"]
    return bridge.UnitPlan(unit_id="ED-H1-SNAPSHOT", specification_path=unit.SPECIFICATION_PATH,
        source_names=names, source_roster_sha256=wire.digest(wire.encode(list(names))),
        input_pins=tuple(bridge.InputPin(name, pins[name]["path"], pins[name]["sha256"], maximum)
                         for name, maximum in wire.ED_H1_INPUT_BOUNDS),
        decoded_stream_sha256=unit.decoded_stream_pin(identity["original_price_archive"]["decoded_stream_sha256"],
            identity["original_volume_archive"]["decoded_stream_sha256"]),
        window=wire.encode({"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}),
        full_geometry=wire.encode(unit.FULL_GEOMETRY), candidate_bounds=wire.encode(unit.CANDIDATE_BOUNDS),
        private_entrypoint="var/invented-ed-entry.py", local_worktree=local, remote_worktree=remote,
        actual_cache=parent / "cache" / "actual", actual_output=parent / "outputs" /
            bridge.canonical(bridge.ED_H1_RECOVERY_CONTRACT)["snapshot_output_directory"],
        actual_diagnostics=parent / "diagnostics" / "actual", canonical_journal=parent / "canonical" / "runs.jsonl",
        global_marker=parent / "canonical" /
            bridge.canonical(bridge.ED_H1_RECOVERY_CONTRACT)["recovery_marker_name"],
        research_locks=(parent / "canonical" / "research.lock", parent / "outputs" / "campaign.lock"),
        invented_cache_parent=parent / "cache", invented_output_parent=parent / "outputs",
        invented_diagnostics_parent=parent / "diagnostics", output_filename=unit.OUTPUT_FILENAME,
        output_max_bytes=512 * wire.MIB, ssh_argv=("ssh", "-i", "/invented-unused/key", "invented-unused.invalid"),
        remote_prefix=("python3", "-I", "-S", str(remote / "var/invented-ed-entry.py"), "coordinator"),
        input_contract=wire.ED_INPUTS_CONTRACT, resource_profile=wire.ED_H1_600_AS520.id,
        recovery_contract=bridge.ED_H1_RECOVERY_CONTRACT,
        actual_runner_ready=spec["actual_runner_ready"])


def hook_fixture():
    raw = specification()
    plan = plan_for(raw)
    config = {"invented_only": True, "source_hashes": {name: "a" * 64 for name in plan.source_names},
              "local_identity": {"runtime_sha256": "b" * 64}}
    config["source_hashes"][unit.SPECIFICATION_PATH] = wire.digest(raw)
    source_pin = wire.digest(wire.encode(config["source_hashes"]))
    hooks = unit.snapshot_hooks(plan, raw, invented_only=True, source_manifest_sha256=source_pin,
                                runtime_sha256="b" * 64)
    return raw, plan, config, hooks


def checkpoint(_stage):
    pass


def received_fixture(tmp_path, sessions=2):
    raw, plan, config, hooks = hook_fixture()
    generated = tmp_path / "generated"
    generated.mkdir(mode=0o700)
    envelope = hooks.make_invented(generated, sessions, checkpoint)
    received = tmp_path / "received"
    received.mkdir(mode=0o700)
    for item in envelope.manifest:
        (received / item.name).write_bytes((generated / item.name).read_bytes())
        (received / item.name).chmod(0o400)
    return raw, plan, config, hooks, envelope


def test_hook_factory_and_plan_load_do_not_access_declared_locations(monkeypatch):
    raw = specification()
    plan = plan_for(raw)
    hashes = {name: "a" * 64 for name in plan.source_names}
    hashes[unit.SPECIFICATION_PATH] = wire.digest(raw)
    config = {"invented_only": True, "source_hashes": hashes, "local_identity": {"runtime_sha256": "b" * 64}}
    def inaccessible(*args, **kwargs):
        raise AssertionError("source-only construction attempted evidence access")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", inaccessible)
        patch.setattr(Path, "lstat", inaccessible)
        patch.setattr(Path, "open", inaccessible)
        patch.setattr(wire, "file_digest", inaccessible)
        patch.setattr(bridge, "bounded_read", inaccessible)
        hooks = unit.snapshot_hooks(plan, raw, invented_only=True,
            source_manifest_sha256=wire.digest(wire.encode(hashes)), runtime_sha256="b" * 64)
        assert hooks.load_plan(config) is plan
        assert unit.contract(raw)["actual_runner_ready"] is True


@pytest.mark.parametrize("change", ["actual", "spec_pin", "source_map", "runtime"])
def test_fixed_hook_config_cannot_choose_another_science_or_runtime(change):
    _, _, config, hooks = hook_fixture()
    changed = deepcopy(config)
    if change == "actual":
        changed["invented_only"] = False
    elif change == "spec_pin":
        changed["source_hashes"][unit.SPECIFICATION_PATH] = "c" * 64
    elif change == "source_map":
        changed["source_hashes"]["unregistered.py"] = "c" * 64
    else:
        changed["local_identity"]["runtime_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="hook/source/runtime"):
        hooks.load_plan(changed)


def test_config_callback_is_never_used_as_executable_dispatch():
    _, plan, config, hooks = hook_fixture()
    def forbidden(*args):
        pytest.fail("config callback executed")
    config["compute_verify"] = forbidden
    config["load_plan"] = forbidden
    assert hooks.load_plan(config) is plan
    assert hooks.compute_verify is not forbidden


def test_actual_runtime_remains_closed_even_if_one_readiness_flag_is_changed():
    spec = json.loads(specification())
    spec["actual_runner_ready"] = False
    spec["numbers_frozen_for_execution"] = False
    spec["execution_not_registered"]["source_only_at_this_stage"] = True
    raw = wire.encode(spec)
    plan = plan_for(raw)
    with pytest.raises(ValueError, match="not registered ready"):
        unit.snapshot_hooks(plan, raw, invented_only=False, source_manifest_sha256="a" * 64,
                            runtime_sha256="b" * 64)
    spec["actual_runner_ready"] = True
    with pytest.raises(ValueError, match="not registered ready"):
        unit.contract(wire.encode(spec), actual=True)


def test_as520_runtime_preserves_historical_cpu_decision_and_output_ceiling():
    spec = unit.contract(specification(), actual=True)
    execution = spec["execution_not_registered"]
    assert execution["resource_profile"] == wire.ED_H1_600_AS520.id == "ed-h1-cpu600-as520-v1"
    assert spec["public_runtime"] == unit.runtime_contract()
    assert spec["public_runtime"]["snapshot_maximum_bytes"] == 512 * wire.MIB
    historic = execution["operator_resource_decision"]
    assert historic["approval"] == "call_999db0d0f39f4712be1e7a41a28b59a8 option1"
    assert historic["shared_address_space_bytes"] == 512 * wire.MIB
    memory = execution["operator_memory_decision"]
    assert memory["shared_address_space_bytes"] == 520 * wire.MIB
    assert memory["coordinator_address_space_bytes"] == 72 * wire.MIB
    assert memory["coordinator_parent_address_space_bytes"] == 40 * wire.MIB
    assert memory["coordinator_git_helper_address_space_bytes"] == 32 * wire.MIB
    assert memory["worker_address_space_bytes"] == historic["worker_address_space_bytes"] == 352 * wire.MIB


@pytest.mark.parametrize("actual", [False, True])
@pytest.mark.parametrize("change", ["missing", "object", "extra", "approval", "scope", "attempt", "limit",
    "automatic", "bool_integer", "float_integer", "source", "run", "status", "offset", "marker", "output"])
def test_registration_requires_the_exact_single_technical_retry_decision(actual, change):
    spec = json.loads(specification())
    execution = spec["execution_not_registered"]
    recovery = execution["operator_technical_retry_decision"]
    if change == "missing":
        execution.pop("operator_technical_retry_decision")
    elif change == "object":
        execution["operator_technical_retry_decision"] = []
    elif change == "extra":
        recovery["another_retry"] = True
    else:
        key, value = {"approval": ("approval", "unrecorded"), "scope": ("scope", "All ED units may retry."),
            "attempt": ("next_attempt", "actual3"), "limit": ("maximum_new_actual_attempts", 2),
            "automatic": ("automatic_actual_retries", 1), "bool_integer": ("maximum_new_actual_attempts", True),
            "float_integer": ("maximum_new_actual_attempts", 1.0),
            "source": ("previous_source_manifest_sha256", "a" * 64), "run": ("previous_run_id", "forged"),
            "status": ("previous_terminal_status", "completed"), "offset": ("previous_terminal_offsets", [0, 1]),
            "marker": ("recovery_marker_name", "ed-h1-common-unresolved.json"),
            "output": ("snapshot_output_directory", "old-output")}[change]
        recovery[key] = value
    with pytest.raises(ValueError, match="technical retry decision"):
        unit.contract(wire.encode(spec), actual=actual)


@pytest.mark.parametrize("contract", [None, b"{}\n", bytearray(bridge.ED_H1_RECOVERY_CONTRACT)])
def test_snapshot_factory_requires_immutable_exact_recovery_contract(contract):
    raw, plan, config, _ = hook_fixture()
    with pytest.raises(ValueError, match="recovery contract"):
        unit.snapshot_hooks(replace(plan, recovery_contract=contract), raw, invented_only=True,
            source_manifest_sha256=wire.digest(wire.encode(config["source_hashes"])), runtime_sha256="b" * 64)


@pytest.mark.parametrize("actual", [False, True])
@pytest.mark.parametrize("change", ["absent", "not_object", "unknown_field", "missing_field",
    "approval", "date", "scope", "generic_budget", "role_swap", "integer_bool", "integer_float"])
def test_registration_requires_exact_scoped_memory_approval(actual, change):
    spec = json.loads(specification())
    execution = spec["execution_not_registered"]
    memory = execution["operator_memory_decision"]
    if change == "absent":
        execution.pop("operator_memory_decision")
    elif change == "not_object":
        execution["operator_memory_decision"] = []
    elif change == "unknown_field":
        memory["all_research_authorized"] = True
    elif change == "missing_field":
        memory.pop("coordinator_parent_address_space_bytes")
    elif change == "approval":
        memory["approval"] = execution["operator_resource_decision"]["approval"]
    elif change == "date":
        memory["date"] = "2026-10-31 KST"
    elif change == "scope":
        memory["scope"] = "All ED branches including H2/TCN."
    elif change == "generic_budget":
        execution["operator_memory_decision"] = {"shared_address_space_bytes": 520 * wire.MIB,
            "approved": True}
    elif change == "role_swap":
        memory["worker_address_space_bytes"] += wire.MIB
        memory["controller_address_space_bytes"] -= wire.MIB
    elif change == "integer_bool":
        memory["concurrent_units"] = True
    else:
        memory["shared_cpu_seconds"] = 600.0
    with pytest.raises(ValueError, match="AS520 memory amendment"):
        unit.contract(wire.encode(spec), actual=actual)


@pytest.mark.parametrize("name", ["shared_address_space_bytes", "coordinator_address_space_bytes",
    "coordinator_parent_address_space_bytes", "coordinator_git_helper_address_space_bytes",
    "worker_address_space_bytes", "controller_address_space_bytes", "ssh_client_address_space_bytes",
    "shared_cpu_seconds", "worker_cpu_seconds", "wall_seconds", "nice", "concurrent_units",
    "automatic_actual_retries"])
def test_memory_approval_cannot_change_role_caps_or_execution_limits(name):
    spec = json.loads(specification())
    spec["execution_not_registered"]["operator_memory_decision"][name] += 1
    with pytest.raises(ValueError, match="AS520 memory amendment"):
        unit.contract(wire.encode(spec), actual=True)


@pytest.mark.parametrize("name,value", [("approval", "call_e02edfb80fe843419c9925c03f1baf90 option1"),
    ("shared_address_space_bytes", 520 * wire.MIB), ("scope", "All research.")])
def test_historical_cpu_approval_cannot_be_rewritten_as_memory_approval(name, value):
    spec = json.loads(specification())
    spec["execution_not_registered"]["operator_resource_decision"][name] = value
    with pytest.raises(ValueError, match="historical ED CPU"):
        unit.contract(wire.encode(spec), actual=True)


@pytest.mark.parametrize("profile", [wire.ED_H1_600.id, wire.CORE600.id, wire.ORDINARY300.id])
def test_old_as512_registration_or_runtime_does_not_authorize_active_as520(profile):
    for declaration in ("execution_not_registered", "public_runtime"):
        spec = json.loads(specification())
        spec[declaration]["resource_profile"] = profile
        with pytest.raises(ValueError):
            unit.contract(wire.encode(spec), actual=True)


@pytest.mark.parametrize("change", ["geometry", "source_sha", "source_path", "source_maximum", "extra_source",
    "query_code", "timing", "bool_timing", "tensor", "channel_order", "static_order", "category_order",
    "fold", "cost", "promotion", "input_contract"])
def test_registration_rejects_frozen_contract_drift(change):
    spec = json.loads(specification())
    if change == "geometry":
        spec["source_geometry"]["cc_rows"] += 1
    elif change == "source_sha":
        spec["actual_source_inputs"][0]["sha256"] = "c" * 64
    elif change == "source_path":
        spec["actual_source_inputs"][0]["path"] = "/different/universe"
    elif change == "source_maximum":
        spec["actual_source_inputs"][0]["maximum"] += 1
    elif change == "extra_source":
        spec["actual_source_inputs"].append(deepcopy(spec["actual_source_inputs"][0]))
    elif change == "query_code":
        spec["filing_query_codes"].reverse()
    elif change == "timing":
        spec["timing"]["entry_latency_minutes"] = 2
    elif change == "bool_timing":
        spec["timing"]["scenarios"]["S1"]["universe_available_session_lag"] = True
    elif change == "tensor":
        spec["H1_common_support_and_tensor"]["tensor_shape"] = [30, 7]
    elif change == "channel_order":
        spec["H1_common_support_and_tensor"]["dynamic_channels"].reverse()
    elif change == "static_order":
        spec["H1_common_support_and_tensor"]["static_numeric_order"].reverse()
    elif change == "category_order":
        spec["H1_common_support_and_tensor"]["static_categories"]["event_age_one_hot_order"].reverse()
    elif change == "fold":
        spec["chronology_and_training"]["fold_lengths"][-1] += 1
    elif change == "cost":
        spec["cost_evidence_table"]["primary_continuity"]["total_bps"] = "0"
    elif change == "promotion":
        spec["promotion_allowed"] = True
    else:
        spec["execution_not_registered"]["input_contracts"]["ED-H1-SNAPSHOT"] = wire.EC_INPUT_CONTRACT
    with pytest.raises(ValueError):
        unit.contract(wire.encode(spec))


def test_duplicate_json_keys_are_not_an_implicit_registration_override():
    with pytest.raises(ValueError, match="duplicate registration"):
        unit.contract(b'{"id":"wrong","id":"intraday-ed-h1-common-comparison-v1"}')


@pytest.mark.parametrize("change", ["decoded_pin", "input_pin", "output", "geometry", "bounds", "readiness", "unit"])
def test_factory_rejects_caller_plan_overrides(change):
    raw, plan, config, _ = hook_fixture()
    if change == "decoded_pin":
        plan = replace(plan, decoded_stream_sha256="c" * 64)
    elif change == "input_pin":
        plan = replace(plan, input_pins=(replace(plan.input_pins[0], sha256="c" * 64), *plan.input_pins[1:]))
    elif change == "output":
        plan = replace(plan, output_filename="different.gz")
    elif change == "geometry":
        plan = replace(plan, full_geometry=wire.encode(unit.invented_geometry(2)))
    elif change == "bounds":
        plan = replace(plan, candidate_bounds=wire.encode({"S1": 5141, "S2": 5140}))
    elif change == "readiness":
        plan = replace(plan, actual_runner_ready=not plan.actual_runner_ready)
    else:
        plan = replace(plan, unit_id="ED-H1-RULES")
    with pytest.raises(ValueError):
        unit.snapshot_hooks(plan, raw, invented_only=True,
            source_manifest_sha256=wire.digest(wire.encode(config["source_hashes"])), runtime_sha256="b" * 64)


@pytest.mark.parametrize("profile", [wire.ED_H1_600.id, wire.CORE600.id, wire.ORDINARY300.id])
def test_snapshot_factory_rejects_old_as512_plan(profile):
    raw, plan, config, _ = hook_fixture()
    with pytest.raises(ValueError):
        unit.snapshot_hooks(replace(plan, resource_profile=profile), raw, invented_only=True,
            source_manifest_sha256=wire.digest(wire.encode(config["source_hashes"])), runtime_sha256="b" * 64)


def test_full_invented_calendar_and_universe_geometry_without_creating_price_bodies():
    spec = unit.contract(specification())
    geometry = unit.invented_geometry(257)
    assert geometry == unit.FULL_GEOMETRY
    days = unit._invented_calendar(257)
    original, union = unit._invented_universe(days, tuple(spec["filing_query_codes"]), geometry)
    raw = wire.encode(original)
    calendar = sources.load_universe(raw, wire.digest(raw))
    assert len(calendar.sessions) == 257
    assert len(calendar.coordinates("S1")) == len(calendar.coordinates("S2")) == 5140
    assert len(union) == len({(row.session, row.symbol) for row in calendar.targets}) == 5158
    assert sum(rows for _, _, rows in union) == 1002211
    assert all(100 <= rows <= 600 for _, _, rows in union)
    assert all(day >= "2025-09-04" and day <= "2026-09-23" for day in days)
    assert not unit.runtime_contract()["tiny_invented_profile_qualifies_full_geometry"]
    for sessions in (0, 258, True, 1.0):
        with pytest.raises(ValueError):
            unit.invented_geometry(sessions)


def test_invented_first_parent_clocks_span_days_symbols_and_seventeen_reference_offsets():
    days = unit._invented_calendar(257)
    offsets = {}
    for symbol_index in (0, 1, 20):
        symbol = f"KRX:{symbol_index + 1:06d}"
        offsets[symbol] = set()
        for day_index, day in enumerate(days[:17]):
            price = unit._invented_chunk("CC", day, symbol, 194, day_index, symbol_index, False)
            volume = unit._invented_chunk("CX", day, symbol, 194, day_index, symbol_index, True)
            assert sources._raw_identity(price, "CC") == (symbol, day, 194, 0)
            assert sources._raw_identity(volume, "CX") == (symbol, day, 194, 1)
            session = sources.session_inputs(symbol, day, price, volume).session
            scan = inputs.first_h1_parent(session, through=session.closing - 78 * inputs.MINUTE)
            independent = verifier.reconstruct_parent(session)
            assert scan == independent and scan.status == "parent"
            end_offset = int((scan.parent.trigger.end_at - session.opening) // inputs.MINUTE)
            assert end_offset == 30 + 5 * ((day_index * 7 + symbol_index * 11) % 17)
            offsets[symbol].add(end_offset - 30)
            # Every parent has the common30 completed minutes before decision;
            # its fixed entry,60-minute cap and publication lag fit stored rows.
            candidate = replay.candidate_for_parent(scan.parent, closing=session.closing, parent_id="invented")
            reference = sources.reference_session_inputs(symbol, day, price, volume,
                requested_starts=tuple(scan.parent.trigger.end_at - (30 - index) * inputs.MINUTE for index in range(30)))
            assert len(reference.minutes) == 30
            assert all(minute.available_at <= candidate.decision_at for minute in reference.minutes.values())
            assert candidate.entry_at > candidate.decision_at
            assert candidate.cap_exit_at == candidate.entry_at + 60 * inputs.MINUTE <= session.closing - 15 * inputs.MINUTE
            assert candidate.cap_exit_at in session.minutes
            assert session.minutes[candidate.cap_exit_at].available_at <= session.opening + 194 * inputs.MINUTE
            assert session.minutes[session.opening + 120 * inputs.MINUTE].shares == 0
    assert all(starts == set(range(0, 81, 5)) for starts in offsets.values())


@pytest.mark.parametrize("day", ["2025-09-04", "2025-11-13", "2026-01-02"])
@pytest.mark.parametrize("day_index,symbol_index", [(0, 0), (17, 0), (1, 0), (0, 1), (16, 20)])
def test_invented_clock_shift_changes_only_price_path_not_source_geometry(day, day_index, symbol_index):
    symbol, count = "KRX:000001", 194
    price = unit._invented_chunk("CC", day, symbol, count, day_index, symbol_index, False)
    volume = unit._invented_chunk("CX", day, symbol, count, day_index, symbol_index, True)
    assert price == unit._invented_chunk("CC", day, symbol, count, day_index, symbol_index, False)
    opening = sources.calendar_source.session_open(day)
    stamp = int(opening.timestamp()) * 1000
    base, sign = 1000 + symbol_index * 100, 1 if (day_index + symbol_index) % 2 else -1
    delay = 5 * ((day_index * 7 + symbol_index * 11) % 17)
    for offset, (prow, vrow) in enumerate(zip(price["rows"], volume["rows"], strict=True)):
        relative = offset - delay
        # The previous fixture's literal price template at its shifted minute.
        op = base if relative < 25 else base + 2 if relative < 35 else base + 3 + sign * ((relative - 35) // 10)
        close = op if relative < 35 else op + sign
        assert prow == {"open_time_ms": stamp + offset * 60000, "fetched_at": "2026-10-31T00:00:00Z",
            "symbol": symbol, "interval": "1m", "open": str(op), "high": str(max(op, close) + 1),
            "low": str(min(op, close) - 1), "close": str(close)}
        assert vrow == {"open_time_ms": stamp + offset * 60000, "fetched_at": "2026-10-31T00:00:00Z",
            "volume": "0" if offset == 120 else str(10 + day_index // 7), "quote_volume": None}
    assert sources._raw_identity(price, "CC") == (symbol, day, count, 0)
    assert sources._raw_identity(volume, "CX") == (symbol, day, count, 1)
    assert volume["daily_rows"] == [{"open_time_ms": stamp, "fetched_at": "2026-10-31T00:00:00Z",
                                     "volume": "100000", "quote_volume": None}]
    # Source/schema geometry and model fit/label contracts are unchanged;
    # these fixtures establish no market or full-fit qualification result.
    assert unit.invented_geometry(257) == unit.FULL_GEOMETRY


def test_source_contract_is_immutable_and_runtime_does_not_return_mutable_globals():
    source = unit.SnapshotSourceContract(wire.encode(unit.FULL_GEOMETRY), ("000001",), "a" * 64, "b" * 64)
    with pytest.raises(FrozenInstanceError):
        source.cc_decoded_sha256 = "c" * 64
    runtime = unit.runtime_contract()
    runtime["snapshot_geometry"]["sessions"] = 1
    runtime["input_contracts"].clear()
    assert unit.runtime_contract()["snapshot_geometry"]["sessions"] == 257
    assert len(unit.runtime_contract()["input_contracts"]) == 4


def test_tiny_invented_42_source_files_have_exact_eof_counts_and_repaired_filing_chain(tmp_path):
    raw, _, _, hooks = hook_fixture()
    generated = tmp_path / "generated"
    generated.mkdir(mode=0o700)
    envelope = hooks.make_invented(generated, 2, checkpoint)
    assert envelope.input_contract == wire.ED_INPUTS_CONTRACT
    assert bridge.canonical(envelope.geometry) == unit.invented_geometry(2)
    assert len(envelope.manifest) == 42
    manifest = {item.name: item for item in envelope.manifest}
    decoded = {}
    for kind in ("CC", "CX"):
        decoded[kind] = unit._decoded_digest(generated / kind, sources.MAX_STREAM, checkpoint)
        geometry = bridge.canonical(envelope.geometry)
        pin = sources.RawArchivePin(kind, manifest[kind].sha256, decoded[kind], geometry[kind.lower() + "_chunks"],
            geometry[kind.lower() + "_rows"], geometry["cx_daily_rows"] if kind == "CX" else 0)
        chunks = tuple(sources.canonical_chunks(generated / kind, pin, checkpoint))
        assert len(chunks) == geometry["union_coordinates"]
        assert sum(chunk.row_count for chunk in chunks) == geometry["cc_rows"]
    assert envelope.stream_sha256 == unit.decoded_stream_pin(decoded["CC"], decoded["CX"])
    filings = {name: (generated / name).read_bytes() for name in manifest if name.startswith("DART-")}
    source = sources.load_filing_source(filings, {name: manifest[name].sha256 for name in filings},
        expected_query_codes=tuple(unit.contract(raw)["filing_query_codes"]))
    assert len(source.rows) == 61 and len(source.complete_codes) == 14 and len(source.unknown_codes) == 4
    assert any(row.semantic_state == "semantic_unknown_annotation" for row in source.rows)
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    again = hooks.make_invented(other, 2, checkpoint)
    assert [(item.name, item.sha256, item.size) for item in again.manifest] == [
        (item.name, item.sha256, item.size) for item in envelope.manifest]
    with pytest.raises(FileExistsError):
        hooks.make_invented(generated, 2, checkpoint)


def test_tiny_snapshot_orchestration_independently_verifies_and_seals_one_output(tmp_path):
    _, plan, config, hooks, envelope = received_fixture(tmp_path)
    assert hooks.load_plan(config) is plan
    receipt = hooks.compute_verify(tmp_path, envelope, checkpoint)
    bridge.validate_result(receipt.payload(), envelope, plan)
    output = tmp_path / unit.OUTPUT_FILENAME
    assert output.stat().st_mode & 0o777 == 0o400
    assert wire.file_digest(output, plan.output_max_bytes, checkpoint) == (receipt.output_sha256, receipt.output_bytes)
    verified = bridge.canonical(receipt.independent_verification)
    assert verified["verified"] and verified["all_original_coordinates_verified"]
    assert verified["sampled_scientific_rows"] is False
    assert verified["record_counts"] == {"session": 2, "coordinate": 80}
    assert verified["records"] == 84
    assert verified["invented_only"] is True and verified["full_geometry"] is False
    assert verified["qualifies_actual_by_itself"] is False
    assert bridge.canonical(receipt.candidate_counts) == {"S1": 40, "S2": 40}
    assert verified["raw_byte_audit"]["full_eof_verified"] is True
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        hooks.compute_verify(tmp_path, envelope, checkpoint)
    assert output.read_bytes() == before


def test_changed_received_source_is_refused_before_cache_or_output(tmp_path):
    _, _, _, hooks, envelope = received_fixture(tmp_path)
    path = tmp_path / "received" / "DART-R001"
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(sources.SourceRefusal, match="received_input_pin_differs"):
        hooks.compute_verify(tmp_path, envelope, checkpoint)
    assert not (tmp_path / "raw-byte-chunks.sqlite").exists()
    assert not (tmp_path / unit.OUTPUT_FILENAME).exists()


def test_bad_invented_geometry_and_contract_are_rejected_before_any_source_io(tmp_path, monkeypatch):
    _, _, _, hooks, envelope = received_fixture(tmp_path)
    def inaccessible(*args, **kwargs):
        raise AssertionError("invalid contract reached source IO")
    monkeypatch.setattr(sources, "received_inputs", inaccessible)
    geometry = bridge.canonical(envelope.geometry)
    geometry["cc_rows"] += 1
    for changed in (replace(envelope, geometry=wire.encode(geometry)),
                    replace(envelope, input_contract=wire.EC_INPUT_CONTRACT)):
        with pytest.raises(ValueError):
            hooks.compute_verify(tmp_path, changed, checkpoint)


def test_unverified_snapshot_is_retained_unsealed_and_never_returned_as_output(tmp_path, monkeypatch):
    from research import intraday_ed_snapshot_verify as verifier
    _, _, _, hooks, envelope = received_fixture(tmp_path)
    monkeypatch.setattr(verifier, "verify_h1_snapshot", lambda *args, **kwargs: {"verified": False})
    with pytest.raises(ValueError, match="complete independent"):
        hooks.compute_verify(tmp_path, envelope, checkpoint)
    output = tmp_path / unit.OUTPUT_FILENAME
    assert output.exists() and output.stat().st_mode & 0o777 == 0o600


def test_public_module_has_no_numeric_import_network_dispatch_or_module_body_io():
    tree = ast.parse((ROOT / "python/research/intraday_ed_comparison_unit.py").read_text())
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(name in ast.unparse(node) for node in imports
                   for name in ("numpy", "lightgbm", "torch", "socket", "subprocess", "requests", "importlib"))
    forbidden_calls = {"eval", "exec", "compile", "__import__", "getattr"}
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in forbidden_calls for node in ast.walk(tree))
    body = [node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"open", "read_bytes", "read_text", "file_digest", "bounded_read", "glob", "rglob"}
        for statement in body for node in ast.walk(statement))
