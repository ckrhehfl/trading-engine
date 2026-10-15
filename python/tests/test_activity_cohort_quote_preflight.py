"""Fabricated complete holding receipts; no actual saved input or scan is opened."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_cohort_quote_preflight as module
from research import activity_preflight as saved
from research import experiment_log
from research.activity_holding_restore import restore_holding_inputs
from research.activity_pair_calibration import PARAMETERS
from research.activity_quote_extension import extend_holding_quotes
from research.activity_reviewed_actions import reviewed_action_terms
from test_activity_cohort_preflight import fixture as holding_fixture
from test_activity_cohort_holding_preflight import scan_from_quotes
from test_activity_cohort_comparison import quotes
import test_activity_cohort_comparison as comparison_fixture
import test_activity_cohort_preflight as holding_fixture_module


@pytest.fixture(autouse=True)
def deny_actual_network_database(monkeypatch):
    def denied(*args, **kwargs): pytest.fail("actual network/database access")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(sqlite3, "connect", denied)
    monkeypatch.setattr(experiment_log, "_git_head_sha", lambda: "a" * 40)


def case(tmp_path, *, zero=False, raw=False, event=True):
    old, bodies, declarations, original, receipt = holding_fixture(tmp_path, zero=zero, raw=raw)
    spec = deepcopy(old)
    spec.update(schema=module.SCHEMA, study_id=module.SCHEMA, mode=module.MODE)
    spec["inputs"] = {role: spec["inputs"][role] for role in (*module.cohort.HOLDING_ROLES, "holding_verification")}
    spec["inputs"]["scan_database"] = dict(path=str(tmp_path / "database" / "never-open.db"), mode="ro", columns=list(module.prices.READ_COLUMNS))
    plan = dict(schema=module.PLAN_SCHEMA, original_quote_snapshot_sha256=original.quote_snapshot_sha256,
        original_read_scope_sha256=original.read_scope_sha256, activity_snapshot_sha256=original.activity_snapshot_sha256,
        cohort_declarations_sha256=spec["inputs"]["cohort_declarations"]["sha256"],
        ordinary_exit_scope_sha256=spec["inputs"]["ordinary_exit_scope"]["sha256"], events=[], requested_code_dates={})
    if event and not zero:
        calendar = original.calendar
        parent = min(original.requested_coordinates, key=lambda key: (key[1], key[0]))[0]
        identities = saved.strict_json(bodies[old["inputs"]["holding_read_scope"]["path"]])["issue_isins"]
        row = dict(event_id="synthetic-split", kind="compulsory_spin_off", code=parent, isin=identities[parent],
            successor_code="999999", successor_isin="KR7999999000", last_eligible_entry_on=str(calendar[88]),
            record_on=str(calendar[89]), effective_on=str(calendar[90]), retained_available_on=str(calendar[95]),
            new_available_on=str(calendar[100]), old_basis_on=str(calendar[60]), retained_basis_on=str(calendar[95]),
            new_basis_on=str(calendar[100]))
        plan["events"] = [row]
        plan["requested_code_dates"] = {parent: [str(calendar[60])], "999999": [str(day) for day in calendar[100:315]]}
    if raw:
        coverage = saved.strict_json(bodies[old["inputs"]["reviewed_action_coverage"]["path"]])
        basis = saved.strict_json(bodies[old["inputs"]["basis_evidence"]["path"]])
        event_row = coverage["events"][0]
        plan["events"] = [{name: event_row[name] for name in ("event_id", "kind", "code", "isin", "successor_code", "successor_isin",
            "last_eligible_entry_on", "record_on", "effective_on", "retained_available_on", "new_available_on")}]
        for role in ("old", "retained", "new"):
            plan["events"][0][role + "_basis_on"] = basis["bases"][event_row[role + "_basis_id"]]["session"]
        plan["requested_code_dates"] = {"383800": [str(day) for day in original.calendar[102:315]]}
    row = dict(path=str(tmp_path / "plan" / "coordinate-plan.json"), sha256=saved.digest(saved.encoded(plan)))
    spec["inputs"]["coordinate_plan"] = row
    bodies[row["path"]] = saved.encoded(plan)
    return spec, bodies, original, plan, old, receipt


def pinned(bodies): return saved.PinnedInputs(lambda path: bodies[str(path)])


def repin(spec, bodies, role, value):
    body = saved.encoded(value)
    spec["inputs"][role]["sha256"] = saved.digest(body)
    bodies[spec["inputs"][role]["path"]] = body


def synthetic_loader(original, output, calls, *, changes=None):
    def load(path, plan, inputs, scope_sha):
        scope = saved.strict_json((output / "read-scope.json").read_bytes())
        assert scope_sha == saved.digest(saved.encoded(scope))
        assert plan == {"calendar_dates": scope["calendar_dates"], "intersection_code_dates": scope["requested_code_dates"]}
        assert len(inputs.manifest) == len(module.SAVED_ROLES)
        assert all(row["read_status"] == "hash_verified" for row in inputs.manifest)
        cells = {(code, date.fromisoformat(day)) for code, days in plan["intersection_code_dates"].items() for day in days}
        assert not cells & set(original.requested_coordinates)
        calls.append(plan)
        value = quotes(original.calendar, scope={code: tuple(date.fromisoformat(day) for day in days)
            for code, days in plan["intersection_code_dates"].items()}, scope_pin=scope_sha, changes=changes)
        return scan_from_quotes(value)
    return load


def test_complete_original_receipt_and_full_union_persist_before_one_scan(tmp_path):
    spec, bodies, original, plan, *_ = case(tmp_path)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    calls, inputs, before = [], pinned(bodies), deepcopy(bodies)
    result = module.evaluate_inputs(spec, inputs, output, scan_loader=synthetic_loader(original, output, calls))
    assert len(calls) == 1 and bodies == before
    assert [row["role"] for row in inputs.manifest] == ["holding_verification", "holding_result", "cohort_declarations",
        "holding_read_scope", "ordinary_exit_scope", "holding_input_audit", "coordinate_plan"]
    assert result["database_access"] and result["original_quote_requests_preserved"]
    assert all(result[name] is False for name in module.FALSE_FLAGS)
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == result["performance_studies_added"] == 0
    envelope = saved.strict_json((output / "quote-extension.json").read_bytes())
    assert envelope["original_quote_snapshot_sha256"] == original.quote_snapshot_sha256
    assert envelope["read_scope"]["requested_code_dates"] == plan["requested_code_dates"]
    audit, scope = envelope["holding_input_audit"], envelope["read_scope"]
    restored = restore_holding_inputs(audit, scope, quote_snapshot_sha256=result["extension_quote_snapshot_sha256"],
        activity_snapshot_sha256=original.activity_snapshot_sha256, read_scope_sha256=result["read_scope_sha256"])
    composite = extend_holding_quotes(original, restored)
    assert composite.original == original and composite.quote_snapshot_sha256 == result["composite_quote_snapshot_sha256"]
    assert composite.fingerprint_json == (output / "composite-lineage.json").read_bytes()
    assert (output / "holding-input-audit.json").read_bytes() == saved.encoded(audit)
    assert all(saved.digest((output / name).read_bytes()) == pin for name, pin in result["output_sha256"].items())


@pytest.mark.parametrize("zero", [False, True])
def test_no_extra_coordinates_preserves_original_identity_without_scan_or_empty_composite(tmp_path, zero):
    spec, bodies, original, *_ = case(tmp_path, zero=zero, event=False)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    result = module.evaluate_inputs(spec, pinned(bodies), output, scan_loader=lambda *a: pytest.fail("empty scope opened scan"))
    assert result["database_access"] is False and result["extension_quote_snapshot_sha256"] is None
    assert result["composite_quote_snapshot_sha256"] == original.quote_snapshot_sha256
    assert set(path.name for path in output.iterdir()) == {"read-scope.json"}


def test_raw_basis_dates_keep_full_successor_union_instead_of_bounded_component_exit(tmp_path):
    spec, bodies, original, plan, old, *_ = case(tmp_path, raw=True)
    prepared = module.prepare_original(spec, pinned(bodies))
    requested = module.extension_coordinates(plan, prepared, spec)
    raw_unit = saved.strict_json(bodies[old["inputs"]["raw_episode_unit_plan"]["path"]])
    assert requested == {"383800": [str(day) for day in original.calendar[102:315]]}
    assert requested["383800"][-1] > raw_unit["episodes"][0]["end"]
    assert plan["events"][0]["old_basis_on"] == str(original.calendar[99])
    assert plan["events"][0]["retained_basis_on"] == str(original.calendar[101])


def test_metadata_projection_matches_existing_full_window_action_decoder(tmp_path):
    spec, bodies, original, plan, old, *_ = case(tmp_path, raw=True)
    prepared = module.prepare_original(spec, pinned(bodies))
    coverage = saved.strict_json(bodies[old["inputs"]["reviewed_action_coverage"]["path"]])
    basis = saved.strict_json(bodies[old["inputs"]["basis_evidence"]["path"]])
    # Terms validation does not use prices to derive successor scope; its typed
    # price/ratio fields below already belong to the fabricated fixture.
    snapshot = coverage["quote_snapshot_sha256"]
    windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
        for row in prepared.ordinary["entries"])
    terms = reviewed_action_terms(coverage, basis, expected_windows=windows, calendar=original.calendar,
        activity_snapshot_sha256=original.activity_snapshot_sha256, quote_snapshot_sha256=snapshot)
    required = {(code, date.fromisoformat(day)) for code, days in terms.successor_required for day in days}
    required.update((row["code"], date.fromisoformat(row["session"])) for row in basis["bases"].values())
    required.difference_update(original.requested_coordinates)
    projected = module.extension_coordinates(plan, prepared, spec)
    assert {(code, date.fromisoformat(day)) for code, days in projected.items() for day in days} == required


@pytest.mark.parametrize("kind", ["compulsory_stock_exchange", "final_cash_payment"])
def test_other_existing_event_primitives_have_explicit_date_only_requirements(tmp_path, kind):
    spec, bodies, original, plan, *_ = case(tmp_path)
    row = plan["events"][0]
    common = {key: row[key] for key in ("event_id", "kind", "code", "isin")}
    common["kind"] = kind
    if kind == "compulsory_stock_exchange":
        common.update({key: row[key] for key in ("successor_code", "successor_isin", "effective_on", "old_basis_on", "new_basis_on")})
        common["available_on"] = row["new_available_on"]
    else:
        common.update(last_trading_on=row["old_basis_on"], record_on=str(original.calendar[65]),
            paid_on=row["effective_on"], basis_on=row["old_basis_on"])
        plan["requested_code_dates"].pop("999999")
    plan["events"] = [common]
    repin(spec, bodies, "coordinate_plan", plan)
    prepared = module.prepare_original(spec, pinned(bodies))
    assert module.extension_coordinates(plan, prepared, spec) == plan["requested_code_dates"]
    assert not {"ratio", "raw_close", "adjusted_close", "net_cash_per_raw_share"} & set(common)


def test_original_requested_absence_is_subtracted_without_repair_or_reread(tmp_path):
    spec, bodies, original, plan, *_ = case(tmp_path)
    prepared = module.prepare_original(spec, pinned(bodies))
    code = plan["events"][0]["code"]
    absent_on = original.calendar[95]  # The retained basis was originally requested.
    absent = quotes(original.calendar, scope={key: tuple(date.fromisoformat(day) for day in days)
        for key, days in prepared.scope["requested_code_dates"].items()}, scope_pin=original.read_scope_sha256,
        changes={(code, absent_on): None})
    plan["original_quote_snapshot_sha256"] = absent.quote_snapshot_sha256
    prepared = replace(prepared, original=absent)
    projected = module.extension_coordinates(plan, prepared, spec)
    assert absent.absent_coordinates == ((code, absent_on),)
    assert str(absent_on) not in projected.get(code, ())
    assert projected == plan["requested_code_dates"]


def test_independently_selected_successor_keeps_original_requests_and_only_reads_remaining_union(tmp_path, monkeypatch):
    base = holding_fixture_module.raw_package
    monkeypatch.setattr(holding_fixture_module, "raw_package", lambda: base(later_code="383800"))
    spec, bodies, original, plan, *_ = case(tmp_path, raw=True)
    plan["requested_code_dates"] = {"383800": [str(day) for day in original.calendar[102:187]]}
    repin(spec, bodies, "coordinate_plan", plan)
    prepared = module.prepare_original(spec, pinned(bodies))
    projected = module.extension_coordinates(plan, prepared, spec)
    assert projected == plan["requested_code_dates"]
    assert any(row["code"] == "383800" for row in prepared.ordinary["entries"])
    assert ("383800", original.calendar[187]) in original.requested_coordinates
    assert str(original.calendar[187]) not in projected["383800"]


def test_entire_synthetic_fourteen_formation_family_is_validated_without_subset(tmp_path, monkeypatch):
    original_case = comparison_fixture.case
    monkeypatch.setattr(comparison_fixture, "case", lambda ks: original_case((1,) * 14))
    spec, bodies, original, plan, *_ = case(tmp_path, event=False)
    prepared = module.prepare_original(spec, pinned(bodies))
    assert len(prepared.declarations) == 168
    assert len({row.formation_on for row in prepared.declarations}) == 14
    assert len(prepared.ordinary["entries"]) == sum(len(row.eligible_codes) for rows in
        module.cohort.cohort_proof_selections(prepared.declarations, original.calendar, PARAMETERS).values() for row in rows)
    assert module.extension_coordinates(plan, prepared, spec) == {}


@pytest.mark.parametrize("change", ["plan_snapshot", "scope", "activity", "declarations", "ordinary", "price", "ratio", "missing_date",
    "duplicate", "isin", "successor_isin", "chain", "date_role", "basis_day", "truncated", "overlap", "extra", "empty_kind"])
def test_invalid_price_free_plan_refuses_before_scan(tmp_path, change):
    spec, bodies, original, plan, *_ = case(tmp_path)
    row = plan["events"][0]
    if change in ("plan_snapshot", "scope", "activity", "declarations", "ordinary"):
        field = {"plan_snapshot": "original_quote_snapshot_sha256", "scope": "original_read_scope_sha256",
            "activity": "activity_snapshot_sha256", "declarations": "cohort_declarations_sha256", "ordinary": "ordinary_exit_scope_sha256"}[change]
        plan[field] = "0" * 64
    elif change in ("price", "ratio"): row[change] = "1"
    elif change == "missing_date": row.pop("new_available_on")
    elif change == "duplicate": plan["events"].append(deepcopy(row))
    elif change == "isin": row["isin"] = "KR7000000000"
    elif change == "successor_isin": row["successor_code"] = next(code for code in original.panel if code != row["code"])
    elif change == "chain":
        second = deepcopy(row); second.update(event_id="chain", code=row["successor_code"], isin=row["successor_isin"], successor_code="888888")
        plan["events"].append(second)
    elif change == "date_role": row["new_basis_on"] = str(original.calendar[101])
    elif change == "basis_day": row["old_basis_on"] = "2018-12-31"
    elif change == "truncated": plan["requested_code_dates"]["999999"].pop()
    elif change == "overlap": plan["requested_code_dates"][row["code"]].append(str(original.calendar[95]))
    elif change == "extra": plan["requested_code_dates"]["888888"] = [str(original.calendar[100])]
    else: row["kind"] = ""
    repin(spec, bodies, "coordinate_plan", plan)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    with pytest.raises(ValueError): module.evaluate_inputs(spec, pinned(bodies), output, scan_loader=lambda *a: pytest.fail("bad plan reached scan"))
    assert not (output / "read-scope.json").exists()


@pytest.mark.parametrize("change", ["parameter_type", "role", "alias", "scan_alias", "relative", "pin", "receipt", "producer", "membership", "collector"])
def test_strict_registration_contract_keeps_all_original_holding_guards(tmp_path, change):
    spec, bodies, *_ = case(tmp_path)
    if change == "parameter_type": spec["parameters"]["slots"] = float(spec["parameters"]["slots"])
    elif change == "role": spec["inputs"]["legacy_extension"] = spec["inputs"]["coordinate_plan"]
    elif change == "alias": spec["inputs"]["coordinate_plan"]["path"] = spec["inputs"]["holding_result"]["path"]
    elif change == "scan_alias": spec["inputs"]["scan_database"]["path"] = spec["inputs"]["holding_result"]["path"]
    elif change == "relative": spec["inputs"]["coordinate_plan"]["path"] = "relative.json"
    elif change == "pin": spec["inputs"]["coordinate_plan"]["sha256"] = "bad"
    elif change == "receipt": spec["inputs"]["holding_result"]["sha256"] = "9" * 64
    elif change == "producer": spec["holding_verification_identity"]["expected_commit"] = "bad"
    elif change == "membership": spec["holding_verification_identity"]["file_sha256"].pop("proof-selections.json")
    else: spec["holding_verification_identity"]["collector_commit"] = "0" * 40
    with pytest.raises(ValueError): module.validate_spec(spec, {"parameters": PARAMETERS})


@pytest.mark.parametrize("change", ["receipt_claim", "receipt_count", "receipt_checkout", "hash", "ordinary", "family"])
def test_changed_original_evidence_cannot_reach_extension_scan(tmp_path, change):
    spec, bodies, original, plan, old, receipt = case(tmp_path)
    role = "holding_verification"
    value = receipt
    if change == "receipt_claim": value["price_basis_certified"] = True
    elif change == "receipt_count": value["completed_reads"] += 1
    elif change == "receipt_checkout": value["checkouts"]["research"]["clean"] = False
    elif change == "hash": bodies[spec["inputs"]["holding_input_audit"]["path"]] += b" "
    else:
        role = "ordinary_exit_scope" if change == "ordinary" else "cohort_declarations"
        value = saved.strict_json(bodies[spec["inputs"][role]["path"]])
        if change == "ordinary": value["entries"].pop()
        else: value.pop()
    if change != "hash": repin(spec, bodies, role, value)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    with pytest.raises(ValueError): module.evaluate_inputs(spec, pinned(bodies), output, scan_loader=lambda *a: pytest.fail("bad original reached scan"))


def test_absence_unresolved_and_vintage_are_preserved_in_new_audit(tmp_path):
    spec, bodies, original, plan, *_ = case(tmp_path)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    successor = "999999"; days = original.calendar
    changes = {(successor, days[100]): None, (successor, days[101]): (str(days[101]).replace("-", ""), "bad", "101", "99", "100", "1", "10")}
    result = module.evaluate_inputs(spec, pinned(bodies), output, scan_loader=synthetic_loader(original, output, [], changes=changes))
    audit = saved.strict_json((output / "holding-input-audit.json").read_bytes())
    assert audit["summary"]["state_counts"]["absent_requested"] == audit["summary"]["state_counts"]["unresolved_observed"] == 1
    assert audit["absent_coordinates"] == [[successor, str(days[100])]]
    assert all(row["provenance"]["retrieved_at"] == "unknown" and row["provenance"]["data_vintage"] is None for row in audit["rows"])
    assert result["historical_vintage_certified"] is result["price_basis_certified"] is False


def logging_case(tmp_path, monkeypatch, *, event=False):
    spec, bodies, original, plan, *_ = case(tmp_path, event=event)
    root, private = tmp_path / "repo", tmp_path / "private"
    root.mkdir(); private.mkdir(mode=0o700)
    monkeypatch.chdir(root); monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    sources = {"code_version": "a" * 40, "files": [{"path": module.SOURCE_PATH, "sha256": "b" * 64}]}
    calls = []
    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_spec
        module.validate_spec(spec, {"parameters": PARAMETERS}); calls.append(True)
        return deepcopy(sources), saved.encoded(spec), deepcopy(spec)
    runs = tmp_path / "canonical" / "experiments.jsonl"
    def reader(path):
        records = [saved.strict_json(line) for line in runs.read_bytes().splitlines()]
        assert len(records) == 1 and records[0]["status"] == "started"
        assert records[0]["parameters"]["performance_studies_added"] == 0
        return bodies[str(path)]
    return root, private / "run", runs, spec, sources, reader, freeze, calls


def test_durable_start_precedes_each_saved_read_and_final_freeze_precedes_completion(tmp_path, monkeypatch):
    root, output, runs, spec, sources, reader, freeze, calls = logging_case(tmp_path, monkeypatch)
    record = module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze)
    records = [saved.strict_json(line) for line in runs.read_bytes().splitlines()]
    assert [row["status"] for row in records] == ["started", "completed"] and len(calls) == 2
    assert record["result"]["D1_completed_studies"] == record["result"]["D2_completed_studies"] == 0
    assert (output.stat().st_mode & 0o777) == 0o700
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in output.iterdir())
    resources = saved.strict_json((output / "resource-manifest.json").read_bytes())
    assert resources["wall_limit_seconds"] == 900


@pytest.mark.parametrize("changed_source", [False, True])
def test_preserved_failure_records_inputs_resources_and_zero_studies_without_retry(tmp_path, monkeypatch, changed_source):
    root, output, runs, spec, sources, reader, freeze, calls = logging_case(tmp_path, monkeypatch)
    if changed_source:
        baseline = freeze
        def freeze(*args, **kwargs):
            values, raw, value = baseline(*args, **kwargs)
            if len(calls) == 2: values["code_version"] = "9" * 40
            return values, raw, value
    else:
        def reader(path): raise MemoryError("synthetic input failure")
    with pytest.raises((MemoryError, ValueError)):
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze)
    failure = saved.strict_json((output / "failure.json").read_bytes())
    assert failure["partial_package_preserved"] is True and failure["automatic_retries"] == 0
    assert failure["D1_completed_studies"] == failure["D2_completed_studies"] == failure["performance_studies_added"] == 0
    assert (output / "resource-manifest.json").exists() and (output / "input-manifest.json").exists()
    assert [saved.strict_json(line)["status"] for line in runs.read_bytes().splitlines()] == ["started", "failed"]
    assert not (output / "result.json").exists()


def test_existing_output_refuses_before_log_or_input_read(tmp_path, monkeypatch):
    root, output, runs, spec, sources, reader, freeze, calls = logging_case(tmp_path, monkeypatch)
    output.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="exclusive"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=lambda path: pytest.fail("existing output read input"), freeze=freeze)
    assert not runs.exists()


@pytest.mark.parametrize("failed_artifacts", [
    {"resource-manifest.json"},
    {"resource-manifest.json", "input-manifest.json", "failure.json", "failure-cleanup.json"},
])
def test_compound_cleanup_failures_preserve_primary_object_and_attempt_every_artifact(tmp_path, monkeypatch, failed_artifacts):
    root, output, runs, spec, sources, reader, freeze, calls = logging_case(tmp_path, monkeypatch)
    primary, attempted, original_write = MemoryError("private primary text"), [], saved.write_exclusive
    def evaluate(*args): raise primary
    def write(path, payload):
        attempted.append(path.name)
        if path.name in failed_artifacts: raise OSError("private cleanup text")
        return original_write(path, payload)
    monkeypatch.setattr(saved, "write_exclusive", write)
    with pytest.raises(MemoryError) as caught:
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze, evaluator=evaluate)
    assert caught.value is primary
    assert attempted[-4:] == ["resource-manifest.json", "input-manifest.json", "failure.json", "failure-cleanup.json"]
    records = [saved.strict_json(line) for line in runs.read_bytes().splitlines()]
    assert [row["status"] for row in records] == ["started", "failed"]
    assert records[-1]["error_type"] == "MemoryError" and records[-1]["parameters"]["performance_studies_added"] == 0
    assert not (output / "result.json").exists() and len(calls) == 1
    if "failure-cleanup.json" not in failed_artifacts:
        cleanup = saved.strict_json((output / "failure-cleanup.json").read_bytes())
        assert cleanup["primary_error_type"] == "MemoryError"
        assert cleanup["secondary_errors"] == [{"operation": "resource-manifest.json", "error_type": "OSError"}]
        failure = saved.strict_json((output / "failure.json").read_bytes())
        assert failure["error_type"] == "MemoryError"
        assert failure["D1_completed_studies"] == failure["D2_completed_studies"] == failure["performance_studies_added"] == 0
    assert all(b"private primary text" not in path.read_bytes() and b"private cleanup text" not in path.read_bytes()
        for path in output.iterdir())


def test_resource_payload_failure_does_not_skip_input_or_primary_failure_receipt(tmp_path, monkeypatch):
    root, output, runs, spec, sources, reader, freeze, calls = logging_case(tmp_path, monkeypatch)
    primary = MemoryError("synthetic primary")
    def evaluate(*args): raise primary
    def resources(*args): raise OSError("synthetic resource collection failure")
    monkeypatch.setattr(module.holding, "_resources", resources)
    with pytest.raises(MemoryError) as caught:
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze, evaluator=evaluate)
    assert caught.value is primary
    assert (output / "input-manifest.json").exists() and (output / "failure.json").exists()
    assert saved.strict_json((output / "failure-cleanup.json").read_bytes())["secondary_errors"] == [
        {"operation": "resource-manifest.json", "error_type": "OSError"}]


def test_final_freeze_primary_survives_resource_cleanup_failure(tmp_path, monkeypatch):
    root, output, runs, spec, sources, reader, freeze, calls = logging_case(tmp_path, monkeypatch)
    primary, original_freeze, original_write = MemoryError("synthetic final freeze"), freeze, saved.write_exclusive
    def freeze(*args, **kwargs):
        if calls: raise primary
        return original_freeze(*args, **kwargs)
    def write(path, payload):
        if path.name == "resource-manifest.json": raise OSError("synthetic cleanup failure")
        return original_write(path, payload)
    monkeypatch.setattr(saved, "write_exclusive", write)
    with pytest.raises(MemoryError) as caught:
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze)
    assert caught.value is primary and (output / "read-scope.json").exists()
    assert not (output / "result.json").exists()
    assert saved.strict_json(runs.read_bytes().splitlines()[-1])["error_type"] == "MemoryError"


def test_failed_canonical_append_cannot_replace_primary_evaluation_error(tmp_path, monkeypatch):
    root, output, runs, spec, sources, reader, freeze, calls = logging_case(tmp_path, monkeypatch)
    primary, original_append, attempted = MemoryError("synthetic primary"), experiment_log._append_record, []
    def evaluate(*args): raise primary
    def append(record, path):
        attempted.append(record)
        if record["status"] == "failed": raise OSError("synthetic canonical append failure")
        return original_append(record, path)
    monkeypatch.setattr(experiment_log, "_append_record", append)
    with pytest.raises(MemoryError) as caught:
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze, evaluator=evaluate)
    assert caught.value is primary
    assert [row["status"] for row in attempted] == ["started", "failed"] and attempted[-1]["error_type"] == "MemoryError"
    assert len(runs.read_bytes().splitlines()) == 1  # The durable start survives a failed failed-record append.
    assert saved.strict_json((output / "logging-failure.json").read_bytes())["secondary_errors"] == [
        {"operation": "canonical_failure_append", "error_type": "OSError"}]


@pytest.mark.parametrize("primary_failure", [False, True])
def test_umask_cleanup_preserves_primary_but_cleanup_alone_still_fails(tmp_path, monkeypatch, primary_failure):
    root, output, runs, spec, sources, reader, freeze, calls = logging_case(tmp_path, monkeypatch)
    primary, secondary, original_umask, masks = MemoryError("synthetic primary"), OSError("synthetic umask failure"), module.os.umask, []
    initial = original_umask(0o077); original_umask(initial)
    def umask(mask):
        masks.append(mask)
        if len(masks) == 2: raise secondary
        return original_umask(mask)
    def evaluate(*args): raise primary
    monkeypatch.setattr(module.os, "umask", umask)
    try:
        with pytest.raises(MemoryError if primary_failure else OSError) as caught:
            module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze,
                **({"evaluator": evaluate} if primary_failure else {}))
        assert caught.value is (primary if primary_failure else secondary)
        if primary_failure:
            assert saved.strict_json(runs.read_bytes().splitlines()[-1])["error_type"] == "MemoryError"
            assert (output / "umask-cleanup-failure.json").exists()
    finally:
        original_umask(initial)


@pytest.mark.parametrize("failure_kind", ["memory", "interrupt", "none"])
def test_cli_alarm_cleanup_keeps_primary_and_cleanup_alone_cannot_report_success(tmp_path, monkeypatch, capsys, failure_kind):
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    primary = MemoryError("private primary text") if failure_kind == "memory" else KeyboardInterrupt()
    secondary = OSError("private alarm cleanup text")
    monkeypatch.setattr(module.resource, "setrlimit", lambda *a: None)
    monkeypatch.setattr(module.signal, "signal", lambda *a: None)
    monkeypatch.setattr(module.os, "getpriority", lambda *a: 10)
    monkeypatch.setattr(module.os, "nice", lambda value: 10)
    def alarm(seconds):
        if seconds == 0: raise secondary
    def run(*args):
        if failure_kind != "none": raise primary
        return {"result": {"status": "completed"}}
    monkeypatch.setattr(module.signal, "alarm", alarm)
    monkeypatch.setattr(module, "run_preflight", run)
    args = ["--spec", str(tmp_path / "spec.json"), "--output-dir", str(output), "--runs-path", str(tmp_path / "runs.jsonl")]
    if failure_kind == "memory":
        assert module.main(args) == 1
        assert saved.strict_json(capsys.readouterr().out.encode())["error_type"] == "MemoryError"
    else:
        with pytest.raises(KeyboardInterrupt if failure_kind == "interrupt" else OSError) as caught:
            module.main(args)
        assert caught.value is (primary if failure_kind == "interrupt" else secondary)
        assert not capsys.readouterr().out
    if failure_kind != "none":
        receipt = saved.strict_json((output / "alarm-cleanup-failure.json").read_bytes())
        assert receipt["primary_error_type"] == type(primary).__name__
        assert receipt["secondary_errors"] == [{"operation": "cancel_wall_alarm", "error_type": "OSError"}]
