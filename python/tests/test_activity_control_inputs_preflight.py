"""BR integrated diagnostics use fabricated receipts and temporary SQLite only."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import weakref

import pytest

from research import activity_control_inputs_preflight as module
from research import activity_preflight as saved
from research.activity_failure import activity_failure
from research.activity_failure_inputs import load_activity_failure_inputs
from research.activity_holding_restore import restore_holding_inputs
from research.activity_pair_calibration import PARAMETERS
from test_activity_control_normal_inputs import fixture as normal_fixture
from test_activity_control_scope_preflight import fixture as scope_fixture, plan as scope_plan


def specification():
    declared = deepcopy(module.FIXED_INPUTS)
    for role in module.NEW_ROLES:
        declared[role] = {"path": f"/synthetic/{role}.json", "sha256": "a" * 64}
    declared["scan_database"] = {"path": "/synthetic/scan.sqlite3", "mode": "ro", "columns": list(module.prices.READ_COLUMNS)}
    return {"schema": module.SCHEMA, "study_id": module.SCHEMA, "mode": module.MODE,
        "promotion_allowed": False, "window": deepcopy(saved.WINDOW), "reference_specification": saved.REFERENCE,
        "parameters": deepcopy(PARAMETERS), "inputs": declared}


@pytest.mark.parametrize("change", [None, "threshold_scale", "slippage_scale", "old_pin", "new_pin", "mode",
    "promotion", "database_mode", "database_columns", "input_role", "alias"])
def test_fixed_spec_retains_canonical_float_parameters_and_exact_input_roles(change):
    spec = specification()
    if change == "threshold_scale": spec["parameters"]["threshold"] = 3
    elif change == "slippage_scale": spec["parameters"]["slippage_bps_per_side"] = 5
    elif change == "old_pin": spec["inputs"]["bi_result"]["sha256"] = "f" * 64
    elif change == "new_pin": spec["inputs"]["br_adjudication"]["sha256"] = "unknown"
    elif change == "mode": spec["mode"] = "performance"
    elif change == "promotion": spec["promotion_allowed"] = True
    elif change == "database_mode": spec["inputs"]["scan_database"]["mode"] = "rw"
    elif change == "database_columns": spec["inputs"]["scan_database"]["columns"] = ["close"]
    elif change == "input_role": spec["inputs"]["bk_adjudication"] = deepcopy(spec["inputs"]["br_adjudication"])
    elif change == "alias": spec["inputs"]["br_adjudication"]["path"] = spec["inputs"]["br_scope_result"]["path"]
    if change is None:
        module.validate_spec(spec, {"parameters": PARAMETERS})
        assert type(spec["parameters"]["threshold"]) is type(spec["parameters"]["slippage_bps_per_side"]) is float
    else:
        with pytest.raises(ValueError): module.validate_spec(spec, {"parameters": PARAMETERS})


def bind_scope(scope, activity_sha):
    pins = {role: row["sha256"] for role, row in module.scope_source.INPUTS.items()}
    scope.update(activity_snapshot_sha256=activity_sha, frontier_complete=False,
        compulsory_successor_closure_complete=False, control_targets_frozen=False,
        potential_holding_origins_bound_complete=False, holding_selection_executed=False, returns_computed=False)
    outputs = {name: str(index + 1) * 64 for index, name in enumerate(module.SCOPE_FILES)}
    for field, filename in (("specification_sha256", "specification.json"), ("source_manifest_sha256", "source-manifest.json"),
            ("input_manifest_sha256", "input-manifest.json"), ("resource_manifest_sha256", "resource-manifest.json")):
        scope[field] = outputs[filename]
    outputs["result.json"] = saved.digest(saved.encoded(scope))
    receipt = {"schema": "br-control-scope-independent-verification-v1", "verified": True,
        "scope": "new_scope_metadata_partitions_dates_ranks_targets_and_hash_links_only", "output_sha256": outputs,
        "input_sha256": pins, "summary": deepcopy(scope["summary"]), "activity_preopening_k": {
            row["formation"]: deepcopy(row["activity_preopening_k"]) for row in scope["formations"]},
        **dict.fromkeys(("eligible_control_certified", "compulsory_successor_closure_complete", "event_coverage_certified",
            "price_basis_certified", "source_truth_certified", "books_replayed", "returns_computed", "promotion_allowed"), False),
        "automatic_retries": 0, "D1_completed_studies": 0, "D2_completed_studies": 0}
    return receipt


def fixture(tmp_path, monkeypatch, *, noneligible=False):
    data = normal_fixture()
    calendar, formation, code = data["calendar"], data["calendar"][60], "000001"
    compact, dates = formation.strftime("%Y%m%d"), calendar[:61]
    database_dir, evidence = tmp_path / "database", tmp_path / "evidence"
    database_dir.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    database = database_dir / "synthetic.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("CREATE TABLE scan_panel(id,start,end);"
            "CREATE TABLE scan_progress(code,first_date,last_date,bars,frozen,status,fetched_at);"
            "CREATE TABLE scan_bars(code,bsop_date,open,high,low,close,volume,turnover);")
        connection.execute("INSERT INTO scan_panel VALUES(1,'20190102','20260918')")
        connection.execute("INSERT INTO scan_progress VALUES(?,?,?,?,?,?,?)", (code, calendar[0].strftime("%Y%m%d"),
            calendar[188].strftime("%Y%m%d"), 189, 0, "done", "2026-10-15T00:00:00+00:00"))
        connection.executemany("INSERT INTO scan_bars VALUES(?,?,?,?,?,?,?,?)", [(code, day.strftime("%Y%m%d"),
            "100", "100" if index <= 60 else "101", "100" if index <= 60 else "99", "100", "100",
            "1" if day == formation else "10") for index, day in enumerate(calendar[:189])])
    loaded = load_activity_failure_inputs(database, calendar, {code: dates})
    numeric_result = activity_failure(calendar, formation, loaded.observations[code],
        bb_required_observed_dates=dates, bb_frozen_dates=(), threshold=Decimal("3.0")).to_dict()
    bh_row = {"formation": compact, "code": code, "required_dates": 61,
              "proven_numeric_failures": [], "potentially_influential_unresolved": True}
    bb_row = {"formation": compact, "code": code, "inventory_start": dates[0].strftime("%Y%m%d"),
        "required_observed_dates": [day.strftime("%Y%m%d") for day in dates], "prior_count": 60, "formation_state": "NONFROZEN"}
    scope = data["scope"]
    scope["summary"]["prior_disposition_counts"] = {"synthetic": 6}
    scope["formations"][0].update(activity_preopening_k={"D1": 1, "D2": 0},
                                  activity_preopening_targets={"D1": [code], "D2": []})
    receipt = bind_scope(scope, loaded.snapshot.dataset_sha256)
    bodies = {"calendar_manifest": {}, "bb_result": {}, "bh_result": {"coverage": {"windows": [bh_row]}},
        "au_result": {"rows": [{"formation": compact, "code": code, "formation_state": {"state": "NONFROZEN"},
            "prior_nonfrozen_candidates": {"count": 60, "dates": [day.strftime("%Y%m%d") for day in dates[:-1]],
                "skipped_frozen_dates": [], "missing_dates": [], "invalid_dates": []}}]},
        "bi_result": {"windows": [{"formation": compact, "code": code, **numeric_result}], "scan_snapshot": loaded.snapshot.to_dict()},
        "br_scope_result": scope, "br_scope_verification": receipt, "bl_population_audit": data["population_audit"],
        "br_adjudication": {"records": data["accepted_records"]}}
    store, declared = {}, {}
    for role, value in bodies.items():
        path = evidence / (role + ".json")
        store[path] = saved.encoded(value)
        declared[role] = {"path": str(path), "sha256": saved.digest(store[path])}
    declared["scan_database"] = {"path": str(database), "mode": "ro", "columns": list(module.prices.READ_COLUMNS)}
    for role in ("aq", "ar_original", "ar_recovery"):
        declared[role] = deepcopy(module.FIXED_INPUTS[role])
    spec = {**specification(), "inputs": declared,
        "parameters": {**deepcopy(PARAMETERS), "end": data["params"]["end"]}}
    root, private = tmp_path / "repo", tmp_path / "private"
    root.mkdir()
    private.mkdir(mode=0o700)
    source = {"files": [{"path": "python/synthetic.py", "sha256": "a" * 64}], "code_version": "a" * 40}
    monkeypatch.chdir(root)
    monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    monkeypatch.setattr(saved, "freeze_sources", lambda *a, **kw: (source, saved.encoded(spec), spec))
    monkeypatch.setattr(saved, "calendar_schedule", lambda *a: ([day.strftime("%Y%m%d") for day in calendar], {}, scope["paired_schedule"]))
    monkeypatch.setattr(saved, "_bb_windows", lambda *a: ([bb_row], set()))
    monkeypatch.setattr(module.normal.prior, "population_snapshot", lambda *a: {"prior_dispositions": [bh_row]})
    monkeypatch.setattr(module.scope_source, "bi_metadata", lambda *a: {"snapshot_sha256": loaded.snapshot.dataset_sha256})
    recomputed = {key: deepcopy(value) for key, value in scope.items() if key not in {
        "specification_sha256", "source_manifest_sha256", "input_manifest_sha256", "resource_manifest_sha256"}}
    monkeypatch.setattr(module.scope_source, "plan_control", lambda *a: recomputed)
    fresh = deepcopy(data["population_audit"]["rows"])
    for row in fresh:
        for field in ("capitalization_availability", "classification_availability", "trade_section_availability"):
            row[field] = data["population_audit"]["availability_metadata"][row[field]]
    def load_sources(*args, expected_window_count):
        assert expected_window_count == 1 and args[4] == {(compact, code)}
        assert (args[5] / "normal-read-scope.json").exists()
        saved.write_exclusive(args[5] / "raw-source-read-scope.json", b"{}")
        potential = deepcopy(data["potential_sources"])
        if noneligible:
            target = potential[compact, code]
            target["liquidity"] = {day: replace(row, turnover=Decimal(1)) for day, row in target["liquidity"].items()}
        return {"population": fresh, "potential_sources": potential}
    monkeypatch.setattr(module, "load_sources", load_sources)
    return root, private / "run", tmp_path / "runs.jsonl", spec, store, source, calendar


def statuses(path):
    return [json.loads(line)["status"] for line in path.read_text().splitlines()]


def test_integrated_runner_logs_first_freezes_db_scope_and_reuses_real_restore_screen_scan_exit(tmp_path, monkeypatch):
    root, output, runs, spec, store, _, calendar = fixture(tmp_path, monkeypatch)
    real_scan, scans = module.prices.load_scan, []
    def scan(path, plan, inputs, read_sha):
        assert statuses(runs) == ["started"]
        scope = saved.strict_json((output / "read-scope.json").read_bytes())
        assert saved.digest(saved.encoded(scope)) == read_sha
        assert scope["requested_code_dates"] == plan["intersection_code_dates"]
        assert (output / "normal-input-audit.json").exists() and not (output / "result.json").exists()
        scans.append(plan)
        return real_scan(path, plan, inputs, read_sha)
    monkeypatch.setattr(module.prices, "load_scan", scan)
    def reader(path):
        assert statuses(runs) == ["started"]
        assert (output / "source-manifest.json").exists()
        return store[path]
    module.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    result = saved.strict_json((output / "result.json").read_bytes())
    assert statuses(runs) == ["started", "completed"] and len(scans) == 1
    assert result["database_access"] is True and result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert all(result[key] is False for key in module.FALSE_FLAGS)
    assert result["normal_summary"]["reviewed_normal_windows"] == 1
    scope = saved.strict_json((output / "read-scope.json").read_bytes())
    assert scope["requested_code_dates"] == {"000001": [day.isoformat() for day in calendar[61:189]]}
    assert "BL_file_sha256" not in scope and "BJ_scope_sha256" not in scope
    audit = saved.strict_json((output / "holding-input-audit.json").read_bytes())
    restored = restore_holding_inputs(audit, scope, quote_snapshot_sha256=result["quote_snapshot_sha256"],
        activity_snapshot_sha256=result["activity_snapshot_sha256"], read_scope_sha256=result["read_scope_sha256"])
    assert len(restored.requested_coordinates) == 128
    exits = saved.strict_json((output / "provisional-exit-scope.json").read_bytes())
    assert [row["candidate_on"] for row in exits["entries"]] == [calendar[187].isoformat(), calendar[188].isoformat()]
    assert all(row["state"] == "provisional_candidate" for row in exits["entries"])


def test_raw_input_roots_are_released_before_bi_recompute_and_restore_with_the_same_nine_reads(tmp_path, monkeypatch):
    root, output, runs, _, store, _, _ = fixture(tmp_path, monkeypatch)
    expected = ["calendar_manifest", "bb_result", "bh_result", "au_result", "bi_result",
        "br_scope_result", "br_scope_verification", "bl_population_audit", "br_adjudication"]
    disposable = {"calendar_manifest", "bb_result", "bh_result", "au_result", "br_scope_verification"}
    references, reads, stages = {}, [], []
    class TrackedDict(dict):
        __slots__ = ("__weakref__",)
    original_json = saved.PinnedInputs.json
    def json_input(self, path, pin, role):
        value = original_json(self, path, pin, role)
        reads.append(role)
        if role in disposable:
            value = TrackedDict(value)
            references[role] = weakref.ref(value)
        return value
    monkeypatch.setattr(saved.PinnedInputs, "json", json_input)
    original_plan, original_metadata, original_restore = module.plan_windows, module.scope_source.bi_metadata, module.restore_activity_failure
    def plan(*args):
        stages.append("plan_windows")
        return original_plan(*args)
    def released():
        assert reads == expected and set(references) == disposable
        assert all(value() is None for value in references.values())
    def metadata(*args):
        released()
        assert stages == ["plan_windows"]
        stages.append("bi_metadata")
        return original_metadata(*args)
    def restore(*args):
        released()
        assert stages == ["plan_windows", "bi_metadata"]
        stages.append("restore")
        return original_restore(*args)
    monkeypatch.setattr(module, "plan_windows", plan)
    monkeypatch.setattr(module.scope_source, "bi_metadata", metadata)
    monkeypatch.setattr(module, "restore_activity_failure", restore)
    module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    assert stages == ["plan_windows", "bi_metadata", "restore"] and statuses(runs) == ["started", "completed"]


def test_noneligible_reviewed_nominee_preserves_refusal_before_any_db_price_access(tmp_path, monkeypatch):
    root, output, runs, _, store, _, _ = fixture(tmp_path, monkeypatch, noneligible=True)
    monkeypatch.setattr(module.prices, "load_scan", lambda *a: pytest.fail("noneligible nominee opened holding DB"))
    with pytest.raises(ValueError, match="new scope required"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    assert statuses(runs) == ["started", "failed"]
    refusal = saved.strict_json((output / "scope-refusal.json").read_bytes())
    assert refusal["new_scope_required"] is True and refusal["database_access"] is False
    assert not (output / "read-scope.json").exists() and not (output / "result.json").exists()
    assert (output / "normal-input-audit.json").exists() and (output / "failure.json").exists()


def test_started_manifest_precedes_first_body_and_failed_hash_is_not_retried(tmp_path, monkeypatch):
    root, output, runs, _, _, _, _ = fixture(tmp_path, monkeypatch)
    calls = []
    def reader(path):
        calls.append(path)
        assert statuses(runs) == ["started"] and (output / "source-manifest.json").exists()
        return b"{}"
    with pytest.raises(ValueError, match="hash mismatch"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    assert statuses(runs) == ["started", "failed"] and len(calls) == 1
    failure = saved.strict_json((output / "failure.json").read_bytes())
    assert failure["automatic_retries"] == 0 and failure["actual_inputs_attempted"][0]["read_status"] == "hash_mismatch"
    assert all(set(row) == {"file", "line", "function"} for row in failure["error_frames"])


def test_source_change_after_diagnostic_cannot_publish_completed_result(tmp_path, monkeypatch):
    root, output, runs, spec, store, source, _ = fixture(tmp_path, monkeypatch)
    changes = iter([source, {**source, "code_version": "b" * 40}])
    monkeypatch.setattr(saved, "freeze_sources", lambda *a, **kw: (next(changes), saved.encoded(spec), spec))
    with pytest.raises(ValueError, match="source/runtime changed"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    assert statuses(runs) == ["started", "failed"]
    assert (output / "holding-input-audit.json").exists() and not (output / "result.json").exists()


@pytest.mark.parametrize("corruption", ["output_link", "input_link", "input_missing", "input_extra", "manifest_link",
    "manifest_output", "invalid_pin", "summary", "targets", "flag", "count"])
def test_scope_receipt_cannot_certify_another_output_or_target_declaration(corruption):
    data = normal_fixture()
    scope = data["scope"]
    scope["formations"][0]["activity_preopening_k"] = {"D1": 1, "D2": 0}
    receipt = bind_scope(scope, "a" * 64)
    declared = {"br_scope_result": {"sha256": receipt["output_sha256"]["result.json"]}}
    if corruption == "output_link": receipt["output_sha256"]["result.json"] = "f" * 64
    elif corruption == "input_link": receipt["input_sha256"]["bi_result"] = "f" * 64
    elif corruption == "input_missing": del receipt["input_sha256"]["bi_result"]
    elif corruption == "input_extra": receipt["input_sha256"]["unexpected"] = "f" * 64
    elif corruption == "manifest_link": scope["input_manifest_sha256"] = "f" * 64
    elif corruption == "manifest_output": receipt["output_sha256"]["input-manifest.json"] = "f" * 64
    elif corruption == "invalid_pin": receipt["output_sha256"]["input-manifest.json"] = "unknown"
    elif corruption == "summary": receipt["summary"]["source_rows"] = 1
    elif corruption == "targets": receipt["activity_preopening_k"]["20200301"]["D1"] = 5
    elif corruption == "flag": receipt["eligible_control_certified"] = True
    else: receipt["D1_completed_studies"] = 1
    with pytest.raises(ValueError): module.validate_scope(scope, receipt, declared, "a" * 64)


def test_actual_scope_producer_plan_contract_has_a_separate_manifest_hash_and_no_embedded_list():
    scope = scope_plan(scope_fixture())
    assert "input_manifest" not in scope
    before = deepcopy(scope)
    activity_sha = scope["activity_snapshot_sha256"]
    receipt = bind_scope(scope, activity_sha)
    assert "input_manifest" not in scope and len(receipt["input_sha256"]) == 10
    assert {key: scope[key] for key in before} == before
    assert scope["input_manifest_sha256"] == receipt["output_sha256"]["input-manifest.json"]
    declared = {"br_scope_result": {"sha256": receipt["output_sha256"]["result.json"]}}
    module.validate_scope(scope, receipt, declared, activity_sha)


def test_wrong_separate_scope_manifest_hash_preserves_failure_before_holding_database(tmp_path, monkeypatch):
    root, output, runs, spec, store, _, _ = fixture(tmp_path, monkeypatch)
    role = "br_scope_verification"
    path = Path(spec["inputs"][role]["path"])
    receipt = saved.strict_json(store[path])
    receipt["output_sha256"]["input-manifest.json"] = "f" * 64
    store[path] = saved.encoded(receipt)
    spec["inputs"][role]["sha256"] = saved.digest(store[path])
    monkeypatch.setattr(module.prices, "load_scan", lambda *args: pytest.fail("invalid scope opened holding DB"))
    with pytest.raises(ValueError, match="scope internal output links"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    failure = saved.strict_json((output / "failure.json").read_bytes())
    assert statuses(runs) == ["started", "failed"] and failure["automatic_retries"] == 0
    assert len(failure["actual_inputs_attempted"]) == 9
    assert all(row["read_status"] == "hash_verified" for row in failure["actual_inputs_attempted"])
    assert not (output / "normal-read-scope.json").exists() and not (output / "read-scope.json").exists()
    assert not (output / "result.json").exists()


def test_input_cannot_alias_log_or_existing_output_and_retry_does_not_read(tmp_path, monkeypatch):
    root, output, runs, spec, store, _, _ = fixture(tmp_path, monkeypatch)
    alias = Path(spec["inputs"]["bi_result"]["path"])
    with pytest.raises(ValueError, match="immutable input"):
        module.run_preflight(root, root / "spec.json", output, alias, reader=store.__getitem__)
    module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    before = runs.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=lambda *a: pytest.fail("retry read input"))
    assert runs.read_bytes() == before
