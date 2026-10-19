"""Blind pilot IO tests use private temporary metadata and fabricated SQLite."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
import math
from pathlib import Path
from types import SimpleNamespace
import socket
import sqlite3
import stat

import pytest

from research import activity_preflight as saved
from research import experiment_log
from research import price_trend_pilot as module
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from test_activity_cohort_return_policy import synthetic_policy_v3
import test_price_trend_comparison as fixture


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network is forbidden"))
    monkeypatch.setattr(experiment_log, "_git_head_sha", lambda: "a" * 40)


def production_spec():
    return saved.strict_json((ROOT / "configs/research/discovery/price-trend-pilot-v1.json").read_bytes())


def database(path, universe):
    """A new temporary spent-shaped database containing only fabricated prices."""
    coordinates = universe.required_price_coordinates
    with sqlite3.connect(path) as scan:
        scan.execute("CREATE TABLE scan_panel (id INTEGER,start TEXT,end TEXT)")
        scan.execute("INSERT INTO scan_panel VALUES (1,'20190102','20260918')")
        scan.execute("CREATE TABLE scan_progress (code TEXT,first_date TEXT,last_date TEXT,bars INTEGER,frozen INTEGER,status TEXT,fetched_at TEXT)")
        scan.execute("CREATE TABLE scan_bars (code TEXT,bsop_date TEXT,open TEXT,high TEXT,low TEXT,close TEXT,volume TEXT,turnover TEXT)")
        for code in sorted({code for code, _ in coordinates}):
            days = tuple(day for item, day in coordinates if item == code)
            scan.execute("INSERT INTO scan_progress VALUES (?,?,?,?,?,?,?)", (code, min(days).strftime("%Y%m%d"),
                max(days).strftime("%Y%m%d"), len(days), 0, "done", "2026-10-19T00:00:00+00:00"))
        scan.executemany("INSERT INTO scan_bars VALUES (?,?,?,?,?,?,?,?)", [
            (code, day.strftime("%Y%m%d"), "100", "101", "99", "100", "100", "10000")
            for code, day in coordinates])
        scan.execute("INSERT INTO scan_bars VALUES ('000001','20260918','999','999','999','999','1','999')")


def harness(tmp_path, monkeypatch, *, fail_second=False, changed_after=False):
    monkeypatch.chdir(ROOT)
    real_resources = module._resources
    monkeypatch.setattr(module, "_self_oom_score_adj", lambda: 1000)
    # This is an isolated invocation fixture, independent of CPU already spent
    # by unrelated tests in the same full-suite Python process.
    monkeypatch.setattr(module, "_resources", lambda started: {**real_resources(started),
        "process_user_cpu_seconds": 0.0, "process_system_cpu_seconds": 0.0})
    frontier, universe, _, holding = fixture.inputs()
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    input_root = parent / "inputs"
    input_root.mkdir(mode=0o700)
    output, runs = parent / "output", parent / "trials.jsonl"
    spec = production_spec()
    spec.update(expected_candidate_windows=universe.candidate_window_count, expected_formations=2, expected_active_formations=1)
    bodies = {role: saved.encoded({"synthetic": role}) for role in module.OLD_ROLES}
    declarations = universe.original_declarations
    bodies["cohort_declarations"] = saved.encoded([_json_value(asdict(row)) for row in declarations])
    bodies["holding_read_scope"] = saved.encoded({"producer_schema": module.base.holding.SCHEMA,
        "scope_kind": "complete_BS_family_proof_only_original_issue_union_not_successor_closure",
        "returns_computed": False, "cohort_declarations_sha256": saved.digest(bodies["cohort_declarations"]),
        "activity_snapshot_sha256": holding.activity_snapshot_sha256,
        "calendar_dates": [day.strftime("%Y%m%d") for day in holding.calendar]})
    bodies["frontier"] = saved.encoded(frontier)
    for role, body in bodies.items():
        spec["inputs"][role] = {"path": str(input_root / (role + ".json")), "sha256": saved.digest(body)}
    db = input_root / "synthetic.sqlite3"
    database(db, universe)
    spec["inputs"]["scan_database"]["path"] = str(db)
    raw, sources = saved.encoded(spec), {"files": [], "code_version": "a" * 40}
    reads, stages, freezes = [], [], []
    by_path = {spec["inputs"][role]["path"]: (role, body) for role, body in bodies.items()}
    def reader(path):
        assert list(experiment_log.read_records(runs))[0]["status"] == "started"
        role, body = by_path[str(path)]
        reads.append(role)
        if role not in ("frontier", *module.CACHED_ROLES):
            assert (output / "fixed-trend-declarations.json").exists()
        return body
    def freeze(*args, **kwargs):
        freezes.append(True)
        return ({**sources, "changed": True} if changed_after and len(freezes) > 1 else sources), raw, spec
    def prepare(original, inputs):
        assert (output / "fixed-trend-declarations.json").exists()
        assert set(original["inputs"]) == set(module.OLD_ROLES)
        for role in module.OLD_ROLES:
            row = original["inputs"][role]
            inputs.read(Path(row["path"]), row["sha256"], role)
        stages.append("restored_original_quotes")
        binding = SimpleNamespace(events=(), return_policy_json=saved.encoded(synthetic_policy_v3()), binding_sha256="d" * 64)
        return module.base.PreparedCohorts(declarations, holding, holding, binding)
    real_scan = module.prices.load_scan
    def load_scan(path, plan, inputs, pin):
        assert reads == ["frontier", "cohort_declarations", "holding_read_scope"]
        scope = saved.read_private(output / "signal-read-scope.json")
        assert saved.digest(scope) == pin
        assert saved.strict_json(scope)["intersection_code_dates"] == plan["intersection_code_dates"]
        stages.append("signal_scan")
        return real_scan(path, plan, inputs, pin)
    real_replay = module.trend.replay_price_trend_cohorts
    def replay(*args, **kwargs):
        assert stages == ["signal_scan", "restored_original_quotes"]
        assert (output / "fixed-trend-declarations.json").exists()
        kwargs["scope_binding"] = None  # This IO fixture delegates real binding tests to the pure adapter suite.
        before, attempted = kwargs["before_cohort"], []
        def start(row):
            before(row)
            attempted.append(row)
            if fail_second and len(attempted) == 2:
                raise RuntimeError("synthetic second-book failure")
        kwargs["before_cohort"] = start
        return real_replay(*args, **kwargs)
    monkeypatch.setattr(module.base, "prepare_inputs", prepare)
    monkeypatch.setattr(module.prices, "load_scan", load_scan)
    monkeypatch.setattr(module.trend, "replay_price_trend_cohorts", replay)
    monkeypatch.setattr(module, "blind_calibration", lambda replay, parameters: {arm: {
        "detectable_daily_bps": 1.23, "cost_floor_daily_bps": 0.34, "cost_floor_resolvable": False,
        "sessions": len(replay["aggregates"][arm]["TREND"]["metrics"]["path"])} for arm in ("D1", "D2")})
    return SimpleNamespace(output=output, runs=runs, spec=spec, raw=raw, reader=reader, freeze=freeze,
        reads=reads, stages=stages, db=db, by_path=by_path)


def invoke(h):
    return module.run_preflight(ROOT, ROOT / "configs/research/discovery/price-trend-pilot-v1.json",
        h.output, h.runs, reader=h.reader, freeze=h.freeze)


def test_committed_registration_reuses_strict_original_contract():
    spec = production_spec()
    module.validate_spec(spec, {"parameters": PARAMETERS})
    original = module.source_contract(spec)
    module.base.validate_contract(original, {"parameters": PARAMETERS})
    assert set(original["inputs"]) == set(module.OLD_ROLES)
    assert original["return_policy_schema"] == module.base.RETURN_POLICY_SCHEMA_V3
    assert (module.ADDRESS_SPACE_BYTES, module.CPU_LIMIT_SECONDS, module.WALL_LIMIT_SECONDS) == (1536 * 1024**2, 2400, 3600)
    assert module.base.CPU_LIMIT_SECONDS == 7200 and module.base.WALL_LIMIT_SECONDS == 10800


@pytest.mark.parametrize("change", ["candidate_count", "formation_count", "active_count", "parameters", "reference",
    "scan_mode", "scan_columns", "frontier_pin", "alias", "policy", "extra"])
def test_registration_changes_fail_before_actual_inputs(change):
    spec = production_spec()
    if change == "candidate_count": spec["expected_candidate_windows"] = 180
    elif change == "formation_count": spec["expected_formations"] = True
    elif change == "active_count": spec["expected_active_formations"] = 14
    elif change == "parameters": spec["parameters"]["threshold"] = 3
    elif change == "reference": spec["reference_specification"] = "other.json"
    elif change == "scan_mode": spec["inputs"]["scan_database"]["mode"] = "rw"
    elif change == "scan_columns": spec["inputs"]["scan_database"]["columns"].append("secret")
    elif change == "frontier_pin": spec["inputs"]["frontier"]["sha256"] = "0" * 64
    elif change == "alias": spec["inputs"]["frontier"]["path"] = spec["inputs"]["cohort_declarations"]["path"]
    elif change == "policy": spec["source_cohort_contract"]["return_policy_schema"] = module.base.RETURN_POLICY_SCHEMA_V2
    else: spec["extra"] = True
    with pytest.raises(ValueError):
        module.validate_spec(spec, {"parameters": PARAMETERS})


def test_metadata_cache_preserves_one_actual_read_and_refuses_repinned_reuse():
    calls, body = [], b'{"synthetic": true}'
    inputs = module.PilotInputs(lambda path: calls.append(path) or body)
    pin, path = saved.digest(body), Path("/synthetic/cohort.json")
    assert inputs.read(path, pin, "cohort_declarations") == body
    assert inputs.read(path, pin, "cohort_declarations") == body
    assert len(calls) == len(inputs.manifest) == len(inputs.reused_roles) == 1
    assert inputs.reused_roles[0]["read_status"] == "verified_memory_reuse_no_filesystem_read"
    with pytest.raises(ValueError, match="declaration changed"):
        inputs.read(path, "0" * 64, "cohort_declarations")
    inputs.read(path, pin, "holding_input_audit")
    with pytest.raises(ValueError, match="repeated input"):
        inputs.read(path, pin, "holding_input_audit")
    assert set(inputs._metadata) == {"cohort_declarations"}


def test_full_order_exact_scan_private_books_and_blind_public_output(tmp_path, monkeypatch):
    h = harness(tmp_path, monkeypatch)
    record = invoke(h)
    result = record["result"]
    assert h.reads[:3] == ["frontier", "cohort_declarations", "holding_read_scope"]
    assert h.reads.count("cohort_declarations") == h.reads.count("holding_read_scope") == 1
    assert set(h.reads) == {*module.OLD_ROLES, "frontier"}
    manifest = saved.strict_json(saved.read_private(h.output / "input-manifest.json"))
    assert len(manifest) == len(module.OLD_ROLES) + 2
    assert len({row["role"] for row in manifest}) == len(manifest)
    reused = saved.strict_json(saved.read_private(h.output / "reused-roles.json"))
    assert {row["role"] for row in reused} == set(module.CACHED_ROLES)
    assert result["pilot_cohorts_by_arm"] == {"D1": 2, "D2": 2}
    assert result["pilot_cohort_looks_completed"] == 4
    assert result["D1_completed_comparison_studies"] == result["D2_completed_comparison_studies"] == 0
    assert not result["own_dispersion_cost_floor_resolvable"] and not result["comparison_family_registered"]
    assert result["old_control_books_replayed"] == 0
    inventory = saved.strict_json(saved.read_private(h.output / "cohort-inventory.json"))
    assert len(inventory) == 4
    for row in inventory:
        body = saved.read_private(h.output / "cohorts" / row["file"])
        assert saved.digest(body) == row["sha256"] and len(body) == row["bytes"]
        book = saved.strict_json(body)
        assert book["group_id"] == "TREND" and "books" in book["replay"]
    audit = saved.strict_json(saved.read_private(h.output / "signal-input-audit.json"))
    for row in audit["observations"]:
        assert row["provenance"]["retrieved_at"] == "unknown"
        assert row["typed_values"] and not row["issues"]
    declared = saved.strict_json(saved.read_private(h.output / "fixed-trend-declarations.json"))
    for row in declared:
        for quote in row["observations"]:
            assert quote["metadata"]["retrieved_at"] == "unknown"
            assert quote["metadata"]["is_final"] is quote["metadata"]["data_vintage"] is None
            assert quote["metadata"]["evidence_level"] == "assumed"
    public = saved.encoded(result).decode()
    assert not any(name in public for name in ("chosen_codes", "ranked_codes", "total_return", "Sharpe", "maximum_drawdown"))
    assert saved.read_private(h.output / "specification.json") == h.raw
    assert list(experiment_log.read_records(h.runs))[-1]["status"] == "completed"
    for path in h.output.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    with pytest.raises(ValueError, match="already exists"):
        invoke(h)


def test_second_attempt_failure_preserves_first_full_book_and_both_durable_starts(tmp_path, monkeypatch):
    h = harness(tmp_path, monkeypatch, fail_second=True)
    with pytest.raises(RuntimeError, match="second-book"):
        invoke(h)
    assert len(list((h.output / "cohorts").glob("*.started.json"))) == 2
    assert len(list((h.output / "cohorts").glob("*.book.json"))) == 1
    failure = saved.strict_json(saved.read_private(h.output / "failure.json"))
    assert failure["pilot_cohorts_started_by_arm"] == {"D1": 2, "D2": 0}
    assert failure["pilot_cohorts_completed_by_arm"] == {"D1": 1, "D2": 0}
    assert failure["partial_package_preserved"] and failure["automatic_retries"] == 0
    assert not (h.output / "result.json").exists()
    assert list(experiment_log.read_records(h.runs))[-1]["status"] == "failed"


@pytest.mark.parametrize("change", ["missing", "null", "float", "negative"])
def test_bad_signal_quote_fails_before_any_old_holding_body(tmp_path, monkeypatch, change):
    h = harness(tmp_path, monkeypatch)
    with sqlite3.connect(h.db) as scan:
        if change == "missing": scan.execute("DELETE FROM scan_bars WHERE rowid=1")
        elif change == "null": scan.execute("UPDATE scan_bars SET close=NULL WHERE rowid=1")
        elif change == "float": scan.execute("UPDATE scan_bars SET close=? WHERE rowid=1", (float("inf"),))
        else: scan.execute("UPDATE scan_bars SET close='-1' WHERE rowid=1")
    with pytest.raises(ValueError):
        invoke(h)
    assert h.reads == ["frontier", "cohort_declarations", "holding_read_scope"]
    assert not (h.output / "fixed-trend-declarations.json").exists()
    assert (h.output / "signal-read-scope.json").exists()
    assert (h.output / "failure.json").exists()


def test_source_change_after_books_preserves_private_outputs_and_refuses_completion(tmp_path, monkeypatch):
    h = harness(tmp_path, monkeypatch, changed_after=True)
    with pytest.raises(ValueError, match="source/runtime changed"):
        invoke(h)
    assert len(list((h.output / "cohorts").glob("*.book.json"))) == 4
    assert (h.output / "private-trend-aggregates.json").exists()
    assert not (h.output / "result.json").exists()


def test_only_registered_large_metadata_and_holding_audit_use_large_reader(tmp_path, monkeypatch):
    h = harness(tmp_path, monkeypatch)
    large_reads, ordinary_reads = [], []
    real_scope_reader = saved.read_private
    def large(path):
        large_reads.append(path)
        return h.reader(path)
    def ordinary(path):
        ordinary_reads.append(path)
        return h.reader(path)
    monkeypatch.setattr(module.base, "_read_large", large)
    monkeypatch.setattr(module.saved, "read_private", ordinary)
    # The synthetic scan wrapper inspects only its newly owned scope artifact;
    # that inspection is not an upstream input read through bounded_reader.
    original_scan = module.prices.load_scan
    def scan(path, plan, inputs, pin):
        monkeypatch.setattr(module.saved, "read_private", real_scope_reader)
        try:
            return original_scan(path, plan, inputs, pin)
        finally:
            monkeypatch.setattr(module.saved, "read_private", ordinary)
    monkeypatch.setattr(module.prices, "load_scan", scan)
    module.run_preflight(ROOT, ROOT / "configs/research/discovery/price-trend-pilot-v1.json",
        h.output, h.runs, freeze=h.freeze)
    assert set(large_reads) == {Path(h.spec["inputs"][role]["path"])
        for role in ("cohort_declarations", "holding_input_audit")}
    assert len(large_reads) == 2
    assert not set(large_reads) & set(ordinary_reads)


def test_observed_resource_overrun_is_not_accepted_as_success(tmp_path, monkeypatch):
    h = harness(tmp_path, monkeypatch)
    real = module._resources
    monkeypatch.setattr(module, "_resources", lambda started: {
        **real(started), "process_user_cpu_seconds": 2401.0, "process_system_cpu_seconds": 0.0})
    with pytest.raises(ValueError, match="observed pilot resources"):
        invoke(h)
    assert (h.output / "failure.json").exists() and not (h.output / "result.json").exists()


def test_own_dispersion_uses_existing_fixed_bootstrap_and_no_mean_or_comparison():
    parameters = deepcopy(PARAMETERS)
    values = tuple(Decimal("0.001") if index % 3 == 0 else Decimal("-0.0002") for index in range(600))
    summary = {"metrics": {"path": tuple({"session_return": value} for value in values)},
        "filled_entries": 7, "ordinary_closed_trades": 6, "terminal_open_lots": 1}
    replay = {"aggregates": {arm: {"TREND": summary} for arm in ("D1", "D2")}}
    result = module.blind_calibration(replay, parameters)
    expected = module.calibration({"returns": [float(value) for value in values],
        "diagnostics": {"filled_entries": 7}, "closed_trades": 6, "unresolved_positions": 1}, parameters)
    assert result["D1"]["block_se"] == expected["block_se"]
    assert set(result["D1"]["block_se"]) == {"63", "126", "252"}
    assert result["D1"]["corrected_to_naive_se"] >= 1
    assert result["D1"]["detectable_daily_bps"] >= expected["detectable_daily_bps"]
    assert math.isfinite(result["D1"]["detectable_daily_bps"])
    assert "mean" not in result["D1"] and "total_return" not in result["D1"]
    assert result["D1"] == result["D2"]


def test_cli_sets_only_new_pilot_limits_and_prints_blind_result(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(module.resource, "setrlimit", lambda kind, values: calls.append((kind, values)))
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    monkeypatch.setattr(module.signal, "alarm", lambda value: calls.append(("alarm", value)))
    monkeypatch.setattr(module.os, "getpriority", lambda *args: 0)
    monkeypatch.setattr(module.os, "nice", lambda delta: 10 if delta == 10 else pytest.fail("wrong nice"))
    monkeypatch.setattr(module, "_protect_self_from_oom", lambda: calls.append(("self_oom_score_adj", 1000)))
    monkeypatch.setattr(module, "run_preflight", lambda *args: {"result": {
        "status": "blind_trend_dispersion_calibrated", "arms": {"D1": {"detectable_daily_bps": 1.23}},
        "pilot_cohorts_by_arm": {"D1": 14, "D2": 14}, "promotion_allowed": False}})
    assert module.main(["--spec", "/synthetic/spec.json", "--output-dir", "/synthetic/output",
        "--runs-path", "/synthetic/trials.jsonl"]) == 0
    assert (module.resource.RLIMIT_AS, (1536 * 1024**2,) * 2) in calls
    assert (module.resource.RLIMIT_CPU, (2400, 2400)) in calls
    assert ("alarm", 3600) in calls and calls[-1] == ("alarm", 0)
    assert ("self_oom_score_adj", 1000) in calls
    public = capsys.readouterr().out
    assert "detectable_daily_bps" in public and "chosen_codes" not in public and "total_return" not in public


def test_self_oom_protection_changes_only_the_literal_self_file_and_verifies_it(monkeypatch):
    calls = []
    monkeypatch.setattr(Path, "write_text", lambda path, text, **kwargs: calls.append((str(path), text)) or len(text))
    monkeypatch.setattr(module, "_self_oom_score_adj", lambda: 1000)
    module._protect_self_from_oom()
    assert calls == [("/proc/self/oom_score_adj", "1000\n")]
    monkeypatch.setattr(module, "_self_oom_score_adj", lambda: 0)
    with pytest.raises(ValueError, match="OOM score adjustment"):
        module._protect_self_from_oom()


def test_self_oom_failure_exits_before_the_pilot_can_read_any_input(monkeypatch, capsys):
    monkeypatch.setattr(module.resource, "setrlimit", lambda *args: None)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    monkeypatch.setattr(module.signal, "alarm", lambda *args: None)
    monkeypatch.setattr(module.os, "getpriority", lambda *args: 0)
    monkeypatch.setattr(module.os, "nice", lambda delta: 10)
    def refused():
        raise PermissionError("synthetic OOM adjustment refusal")
    monkeypatch.setattr(module, "_protect_self_from_oom", refused)
    monkeypatch.setattr(module, "run_preflight", lambda *args: pytest.fail("must not read pilot inputs"))
    assert module.main(["--spec", "/synthetic/spec.json", "--output-dir", "/synthetic/output",
        "--runs-path", "/synthetic/trials.jsonl"]) == 1
    assert '"error_type": "PermissionError"' in capsys.readouterr().out


def test_resource_manifest_records_the_self_oom_adjustment(monkeypatch):
    monkeypatch.setattr(module, "_self_oom_score_adj", lambda: 1000)
    assert module._resources(module.time.monotonic())["oom_score_adj"] == 1000
