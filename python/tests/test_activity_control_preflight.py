"""Synthetic saved-output linkage and logging; no actual study/DB/API access.

Use the existing pure replay fixture's emitted holding audit, provisional proof,
saved ACTIVITY books and report. The metadata declarations below follow the
existing BR/BQ producers' exact emitted receipt/result contracts.
"""
from copy import deepcopy
from datetime import date
from pathlib import Path
import socket
import sqlite3
import stat
import weakref

import pytest

from research import activity_control_preflight as module
from research import activity_preflight as saved
from research.activity_pair_calibration import PARAMETERS
from research.activity_holding_restore import restore_holding_inputs
from research.activity_exit_bounds import provisional_exit_bounds
from research.activity_timing import SessionLagPolicy
import test_activity_control_replay_connection as pure_fixture


def statuses(path):
    return [saved.strict_json(line)["status"] for line in path.read_bytes().splitlines()]


def fixture(tmp_path, monkeypatch, *, cash_only=False, frozen_entry=False, frozen_exit=False):
    captured = {}
    bind = pure_fixture.bind_control_holding_scope
    def capture(selections, **kwargs):
        captured.update(deepcopy(kwargs))
        return bind(selections, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(pure_fixture, "bind_control_holding_scope", capture)
        data = pure_fixture.fixture(cash_only=cash_only, frozen_entry=frozen_entry, frozen_exit=frozen_exit)
    calendar, parameters = captured["calendar"], data["parameters"]
    formation, compact = calendar[60], calendar[60].strftime("%Y%m%d")
    scope = captured["scope"]
    scope.update(schema="activity-control-scope-v1", status="complete_control_rank_metadata_planned",
        activity_snapshot_sha256=pure_fixture.ACTIVITY,
        **dict.fromkeys(("frontier_complete", "compulsory_successor_closure_complete", "control_targets_frozen",
            "potential_holding_origins_bound_complete", "holding_selection_executed", "returns_computed"), False))
    identities = data["bound"].selections_d1[0].source_population
    population = {"schema": "activity-normal-population-audit-v1", "rows": [
        dict(formation=compact, code=code, isin=isin, disposition="normal_window") for code, isin in identities]}
    scope["summary"] = {"source_rows": len(identities)}
    scope["population_partition"] = [dict(formation=compact, code=code, isin=isin,
        prior_disposition="normal_window", control_disposition="numeric_possible") for code, isin in identities]
    scope["formations"][0].update(activity_preopening_targets={arm: list(data["bound"].selections_d1[0].activity_targets) for arm in module.ARMS},
        activity_preopening_k={arm: len(data["bound"].selections_d1[0].activity_targets) for arm in module.ARMS})
    normal, dispositions = captured["normal_audits"], captured["dispositions"]
    for row in normal:
        for arm, lag in (("D1", 1), ("D2", 2)):
            policy = SessionLagPolicy(lag, paired=True)
            row["control_arms"][arm].update(selection_at=policy.selection_at(calendar, 60).isoformat(),
                decision_on=policy.execution_on(calendar, 60).isoformat())
    for row in dispositions:
        row.update(prior_disposition="normal_window", control_disposition="numeric_possible", normal_input_exclusion_reasons=[])
    # Rebuild the same real holding diagnostic after binding the producer pins.
    # Its read-scope hash is a component of the typed fingerprint, so merely
    # editing the earlier fixture's read-scope would invalidate quote evidence.
    normal_sha, population_sha, scope_sha = (saved.digest(saved.encoded(value)) for value in (normal, population, scope))
    producer_pins = {role: str(index + 1) * 64 for index, role in enumerate(("br_scope_verification", "br_adjudication", "bi_result"))}
    producer_pins.update(br_scope_result=scope_sha, bl_population_audit=population_sha, normal_input_audit=normal_sha)
    read_scope = captured["read_scope"]
    read_scope.update(input_sha256=producer_pins, normal_input_audit_sha256=normal_sha)
    read_sha = saved.digest(saved.encoded(read_scope))
    fingerprint = saved.strict_json(data["bound"].holding_inputs.fingerprint_json)
    fingerprint["read_scope_sha256"] = read_sha
    quote_sha = saved.digest(saved.encoded(fingerprint))
    audit = captured["holding_audit"]
    audit.update(read_scope_sha256=read_sha, quote_snapshot_sha256=quote_sha)
    audit["scan_metadata"]["typed_snapshot_sha256"] = quote_sha
    audit["fingerprint"] = fingerprint
    for row in audit["rows"]:
        row["provenance"]["typed_snapshot_sha256"] = quote_sha
    quotes = restore_holding_inputs(audit, read_scope, quote_snapshot_sha256=quote_sha,
        activity_snapshot_sha256=pure_fixture.ACTIVITY, read_scope_sha256=read_sha)
    # Disposition changes alter nominee partition fingerprints; recompute the
    # exact proof using the same production nominee projection as the producer.
    nominees, requested, issue_isins = module.diagnostic.nominee_selections(normal, scope, calendar, parameters, dispositions)
    plan = provisional_exit_bounds(nominees["D1"], nominees["D2"], quotes, parameters, requested, issue_isins, input_sha256=producer_pins)
    coverage, evidence = data["reviewed_coverage"], data["basis_evidence"]
    coverage.update(quote_snapshot_sha256=quote_sha, required_scope_sha256=plan["child_scope_sha256"])
    original = data["original_replay"]
    upstream = {role: {"path": "/synthetic/upstream/" + role + ".json", "sha256": "a" * 64}
                for role in module.paired.BASE_ROLES}
    for files in (module.holding.BL_ROLES, module.paired.paired.BM_ROLES):
        label = "bl" if "bl_result" in files else "bm"
        for role, name in files.items():
            upstream[role]["path"] = "/synthetic/upstream/" + label + "/" + name
    upstream["bl_population_audit"]["sha256"] = population_sha
    original_spec = dict(schema=module.paired.SCHEMA, study_id=module.paired.SCHEMA, mode=module.paired.MODE,
        promotion_allowed=False, window=deepcopy(saved.WINDOW), reference_specification=saved.REFERENCE,
        parameters=deepcopy(PARAMETERS), inputs=upstream)
    original.update(input_sha256={role: row["sha256"] for role, row in upstream.items()},
        activity_snapshot_sha256=pure_fixture.ACTIVITY, quote_snapshot_sha256="b" * 64,
        original_quote_snapshot_sha256="c" * 64, read_scope_sha256="d" * 64, action_scope_sha256="e" * 64,
        actual_study_completed=False, **dict.fromkeys(module.calibration.FALSE_FLAGS, False))
    bodies = {"br_scope_result": scope, "br_population_audit": population,
        "br_control_dispositions": dispositions, "br_normal_input_audit": normal,
        "br_read_scope": read_scope, "br_holding_input_audit": audit, "br_provisional_exit_scope": plan,
        "reviewed_action_coverage": coverage, "basis_evidence": evidence,
        "original_activity_report": data["original_report"], "original_activity_replay": original,
        "original_activity_specification": original_spec}
    br_outputs = {name: "a" * 64 for name in module.BR_OUTPUTS}
    for role, name in module.BR_FILES.items():
        if role != "br_result":
            br_outputs[name] = saved.digest(saved.encoded(bodies[role]))
    br_result = dict(schema=module.diagnostic.SCHEMA, status="reviewed_nominee_inputs_audited",
        normal_summary={"reviewed_normal_windows": len(normal)}, holding_summary=audit["summary"],
        provisional_exit_entries=len(plan["entries"]), activity_snapshot_sha256=pure_fixture.ACTIVITY,
        quote_snapshot_sha256=quote_sha, input_sha256=producer_pins, input_manifest=[],
        nominee_scope_meaning=read_scope["scope_kind"], new_scope_required=False, database_access=True, api_access=False,
        new_source_requests=0, automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0,
        **dict.fromkeys(module.diagnostic.FALSE_FLAGS, False))
    for key, name in (("specification_sha256", "specification.json"), ("source_manifest_sha256", "source-manifest.json"),
        ("input_manifest_sha256", "input-manifest.json"), ("resource_manifest_sha256", "resource-manifest.json"),
        ("normal_input_audit_sha256", "normal-input-audit.json"), ("read_scope_sha256", "read-scope.json"),
        ("holding_input_audit_sha256", "holding-input-audit.json"), ("provisional_exit_scope_sha256", "provisional-exit-scope.json")):
        br_result[key] = br_outputs[name]
    br_outputs["result.json"] = saved.digest(saved.encoded(br_result))
    bodies["br_result"] = br_result
    bodies["br_verification"] = dict(schema="br-control-inputs-independent-verification-v1", verified=True,
        scope="new_BR_nominee_normal_population_typed_holding_and_provisional_exit_consistency_only",
        output_sha256=br_outputs, evidence_sha256={role: producer_pins[role] for role in
            ("br_scope_result", "br_scope_verification", "br_adjudication", "bi_result")},
        normal_summary=br_result["normal_summary"], holding_summary=br_result["holding_summary"],
        activity_snapshot_sha256=pure_fixture.ACTIVITY, quote_snapshot_sha256=quote_sha, read_scope_sha256=read_sha,
        provisional_exit_entries=len(plan["entries"]), original_input_files_read=4, new_output_files_read=14,
        automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0,
        **dict.fromkeys(("database_access", "api_access", "source_truth_certified", "price_basis_certified", "raw_source_bodies_opened",
            "source_body_truth_verified", "original_database_transaction_verified", "historical_eligibility_certified",
            "historical_publication_certified", "event_coverage_certified", "no_event_coverage_certified", "complete_control_prefix_proved",
            "compulsory_successor_closure_complete", "books_replayed", "returns_computed", "promotion_allowed"), False))
    files = {name: "a" * 64 for name in ("result.json", *module.calibration.LINK_FILES.values())}
    for role, name in (("original_activity_report", "report.json"), ("original_activity_replay", "paired-replay.json"),
                       ("original_activity_specification", "specification.json")):
        files[name] = saved.digest(saved.encoded(bodies[role]))
    bodies["original_activity_verification"] = dict(schema="bq-raw-paired-independent-verification-v1", verified=True,
        scope="new_seven_outputs_links_and_five_saved_book_headline_metrics_only", file_sha256=files,
        specification_sha256=files["specification.json"], input_sha256=original["input_sha256"],
        **{key: original[key] for key in module.calibration.SNAPSHOTS},
        metrics={"arms": {arm: {key: value for key, value in data["original_report"]["arms"][arm].items() if key != "path"}
            for arm in module.ARMS}}, published_D1_completed_studies=1, published_D2_completed_studies=1,
        D1_completed_studies=0, D2_completed_studies=0, automatic_retries=0, output_files_read=7,
        publication_files_read=2, original_input_files_read=0,
        **dict.fromkeys(module.calibration.VERIFY_TRUE, True),
        **dict.fromkeys((*module.calibration.FALSE_FLAGS, *module.calibration.VERIFY_FALSE), False))
    coverage_sha = saved.digest(saved.encoded(coverage))
    bodies["control_bounds"] = dict(schema="br-control-bound-declarations-v1", parameters=deepcopy(PARAMETERS),
        calendar_sha256=plan["calendar_sha256"], reviewed_action_coverage_sha256=coverage_sha,
        arms={arm: [dict(formation_on=formation.isoformat(), prior_potential_codes=[], successor_codes=[],
            evidence_sha256=coverage_sha)] for arm in module.ARMS})
    root, private, source_dir = (tmp_path / name for name in ("repo", "private", "evidence"))
    root.mkdir()
    private.mkdir(mode=0o700)
    source_dir.mkdir(mode=0o700)
    declared, store = {}, {}
    original_files = {"original_activity_report": "report.json", "original_activity_replay": "paired-replay.json",
        "original_activity_specification": "specification.json"}
    for role, value in bodies.items():
        if role in module.BR_FILES:
            path = source_dir / "br" / module.BR_FILES[role]
        elif role in original_files:
            path = source_dir / "original" / original_files[role]
        else:
            path = source_dir / (role + ".json")
        store[path] = saved.encoded(value)
        declared[role] = dict(path=str(path), sha256=saved.digest(store[path]))
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode=module.MODE, promotion_allowed=False,
        comparison_power_prerequisite_met=False, power_design_passed=False, window=deepcopy(saved.WINDOW),
        reference_specification=saved.REFERENCE, parameters=deepcopy(PARAMETERS), inputs=declared)
    sources = dict(code_version="a" * 40, files=[dict(path=module.SOURCE_PATH, sha256="a" * 64)])
    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_spec
        module.validate_spec(spec, {"parameters": PARAMETERS})
        return sources, saved.encoded(spec), spec
    monkeypatch.chdir(root)
    monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    monkeypatch.setattr(saved, "freeze_sources", freeze)
    for owner, name in ((sqlite3, "connect"), (socket, "create_connection")):
        monkeypatch.setattr(owner, name, lambda *a, **k: pytest.fail("CONTROL caller attempted DB/network IO"))
    return root, private / "run", tmp_path / "runs.jsonl", spec, store, sources


def replace_input(spec, store, role, mutate):
    path = Path(spec["inputs"][role]["path"])
    value = saved.strict_json(store[path])
    mutate(value)
    store[path] = saved.encoded(value)
    spec["inputs"][role]["sha256"] = saved.digest(store[path])


@pytest.mark.parametrize("variant", ["ordinary", "cash", "frozen_entry", "frozen_exit"])
def test_logged_pair_reuses_saved_activity_and_runs_only_each_control_arm_once(tmp_path, monkeypatch, variant):
    root, output, runs, spec, store, _ = fixture(tmp_path, monkeypatch,
        cash_only=variant == "cash", frozen_entry=variant == "frozen_entry", frozen_exit=variant == "frozen_exit")
    before, reads, calls = deepcopy((spec, store)), [], []
    original = module.control.replay_synthetic
    def replay(*args, **kwargs):
        assert statuses(runs) == ["started"] and not (output / "result.json").exists()
        assert len(reads) == len(module.ROLES)
        calls.append(kwargs["timing_policy"].session_lag)
        return original(*args, **kwargs)
    monkeypatch.setattr(module.control, "replay_synthetic", replay)
    def reader(path):
        assert statuses(runs) == ["started"] and (output / "source-manifest.json").exists()
        reads.append(path)
        return store[path]
    record = module.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    result = saved.strict_json((output / "result.json").read_bytes())
    replayed = saved.strict_json((output / "control-replay.json").read_bytes())
    report = saved.strict_json((output / "comparison-report.json").read_bytes())
    assert (spec, store) == before and calls == [1, 2] and statuses(runs) == ["started", "completed"]
    assert len(reads) == len(set(reads)) == 16
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 1
    assert result["logged_paired_execution_completed"] and not result["original_ACTIVITY_replayed"]
    assert replayed["actual_study_completed"] is False and report == replayed["report"]
    assert report["D1_completed_studies"] == report["D2_completed_studies"] == 0
    assert all(result[key] is report[key] is replayed[key] is False for key in module.POWER_FLAGS)
    for field, filename in (("control_replay_sha256", "control-replay.json"), ("comparison_report_sha256", "comparison-report.json"),
        ("source_manifest_sha256", "source-manifest.json"), ("specification_sha256", "specification.json"),
        ("resource_manifest_sha256", "resource-manifest.json"), ("input_manifest_sha256", "input-manifest.json")):
        assert result[field] == record["result"][field] == saved.digest((output / filename).read_bytes())
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


@pytest.mark.parametrize("failure", ["br_receipt", "br_result_pin", "br_scope_pin", "producer_pins", "original_receipt",
    "original_parameters", "original_report", "bound_pin", "successor", "unknown_coverage", "missing_coverage", "read_pin"])
def test_conflicting_saved_inputs_fail_before_any_book_without_retry(tmp_path, monkeypatch, failure):
    root, output, runs, spec, store, _ = fixture(tmp_path, monkeypatch)
    changes = {
        "br_receipt": ("br_verification", lambda v: v.update(verified=False)),
        "br_result_pin": ("br_verification", lambda v: v["output_sha256"].update({"result.json": "f" * 64})),
        "br_scope_pin": ("br_scope_result", lambda v: v.update(activity_snapshot_sha256="f" * 64)),
        "producer_pins": ("br_read_scope", lambda v: v["input_sha256"].update(normal_input_audit="f" * 64)),
        "original_receipt": ("original_activity_verification", lambda v: v.update(hash_links_verified=False)),
        "original_parameters": ("original_activity_specification", lambda v: v["parameters"].update(slots=19)),
        "original_report": ("original_activity_report", lambda v: v.update(initial_nav="2")),
        "bound_pin": ("control_bounds", lambda v: v["arms"]["D1"][0].update(evidence_sha256="f" * 64)),
        "successor": ("control_bounds", lambda v: v["arms"]["D1"][0].update(successor_codes=["999999"])),
        "unknown_coverage": ("reviewed_action_coverage", lambda v: v["windows"][0].update(review_state="unknown")),
        "missing_coverage": ("reviewed_action_coverage", lambda v: v["windows"].pop()),
    }
    if failure == "read_pin":
        store[Path(spec["inputs"]["basis_evidence"]["path"])] += b" "
    else:
        role, mutate = changes[failure]
        replace_input(spec, store, role, mutate)
        if failure in ("unknown_coverage", "missing_coverage"):
            coverage_sha = spec["inputs"]["reviewed_action_coverage"]["sha256"]
            def bind_new_coverage(value):
                value["reviewed_action_coverage_sha256"] = coverage_sha
                for rows in value["arms"].values():
                    for row in rows:
                        row["evidence_sha256"] = coverage_sha
            replace_input(spec, store, "control_bounds", bind_new_coverage)
    monkeypatch.setattr(module.control, "replay_synthetic", lambda *a, **kw: pytest.fail("invalid input reached CONTROL book"))
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    assert statuses(runs) == ["started", "failed"] and not (output / "result.json").exists()
    result = saved.strict_json((output / "failure.json").read_bytes())
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert result["partial_package_preserved"] and result["automatic_retries"] == 0


@pytest.mark.parametrize("failure", ["second_arm", "source_drift", "spec_drift", "read"])
def test_partial_attempt_preserves_manifests_and_never_marks_completion(tmp_path, monkeypatch, failure):
    root, output, runs, spec, store, sources = fixture(tmp_path, monkeypatch)
    reader = store.__getitem__
    calls = []
    if failure == "second_arm":
        original = module.control.replay_synthetic
        def replay(*args, **kwargs):
            calls.append(kwargs["timing_policy"].session_lag)
            if len(calls) == 2:
                raise ValueError("private body must not leak")
            return original(*args, **kwargs)
        monkeypatch.setattr(module.control, "replay_synthetic", replay)
    elif failure == "read":
        def reader(path):
            raise OSError("private body must not leak")
    else:
        states = iter(((sources, saved.encoded(spec), spec),
            ({**sources, "code_version": "b" * 40} if failure == "source_drift" else sources,
             saved.encoded(spec) + (b" " if failure == "spec_drift" else b""), spec)))
        monkeypatch.setattr(saved, "freeze_sources", lambda *a, **kw: next(states))
    with pytest.raises((ValueError, OSError)):
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    assert statuses(runs) == ["started", "failed"] and not (output / "result.json").exists()
    raw = (output / "failure.json").read_bytes()
    result = saved.strict_json(raw)
    assert b"private body" not in raw and result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert result["paired_execution_may_be_partial"] and not result["logged_paired_execution_completed"]
    assert (output / "input-manifest.json").exists() and (output / "resource-manifest.json").exists()
    if failure == "second_arm":
        assert calls == [1, 2]
    if failure.endswith("drift"):
        assert (output / "control-replay.json").exists() and (output / "comparison-report.json").exists()


def test_default_reader_uses_only_population_larger_bound(tmp_path, monkeypatch):
    root, output, runs, spec, store, _ = fixture(tmp_path, monkeypatch)
    larger, ordinary = [], []
    monkeypatch.setattr(module.holding, "_read_population", lambda path: larger.append(path) or store[path])
    monkeypatch.setattr(saved, "read_private", lambda path: ordinary.append(path) or store[path])
    module.run_preflight(root, root / "spec.json", output, runs)
    assert larger == [Path(spec["inputs"]["br_population_audit"]["path"])] and len(ordinary) == 15


def test_large_document_roots_are_released_before_books(tmp_path, monkeypatch):
    root, output, runs, spec, store, _ = fixture(tmp_path, monkeypatch)
    class TrackedDict(dict):
        __slots__ = ("__weakref__",)
    references, read_order = {}, []
    original_json = saved.PinnedInputs.json
    def read_json(self, path, pin, role):
        read_order.append(role)
        if role in module.DEFERRED_ROLES:
            assert "br_population_audit" in references and references["br_population_audit"]() is None
        value = original_json(self, path, pin, role)
        if role in ("br_population_audit", "br_scope_result", "br_result", "br_verification", "br_read_scope", "br_holding_input_audit"):
            value = TrackedDict(value)
            references[role] = weakref.ref(value)
        return value
    monkeypatch.setattr(saved.PinnedInputs, "json", read_json)
    original_count = module.counted_selections_from_br
    def counted(calendar, parameters, scope, population, *args):
        # The production pure contracts deliberately demand exact dict types.
        return original_count(calendar, parameters, dict(scope), dict(population), *args)
    monkeypatch.setattr(module, "counted_selections_from_br", counted)
    original_bind, original_replay = module.bind_control_holding_scope, module.control.replay_bound_control
    def bind(*args, **kwargs):
        assert references["br_population_audit"]() is None
        for key in ("scope", "holding_audit", "read_scope"):
            kwargs[key] = dict(kwargs[key])
        return original_bind(*args, **kwargs)
    def replay(*args, **kwargs):
        assert all(reference() is None for reference in references.values())
        return original_replay(*args, **kwargs)
    monkeypatch.setattr(module, "bind_control_holding_scope", bind)
    monkeypatch.setattr(module.control, "replay_bound_control", replay)
    module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    assert read_order == [role for role in module.ROLES if role not in ("br_population_audit", *module.DEFERRED_ROLES)] \
        + ["br_population_audit", *module.DEFERRED_ROLES]


def test_cli_keeps_fixed_cpu_and_wall_limits_with_external_address_space_wrapper(monkeypatch, capsys):
    limits, alarms = [], []
    monkeypatch.setattr(module.resource, "setrlimit", lambda kind, value: limits.append((kind, value)))
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    monkeypatch.setattr(module.signal, "alarm", lambda seconds: alarms.append(seconds))
    monkeypatch.setattr(module, "run_preflight", lambda *args: {"result": {"status": "synthetic"}})
    assert module.main(["--spec", "/synthetic/spec.json", "--output-dir", "/synthetic/output", "--runs-path", "/synthetic/runs.jsonl"]) == 0
    assert limits == [(module.resource.RLIMIT_CPU, (300, 300))] and alarms == [900, 0]
    assert saved.strict_json(capsys.readouterr().out.encode())["status"] == "synthetic"


@pytest.mark.parametrize("change", ["parameter_type", "power", "promotion", "extra", "alias", "relative", "filename"])
def test_fixed_spec_rejects_policy_paths_and_roles_before_trial(tmp_path, monkeypatch, change):
    root, output, runs, spec, _, _ = fixture(tmp_path, monkeypatch)
    if change == "parameter_type": spec["parameters"]["slippage_bps_per_side"] = 5
    elif change == "power": spec["power_design_passed"] = True
    elif change == "promotion": spec["promotion_allowed"] = True
    elif change == "extra": spec["inputs"]["scan_database"] = dict(path="/synthetic/db", sha256="a" * 64)
    elif change == "alias": spec["inputs"]["basis_evidence"] = deepcopy(spec["inputs"]["reviewed_action_coverage"])
    elif change == "relative": spec["inputs"]["basis_evidence"]["path"] = "relative.json"
    else: spec["inputs"]["br_population_audit"]["path"] += ".renamed"
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs, reader=lambda p: pytest.fail("invalid spec read inputs"))
    assert not output.exists() and not runs.exists()


def test_wrong_source_output_and_log_aliases_cannot_reach_inputs(tmp_path, monkeypatch):
    root, output, runs, spec, _, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "__file__", str(root / "wrong.py"))
    with pytest.raises(ValueError, match="committed CONTROL source"):
        module.run_preflight(root, root / "spec.json", output, runs)
    monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    for aliased in (Path(spec["inputs"]["basis_evidence"]["path"]), root / saved.REFERENCE):
        with pytest.raises(ValueError):
            module.run_preflight(root, root / "spec.json", output, aliased, reader=lambda p: pytest.fail("alias read"))
    output.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="already exists"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=lambda p: pytest.fail("existing output read"))
    assert not runs.exists()
