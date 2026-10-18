"""Fabricated holding receipts, immutable quotes and private temporary outputs."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date
from pathlib import Path
import os
import socket
import sqlite3

import pytest

from research import activity_cohort_preflight as module
from research import activity_preflight as saved
from research import experiment_log
from research.activity_cohort_connection import cohort_proof_selections
from research.activity_component_bounds import component_exit_bounds
from research.activity_exit_bounds import provisional_exit_bounds
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_quote_extension import extend_holding_quotes
from research import activity_raw_quote_view as raw_view
from research.activity_reviewed_actions import reviewed_action_terms
from research.activity_quote_preflight import _audit
from test_activity_cohort_connection import package, raw_package, rights_package, complete_coverage
from test_activity_cohort_comparison import quotes
from test_activity_cohort_holding_preflight import scan_from_quotes


@pytest.fixture(autouse=True)
def deny_actual_network_database(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("actual network/database access")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(sqlite3, "connect", denied)
    monkeypatch.setattr(experiment_log, "_git_head_sha", lambda: "a" * 40)


def fixture(tmp_path, *, zero=False, raw=False, return_policy=False):
    seed = rights_package(v2=return_policy == 2) if return_policy else raw_package() if raw else package(ks=(0, 0) if zero else (1, 1))
    declarations, calendar = seed["declarations"], seed["original"].calendar
    proofs = cohort_proof_selections(declarations, calendar, PARAMETERS)
    declaration_value = [_json_value(asdict(row)) for row in declarations]
    proof_value = {arm: [_json_value(asdict(row)) for row in rows] for arm, rows in proofs.items()}
    original_pins = {role: saved.digest(role.encode()) for role in module.HOLDING_INPUT_ROLES}
    normal_pins = {name: original_pins[role] for role, name in module.holding.NORMAL_ROLES.items()}
    requested, isins = seed["original_required_code_dates"], seed["issue_isins"]
    scope = dict(schema="activity-holding-price-preflight-v1:read-scope-v1", producer_schema=module.holding.SCHEMA,
        scope_kind="complete_BS_family_proof_only_original_issue_union_not_successor_closure",
        calendar_dates=[day.strftime("%Y%m%d") for day in calendar], requested_code_dates=requested, issue_isins=isins,
        activity_snapshot_sha256=seed["original"].activity_snapshot_sha256, input_sha256=original_pins,
        scan_database={"path": "/synthetic/never-open/scan.db", "mode": "ro", "columns": list(module.READ_COLUMNS)},
        normal_file_sha256=normal_pins, normal_verification_sha256=original_pins["bs_normal_verification"],
        cohort_declarations_sha256=saved.digest(saved.encoded(declaration_value)), proof_selections_sha256=saved.digest(saved.encoded(proof_value)),
        input_manifest_before_database=[dict(role=role, path="/synthetic/never-open/" + role + ".json",
            expected_sha256=pin, sha256=pin, read_status="hash_verified", bytes=1) for role, pin in original_pins.items()],
        new_source_requests=0, automatic_retries=0, returns_computed=False)
    scope_pin = saved.digest(saved.encoded(scope))
    original = quotes(calendar, scope={code: tuple(date.fromisoformat(day) for day in days) for code, days in requested.items()}, scope_pin=scope_pin)
    plan = provisional_exit_bounds(proofs["D1"], proofs["D2"], original, PARAMETERS, requested, isins, input_sha256=original_pins)
    audit = (_audit(scan_from_quotes(original), original, requested, scope_pin) if requested
        else module.holding._empty_audit(original, requested, scope_pin))
    coverage, evidence = saved.strict_json(seed["reviewed_coverage"]), saved.strict_json(seed["basis_evidence"])
    coverage.update(activity_snapshot_sha256=original.activity_snapshot_sha256,
        quote_snapshot_sha256=original.quote_snapshot_sha256, required_scope_sha256=original.read_scope_sha256)
    optional = {}
    if raw:
        old_raw = seed["quotes"]
        extra_dates = {}
        for code, day in old_raw.base.extension.requested_coordinates:
            extra_dates.setdefault(code, []).append(str(day))
        extra_scope = dict(schema="activity-holding-price-preflight-v1:read-scope-v1",
            calendar_dates=scope["calendar_dates"], requested_code_dates=extra_dates, activity_snapshot_sha256=original.activity_snapshot_sha256)
        extra_pin = saved.digest(saved.encoded(extra_scope))
        extension = quotes(calendar, scope={code: tuple(date.fromisoformat(day) for day in days) for code, days in extra_dates.items()}, scope_pin=extra_pin)
        extra_audit = _audit(scan_from_quotes(extension), extension, extra_dates, extra_pin)
        base = extend_holding_quotes(original, extension)
        unit = saved.strict_json(old_raw.unit_plan_json); unit["base_quote_snapshot_sha256"] = base.quote_snapshot_sha256
        unit_raw = saved.encoded(unit)
        artifact = raw_view.encode_raw_artifact(base, old_raw.raw_observations, saved.digest(unit_raw))
        projected = raw_view.build_raw_quote_view(base, old_raw.raw_observations, source_artifact=artifact,
            source_artifact_sha256=saved.digest(artifact), unit_plan=unit_raw, unit_plan_sha256=saved.digest(unit_raw))
        coverage["quote_snapshot_sha256"] = projected.quote_snapshot_sha256
        for row in evidence["bases"].values(): row["quote_snapshot_sha256"] = projected.quote_snapshot_sha256
        windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"]) for row in plan["entries"])
        terms = reviewed_action_terms(coverage, evidence, expected_windows=windows, calendar=calendar,
            activity_snapshot_sha256=original.activity_snapshot_sha256, quote_snapshot_sha256=projected.quote_snapshot_sha256)
        proof = component_exit_bounds(plan, proofs["D1"], proofs["D2"], original, PARAMETERS, requested, isins,
            input_sha256=original_pins, quotes=projected, terms=terms)
        coverage, evidence = complete_coverage(coverage, evidence, proof["windows"], proof["component_scope_sha256"])
        optional = {"component_exit_scope": saved.encoded(proof), "quote_extension": saved.encoded(dict(
            schema="activity-quote-extension-envelope-v1", original_quote_snapshot_sha256=original.quote_snapshot_sha256,
            read_scope=extra_scope, holding_input_audit=extra_audit)), "raw_episode_quotes": artifact, "raw_episode_unit_plan": unit_raw}
    elif not return_policy:
        coverage, evidence = complete_coverage(coverage, evidence, plan["child_windows"], plan["child_scope_sha256"])
    outputs = {name: saved.encoded({"synthetic_unread_metadata": name}) for name in module.HOLDING_FILES}
    outputs.update({"cohort-declarations.json": saved.encoded(declaration_value), "proof-selections.json": saved.encoded(proof_value),
        "read-scope.json": saved.encoded(scope), "holding-input-audit.json": saved.encoded(audit), "provisional-exit-scope.json": saved.encoded(plan)})
    pins = {name: saved.digest(body) for name, body in outputs.items()}
    summary = dict(cohort_declarations=len(declarations), formation_count=len(proofs["D1"]), groups=list(module.execution.comparison.GROUPS),
        union_original_codes=len(requested), union_potential_origins=len(plan["entries"]),
        cohort_chosen_windows=sum(len(row.chosen_codes) for row in declarations),
        assigned_slots_by_arm={arm: sum(row.target_ceiling for row in declarations if row.arm == arm) for arm in module.execution.comparison.ARMS})
    links = {"specification.json": "specification_sha256", "source-manifest.json": "source_manifest_sha256",
        "input-manifest.json": "input_manifest_sha256", "resource-manifest.json": "resource_manifest_sha256",
        "cohort-declarations.json": "cohort_declarations_sha256", "proof-selections.json": "proof_selections_sha256",
        "read-scope.json": "read_scope_sha256", "holding-input-audit.json": "holding_input_audit_sha256", "provisional-exit-scope.json": "provisional_exit_scope_sha256"}
    result = dict(schema=module.holding.SCHEMA, status="cohort_original_issue_inputs_audited", summary=summary,
        holding_summary=audit["summary"], provisional_exit_entries=len(plan["entries"]), activity_snapshot_sha256=original.activity_snapshot_sha256,
        quote_snapshot_sha256=original.quote_snapshot_sha256, input_sha256=original_pins, normal_file_sha256=normal_pins,
        database_access=bool(requested), **{field: pins[name] for name, field in links.items()},
        control_targets_frozen=True, complete_control_prefix_proved=True, original_quote_requests_preserved=True,
        new_source_requests=0, automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0,
        **dict.fromkeys(module.holding.FALSE_FLAGS, False))
    outputs["result.json"] = saved.encoded(result); pins["result.json"] = saved.digest(outputs["result.json"])
    identity = dict(expected_commit="a" * 40, producer_sha256="b" * 64, specification_sha256=pins["specification.json"],
        expected_normal_commit="c" * 40, expected_normal_producer_sha256="d" * 64,
        helper_sha256="e" * 64, io_sha256="f" * 64, verifier_sha256="1" * 64, protocol_sha256="2" * 64,
        dependency_sha256={"normal": "3" * 64, "holding": "4" * 64}, file_sha256=pins, input_sha256=original_pins, collector_commit=module.OPERATIONAL_COMMIT)
    receipt = {**{name: deepcopy(identity[name]) for name in module.IDENTITY_FIELDS - {"collector_commit"}},
        "schema": "bs-cohort-holding-independent-verification-v1", "status": "verified", "producer_code_version": identity["expected_commit"],
        "summary": summary, "holding_summary": audit["summary"], "provisional_exit_entries": len(plan["entries"]),
        "activity_snapshot_sha256": original.activity_snapshot_sha256, "quote_snapshot_sha256": original.quote_snapshot_sha256,
        "new_output_files_read": 10, "saved_input_files_read": 19, "completed_reads": 29,
        "checkouts": {"research": {"head": identity["expected_commit"], "clean": True}, "collector": {"head": identity["collector_commit"], "clean": True}},
        "independent_output_verification": "verified", **dict.fromkeys(module.VERIFY_TRUE, True),
        **dict.fromkeys((*module.VERIFY_FALSE, *module.VERIFY_EXTRA_FALSE), False), **dict.fromkeys(module.VERIFY_ZERO, 0)}
    payloads = {role: outputs[name] for role, name in module.HOLDING_ROLES.items()}
    payloads.update(holding_verification=saved.encoded(receipt), reviewed_action_coverage=saved.encoded(coverage), basis_evidence=saved.encoded(evidence), **optional)
    if return_policy: payloads["return_policy"] = seed["return_policy_json"]
    declared = {role: {"path": str(tmp_path / "holding" / module.HOLDING_ROLES[role] if role in module.HOLDING_ROLES else tmp_path / "evidence" / (role + ".json")),
        "sha256": saved.digest(body)} for role, body in payloads.items()}
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode=module.RETURN_MODE if return_policy else module.MODE, window=deepcopy(saved.WINDOW), reference_specification=saved.REFERENCE,
        parameters=deepcopy(PARAMETERS), controls=list(module.CONTROL_IDS), design=module.frontier.DESIGN, inputs=declared,
        holding_verification_identity=identity, promotion_allowed=False, **dict.fromkeys(module.frontier.POWER_FLAGS, False))
    if return_policy == 2: spec["return_policy_schema"] = module.RETURN_POLICY_SCHEMA_V2
    bodies = {declared[role]["path"]: body for role, body in payloads.items()}
    return spec, bodies, declarations, original, receipt


def inputs_for(spec, bodies):
    return saved.PinnedInputs(lambda path: bodies[str(path)])


def test_ordinary_saved_package_is_restored_without_rereading_normal_inputs(tmp_path):
    spec, bodies, declarations, original, receipt = fixture(tmp_path)
    module.validate_contract(spec, {"parameters": PARAMETERS})
    inputs = inputs_for(spec, bodies)
    prepared = module.prepare_inputs(spec, inputs)
    assert prepared.declarations == declarations and prepared.original == original
    assert prepared.binding.events == () and prepared.quotes is prepared.original
    assert len(inputs.manifest) == len(spec["inputs"]) == 8
    assert {row["role"] for row in inputs.manifest} == set(spec["inputs"])
    assert all(row["read_status"] == "hash_verified" for row in inputs.manifest)


def test_raw_extension_component_proof_restores_existing_projection_before_books(tmp_path):
    spec, bodies, declarations, original, receipt = fixture(tmp_path, raw=True)
    prepared = module.prepare_inputs(spec, inputs_for(spec, bodies))
    assert type(prepared.quotes) is raw_view.RawEpisodeQuoteInputs
    assert prepared.original == original and prepared.quotes.original == original
    assert prepared.binding.component_proof_json is not None
    assert prepared.quotes.source_artifact_sha256 == spec["inputs"]["raw_episode_quotes"]["sha256"]
    assert prepared.binding.events and prepared.quotes.extension.requested_coordinates
    assert max(day for _, day in prepared.quotes.requested_coordinates) == original.calendar[188]


@pytest.mark.parametrize("change", ["parameter", "raw_pair", "alias", "holding_pin", "ten_pins", "producer", "collector", "extra_role", "relative", "design"])
def test_invalid_contract_refuses_before_any_saved_read(tmp_path, change):
    spec, bodies, *_ = fixture(tmp_path)
    if change == "parameter": spec["parameters"]["slots"] = 21
    elif change == "raw_pair": spec["inputs"]["raw_episode_quotes"] = {"path": str(tmp_path / "raw.json"), "sha256": "a" * 64}
    elif change == "alias": spec["inputs"]["basis_evidence"]["path"] = spec["inputs"]["reviewed_action_coverage"]["path"]
    elif change == "holding_pin": spec["inputs"]["holding_result"]["sha256"] = "a" * 64
    elif change == "ten_pins": spec["holding_verification_identity"]["file_sha256"].pop("resource-manifest.json")
    elif change == "producer": spec["holding_verification_identity"]["expected_commit"] = "bad"
    elif change == "collector": spec["holding_verification_identity"]["collector_commit"] = "5" * 40
    elif change == "extra_role": spec["inputs"]["normal_body"] = {"path": str(tmp_path / "forbidden.json"), "sha256": "a" * 64}
    elif change == "relative": spec["inputs"]["holding_verification"]["path"] = "relative.json"
    else: spec["design"] = "changed"
    with pytest.raises(ValueError): module.validate_contract(spec, {"parameters": PARAMETERS})


@pytest.mark.parametrize("change", ["pure_only", "extra_field", "producer", "normal_identity", "normal_pin", "wrapper_pin", "read_count", "false_claim", "study", "checkout", "summary", "byte_pin"])
def test_invalid_final_receipt_or_byte_pin_never_reaches_books(tmp_path, monkeypatch, change):
    spec, bodies, declarations, original, receipt = fixture(tmp_path)
    if change == "pure_only": receipt.pop("independent_output_verification")
    elif change == "extra_field": receipt["arbitrary"] = False
    elif change == "producer": receipt["producer_code_version"] = "b" * 40
    elif change == "normal_identity": receipt["expected_normal_commit"] = "b" * 40
    elif change == "normal_pin": receipt["input_sha256"]["bs_normal_result"] = "9" * 64
    elif change == "wrapper_pin": receipt["helper_sha256"] = "9" * 64
    elif change == "read_count": receipt["completed_reads"] = 30
    elif change == "false_claim": receipt["event_coverage_certified"] = True
    elif change == "study": receipt["D1_completed_studies"] = 1
    elif change == "checkout": receipt["checkouts"]["collector"]["clean"] = False
    elif change == "summary": receipt["summary"]["cohort_chosen_windows"] += 1
    if change == "byte_pin": bodies[spec["inputs"]["holding_input_audit"]["path"]] += b" "
    else:
        raw = saved.encoded(receipt); row = spec["inputs"]["holding_verification"]
        bodies[row["path"]], row["sha256"] = raw, saved.digest(raw)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    monkeypatch.setattr(module.execution, "run_cohort_family", lambda *a, **k: pytest.fail("invalid inputs reached books"))
    with pytest.raises(ValueError):
        module.evaluate_inputs(spec, inputs_for(spec, bodies), output, runs_path=tmp_path / "log.jsonl",
            source_manifest_sha256="1" * 64, specification_sha256="2" * 64)
    assert not (output / "cohorts").exists()


@pytest.mark.parametrize("change", ["coverage", "component_proof", "raw_unit"])
def test_changed_action_or_raw_proofs_are_recomputed_before_first_book(tmp_path, monkeypatch, change):
    spec, bodies, *_ = fixture(tmp_path, raw=change != "coverage")
    role = {"coverage": "reviewed_action_coverage", "component_proof": "component_exit_scope", "raw_unit": "raw_episode_unit_plan"}[change]
    row = spec["inputs"][role]
    value = saved.strict_json(bodies[row["path"]])
    if change == "coverage": value["windows"].pop()
    elif change == "component_proof": value["entries"][0]["end_on"] = value["entries"][0]["origin"]["entry_on"]
    else: value["episodes"][0]["end"] = value["episodes"][0]["start"]
    body = saved.encoded(value); bodies[row["path"]], row["sha256"] = body, saved.digest(body)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    monkeypatch.setattr(module.execution, "run_cohort_family", lambda *a, **k: pytest.fail("changed evidence reached first book"))
    with pytest.raises(ValueError):
        module.evaluate_inputs(spec, inputs_for(spec, bodies), output, runs_path=tmp_path / "log.jsonl",
            source_manifest_sha256="1" * 64, specification_sha256="2" * 64)
    assert not (output / "cohorts").exists()


def logging_case(tmp_path, monkeypatch, *, zero=False, raw=False, return_policy=False):
    spec, bodies, declarations, original, receipt = fixture(tmp_path, zero=zero, raw=raw, return_policy=return_policy)
    root, private = tmp_path / "repo", tmp_path / "private"
    root.mkdir(); private.mkdir(mode=0o700)
    monkeypatch.chdir(root); monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    sources = {"code_version": "a" * 40, "files": [{"path": module.SOURCE_PATH, "sha256": "a" * 64}]}
    calls = []
    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_contract
        module.validate_contract(spec, {"parameters": PARAMETERS})
        calls.append(True)
        return deepcopy(sources), saved.encoded(spec), deepcopy(spec)
    runs = tmp_path / "canonical" / "experiments.jsonl"
    def reader(path):
        records = [saved.strict_json(line) for line in runs.read_bytes().splitlines()]
        assert records[0]["status"] == "started" and records[0]["parameters"]["mode"] == (module.RETURN_MODE if return_policy else module.MODE)
        assert records[0]["parameters"]["performance_studies_added"] == 0
        return bodies[str(path)]
    return root, private / "run", runs, reader, freeze, sources, calls


@pytest.mark.parametrize("zero, raw", [(False, False), (True, False), (False, True)])
def test_outer_start_precedes_reads_and_individual_trials_own_performance_counts(tmp_path, monkeypatch, zero, raw):
    root, output, runs, reader, freeze, sources, calls = logging_case(tmp_path, monkeypatch, zero=zero, raw=raw)
    record = module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze)
    result = saved.strict_json((output / "result.json").read_bytes())
    assert result["cohort_evaluation_counts"] == {"D1": 12, "D2": 12}
    records = [saved.strict_json(line) for line in runs.read_bytes().splitlines()]
    assert len(records) == 50 and records[0]["status"] == "started" and records[-1]["status"] == "completed"
    assert [row["status"] for row in records[1:-1]] == ["started", "completed"] * 24
    assert sum(row.get("result", {}).get("D1_completed_studies", 0) for row in records) == 12
    assert sum(row.get("result", {}).get("D2_completed_studies", 0) for row in records) == 12
    assert record["result"]["D1_completed_studies"] == record["result"]["D2_completed_studies"] == 0
    assert record["result"]["family_cohort_evaluation_counts"] == result["cohort_evaluation_counts"]
    report = saved.strict_json((output / "cohorts" / "report.json").read_bytes())
    if zero:
        assert all(row["metrics"]["total_return"] is None for values in report["aggregates"].values() for row in values.values())
    assert result["independent_output_verification"] == "pending" and result["database_access"] is result["promotion_allowed"] is False
    assert result["books_replayed"] is result["returns_computed"] is True
    assert calls == [True, True]
    before = runs.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze)
    assert runs.read_bytes() == before


def test_final_identity_failure_preserves_completed_individual_counts(tmp_path, monkeypatch):
    root, output, runs, reader, freeze, sources, calls = logging_case(tmp_path, monkeypatch)
    def changed(*args, **kwargs):
        value, body, spec = freeze(*args, **kwargs)
        if len(calls) > 1: value["code_version"] = "b" * 40
        return value, body, spec
    def evaluator(spec, inputs, output, **kwargs):
        inputs.read(Path(spec["inputs"]["holding_verification"]["path"]), spec["inputs"]["holding_verification"]["sha256"], "holding_verification")
        return dict(schema=module.SCHEMA, status="synthetic_family_completed", cohort_evaluation_counts={"D1": 84, "D2": 84})
    with pytest.raises(ValueError, match="source/runtime changed"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=changed, evaluator=evaluator)
    failure = saved.strict_json((output / "failure.json").read_bytes())
    assert failure["family_cohort_evaluation_counts"] == {"D1": 84, "D2": 84}
    assert failure["partial_package_preserved"] and failure["outer_diagnostic_performance_studies_added"] == 0
    assert not (output / "result.json").exists()
    assert [saved.strict_json(line)["status"] for line in runs.read_bytes().splitlines()] == ["started", "failed"]


def test_partial_execution_counts_come_from_preserved_private_failure(tmp_path, monkeypatch):
    root, output, runs, reader, freeze, *_ = logging_case(tmp_path, monkeypatch)
    def evaluator(spec, inputs, output, **kwargs):
        child = output / "cohorts"; child.mkdir(mode=0o700)
        saved.write_exclusive(child / "failure.json", saved.encoded({"completed_cohort_studies_by_arm": {"D1": 2, "D2": 0}}))
        raise ValueError("synthetic cohort failure")
    with pytest.raises(ValueError, match="cohort failure"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze, evaluator=evaluator)
    failure = saved.strict_json((output / "failure.json").read_bytes())
    assert failure["family_cohort_evaluation_counts"] == {"D1": 2, "D2": 0}
    assert failure["automatic_retries"] == 0 and not (output / "result.json").exists()


def test_partial_child_failure_keeps_unknown_counts_and_original_exception(tmp_path, monkeypatch):
    root, output, runs, reader, freeze, *_ = logging_case(tmp_path, monkeypatch)
    def evaluator(spec, inputs, output, **kwargs):
        child = output / "cohorts"; child.mkdir(mode=0o700)
        saved.write_exclusive(child / "failure.json", b"preserved partial synthetic metadata")
        raise ValueError("original synthetic failure")
    with pytest.raises(ValueError, match="original synthetic failure"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze, evaluator=evaluator)
    failure = saved.strict_json((output / "failure.json").read_bytes())
    assert failure["family_cohort_evaluation_counts"] is None
    assert (output / "cohorts" / "failure.json").read_bytes() == b"preserved partial synthetic metadata"


def test_large_typed_reader_is_private_single_link_and_bounded(tmp_path):
    private = tmp_path / "private"; private.mkdir(mode=0o700)
    path = private / "audit.json"; path.write_bytes(b"{}"); path.chmod(0o600)
    assert module._read_large(path) == b"{}"
    alias = private / "alias.json"; alias.hardlink_to(path)
    with pytest.raises(ValueError, match="single-link"): module._read_large(path)
    alias.unlink()
    with path.open("wb") as stream: stream.truncate(256 * 1024**2 + 1)
    with pytest.raises(ValueError, match="bounded"): module._read_large(path)


def test_cli_installs_exact_resources_without_actual_execution(monkeypatch, tmp_path):
    limits, alarms, priorities = [], [], []
    monkeypatch.setattr(module.resource, "setrlimit", lambda kind, value: limits.append((kind, value)))
    monkeypatch.setattr(module.signal, "signal", lambda *a: None)
    monkeypatch.setattr(module.signal, "alarm", alarms.append)
    monkeypatch.setattr(module.os, "getpriority", lambda *a: 0)
    monkeypatch.setattr(module.os, "nice", lambda value: priorities.append(value) or 10)
    monkeypatch.setattr(module, "run_preflight", lambda *a, **k: {"result": {"synthetic": True}})
    assert module.main(["--spec", str(tmp_path / "spec.json"), "--output-dir", str(tmp_path / "output"), "--runs-path", str(tmp_path / "log.jsonl")]) == 0
    assert limits == [(module.resource.RLIMIT_AS, (768 * 1024**2,) * 2), (module.resource.RLIMIT_CPU, (7200, 7200))]
    assert alarms == [10800, 0] and priorities == [10]


def test_resource_manifest_retains_measured_usage_with_new_registered_budget(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(module.resource, "getrusage", lambda kind: SimpleNamespace(
        ru_maxrss=705064, ru_utime=1596.357314, ru_stime=73.861722))
    monkeypatch.setattr(module.resource, "getrlimit", lambda kind:
        (805306368, 805306368) if kind == module.resource.RLIMIT_AS else (7200, 7200))
    monkeypatch.setattr(module.time, "monotonic", lambda: 1800.0)
    monkeypatch.setattr(module.os, "getpriority", lambda *args: 10)
    assert module._resources(5.0) == {
        "elapsed_seconds": 1795.0, "process_peak_rss_kib": 705064,
        "process_user_cpu_seconds": 1596.357314, "process_system_cpu_seconds": 73.861722,
        "address_space_limit_bytes": [805306368, 805306368], "cpu_limit_seconds": [7200, 7200],
        "wall_limit_seconds": 10800, "nice": 10}


@pytest.mark.parametrize("change", ["old_mode", "missing_policy", "unknown_mode"])
def test_price_return_role_requires_its_explicit_approved_mode_before_reads(tmp_path, change):
    spec, *_ = fixture(tmp_path, return_policy=True)
    if change == "old_mode": spec["mode"] = module.MODE
    elif change == "missing_policy": spec["inputs"].pop("return_policy")
    else: spec["mode"] = "total_investment_return"
    with pytest.raises(ValueError): module.validate_contract(spec, {"parameters": PARAMETERS})


@pytest.mark.parametrize("change", ["wrong_pin", "fourth_code", "unknown_kind", "fake_no_event", "missing_event"])
def test_saved_price_return_policy_and_coverage_refuse_before_first_book(tmp_path, monkeypatch, change):
    spec, bodies, *_ = fixture(tmp_path, return_policy=True)
    role = "return_policy" if change in ("wrong_pin", "fourth_code", "unknown_kind") else "reviewed_action_coverage"
    declared = spec["inputs"][role]
    value = saved.strict_json(bodies[declared["path"]])
    if change == "wrong_pin": declared["sha256"] = "f" * 64
    else:
        if change == "fourth_code": value["events"][0]["code"] = "005930"
        elif change == "unknown_kind": value["events"][0]["kind"] = "voluntary_offer"
        elif change == "fake_no_event": value["windows"][0].update(review_state="reviewed_no_event", event_ids=[])
        else: value["events"].pop()
        body = saved.encoded(value); bodies[declared["path"]], declared["sha256"] = body, saved.digest(body)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    monkeypatch.setattr(module.execution, "run_cohort_family", lambda *a, **k: pytest.fail("bad rights input reached book"))
    with pytest.raises(ValueError): module.evaluate_inputs(spec, inputs_for(spec, bodies), output,
        runs_path=tmp_path / "log.jsonl", source_manifest_sha256="1" * 64, specification_sha256="2" * 64)
    assert not (output / "cohorts").exists()


def test_saved_price_return_policy_passes_exact_original_bytes_to_existing_runner(tmp_path):
    spec, bodies, declarations, original, _ = fixture(tmp_path, return_policy=True)
    inputs = inputs_for(spec, bodies)
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    calls = []
    def runner(rows, quotes, events, **kwargs):
        binding = kwargs["scope_binding"]
        assert rows == declarations and quotes == original and events == ()
        assert binding.return_policy_json == bodies[spec["inputs"]["return_policy"]["path"]]
        calls.append(binding)
        return dict(report_sha256="a" * 64, completion_sha256="b" * 64, cohort_plan_sha256="c" * 64,
            completed_cohort_studies_by_arm={"D1": 12, "D2": 12}, D1_completed_studies=12, D2_completed_studies=12)
    result = module.evaluate_inputs(spec, inputs, output, runs_path=tmp_path / "log.jsonl",
        source_manifest_sha256="1" * 64, specification_sha256="2" * 64, family_runner=runner)
    assert len(calls) == 1 and len(inputs.manifest) == 9
    assert result["return_policy_sha256"] == spec["inputs"]["return_policy"]["sha256"]
    assert result["actual_investment_return"] is result["promotion_allowed"] is False
    assert result["automatic_subscription_rights_cash"] == "not_modeled"


@pytest.mark.parametrize("version", [1, 2])
def test_outer_price_return_trial_and_receipts_preserve_declared_convention(tmp_path, monkeypatch, version):
    root, output, runs, reader, freeze, *_ = logging_case(tmp_path, monkeypatch, return_policy=version)
    record = module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze)
    result = saved.strict_json((output / "result.json").read_bytes())
    started = saved.strict_json(runs.read_bytes().splitlines()[0])
    for value in (result, record["result"], started["parameters"]):
        assert value["return_measure"] == module.RETURN_MODE and value["actual_investment_return"] is False
        assert value["return_policy_sha256"] == result["return_policy_sha256"]
    assert result["cohort_evaluation_counts"] == {"D1": 12, "D2": 12}
    if version == 2:
        for value in (result, record["result"], started["parameters"]):
            assert value["return_policy_schema"] == module.RETURN_POLICY_SCHEMA_V2
            assert value["automatic_stock_dividend_assets"] == "not_modeled"
            assert value["unmodeled_asset_codes"] == ["003670", "005070", "006400", "006800"]


@pytest.mark.parametrize("change", ["missing_version", "v1_bytes", "unknown_version", "no_policy"])
def test_registered_version_and_loaded_policy_must_agree_before_any_book(tmp_path, monkeypatch, change):
    spec, bodies, *_ = fixture(tmp_path, return_policy=2)
    if change == "missing_version": spec.pop("return_policy_schema")
    elif change == "v1_bytes":
        from test_activity_cohort_return_policy import synthetic_policy
        row = spec["inputs"]["return_policy"]
        bodies[row["path"]] = saved.encoded(synthetic_policy())
        row["sha256"] = saved.digest(bodies[row["path"]])
    elif change == "unknown_version": spec["return_policy_schema"] = "all-future-assets"
    else: spec["inputs"].pop("return_policy"); spec["mode"] = module.MODE
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    monkeypatch.setattr(module.execution, "run_cohort_family", lambda *a, **k: pytest.fail("mixed version reached book"))
    with pytest.raises(ValueError):
        module.validate_contract(spec, {"parameters": PARAMETERS})
        module.evaluate_inputs(spec, inputs_for(spec, bodies), output, runs_path=tmp_path / "log.jsonl",
            source_manifest_sha256="1" * 64, specification_sha256="2" * 64)
