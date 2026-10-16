"""Fabricated saved BS outputs only; no actual artifact, source body or network."""
from copy import deepcopy
from datetime import date
import gc
import json
from pathlib import Path
import socket
import sqlite3
import weakref

import pytest

from research import activity_cohort_component_preflight as module
from research import activity_cohort_preflight as cohort
from research import activity_preflight as saved
from research import activity_raw_quote_view as raw_view
from research import experiment_log
from research.activity_pair_calibration import PARAMETERS
from research.activity_quote_extension import extend_holding_quotes
from research.activity_quote_preflight import _audit
from test_activity_cohort_comparison import quotes
from test_activity_cohort_holding_preflight import scan_from_quotes
import test_activity_cohort_preflight as holding_fixture
from test_activity_cohort_connection import package
from test_activity_cohort_return_policy import synthetic_policy_v2


SOURCE = Path(module.__file__).resolve()


@pytest.fixture(autouse=True)
def deny_actual_io(monkeypatch):
    def denied(*args, **kwargs): pytest.fail("unexpected actual database/network/book execution")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(sqlite3, "connect", denied)
    monkeypatch.setattr(cohort.execution, "run_cohort_family", denied)
    monkeypatch.setattr(experiment_log, "_git_head_sha", lambda: "a" * 40)


def repin(spec, bodies, role, value):
    raw = value if type(value) is bytes else saved.encoded(value)
    row = spec["inputs"][role]
    row["sha256"], bodies[row["path"]] = saved.digest(raw), raw


def fixture(tmp_path):
    old, bodies, declarations, original, _ = holding_fixture.fixture(tmp_path, raw=True)
    spec = {**deepcopy(old), "schema": module.SCHEMA, "study_id": module.SCHEMA, "mode": module.MODE,
        "return_policy_schema": module.SCHEMA_V2}
    inputs = spec["inputs"]
    inputs["reviewed_terms"] = inputs.pop("reviewed_action_coverage")
    inputs.pop("component_exit_scope")
    envelope = saved.strict_json(bodies[inputs["quote_extension"]["path"]])
    extra_scope = envelope["read_scope"]
    extra_scope.update(original_quote_snapshot_sha256=original.quote_snapshot_sha256,
        original_read_scope_sha256=original.read_scope_sha256,
        original_input_sha256=spec["holding_verification_identity"]["input_sha256"])
    extra_pin = saved.digest(saved.encoded(extra_scope))
    extension = quotes(original.calendar, scope={code: tuple(date.fromisoformat(day) for day in days)
        for code, days in extra_scope["requested_code_dates"].items()}, scope_pin=extra_pin)
    extra_audit = _audit(scan_from_quotes(extension), extension, extra_scope["requested_code_dates"], extra_pin)
    envelope["holding_input_audit"] = extra_audit
    base = extend_holding_quotes(original, extension)
    old_raw = saved.strict_json(bodies[inputs["raw_episode_quotes"]["path"]])
    unit = saved.strict_json(bodies[inputs["raw_episode_unit_plan"]["path"]])
    unit["base_quote_snapshot_sha256"] = base.quote_snapshot_sha256
    repin(spec, bodies, "raw_episode_unit_plan", unit)
    old_raw["base_quote_snapshot_sha256"] = base.quote_snapshot_sha256
    old_raw["unit_plan_sha256"] = inputs["raw_episode_unit_plan"]["sha256"]
    repin(spec, bodies, "raw_episode_quotes", old_raw)
    view = raw_view.restore_raw_quote_view(base, bodies[inputs["raw_episode_quotes"]["path"]], inputs["raw_episode_quotes"]["sha256"],
        bodies[inputs["raw_episode_unit_plan"]["path"]], inputs["raw_episode_unit_plan"]["sha256"])
    repin(spec, bodies, "quote_extension", envelope)
    coverage = saved.strict_json(bodies[inputs["reviewed_terms"]["path"]])
    coverage.update(windows=[], noops=[], unresolved=[], required_scope_sha256=original.read_scope_sha256,
        quote_snapshot_sha256=view.quote_snapshot_sha256)
    repin(spec, bodies, "reviewed_terms", coverage)
    evidence = saved.strict_json(bodies[inputs["basis_evidence"]["path"]])
    for row in evidence["bases"].values(): row["quote_snapshot_sha256"] = view.quote_snapshot_sha256
    repin(spec, bodies, "basis_evidence", evidence)
    for role, value in (("return_policy", synthetic_policy_v2()), ("unresolved_priority_intervals", [])):
        inputs[role] = dict(path=str(tmp_path / "evidence" / (role + ".json")), sha256="a" * 64)
        repin(spec, bodies, role, value)
    output_pins = {name: saved.digest(name.encode()) for name in module.QUOTE_FILES}
    output_pins.update({"quote-extension.json": inputs["quote_extension"]["sha256"], "read-scope.json": extra_pin,
        "holding-input-audit.json": saved.digest(saved.encoded(extra_audit)), "composite-lineage.json": base.quote_snapshot_sha256})
    reference_pins = dict(zip(module.QUOTE_REFERENCES,
        (inputs["holding_read_scope"]["sha256"], inputs["holding_verification"]["sha256"], inputs["ordinary_exit_scope"]["sha256"], "c" * 64), strict=True))
    pure = dict(verified=True, scope="BS_quote_output_typed_rows_full_origin_union_and_internal_hash_links_only",
        file_sha256=output_pins, reference_sha256=reference_pins,
        package_sha256=saved.digest(json.dumps([[name, output_pins[name]] for name in module.QUOTE_FILES],
            sort_keys=True, separators=(",", ":")).encode()), summary=extra_audit["summary"],
        activity_snapshot_sha256=original.activity_snapshot_sha256, original_quote_snapshot_sha256=original.quote_snapshot_sha256,
        extension_quote_snapshot_sha256=extension.quote_snapshot_sha256, composite_quote_snapshot_sha256=base.quote_snapshot_sha256,
        original_component_relies_on_prior_independent_verification=True, independent_coordinate_union_recomputed=True,
        original_full_family_metadata_reread=True, injected_BP_source_freeze_is_caller_obligation=True,
        **dict.fromkeys((*module.QUOTE_FALSE, "producer_imported", "database_access", "database_transaction_verified",
            "rights_accounting_certified", "actual_historical_availability_certified", "publication_or_trial_records_verified",
            "original_typed_rows_reread", "original_cohort_declarations_reread", "original_receipt_time_independently_verified",
            "original_source_inputs_opened", "source_body_opened"), False),
        **dict.fromkeys((name for name in module.ZERO if name != "new_source_requests"), 0))
    quote_receipt = dict(schema="bs-cohort-quote-independent-receipt-v1", verified=True, expected_commit="d" * 40,
        registration_sha256="1" * 64, protocol_sha256="2" * 64, publication_config_sha256="3" * 64,
        source_sha256=dict.fromkeys(("helper.py", "register.py", "verifier.py", "bp.py", "io.py", "publication.py"), "4" * 64),
        publication_sha256=dict.fromkeys(("receipt.json", "trial-receipts.jsonl"), "5" * 64),
        file_sha256=output_pins, reference_sha256=reference_pins, publication_or_trial_records_verified=True,
        checkouts=dict(research=dict(head="d" * 40, clean=True), collector=dict(head=cohort.OPERATIONAL_COMMIT, clean=True)),
        pure_verification=pure, read_manifest=[], tracked_source_manifest={},
        **dict.fromkeys((name for name in module.ZERO if name != "new_source_requests"), 0))
    inventory = {name: dict(sha256=saved.digest(name.encode()), bytes=100) for name in module.RAW_FILES}
    inventory["raw-episode-quotes.json"] = dict(sha256=view.source_artifact_sha256, bytes=len(view.source_artifact_json))
    inventory["unit-plan.json"] = dict(sha256=view.unit_plan_sha256, bytes=len(view.unit_plan_json))
    manifest = [dict(role="cached_response", path=str(tmp_path / "never-open" / "raw-response.json"),
        sha256="d" * 64, bytes=100, read_status="hash_verified")]
    raw_receipt = dict(schema="bs-cohort-raw-independent-verification-v1", verified=True,
        scope="saved_raw_original_bodies_typed_rows_receipts_and_registered_internal_links_only", expected_commit="e" * 40,
        specification_sha256=inventory["specification.json"]["sha256"], output_inventory=inventory,
        planning_metadata_sha256="6" * 64, composite_lineage_sha256=base.quote_snapshot_sha256,
        acquisition_plan_sha256=inventory["acquisition-plan.json"]["sha256"], unit_plan_sha256=view.unit_plan_sha256,
        publication_or_trial_records_verified=True, all_original_cached_bodies_independently_redecoded=True,
        all_new_bodies_independently_redecoded=True, typed_raw_rows_verified=True, relies_on_pinned_preregistered_cohort_plan=True,
        counts=dict(cached_response_bodies_redecoded=1, new_response_bodies_redecoded=0, raw_rows_verified=len(view.raw_observations), response_bytes=100),
        read_manifest=manifest, registration_sha256="7" * 64,
        source_sha256=dict.fromkeys(("helper.py", "numeric.py", "io.py", "publication.py"), "8" * 64),
        publication_sha256=dict.fromkeys(("receipt.json", "trial-receipts.jsonl"), "9" * 64),
        checkouts=dict(research=dict(head="e" * 40, clean=True), collector=dict(head=cohort.OPERATIONAL_COMMIT, clean=True)),
        **dict.fromkeys(module.RAW_FALSE, False), **dict.fromkeys(module.ZERO, 0))
    for role, value in (("quote_verification", quote_receipt), ("raw_episode_verification", raw_receipt)):
        inputs[role] = dict(path=str(tmp_path / "evidence" / (role + ".json")), sha256="a" * 64)
        repin(spec, bodies, role, value)
    module.validate_contract(spec, {"parameters": PARAMETERS})
    return spec, bodies, declarations, original, view


def evaluate(tmp_path, spec, bodies):
    output = tmp_path / "proof"; output.mkdir(mode=0o700)
    inputs = saved.PinnedInputs(lambda path: bodies[str(path)])
    return module.evaluate_inputs(spec, inputs, output), inputs, output


def test_real_restore_and_existing_proof_engine_emit_direct_proof_without_books(tmp_path, monkeypatch):
    spec, bodies, declarations, original, view = fixture(tmp_path)
    before, calls = deepcopy((spec, bodies)), []
    real = module.component_exit_bounds
    def capture(*args, **kwargs):
        calls.append((args, kwargs)); return real(*args, **kwargs)
    monkeypatch.setattr(module, "component_exit_bounds", capture)
    result, inputs, output = evaluate(tmp_path, spec, bodies)
    proof = saved.strict_json((output / "component-exit-scope.json").read_bytes())
    terms = saved.strict_json((output / "canonical-terms.json").read_bytes())
    assert (spec, bodies) == before and len(calls) == 1
    assert proof["schema"] == "activity-raw-component-lifetime-bounds-v1" and "component_scope" not in proof
    assert calls[0][0][3].requested_coordinates == original.requested_coordinates
    assert calls[0][1]["quotes"].source_artifact_json == view.source_artifact_json
    assert len(inputs.manifest) == 15 and all(row["read_status"] == "hash_verified" for row in inputs.manifest)
    assert [row["role"] for row in inputs.manifest[:6]] == ["holding_verification", "holding_result", "cohort_declarations",
        "holding_read_scope", "ordinary_exit_scope", "holding_input_audit"]
    assert result["summary"]["cohort_declarations"] == len(declarations)
    assert terms["status"] == "terms_only" and terms["final_coverage_verified"] is False
    assert all(result[name] is False for name in module.FALSE_FLAGS)
    assert all(type(result[name]) is int and result[name] == 0 for name in module.ZERO)
    assert set(result["unmodeled_asset_values"]) == set(module.decode_return_policy(bodies[spec["inputs"]["return_policy"]["path"]])["events"][i]["event_id"] for i in range(4))
    assert set(result["unmodeled_asset_values"].values()) == {None}
    assert result["return_policy_schema"] == module.SCHEMA_V2 and result["actual_investment_return"] is False
    assert result["output_sha256"]["component-exit-scope.json"] == saved.digest((output / "component-exit-scope.json").read_bytes())


@pytest.mark.parametrize("change", ["role", "extra_role", "alias", "relative", "identity13", "holding_pin", "parameter", "window", "controls", "schema", "mode", "policy_schema"])
def test_fixed_contract_refuses_scope_or_identity_drift(tmp_path, change):
    spec, *_ = fixture(tmp_path)
    if change == "role": spec["inputs"].pop("quote_verification")
    elif change == "extra_role": spec["inputs"]["source_body"] = dict(path=str(tmp_path / "forbidden.json"), sha256="a" * 64)
    elif change == "alias": spec["inputs"]["quote_verification"]["path"] = spec["inputs"]["raw_episode_verification"]["path"]
    elif change == "relative": spec["inputs"]["quote_extension"]["path"] = "relative.json"
    elif change == "identity13": spec["holding_verification_identity"]["fabricated"] = "a" * 64
    elif change == "holding_pin": spec["holding_verification_identity"]["file_sha256"]["read-scope.json"] = "f" * 64
    elif change == "parameter": spec["parameters"]["holding_sessions"] += 1
    elif change == "window": spec["window"]["start"] = "2018-01-01"
    elif change == "controls": spec["controls"].pop()
    elif change == "schema": spec["schema"] = "other"
    elif change == "mode": spec["mode"] = "total_return"
    else: spec["return_policy_schema"] = "activity-cohort-return-policy-v1"
    with pytest.raises(ValueError): module.validate_contract(spec, {"parameters": PARAMETERS})


@pytest.mark.parametrize("role,change", [
    ("holding_verification", "claim"), ("holding_verification", "read_count"), ("holding_verification", "extra"),
    ("quote_verification", "schema"), ("quote_verification", "extra"), ("quote_verification", "reference"),
    ("quote_verification", "claim"), ("quote_verification", "composite"), ("quote_verification", "package"),
    ("raw_episode_verification", "schema"), ("raw_episode_verification", "extra"), ("raw_episode_verification", "claim"),
    ("raw_episode_verification", "unit"), ("raw_episode_verification", "base"), ("raw_episode_verification", "source"),
    ("raw_episode_verification", "rows"), ("raw_episode_verification", "body"), ("raw_episode_verification", "count_bool"),
    ("raw_episode_verification", "length"), ("raw_episode_verification", "checkout"),
    ("reviewed_terms", "final_coverage"), ("reviewed_terms", "final_hash"), ("basis_evidence", "raw_basis"),
    ("raw_episode_unit_plan", "typed"), ("return_policy", "downgrade"), ("return_policy", "zero"),
])
def test_invalid_saved_contracts_never_emit_component_proof(tmp_path, role, change):
    spec, bodies, *_ = fixture(tmp_path)
    value = saved.strict_json(bodies[spec["inputs"][role]["path"]])
    if change == "claim":
        (value["pure_verification"] if role == "quote_verification" else value)["returns_computed"] = True
    elif change == "read_count": value["completed_reads"] += 1
    elif change == "extra": value["unexpected"] = False
    elif change == "schema": value["schema"] = "wrong-receipt"
    elif change == "reference": value["reference_sha256"]["holding-verification.json"] = "f" * 64
    elif change == "composite": value["pure_verification"]["composite_quote_snapshot_sha256"] = "f" * 64
    elif change == "package": value["pure_verification"]["package_sha256"] = "f" * 64
    elif change == "unit": value["unit_plan_sha256"] = "f" * 64
    elif change == "base": value["composite_lineage_sha256"] = "f" * 64
    elif change == "source": value["output_inventory"]["raw-episode-quotes.json"]["sha256"] = "f" * 64
    elif change == "rows": value["counts"]["raw_rows_verified"] -= 1
    elif change == "body": value["read_manifest"][0]["sha256"] = "f" * 64
    elif change == "count_bool": value["performance_studies_added"] = False
    elif change == "length": value["output_inventory"]["raw-episode-quotes.json"]["bytes"] += 1
    elif change == "checkout": value["checkouts"]["collector"]["clean"] = False
    elif change == "final_coverage": value["windows"] = [{"invented": True}]
    elif change == "final_hash": value["required_scope_sha256"] = "f" * 64
    elif change == "raw_basis": value["bases"]["old"]["raw_close"] = "101"
    elif change == "typed": value["price_unit"] = "adjusted"
    elif change == "downgrade": value["schema"] = "activity-cohort-return-policy-v1"
    else: value["automatic_stock_dividend_assets"] = "zero"
    repin(spec, bodies, role, value)
    with pytest.raises(ValueError): evaluate(tmp_path, spec, bodies)
    assert not (tmp_path / "proof" / "component-exit-scope.json").exists()


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize("change", ["shorten", "missing_priority", "short_priority"])
def test_all_four_named_unmodeled_assets_keep_full_original_obligations(index, change):
    policy = synthetic_policy_v2(); event = policy["events"][index]
    origin = dict(code=event["code"], isin=event["isin"], entry_on=event["start"])
    proof = dict(entries=[dict(origin=origin, components=[origin], end_on=event["end"], original_end_on=event["end"])],
        unresolved_priority_intervals=[dict(code=event["code"], isin=event["isin"], start=event["start"], end=event["end"])])
    module._policy_obligations(proof, policy)
    if change == "shorten": proof["entries"][0]["end_on"] = "2019-01-01"
    elif change == "missing_priority": proof["unresolved_priority_intervals"] = []
    else: proof["unresolved_priority_intervals"][0]["start"] = "2099-01-01"
    with pytest.raises(ValueError): module._policy_obligations(proof, policy)


def test_large_original_audit_lifetime_ends_before_extension_read(tmp_path, monkeypatch):
    spec, bodies, *_ = fixture(tmp_path)
    class Audit(dict): pass
    references, original_decoder = [], module.saved.strict_json
    def decode(raw):
        value = original_decoder(raw)
        if raw == bodies[spec["inputs"]["holding_input_audit"]["path"]]:
            value = Audit(value); references.append(weakref.ref(value))
        return value
    monkeypatch.setattr(module.saved, "strict_json", decode)
    restore = module.restore_holding_inputs
    monkeypatch.setattr(module, "restore_holding_inputs", lambda audit, *args, **kwargs: restore(dict(audit), *args, **kwargs))
    def reader(path):
        if str(path) == spec["inputs"]["quote_verification"]["path"]:
            gc.collect(); assert references and references[0]() is None
        return bodies[str(path)]
    output = tmp_path / "proof"; output.mkdir(mode=0o700)
    result = module.evaluate_inputs(spec, saved.PinnedInputs(reader), output)
    assert result["summary"]["potential_entries"] > 0


def logging_case(tmp_path, monkeypatch):
    spec, bodies, *_ = fixture(tmp_path)
    root = SOURCE.parents[2]
    monkeypatch.chdir(root)
    sources = dict(code_version="a" * 40, files=[dict(path=module.SOURCE_PATH, sha256=saved.digest(SOURCE.read_bytes()))])
    freezes = []
    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_contract
        module.validate_contract(spec, {"parameters": PARAMETERS}); freezes.append(True)
        return deepcopy(sources), saved.encoded(spec), deepcopy(spec)
    private = tmp_path / "private"; private.mkdir(mode=0o700)
    runs = tmp_path / "canonical" / "experiments.jsonl"
    def reader(path):
        started = saved.strict_json(runs.read_bytes().splitlines()[0])
        assert started["status"] == "started" and started["parameters"]["performance_studies_added"] == 0
        assert started["parameters"]["return_policy_schema"] == module.SCHEMA_V2
        return bodies[str(path)]
    return root, spec, bodies, private / "run", runs, reader, freeze, freezes, sources


def test_durable_start_precedes_all_fifteen_saved_reads_and_keeps_zero_counts(tmp_path, monkeypatch):
    root, spec, bodies, output, runs, reader, freeze, freezes, _ = logging_case(tmp_path, monkeypatch)
    result = module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader, freeze=freeze)
    assert [saved.strict_json(line)["status"] for line in runs.read_bytes().splitlines()] == ["started", "completed"]
    assert len(freezes) == 2 and {path.name for path in output.iterdir()} == set(module.OUTPUT_FILES)
    assert len(saved.strict_json((output / "input-manifest.json").read_bytes())) == 15
    assert all(result["result"][name] == 0 for name in module.ZERO)
    with pytest.raises(ValueError): module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader, freeze=freeze)
    assert len(runs.read_bytes().splitlines()) == 2


@pytest.mark.parametrize("failure", ["read", "source_drift", "secondary"])
def test_failures_retain_partial_manifest_and_preserve_primary_error(tmp_path, monkeypatch, failure):
    root, spec, bodies, output, runs, reader, freeze, freezes, sources = logging_case(tmp_path, monkeypatch)
    if failure == "source_drift":
        def changed(*args, **kwargs):
            value, raw, contract = freeze(*args, **kwargs)
            if len(freezes) > 1: value["code_version"] = "b" * 40
            return value, raw, contract
        freeze_runner = changed
    else:
        freeze_runner = freeze
        base_reader = reader
        def reader(path):
            if str(path) == spec["inputs"]["quote_extension"]["path"]: raise LookupError("synthetic primary")
            return base_reader(path)
    if failure == "secondary":
        write = saved.write_exclusive
        def broken(path, raw):
            if path.name == "resource-manifest.json": raise OSError("synthetic preservation failure")
            return write(path, raw)
        monkeypatch.setattr(saved, "write_exclusive", broken)
    with pytest.raises(ValueError if failure == "source_drift" else LookupError) as caught:
        module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader, freeze=freeze_runner)
    failure_receipt = saved.strict_json((output / "failure.json").read_bytes())
    assert failure_receipt["partial_package_preserved"] is True and failure_receipt["automatic_retries"] == 0
    assert [saved.strict_json(line)["status"] for line in runs.read_bytes().splitlines()] == ["started", "failed"]
    assert len(failure_receipt["actual_inputs_attempted"]) == (15 if failure == "source_drift" else 8)
    if failure == "secondary": assert "resource-manifest.json" in caught.value.__notes__[0]


@pytest.mark.parametrize("error_type", [ValueError, TimeoutError])
@pytest.mark.parametrize("secondary", ["append", "umask", "both"])
def test_canonical_append_and_umask_failures_preserve_explicit_primary(tmp_path, monkeypatch, error_type, secondary):
    root, spec, bodies, output, runs, reader, freeze, *_ = logging_case(tmp_path, monkeypatch)
    primary = error_type("synthetic original failure")
    def fail(*args, **kwargs): raise primary
    monkeypatch.setattr(module, "evaluate_inputs", fail)
    if secondary in ("append", "both"):
        append = experiment_log._append_record
        def broken_append(record, path):
            if record["status"] == "failed": raise OSError("synthetic canonical append failure")
            return append(record, path)
        monkeypatch.setattr(experiment_log, "_append_record", broken_append)
    if secondary in ("umask", "both"):
        umask, mask_calls = module.os.umask, []
        def broken_umask(value):
            mask_calls.append(value)
            if len(mask_calls) == 2:
                umask(value)
                raise OSError("synthetic umask cleanup failure")
            return umask(value)
        monkeypatch.setattr(module.os, "umask", broken_umask)
    with pytest.raises(error_type) as caught:
        module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader, freeze=freeze)
    assert caught.value is primary
    assert saved.strict_json((output / "failure.json").read_bytes())["error_type"] == error_type.__name__
    names = []
    if secondary in ("append", "both"): names.append(("logging-failure.json", "canonical_failure_append"))
    if secondary in ("umask", "both"): names.append(("umask-cleanup-failure.json", "restore_umask"))
    for name, operation in names:
        receipt = saved.strict_json((output / name).read_bytes())
        assert receipt["primary_error_type"] == error_type.__name__
        assert receipt["secondary_errors"] == [dict(operation=operation, error_type="OSError")]
        assert all(receipt[key] == 0 for key in module.ZERO)
    assert len(primary.__notes__) == len(names)
    assert len(runs.read_bytes().splitlines()) == (1 if secondary in ("append", "both") else 2)


def test_umask_failure_is_not_suppressed_by_unrelated_handled_exception(tmp_path, monkeypatch):
    root, spec, bodies, output, runs, reader, freeze, *_ = logging_case(tmp_path, monkeypatch)
    monkeypatch.setattr(experiment_log, "run_discovery_trial", lambda **kwargs: {"result": {}})
    umask, mask_calls = module.os.umask, []
    primary = OSError("synthetic cleanup is the only current failure")
    def broken_umask(value):
        mask_calls.append(value)
        if len(mask_calls) == 2:
            umask(value)
            raise primary
        return umask(value)
    monkeypatch.setattr(module.os, "umask", broken_umask)
    try: raise ValueError("unrelated ambient exception")
    except ValueError:
        with pytest.raises(OSError) as caught:
            module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader, freeze=freeze)
    assert caught.value is primary and not output.exists()


@pytest.mark.parametrize("error_type", [ValueError, TimeoutError])
def test_cli_alarm_cleanup_keeps_original_failure_type(tmp_path, monkeypatch, capsys, error_type):
    output = tmp_path / "private-output"; output.mkdir(mode=0o700)
    primary = error_type("synthetic original CLI failure")
    monkeypatch.setattr(module.resource, "setrlimit", lambda *args: None)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    alarms = []
    def alarm(value):
        alarms.append(value)
        if value == 0: raise OSError("synthetic alarm cleanup failure")
    monkeypatch.setattr(module.signal, "alarm", alarm)
    monkeypatch.setattr(module.os, "getpriority", lambda *args: 0)
    monkeypatch.setattr(module.os, "nice", lambda delta: delta)
    def fail(*args, **kwargs): raise primary
    monkeypatch.setattr(module, "run_preflight", fail)
    assert module.main(["--spec", str(tmp_path / "unopened.json"), "--output-dir", str(output), "--runs-path", str(tmp_path / "unused.jsonl")]) == 1
    assert saved.strict_json(capsys.readouterr().out.encode())["error_type"] == error_type.__name__
    receipt = saved.strict_json((output / "alarm-cleanup-failure.json").read_bytes())
    assert receipt["primary_error_type"] == error_type.__name__ and alarms == [900, 0]
    assert receipt["secondary_errors"] == [dict(operation="cancel_wall_alarm", error_type="OSError")]
    assert len(primary.__notes__) == 1


def test_cli_alarm_failure_is_not_suppressed_by_unrelated_handled_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(module.resource, "setrlimit", lambda *args: None)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    primary = OSError("synthetic alarm is the only current failure")
    def alarm(value):
        if value == 0: raise primary
    monkeypatch.setattr(module.signal, "alarm", alarm)
    monkeypatch.setattr(module.os, "getpriority", lambda *args: 0)
    monkeypatch.setattr(module.os, "nice", lambda delta: delta)
    monkeypatch.setattr(module, "run_preflight", lambda *args, **kwargs: {"result": {}})
    try: raise ValueError("unrelated ambient exception")
    except ValueError:
        with pytest.raises(OSError) as caught:
            module.main(["--spec", str(tmp_path / "unopened.json"), "--output-dir", str(tmp_path / "unused"), "--runs-path", str(tmp_path / "unused.jsonl")])
    assert caught.value is primary


def test_complete_168_declarations_expand_both_proof_arms_from_all_five_controls(tmp_path, monkeypatch):
    monkeypatch.setattr(holding_fixture, "package", lambda **kwargs: package(ks=(1,) * 14))
    spec, bodies, declarations, original, _ = holding_fixture.fixture(tmp_path)
    # The existing receipt/family verifier enforces the entire immutable grid.
    calls = []
    def load(role): calls.append(role); return bodies[spec["inputs"][role]["path"]]
    restored, actual, scope, ordinary = module._restore_original(spec, load)
    selections = module.cohort_proof_selections(restored, actual.calendar, PARAMETERS)
    assert len(declarations) == len(restored) == 168
    assert len(selections["D1"]) == len(selections["D2"]) == 14
    assert actual == original and {row["arm"] for row in ordinary["entries"]} == {"D1", "D2"}
    assert sum(len(row.eligible_codes) for rows in selections.values() for row in rows) == len(ordinary["entries"])
    assert scope["requested_code_dates"] and len(calls) == 6


def test_cli_enforces_fixed_resources_without_running_actual_inputs(monkeypatch, tmp_path):
    limits, alarms, calls = [], [], []
    monkeypatch.setattr(module.resource, "setrlimit", lambda kind, value: limits.append((kind, value)))
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    monkeypatch.setattr(module.signal, "alarm", alarms.append)
    monkeypatch.setattr(module.os, "getpriority", lambda *args: 0)
    monkeypatch.setattr(module.os, "nice", lambda delta: delta)
    monkeypatch.setattr(module, "run_preflight", lambda *args, **kwargs: calls.append(args) or {"result": dict(status="synthetic")})
    assert module.main(["--spec", str(tmp_path / "unopened.json"), "--output-dir", str(tmp_path / "unused"), "--runs-path", str(tmp_path / "unused.jsonl")]) == 0
    assert limits == [(module.resource.RLIMIT_AS, (768 * 1024**2,) * 2), (module.resource.RLIMIT_CPU, (300, 300))]
    assert alarms == [900, 0] and len(calls) == 1
