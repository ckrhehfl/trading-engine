"""Synthetic bounded quote-caller checks; no actual data or database access."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

from research import activity_preflight as saved
from research import activity_quote_preflight as module
from research.activity_holding_restore import restore_holding_inputs
from research.activity_paired_replay import prepare_inputs
from research.activity_quote_extension import extend_holding_quotes
from test_activity_paired_replay import fixture as paired_fixture, synthetic_scan


def test_committed_diagnostic_pins_price_free_plan_and_preserves_original_package_roles():
    root = Path(module.__file__).resolve().parents[2]
    directory = root / "configs/research/discovery"
    spec = saved.strict_json((directory / "activity-quote-extension-preflight-v1.json").read_bytes())
    original = saved.strict_json((directory / "activity-ordinary-exit-preflight-v1.json").read_bytes())
    reference = saved.strict_json((root / saved.REFERENCE).read_bytes())
    plan_raw = (directory / "activity-lg-quote-coordinates-v1.json").read_bytes()
    module.validate_spec(spec, reference)
    assert spec["inputs"]["coordinate_plan"]["sha256"] == saved.digest(plan_raw)
    assert {role: spec["inputs"][role] for role in module.PACKAGE_ROLES} == original["inputs"]
    plan = saved.strict_json(plan_raw)
    assert set(plan) == {"schema", "original_quote_snapshot_sha256", "original_read_scope_sha256", "events"}
    assert plan["schema"] == module.PLAN_SCHEMA and len(plan["events"]) == 1
    assert set(plan["events"][0]) == {"event_id", "kind", "code", "successor_code", "old_basis_on",
                                     "effective_on", "retained_available_on", "new_available_on"}
    output = Path("/home/minjun4897/research-evidence/large-liquid-bp-quote-extension-20261010-v1/run")
    runs = Path("/home/minjun4897/trading-engine/runs/experiments.jsonl")
    assert saved.validate_write_boundaries(root, directory / "activity-quote-extension-preflight-v1.json",
                                          spec, {"files": []}, output, runs) == (output, runs)

SOURCE = Path(module.__file__)

def fixture(monkeypatch, *, mode="ordinary"):
    bundles, parameters, pins, calendar = paired_fixture(monkeypatch, mode=mode)
    pins = {role: pins[role] for role in module.PACKAGE_ROLES}
    prepared = prepare_inputs(*bundles[:2], parameters=parameters, expected_pins=pins)
    code = next(iter(prepared.required_code_dates))
    event = dict(event_id="synthetic-spin", kind="compulsory_spin_off", code=code, successor_code="000002",
        old_basis_on=calendar[61].isoformat(), effective_on=calendar[66].isoformat(),
        retained_available_on=calendar[68].isoformat(), new_available_on=calendar[70].isoformat())
    plan = dict(schema=module.PLAN_SCHEMA, original_quote_snapshot_sha256=prepared.quote_snapshot_sha256,
        original_read_scope_sha256=prepared.read_scope_sha256, events=[event])
    payloads = {**{role: bundles[0]["files"][name] for role, name in module.holding.BL_ROLES.items()},
        **{role: bundles[1]["files"][name] for role, name in module.BM_ROLES.items()},
        "bl_verification": bundles[0]["verification"], "bj_scope": bundles[0]["bj_scope"],
        "bm_verification": bundles[1]["verification"], "coordinate_plan": saved.encoded(plan)}
    declared, store = {}, {}
    for role, raw in payloads.items():
        path = (Path("/synthetic/bl") / module.holding.BL_ROLES[role] if role in module.holding.BL_ROLES else
                Path("/synthetic/bm") / module.BM_ROLES[role] if role in module.BM_ROLES else Path("/synthetic") / (role + ".json"))
        declared[role] = dict(path=str(path), sha256=saved.digest(raw))
        store[path] = raw
    declared["scan_database"] = dict(path="/synthetic/extension.db", mode="ro", columns=list(module.prices.READ_COLUMNS))
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode="discovery_quote_extension_diagnostic",
        promotion_allowed=False, window=dict(saved.WINDOW), reference_specification=saved.REFERENCE,
        parameters=parameters, inputs=declared)
    module.validate_spec(spec, {"parameters": parameters})
    return spec, store, plan, prepared

def test_exact_successor_union_and_same_code_anchor_preserve_original_absence(monkeypatch):
    _, _, plan, prepared = fixture(monkeypatch, mode="absent_entry")
    plan["events"][0]["retained_available_on"] = prepared.calendar[-2].isoformat()
    requested = module.extension_coordinates(plan, prepared)
    event = plan["events"][0]
    expected = [day for day in prepared.required_code_dates[event["code"]] if day >= event["new_available_on"]]
    assert requested == {event["code"]: [event["retained_available_on"]], "000002": expected}
    assert (event["code"], prepared.calendar[61]) in prepared.holding_inputs.absent_coordinates
    assert event["old_basis_on"] not in requested[event["code"]]

def test_old_basis_before_first_original_request_is_a_new_anchor(monkeypatch):
    _, _, plan, prepared = fixture(monkeypatch)
    plan["events"][0]["old_basis_on"] = prepared.calendar[60].isoformat()
    requested = module.extension_coordinates(plan, prepared)
    assert requested[plan["events"][0]["code"]] == [prepared.calendar[60].isoformat()]

@pytest.mark.parametrize("mutation", ["quote", "scope", "calendar", "duplicate", "kind", "code", "same_code",
    "compact", "unspent", "non_session", "chronology", "unrelated", "empty", "future_price", "chain"])
def test_coordinate_plan_rejects_unregistered_or_unsupported_expansion(monkeypatch, mutation):
    _, _, plan, prepared = fixture(monkeypatch)
    event = plan["events"][0]
    if mutation in ("quote", "scope"):
        plan[{"quote": "original_quote_snapshot_sha256", "scope": "original_read_scope_sha256"}[mutation]] = "f" * 64
    elif mutation == "calendar": plan["calendar_sha256"] = "f" * 64
    elif mutation == "duplicate": plan["events"].append(deepcopy(event))
    elif mutation == "kind": event["kind"] = "optional_subscription"
    elif mutation == "code": event["successor_code"] = "../bad"
    elif mutation == "same_code": event["successor_code"] = event["code"]
    elif mutation == "compact": event["old_basis_on"] = event["old_basis_on"].replace("-", "")
    elif mutation == "unspent": event["old_basis_on"] = "2018-12-31"
    elif mutation == "non_session": event["new_available_on"] = (prepared.calendar[-1] + timedelta(days=1)).isoformat()
    elif mutation == "chronology": event["new_available_on"] = event["old_basis_on"]
    elif mutation == "unrelated": event["code"] = "999999"
    elif mutation == "empty": plan["events"] = []
    elif mutation == "future_price": event["adjusted_close"] = "100"
    else: plan["events"].append(dict(event, event_id="chain", code=event["successor_code"], successor_code="000003"))
    with pytest.raises(ValueError):
        module.extension_coordinates(plan, prepared)

def test_evaluation_restores_exact_envelope_and_composite_without_original_db(monkeypatch, tmp_path):
    spec, store, _, prepared = fixture(monkeypatch, mode="absent_entry")
    before, scans = deepcopy(store), []
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("unexpected SQLite access"))

    def scan(path, plan, inputs, scope_sha):
        scope = saved.strict_json((tmp_path / "read-scope.json").read_bytes())
        assert saved.digest(saved.encoded(scope)) == scope_sha
        assert len(inputs.manifest) == 19 and scope["input_manifest_before_database"] == inputs.manifest
        assert plan == {"calendar_dates": scope["calendar_dates"], "intersection_code_dates": scope["requested_code_dates"]}
        scans.append(path)
        return synthetic_scan(path, plan, inputs, scope_sha, mode="ordinary")

    monkeypatch.setattr(module.prices, "load_scan", scan)
    result = module.evaluate_inputs(spec, saved.PinnedInputs(store.__getitem__), tmp_path)
    envelope = saved.strict_json((tmp_path / "quote-extension.json").read_bytes())
    assert set(envelope) == {"schema", "original_quote_snapshot_sha256", "read_scope", "holding_input_audit"}
    assert envelope["schema"] == "activity-quote-extension-envelope-v1"
    audit, scope = envelope["holding_input_audit"], envelope["read_scope"]
    extension = restore_holding_inputs(audit, scope, quote_snapshot_sha256=result["extension_quote_snapshot_sha256"],
        activity_snapshot_sha256=prepared.activity_snapshot_sha256, read_scope_sha256=result["read_scope_sha256"])
    composite = extend_holding_quotes(prepared.holding_inputs, extension)
    assert result["composite_quote_snapshot_sha256"] == composite.quote_snapshot_sha256
    assert composite.original == prepared.holding_inputs and composite.original.absent_coordinates
    assert not set(extension.requested_coordinates) & set(composite.original.requested_coordinates)
    assert composite.fingerprint_json == (tmp_path / "composite-lineage.json").read_bytes()
    assert store == before and scans == [Path("/synthetic/extension.db")]
    assert all(result[key] is False for key in module.FALSE)
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0

def test_produced_envelope_connects_existing_paired_spin_off_books(monkeypatch, tmp_path):
    from research.activity_paired_replay import paired_replay
    from test_activity_paired_extension import spin_fixture
    bundles, parameters, pins, _, _, _ = spin_fixture(monkeypatch)
    spec, store, plan, prepared = fixture(monkeypatch)
    coverage, evidence = (saved.strict_json(raw) for raw in bundles[2:])
    event = coverage["events"][0]
    plan["events"] = [{name: event[name] for name in ("event_id", "kind", "code", "successor_code",
        "effective_on", "retained_available_on", "new_available_on")} | {"old_basis_on": evidence["bases"]["old"]["session"]}]
    row = spec["inputs"]["coordinate_plan"]
    store[Path(row["path"])] = saved.encoded(plan)
    row["sha256"] = saved.digest(store[Path(row["path"])])
    monkeypatch.setattr(module.prices, "load_scan", lambda *args: synthetic_scan(*args, mode="ordinary"))
    result = module.evaluate_inputs(spec, saved.PinnedInputs(store.__getitem__), tmp_path)
    sha = result["composite_quote_snapshot_sha256"]
    coverage["quote_snapshot_sha256"] = sha
    for basis in evidence["bases"].values(): basis["quote_snapshot_sha256"] = sha
    bundles[2:] = [saved.encoded(coverage), saved.encoded(evidence)]
    envelope = (tmp_path / "quote-extension.json").read_bytes()
    pins.update(reviewed_action_coverage=saved.digest(bundles[2]), basis_evidence=saved.digest(bundles[3]),
                quote_extension=saved.digest(envelope))
    monkeypatch.setattr(module.prices, "load_scan", lambda *a: pytest.fail("paired path reread a database"))
    output = paired_replay(*bundles, parameters=parameters, expected_pins=pins, quote_extension=envelope)
    assert output["quote_snapshot_sha256"] == sha
    assert output["original_quote_snapshot_sha256"] == prepared.quote_snapshot_sha256
    assert output["actual_study_completed"] is False


@pytest.mark.parametrize("mutation", ["parameter_type", "promotion", "window", "scan_mode", "columns", "extra", "alias"])
def test_spec_rejects_unsafe_or_unfrozen_contracts(monkeypatch, mutation):
    spec, _, _, _ = fixture(monkeypatch)
    reference = {"parameters": deepcopy(spec["parameters"])}
    if mutation == "parameter_type": spec["parameters"]["threshold"] = 3
    elif mutation == "promotion": spec["promotion_allowed"] = True
    elif mutation == "window": spec["window"]["start"] = "2018-01-01"
    elif mutation == "scan_mode": spec["inputs"]["scan_database"]["mode"] = "rw"
    elif mutation == "columns": spec["inputs"]["scan_database"]["columns"] = ["close"]
    elif mutation == "extra": spec["inputs"]["unexpected"] = spec["inputs"]["coordinate_plan"]
    else: spec["inputs"]["coordinate_plan"]["path"] = spec["inputs"]["bj_scope"]["path"]
    with pytest.raises(ValueError):
        module.validate_spec(spec, reference)


@pytest.mark.parametrize("failure", [None, "scan", "drift", "uncommitted"])
def test_logged_attempt_durable_start_failure_retention_and_frozen_producer(monkeypatch, tmp_path, failure):
    spec, store, _, _ = fixture(monkeypatch)
    root, output, runs = SOURCE.resolve().parents[2], tmp_path / "output", tmp_path / "runs.jsonl"
    monkeypatch.chdir(root)
    source = dict(path=SOURCE.resolve().relative_to(root).as_posix(), sha256=saved.digest(SOURCE.read_bytes()))
    sources = dict(files=[] if failure == "uncommitted" else [source], code_version="a" * 40)
    calls = []

    def freeze(*args, **kw):
        module.validate_spec(spec, {"parameters": spec["parameters"]})
        calls.append(1)
        return {**sources, "code_version": "b" * 40 if failure == "drift" and len(calls) > 1 else "a" * 40}, saved.encoded(spec), spec

    def reader(path):
        assert [saved.strict_json(line)["status"] for line in runs.read_bytes().splitlines()] == ["started"]
        return store[path]

    def scan(*args):
        assert (output / "read-scope.json").exists()
        if failure == "scan": raise OSError("private error details")
        return synthetic_scan(*args, mode="ordinary")

    monkeypatch.setattr(saved, "freeze_sources", freeze)
    monkeypatch.setattr(module.prices, "load_scan", scan)
    if failure:
        with pytest.raises((ValueError, OSError)):
            module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader)
        if failure == "uncommitted":
            assert not output.exists() and not runs.exists()
            return
        receipt = (output / "failure.json").read_bytes()
        assert b"private error details" not in receipt and saved.strict_json(receipt)["partial_package_preserved"]
        assert (output / "resource-manifest.json").exists() and (output / "input-manifest.json").exists()
    else:
        result = module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader)
        assert result["result"]["D1_completed_studies"] == 0
    assert [saved.strict_json(line)["status"] for line in runs.read_bytes().splitlines()] == ["started", "failed" if failure else "completed"]


@pytest.mark.parametrize("failure", [False, True])
def test_cli_prints_only_completion_or_sanitized_error(monkeypatch, capsys, failure):
    def run(*args):
        if failure: raise ValueError("private input must not print")
        return {"result": {"status": "quote_extension_inputs_audited", "D1_completed_studies": 0}}
    monkeypatch.setattr(module, "run_preflight", run)
    assert module.main(["--spec", "/synthetic/spec", "--output-dir", "/synthetic/output", "--runs-path", "/synthetic/runs"]) == int(failure)
    raw = capsys.readouterr().out.encode()
    assert b"private input" not in raw
    assert saved.strict_json(raw)["status"] == ("failed" if failure else "quote_extension_inputs_audited")
