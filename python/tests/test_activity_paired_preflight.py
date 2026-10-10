"""Synthetic wrapper verification; no actual saved data, DB, API or GCP."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import stat
import sys

import pytest

from research import activity_preflight as saved
from research import activity_paired_replay as paired
from test_activity_paired_replay import fixture as package_fixture, conditional_case
from test_activity_paired_extension import spin_fixture


from research import activity_paired_preflight as module

MODULE_PATH = Path(module.__file__)


def statuses(path):
    return [json.loads(line)["status"] for line in path.read_text().splitlines()]


def case(tmp_path, monkeypatch, variant="ordinary"):
    if variant == "conditional":
        bundles, parameters, pins, _, child = conditional_case(monkeypatch)
        extras = {paired.CONDITIONAL_ROLE: child}
    elif variant == "extension":
        bundles, parameters, pins, _, extension, _ = spin_fixture(monkeypatch)
        extras = {paired.EXTENSION_ROLE: saved.encoded(extension)}
    else:
        bundles, parameters, pins, _ = package_fixture(monkeypatch, cash=variant == "cash",
            mode=variant if variant in ("deferred_exit", "pending_final_cash") else "ordinary")
        extras = {}
    payloads = {**{role: bundles[0]["files"][name] for role, name in module.holding.BL_ROLES.items()},
        **{role: bundles[1]["files"][name] for role, name in paired.BM_ROLES.items()},
        "bl_verification": bundles[0]["verification"], "bj_scope": bundles[0]["bj_scope"],
        "bm_verification": bundles[1]["verification"],
        "reviewed_action_coverage": bundles[2], "basis_evidence": bundles[3], **extras}
    root, private, evidence = (tmp_path / name for name in ("repo", "private", "evidence"))
    root.mkdir()
    private.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    store, declared = {}, {}
    for role, payload in payloads.items():
        if role in module.holding.BL_ROLES:
            path = evidence / "bl" / module.holding.BL_ROLES[role]
        elif role in paired.BM_ROLES:
            path = evidence / "bm" / paired.BM_ROLES[role]
        else:
            path = evidence / (role + ".json")
        store[path] = payload
        declared[role] = dict(path=str(path), sha256=pins[role])
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode=module.MODE,
        promotion_allowed=False, window=deepcopy(saved.WINDOW), reference_specification=saved.REFERENCE,
        parameters=parameters, inputs=declared)
    sources = dict(files=[dict(path=module.SOURCE_PATH, sha256="a" * 64)], code_version="a" * 40)
    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_spec
        module.validate_spec(spec, {"parameters": parameters})
        return sources, saved.encoded(spec), spec
    monkeypatch.chdir(root)
    monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    monkeypatch.setattr(saved, "freeze_sources", freeze)
    for owner, name in ((sqlite3, "connect"), (socket, "create_connection")):
        monkeypatch.setattr(owner, name, lambda *a, **kw: pytest.fail("unexpected actual IO"))
    return root, private / "run", tmp_path / "runs.jsonl", spec, store, sources


@pytest.mark.parametrize("variant", ["ordinary", "cash", "deferred_exit", "pending_final_cash", "conditional", "extension"])
def test_complete_synthetic_pair_logs_start_before_every_read_and_final_source_check(tmp_path, monkeypatch, variant):
    root, output, runs, spec, store, _ = case(tmp_path, monkeypatch, variant)
    before, reads = deepcopy((spec, store)), []
    freeze = saved.freeze_sources
    def checked_freeze(*args, **kwargs):
        if reads:
            assert statuses(runs) == ["started"] and (output / "report.json").exists()
            assert not (output / "result.json").exists()
        return freeze(*args, **kwargs)
    monkeypatch.setattr(saved, "freeze_sources", checked_freeze)
    def reader(path):
        assert statuses(runs) == ["started"]
        assert (output / "specification.json").exists() and (output / "source-manifest.json").exists()
        reads.append(path)
        return store[path]
    record = module.run_paired(root, root / "spec.json", output, runs, reader=reader)
    result = saved.strict_json((output / "result.json").read_bytes())
    replay = saved.strict_json((output / "paired-replay.json").read_bytes())
    report = saved.strict_json((output / "report.json").read_bytes())
    assert (spec, store) == before and statuses(runs) == ["started", "completed"]
    assert len(reads) == len(set(reads)) == 20 + (variant in ("conditional", "extension"))
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 1
    assert replay["actual_study_completed"] is False and result["logged_paired_execution_completed"] is True
    assert result["independent_output_verification"] == "pending" and result["promotion_allowed"] is False
    assert result["input_sha256"] == {role: row["sha256"] for role, row in spec["inputs"].items()}
    assert set(replay["replays"]) == set(report["arms"]) == {"D1", "D2"} and report == replay["report"]
    for arm in report["arms"].values():
        assert {"total_return", "session_return_sd", "maximum_drawdown", "filled_entries",
            "ordinary_closed_trades", "terminal_open_lots"} <= set(arm)
    assert {"signal_overlap", "target_overlap"} <= set(report)
    for field, filename in (("paired_replay_sha256", "paired-replay.json"), ("report_sha256", "report.json"),
            ("source_manifest_sha256", "source-manifest.json"), ("specification_sha256", "specification.json"),
            ("resource_manifest_sha256", "resource-manifest.json"), ("input_manifest_sha256", "input-manifest.json")):
        assert result[field] == record["result"][field] == saved.digest((output / filename).read_bytes())
    start = saved.strict_json(runs.read_bytes().splitlines()[0])
    assert start["parameters"]["session_lags"] == {"D1": 1, "D2": 2}
    assert start["parameters"]["initial_nav"] == "1" and start["promotion_trial_increment"] == 0
    assert all(row["read_status"] == "hash_verified" for row in result["input_manifest"])
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


@pytest.mark.parametrize("failure", ["read", "action_pin", "child_pin", "extension_pin", "second_arm", "source_drift"])
def test_one_failed_trial_preserves_receipts_without_completion_or_retry(tmp_path, monkeypatch, failure):
    variant = {"child_pin": "conditional", "extension_pin": "extension"}.get(failure, "ordinary")
    root, output, runs, spec, store, sources = case(tmp_path, monkeypatch, variant)
    reader = store.__getitem__
    if failure.endswith("pin"):
        role = {"action_pin": "basis_evidence", "child_pin": paired.CONDITIONAL_ROLE,
                "extension_pin": paired.EXTENSION_ROLE}[failure]
        store[Path(spec["inputs"][role]["path"])] += b" "
    elif failure == "read":
        def reader(path): raise OSError("private source body must stay out of receipts")
    elif failure == "second_arm":
        original, calls = paired.replay_synthetic, []
        def replay(*args, **kwargs):
            calls.append(kwargs["timing_policy"])
            if len(calls) == 2: raise ValueError("private partial book must stay out of receipts")
            return original(*args, **kwargs)
        monkeypatch.setattr(paired, "replay_synthetic", replay)
    else:
        frozen = iter((sources, {**sources, "code_version": "b" * 40}))
        monkeypatch.setattr(saved, "freeze_sources", lambda *a, **kw: (next(frozen), saved.encoded(spec), spec))
    with pytest.raises((ValueError, OSError)):
        module.run_paired(root, root / "spec.json", output, runs, reader=reader)
    assert statuses(runs) == ["started", "failed"] and not (output / "result.json").exists()
    raw = (output / "failure.json").read_bytes()
    failure_result = saved.strict_json(raw)
    assert b"private source body" not in raw and b"private partial book" not in raw
    assert failure_result["D1_completed_studies"] == failure_result["D2_completed_studies"] == 0
    assert failure_result["partial_package_preserved"] and failure_result["paired_execution_may_be_partial"]
    assert failure_result["automatic_retries"] == 0 and not failure_result["logged_paired_execution_completed"]
    assert saved.strict_json((output / "input-manifest.json").read_bytes()) == failure_result["actual_inputs_attempted"]
    assert (output / "resource-manifest.json").exists()
    if failure == "second_arm": assert len(calls) == 2
    if failure == "source_drift": assert (output / "paired-replay.json").exists()


def test_default_reader_routes_only_population_through_existing_larger_bound(tmp_path, monkeypatch):
    root, output, runs, spec, store, _ = case(tmp_path, monkeypatch)
    population, ordinary = [], []
    monkeypatch.setattr(module.holding, "_read_population", lambda path: population.append(path) or store[path])
    monkeypatch.setattr(saved, "read_private", lambda path: ordinary.append(path) or store[path])
    module.run_paired(root, root / "spec.json", output, runs)
    assert population == [Path(spec["inputs"]["bl_population_audit"]["path"])] and len(ordinary) == 19


def test_complete_committed_reference_parameters_remain_exact(tmp_path, monkeypatch):
    reference = saved.strict_json((MODULE_PATH.resolve().parents[2] / saved.REFERENCE).read_bytes())
    _, _, _, spec, _, _ = case(tmp_path, monkeypatch)
    spec["parameters"] = deepcopy(reference["parameters"])
    module.validate_spec(spec, reference)
    assert saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"])
    spec["parameters"]["slippage_bps_per_side"] = 5
    with pytest.raises(ValueError): module.validate_spec(spec, reference)


@pytest.mark.parametrize("mutation", ["parameter", "number_type", "missing", "extra", "duplicate", "relative", "filename", "mode"])
def test_spec_rejects_contract_changes(tmp_path, monkeypatch, mutation):
    *_, spec, store, sources = case(tmp_path, monkeypatch)
    reference = {"parameters": deepcopy(spec["parameters"])}
    if mutation == "parameter": spec["parameters"]["threshold"] += 1
    elif mutation == "number_type": spec["parameters"]["threshold"] = 3
    elif mutation == "missing": spec["inputs"].pop("basis_evidence")
    elif mutation == "extra": spec["inputs"]["scan_database"] = dict(path="/synthetic/db", sha256="f" * 64)
    elif mutation == "duplicate": spec["inputs"]["basis_evidence"] = spec["inputs"]["reviewed_action_coverage"]
    elif mutation == "relative": spec["inputs"]["basis_evidence"]["path"] = "relative.json"
    elif mutation == "filename": spec["inputs"]["bm_result"]["path"] += ".changed"
    else: spec["mode"] = "candidate_evaluation"
    with pytest.raises(ValueError): module.validate_spec(spec, reference)


def test_wrong_source_location_cannot_read_inputs_or_start_a_run(tmp_path, monkeypatch):
    root, output, runs, _, _, _ = case(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "__file__", str(MODULE_PATH))
    monkeypatch.setattr(saved, "freeze_sources", lambda *a, **kw: pytest.fail("wrong location reached source freeze"))
    with pytest.raises(ValueError, match="committed source location"):
        module.run_paired(root, root / "spec.json", output, runs, reader=lambda p: pytest.fail("actual input read"))
    assert not output.exists() and not runs.exists()


def test_existing_output_and_immutable_evidence_aliases_fail_without_reads(tmp_path, monkeypatch):
    root, output, runs, spec, _, _ = case(tmp_path, monkeypatch)
    output.mkdir(mode=0o700)
    sentinel = output / "sentinel"
    sentinel.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        module.run_paired(root, root / "spec.json", output, runs, reader=lambda p: pytest.fail("unexpected read"))
    assert sentinel.read_bytes() == b"original" and statuses(runs) == ["started", "failed"]
    alias = Path(spec["inputs"]["bm_result"]["path"])
    with pytest.raises(ValueError, match="immutable input"):
        module.run_paired(root, root / "spec.json", output, alias, reader=lambda p: pytest.fail("unexpected read"))
    assert not alias.exists()
