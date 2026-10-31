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
from research import intraday_ed_sources as sources


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
        actual_cache=parent / "cache" / "actual", actual_output=parent / "outputs" / "actual",
        actual_diagnostics=parent / "diagnostics" / "actual", canonical_journal=parent / "canonical" / "runs.jsonl",
        global_marker=parent / "canonical" / "unresolved.json",
        research_locks=(parent / "canonical" / "research.lock", parent / "outputs" / "campaign.lock"),
        invented_cache_parent=parent / "cache", invented_output_parent=parent / "outputs",
        invented_diagnostics_parent=parent / "diagnostics", output_filename=unit.OUTPUT_FILENAME,
        output_max_bytes=512 * wire.MIB, ssh_argv=("ssh", "-i", "/invented-unused/key", "invented-unused.invalid"),
        remote_prefix=("python3", "-I", "-S", str(remote / "var/invented-ed-entry.py"), "coordinator"),
        input_contract=wire.ED_INPUTS_CONTRACT, resource_profile=wire.ED_H1_600.id,
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
