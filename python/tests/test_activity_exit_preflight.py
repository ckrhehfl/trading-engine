"""Synthetic logged-runner boundaries; no actual package, database or API reads."""
from copy import deepcopy
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from research import activity_exit_preflight as module
from research import activity_preflight as saved


def fixture(tmp_path, monkeypatch):
    """Isolate logging/IO contracts from the separately tested pure validators."""
    root, private, evidence = tmp_path / "repo", tmp_path / "private", tmp_path / "evidence"
    root.mkdir()
    private.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    parameters = {"lookback": 60, "threshold": 3.0, "holding_sessions": 126,
                  "end": "2026-09-18", "slippage_bps_per_side": 5.0}
    store, declared = {}, {}
    for role in module.PACKAGE_ROLES:
        if role in module.holding.BL_ROLES:
            path = evidence / "bl" / module.holding.BL_ROLES[role]
        elif role in module.BM_ROLES:
            path = evidence / "bm" / module.BM_ROLES[role]
        else:
            path = evidence / (role + ".json")
        store[path] = saved.encoded({"synthetic_role": role})
        declared[role] = {"path": str(path), "sha256": saved.digest(store[path])}
    spec = {"schema": module.SCHEMA, "study_id": module.SCHEMA,
        "mode": "discovery_conditional_exit_scope", "promotion_allowed": False,
        "window": dict(saved.WINDOW), "reference_specification": saved.REFERENCE,
        "parameters": parameters, "inputs": declared}
    reference = {"parameters": deepcopy(parameters)}
    sources = {"files": [{"path": "python/synthetic.py", "sha256": "a" * 64}], "code_version": "a" * 40}

    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_spec
        module.validate_spec(spec, reference)
        return sources, saved.encoded(spec), spec

    prepared = SimpleNamespace(expected_windows=(
        {"code": "000001", "isin": "KR7000001000", "start": "2020-01-02", "end": "2020-01-08"},),
        selections_d1=("synthetic D1",), selections_d2=("synthetic D2",), holding_inputs=object(),
        required_code_dates={"000001": ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"]},
        activity_snapshot_sha256="a" * 64, quote_snapshot_sha256="b" * 64,
        read_scope_sha256=declared["bm_read_scope"]["sha256"],
        input_sha256={role: declared[role]["sha256"] for role in module.PACKAGE_ROLES})

    def prepare(bl, bm, *, parameters, expected_pins):
        assert parameters is spec["parameters"] and expected_pins == prepared.input_sha256
        assert bl["files"] == {name: store[Path(declared[role]["path"])]
                                 for role, name in module.holding.BL_ROLES.items()}
        assert bm["files"] == {name: store[Path(declared[role]["path"])] for role, name in module.BM_ROLES.items()}
        assert bl["verification"] == store[Path(declared["bl_verification"]["path"])]
        assert bl["bj_scope"] == store[Path(declared["bj_scope"]["path"])]
        assert bm["verification"] == store[Path(declared["bm_verification"]["path"])]
        return prepared

    plan = {"schema": "activity-provisional-exit-bounds-v1", "status": "provisional",
        "entries": [{"arm": arm, "candidate_on": "2020-01-06", "state": "provisional_candidate"}
                    for arm in ("D1", "D2")],
        "child_code_dates": {"000001": ["2020-01-02", "2020-01-03", "2020-01-06"]},
        "child_scope_sha256": "c" * 64, "returns_computed": False, "actual_exits_certified": False}

    def planner(*args, **kwargs):
        assert args == (prepared.selections_d1, prepared.selections_d2, prepared.holding_inputs,
                        spec["parameters"], prepared.required_code_dates, {"000001": "KR7000001000"})
        assert kwargs == {"input_sha256": prepared.input_sha256}
        return plan

    monkeypatch.chdir(root)
    monkeypatch.setattr(saved, "freeze_sources", freeze)
    monkeypatch.setattr(module, "prepare_inputs", prepare)
    monkeypatch.setattr(module, "provisional_exit_bounds", planner)
    return root, private / "run", tmp_path / "runs.jsonl", spec, store, sources, prepared, plan


def statuses(path):
    return [json.loads(line)["status"] for line in path.read_text().splitlines()]


def test_registered_source_spec_preserves_exact_reference_parameters():
    root = Path(__file__).resolve().parents[2]
    specification = saved.strict_json((root / "configs/research/discovery/activity-ordinary-exit-preflight-v1.json").read_bytes())
    reference = saved.strict_json((root / saved.REFERENCE).read_bytes())
    module.validate_spec(specification, reference)
    assert saved.encoded(specification["parameters"]) == saved.encoded(reference["parameters"])


def test_durable_start_precedes_all_eighteen_reads_and_preserves_original_scope(tmp_path, monkeypatch):
    root, output, runs, spec, store, _, prepared, plan = fixture(tmp_path, monkeypatch)
    before, reads = deepcopy((store, spec)), []

    def reader(path):
        assert statuses(runs) == ["started"]
        assert (output / "specification.json").exists() and (output / "source-manifest.json").exists()
        assert not (output / "exit-scope.json").exists()
        reads.append(path)
        return store[path]

    record = module.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    result = saved.strict_json((output / "result.json").read_bytes())
    assert statuses(runs) == ["started", "completed"]
    assert reads == [Path(spec["inputs"][role]["path"]) for role in module.PACKAGE_ROLES]
    assert len(reads) == len(set(reads)) == 18 and (store, spec) == before
    assert result["original_read_scope_sha256"] == prepared.read_scope_sha256
    assert result["quote_snapshot_sha256"] == prepared.quote_snapshot_sha256
    assert saved.strict_json((output / "exit-scope.json").read_bytes()) == plan
    assert result["summary"] == {"potential_entries": 2, "entries_by_arm": {"D1": 1, "D2": 1},
        "provisional_candidates": 2, "state_counts": {"provisional_candidate": 2},
        "original_codes": 1, "original_code_dates": 5, "child_codes": 1, "child_code_dates": 3}
    for field, filename in (("exit_scope_sha256", "exit-scope.json"),
                            ("resource_manifest_sha256", "resource-manifest.json")):
        assert result[field] == record["result"][field] == saved.digest((output / filename).read_bytes())
    assert len(result["input_manifest"]) == 18
    assert all(row["read_status"] == "hash_verified" for row in result["input_manifest"])
    assert all(result[name] is False for name in ("returns_computed", "books_computed", "actions_applied",
        "database_access", "api_access", "promotion_allowed", "event_coverage_certified",
        "no_event_coverage_certified", "actual_exits_certified", "source_truth_certified"))
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


@pytest.mark.parametrize("cash", [False, True])
def test_logged_runner_connects_real_package_restoration_and_exit_planner_without_books(tmp_path, monkeypatch, cash):
    from research.activity_exit_bounds import provisional_exit_bounds
    from research.activity_paired_replay import prepare_inputs
    from research import activity_paired_replay
    from test_activity_paired_replay import fixture as paired_fixture
    import sqlite3

    bundles, parameters, pins, _ = paired_fixture(monkeypatch, cash=cash)
    root, output, runs, spec, store, sources, _, _ = fixture(tmp_path, monkeypatch)
    spec["parameters"] = parameters
    payloads = {**{role: bundles[0]["files"][name] for role, name in module.holding.BL_ROLES.items()},
        **{role: bundles[1]["files"][name] for role, name in module.BM_ROLES.items()},
        "bl_verification": bundles[0]["verification"], "bj_scope": bundles[0]["bj_scope"],
        "bm_verification": bundles[1]["verification"]}
    for role in module.PACKAGE_ROLES:
        row = spec["inputs"][role]
        store[Path(row["path"])] = payloads[role]
        row["sha256"] = pins[role]

    def freeze(*args, **kwargs):
        module.validate_spec(spec, {"parameters": parameters})
        return sources, saved.encoded(spec), spec

    monkeypatch.setattr(saved, "freeze_sources", freeze)
    monkeypatch.setattr(module, "prepare_inputs", prepare_inputs)
    monkeypatch.setattr(module, "provisional_exit_bounds", provisional_exit_bounds)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("unexpected database access"))
    monkeypatch.setattr(activity_paired_replay, "replay_synthetic", lambda *a, **kw: pytest.fail("unexpected book execution"))
    before = deepcopy(store)
    module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    result = saved.strict_json((output / "result.json").read_bytes())
    plan = saved.strict_json((output / "exit-scope.json").read_bytes())
    assert store == before and statuses(runs) == ["started", "completed"]
    assert result["child_scope_sha256"] == plan["child_scope_sha256"]
    assert result["input_sha256"] == {role: pins[role] for role in module.PACKAGE_ROLES}
    assert result["original_read_scope_sha256"] == pins["bm_read_scope"]
    assert result["quote_snapshot_sha256"] == plan["quote_snapshot_sha256"]
    assert result["summary"]["potential_entries"] == (0 if cash else 2)
    assert result["summary"]["original_code_dates"] == (0 if cash else 69)
    assert result["summary"]["child_code_dates"] == (0 if cash else 32)
    if cash:
        assert result["quote_snapshot_sha256"] is None and plan["child_windows"] == []


@pytest.mark.parametrize("failure", ["pin", "reader", "prepare", "planner", "source_drift"])
def test_failures_preserve_attempted_manifest_resources_and_private_partial_package(tmp_path, monkeypatch, failure):
    root, output, runs, spec, store, sources, _, _ = fixture(tmp_path, monkeypatch)
    reader = store.__getitem__
    if failure == "pin":
        spec["inputs"]["bm_result"]["sha256"] = "f" * 64
    elif failure == "reader":
        def reader(path):
            raise OSError("synthetic source body must not enter failure receipts")
    elif failure in ("prepare", "planner"):
        def fail(*args, **kwargs):
            raise ValueError("synthetic private values must not enter failure receipts")
        monkeypatch.setattr(module, "prepare_inputs" if failure == "prepare" else "provisional_exit_bounds", fail)
    else:
        frozen = iter([sources, {**sources, "code_version": "b" * 40}])
        monkeypatch.setattr(saved, "freeze_sources", lambda *a, **kw: (next(frozen), saved.encoded(spec), spec))
    with pytest.raises((ValueError, OSError)):
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    assert statuses(runs) == ["started", "failed"] and not (output / "result.json").exists()
    failure_raw = (output / "failure.json").read_bytes()
    failure_value = saved.strict_json(failure_raw)
    assert b"synthetic source body" not in failure_raw and b"synthetic private values" not in failure_raw
    assert failure_value["partial_package_preserved"] is True and failure_value["automatic_retries"] == 0
    assert saved.strict_json((output / "input-manifest.json").read_bytes()) == failure_value["actual_inputs_attempted"]
    assert (output / "resource-manifest.json").exists()
    if failure == "source_drift":
        assert (output / "exit-scope.json").exists()
    if failure == "reader":
        assert failure_value["actual_inputs_attempted"][0]["read_status"] == "started"
    if failure == "pin":
        assert failure_value["actual_inputs_attempted"][-1]["read_status"] == "hash_mismatch"


def test_default_reader_raises_only_population_bound(tmp_path, monkeypatch):
    root, output, runs, spec, store, _, _, _ = fixture(tmp_path, monkeypatch)
    population, ordinary = [], []
    monkeypatch.setattr(module.holding, "_read_population", lambda path: population.append(path) or store[path])
    monkeypatch.setattr(saved, "read_private", lambda path: ordinary.append(path) or store[path])
    module.run_preflight(root, root / "spec.json", output, runs)
    assert population == [Path(spec["inputs"]["bl_population_audit"]["path"])]
    assert len(ordinary) == 17 and not set(ordinary) & set(population)


@pytest.mark.parametrize("corruption", ["parameter", "parameter_type", "mode", "window", "promotion",
    "extra_role", "missing_role", "pin", "relative", "duplicate_path", "package_filename", "package_directory"])
def test_specification_rejects_mutated_reference_and_input_contracts(tmp_path, monkeypatch, corruption):
    _, _, _, spec, _, _, _, _ = fixture(tmp_path, monkeypatch)
    reference = {"parameters": deepcopy(spec["parameters"])}
    module.validate_spec(spec, reference)
    if corruption == "parameter": spec["parameters"]["holding_sessions"] += 1
    elif corruption == "parameter_type": spec["parameters"]["threshold"] = 3
    elif corruption == "mode": spec["mode"] = "return_study"
    elif corruption == "window": spec["window"]["start"] = "2018-01-01"
    elif corruption == "promotion": spec["promotion_allowed"] = True
    elif corruption == "extra_role": spec["inputs"]["scan_database"] = spec["inputs"]["bj_scope"]
    elif corruption == "missing_role": spec["inputs"].pop("bm_verification")
    elif corruption == "pin": spec["inputs"]["bj_scope"]["sha256"] = "missing"
    elif corruption == "relative": spec["inputs"]["bj_scope"]["path"] = "relative.json"
    elif corruption == "duplicate_path": spec["inputs"]["bj_scope"]["path"] = spec["inputs"]["bm_verification"]["path"]
    elif corruption == "package_filename": spec["inputs"]["bm_result"]["path"] += "-wrong"
    else:
        spec["inputs"]["bm_result"]["path"] = str(Path(spec["inputs"]["bm_result"]["path"]).parent / "other" / "result.json")
    with pytest.raises(ValueError):
        module.validate_spec(spec, reference)


def test_existing_output_is_preserved_without_input_reads(tmp_path, monkeypatch):
    root, output, runs, _, _, _, _, _ = fixture(tmp_path, monkeypatch)
    output.mkdir(mode=0o700)
    sentinel = output / "sentinel"
    sentinel.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        module.run_preflight(root, root / "spec.json", output, runs,
                             reader=lambda path: pytest.fail("unexpected input read"))
    assert sentinel.read_bytes() == b"original" and list(output.iterdir()) == [sentinel]
    assert statuses(runs) == ["started", "failed"]


@pytest.mark.parametrize("alias", ["input_log", "source_log", "input_output", "checkout_output", "output_log"])
def test_write_boundaries_reject_aliases_before_start_or_inputs(tmp_path, monkeypatch, alias):
    root, output, runs, spec, _, _, _, _ = fixture(tmp_path, monkeypatch)
    if alias == "input_log": runs = Path(spec["inputs"]["bm_result"]["path"])
    elif alias == "source_log": runs = root / "python/synthetic.py"
    elif alias == "input_output": output = Path(spec["inputs"]["bj_scope"]["path"]).parent / "run"
    elif alias == "checkout_output": output = root / "run"
    else: runs = output / "runs.jsonl"
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs,
                             reader=lambda path: pytest.fail("unexpected input read"))
    assert not output.exists() and not runs.exists()
