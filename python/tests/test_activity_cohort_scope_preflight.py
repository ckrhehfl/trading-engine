"""Synthetic BS metadata frontiers and durable private logging; no actual data."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import socket
import sqlite3
import stat
from types import SimpleNamespace

import pytest

from research import activity_cohort_scope_preflight as module
from research import activity_control_preflight as prior
from research import activity_preflight as saved
from research.activity_cohort_selection import rank_control_codes
from research.activity_control_selection import _rank, activity_targets_fingerprint, population_fingerprint
from research.activity_pair_calibration import PARAMETERS
from research.activity_timing import SessionLagPolicy
import test_activity_control_replay_connection as replay_fixture


def metadata_window(formation, code, isin, calendar, index):
    required = [day.isoformat() for day in calendar[index - 60:index + 1]]
    return dict(formation=formation.strftime("%Y%m%d"), code=code, isin=isin,
        activity_reason="numeric_possible", first_required_observation=required[0],
        normal_candidate_start=required[0], required_dates=required,
        normal_dates=required[:-1], frozen_dates=[], formation_state="observed",
        listing_date=required[0], selection_at={arm: SessionLagPolicy(lag, paired=True)
            .selection_at(calendar, index).isoformat() for arm, lag in (("D1", 1), ("D2", 2))},
        krx_metadata_provenance={"synthetic": True}, source_metadata_refs={"synthetic": "a" * 64})


def frontier_fixture(*, unknown=True, target_count=2, second_formation=False):
    """Small complete population; pure projection has no actual-count override."""
    calendar = tuple(date(2020, 1, 1) + timedelta(days=index) for index in range(400))
    codes = tuple(f"{number:06d}" for number in range(1, 9))
    identities = tuple((code, "KR7" + code + "000") for code in codes)
    indices = (60, 186) if second_formation else (60,)
    scope = dict(activity_snapshot_sha256="a" * 64, formations=[])
    population, dispositions, normal, selections = {"rows": []}, [], [], {arm: [] for arm in module.ARMS}
    for index in indices:
        formation = calendar[index]
        order = rank_control_codes(codes, control_id="C01", formation_on=formation)
        excluded, unresolved = order[:2]
        eligible = tuple(code for code in codes if code not in (excluded, unresolved))
        targets = tuple(_rank(eligible, formation, PARAMETERS["seed"])[:target_count])
        numeric = []
        for code, isin in identities:
            state = "excluded" if code == excluded else "unresolved" if code == unresolved and unknown else "eligible"
            control = "cap_below" if state == "excluded" else "numeric_possible"
            population["rows"].append(dict(formation=formation.strftime("%Y%m%d"), code=code, isin=isin,
                disposition="cap_below" if state == "excluded" else "normal_window"))
            dispositions.append(dict(formation=formation.strftime("%Y%m%d"), code=code, isin=isin,
                prior_disposition="cap_below" if state == "excluded" else "normal_window",
                control_disposition=control, normal_input_disposition=state,
                normal_input_exclusion_reasons=[control] if state == "excluded" else None if state == "unresolved" else []))
            if control == "numeric_possible":
                numeric.append(metadata_window(formation, code, isin, calendar, index))
            if state == "eligible":
                normal.append(dict(formation=formation.isoformat(), code=code, isin=isin, synthetic=True))
        scope["formations"].append(dict(formation=formation.strftime("%Y%m%d"), ranked_numeric_possible=numeric))
        for arm, lag in (("D1", 1), ("D2", 2)):
            policy = SessionLagPolicy(lag, paired=True)
            decision, cutoff = policy.execution_on(calendar, index), policy.selection_at(calendar, index)
            selections[arm].append(SimpleNamespace(formation_on=formation, decision_on=decision, selection_at=cutoff,
                seed=PARAMETERS["seed"], activity_targets=targets,
                activity_targets_sha256=activity_targets_fingerprint(arm=arm, formation_on=formation,
                    decision_on=decision, selection_at=cutoff, targets=targets, seed=PARAMETERS["seed"]),
                source_population=identities, source_population_count=len(identities),
                source_population_sha256=population_fingerprint(identities), source_partition_sha256="b" * 64))
    return scope, population, dispositions, normal, selections, deepcopy(PARAMETERS)


def test_unknown_is_potential_and_never_excluded_or_final_target():
    arguments = frontier_fixture()
    before = deepcopy(arguments)
    result = module.plan_frontiers(*arguments)
    formation = arguments[4]["D1"][0].formation_on
    order = rank_control_codes(tuple(code for code, _ in arguments[4]["D1"][0].source_population),
        control_id="C01", formation_on=formation)
    control = result["formations"][0]["controls"]["C01"]["D1"]
    assert [row["code"] for row in control["prefix"]] == list(order[:3])
    assert control["known_excluded_codes"] == [order[0]] and control["unknown_codes"] == [order[1]]
    assert control["potentially_eligible_codes"] == list(order[1:3])
    assert control["target_codes"] is None and control["complete_target_prefix"] is False
    assert not result["complete_target_prefix"] and not result["frontier_complete"]
    assert len(result["population_partition"]) == 8
    required = {row["code"]: row for row in result["needed_normal_windows"]}
    assert required[order[1]]["positive_operating_normal_adjudication_required"] is True
    assert set(arguments[4]["D1"][0].activity_targets) <= set(required)
    assert result["summary"]["unresolved_normal_windows"] == 1
    assert arguments == before


def test_new_exact_exclusion_requires_frontier_to_advance():
    arguments = list(frontier_fixture())
    initial = module.plan_frontiers(*arguments)
    first = initial["formations"][0]["controls"]["C01"]["D1"]
    unknown = first["unknown_codes"][0]
    row = next(row for row in arguments[2] if row["code"] == unknown)
    row.update(normal_input_disposition="excluded", normal_input_exclusion_reasons=["synthetic_normal_failure"])
    arguments[3].append(dict(formation=date.fromisoformat(row["formation"]).isoformat(),
        code=unknown, isin=row["isin"], synthetic=True))
    advanced = module.plan_frontiers(*arguments)["formations"][0]["controls"]["C01"]["D1"]
    assert len(advanced["prefix"]) == len(first["prefix"]) + 1
    assert advanced["known_excluded_codes"] == [first["known_excluded_codes"][0], unknown]
    assert advanced["complete_target_prefix"] and len(advanced["target_codes"]) == 2


def test_unknown_tail_remains_complete_even_when_prefix_resolved():
    arguments = list(frontier_fixture(unknown=False, target_count=1))
    before = module.plan_frontiers(*arguments)
    used = {row["code"] for row in before["needed_normal_windows"]}
    tail = next(code for code, _ in arguments[4]["D1"][0].source_population if code not in used
        and next(row for row in arguments[2] if row["code"] == code)["normal_input_disposition"] == "eligible")
    next(row for row in arguments[2] if row["code"] == tail).update(
        normal_input_disposition="unresolved", normal_input_exclusion_reasons=None)
    arguments[3] = [row for row in arguments[3] if row["code"] != tail]
    result = module.plan_frontiers(*arguments)
    assert result["complete_target_prefix"]
    assert next(row for row in result["population_partition"] if row["code"] == tail)["normal_input_disposition"] == "unresolved"
    assert all(row["code"] != tail for row in result["needed_normal_windows"])
    assert any(control["D1"]["unknown_tail_count"] == 1 for control in result["formations"][0]["controls"].values())


def test_independent_formations_have_no_prior_held_exclusion():
    result = module.plan_frontiers(*frontier_fixture(unknown=False, second_formation=True))
    for formation in result["formations"]:
        day = date.fromisoformat(formation["formation"])
        population = tuple(row["code"] for row in result["population_partition"] if row["formation"] == day.strftime("%Y%m%d"))
        for control_id, arms in formation["controls"].items():
            for control in arms.values():
                order = rank_control_codes(population, control_id=control_id, formation_on=day)
                assert [row["code"] for row in control["prefix"]] == list(order[:len(control["prefix"])])
                assert control["assigned_capital_fraction"] == {"numerator": 2, "denominator": 20}
    assert not result["control_targets_frozen"] and not result["potential_holding_origins_bound_complete"]


def test_zero_ceiling_reads_no_normal_frontier_and_preserves_all_unknown_tail():
    result = module.plan_frontiers(*frontier_fixture(target_count=0))
    assert result["needed_normal_windows"] == [] and len(result["population_partition"]) == 8
    for arms in result["formations"][0]["controls"].values():
        assert arms["D1"]["prefix"] == [] and arms["D1"]["target_codes"] == []
        assert arms["D1"]["unknown_tail_count"] == 1


def canonical_spec():
    return dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode=module.MODE, promotion_allowed=False,
        comparison_power_prerequisite_met=False, power_design_passed=False, window=deepcopy(saved.WINDOW),
        reference_specification=saved.REFERENCE, parameters=deepcopy(PARAMETERS),
        controls=list(module.CONTROL_IDS), design=module.DESIGN, inputs=deepcopy(module.INPUTS))


def test_committed_source_spec_and_original_sixteen_pins_agree():
    root = Path(__file__).resolve().parents[2]
    spec = saved.strict_json((root / "configs/research/discovery/activity-cohort-scope-v1.json").read_bytes())
    old = saved.strict_json((root / "configs/research/discovery/activity-control-comparison-v1.json").read_bytes())
    module.validate_spec(spec, {"parameters": PARAMETERS})
    assert spec["inputs"] == old["inputs"] == module.INPUTS
    assert len(module.READ_ROLES) == 13 and set(module.UNREAD_ROLES) == set(module.INPUTS) - set(module.READ_ROLES)


@pytest.mark.parametrize("corruption", ["control_order", "extra_control", "design", "pin", "path", "power", "promotion", "parameter"])
def test_fixed_spec_refuses_design_controls_pins_and_parameter_changes(corruption):
    spec = canonical_spec()
    if corruption == "control_order": spec["controls"].reverse()
    elif corruption == "extra_control": spec["controls"].append("C06")
    elif corruption == "design": spec["design"] = "different"
    elif corruption == "pin": spec["inputs"]["br_result"]["sha256"] = "0" * 64
    elif corruption == "path": spec["inputs"]["br_result"]["path"] = "/different/result.json"
    elif corruption == "power": spec["power_design_passed"] = True
    elif corruption == "promotion": spec["promotion_allowed"] = True
    else: spec["parameters"]["slots"] = 19
    with pytest.raises(ValueError, match="fixed BS"):
        module.validate_spec(spec, {"parameters": PARAMETERS})


def statuses(path):
    return [saved.strict_json(line)["status"] for line in path.read_bytes().splitlines()]


def logging_fixture(tmp_path, monkeypatch):
    root, private, evidence = (tmp_path / name for name in ("repo", "private", "evidence"))
    root.mkdir()
    private.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    spec, store = canonical_spec(), {}
    for role in spec["inputs"]:
        path, raw = evidence / (role + ".json"), saved.encoded({"role": role})
        spec["inputs"][role] = dict(path=str(path), sha256=saved.digest(raw))
        store[path] = raw
    sources = dict(code_version="a" * 40, files=[dict(path=module.SOURCE_PATH, sha256="a" * 64)])
    monkeypatch.chdir(root)
    monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_spec
        return deepcopy(sources), saved.encoded(spec), deepcopy(spec)
    def evaluate(spec, inputs):
        for role in module.READ_ROLES:
            inputs.json(Path(spec["inputs"][role]["path"]), spec["inputs"][role]["sha256"], role)
        return module.plan_frontiers(*frontier_fixture())
    return root, private / "run", tmp_path / "runs.jsonl", spec, store, sources, freeze, evaluate


def test_durable_start_precedes_all_thirteen_reads_and_private_manifests(tmp_path, monkeypatch):
    root, output, runs, spec, store, _, freeze, evaluate = logging_fixture(tmp_path, monkeypatch)
    before, reads = deepcopy(store), []
    def reader(path):
        assert statuses(runs) == ["started"] and (output / "source-manifest.json").exists()
        assert saved.strict_json(store[path])["role"] not in module.UNREAD_ROLES
        reads.append(path)
        return store[path]
    for owner, name in ((sqlite3, "connect"), (socket, "create_connection")):
        monkeypatch.setattr(owner, name, lambda *a, **k: pytest.fail("metadata caller attempted DB/network"))
    record = module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze, evaluator=evaluate)
    result = saved.strict_json((output / "result.json").read_bytes())
    manifest = saved.strict_json((output / "input-manifest.json").read_bytes())
    assert statuses(runs) == ["started", "completed"] and len(reads) == len(set(reads)) == 13
    assert [row["role"] for row in manifest] == list(module.READ_ROLES) and store == before
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert result["books_replayed"] is result["returns_computed"] is False
    for field, filename in (("source_manifest_sha256", "source-manifest.json"), ("specification_sha256", "specification.json"),
        ("resource_manifest_sha256", "resource-manifest.json"), ("input_manifest_sha256", "input-manifest.json")):
        assert result[field] == record["result"][field] == saved.digest((output / filename).read_bytes())
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


def test_dirty_source_refused_before_any_log_body_or_output(tmp_path, monkeypatch):
    root, output, runs, _, _, _, _, evaluate = logging_fixture(tmp_path, monkeypatch)
    def dirty(*args, **kwargs):
        raise ValueError("full Python source must be clean")
    with pytest.raises(ValueError, match="clean"):
        module.run_preflight(root, root / "spec.json", output, runs, freeze=dirty, evaluator=evaluate,
            reader=lambda path: pytest.fail("read before dirty source refusal"))
    assert not output.exists() and not runs.exists()


@pytest.mark.parametrize("alias", ["input_log", "source_log", "input_output", "log_in_output", "existing_output"])
def test_alias_and_reused_output_rejected_without_log_or_body_read(tmp_path, monkeypatch, alias):
    root, output, runs, spec, _, _, freeze, evaluate = logging_fixture(tmp_path, monkeypatch)
    if alias == "input_log": runs = Path(spec["inputs"]["br_result"]["path"])
    elif alias == "source_log": runs = root / module.SOURCE_PATH
    elif alias == "input_output": output = Path(spec["inputs"]["br_result"]["path"]).parent / "new"
    elif alias == "log_in_output": runs = output / "runs.jsonl"
    else: output.mkdir(mode=0o700)
    existed = output.exists()
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs, freeze=freeze, evaluator=evaluate,
            reader=lambda path: pytest.fail("read before alias refusal"))
    assert output.exists() == existed and not runs.exists()


@pytest.mark.parametrize("failure", ["hash", "evaluation", "source_change"])
def test_logged_failure_preserves_attempts_and_blocks_retry(tmp_path, monkeypatch, failure):
    root, output, runs, _, store, sources, freeze, evaluate = logging_fixture(tmp_path, monkeypatch)
    calls = 0
    def freezer(*args, **kwargs):
        nonlocal calls
        calls += 1
        manifest, raw, spec = freeze(*args, **kwargs)
        if failure == "source_change" and calls == 2: manifest["code_version"] = "b" * 40
        return manifest, raw, spec
    if failure == "hash":
        first = next(path for path, raw in store.items() if saved.strict_json(raw)["role"] == module.READ_ROLES[0])
        store[first] = b"changed bytes"
    if failure == "evaluation":
        def evaluate(spec, inputs):
            raise RuntimeError("synthetic private message must not be saved")
    with pytest.raises((ValueError, RuntimeError)):
        module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__, freeze=freezer, evaluator=evaluate)
    failure_raw = (output / "failure.json").read_bytes()
    retained = saved.strict_json(failure_raw)
    assert statuses(runs) == ["started", "failed"] and not (output / "result.json").exists()
    assert retained["partial_package_preserved"] and retained["automatic_retries"] == 0
    assert b"synthetic private message" not in failure_raw
    assert (output / "resource-manifest.json").exists() and (output / "input-manifest.json").exists()
    with pytest.raises(ValueError, match="already exists"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__, freeze=freeze, evaluator=evaluate)
    assert statuses(runs) == ["started", "failed"]


def package_fixture():
    """Produce complete fabricated BR/BQ receipts for the real validation path.

    Replay calls only prepare saved toy BQ inputs here. The caller under test
    gets no book callback and only thirteen in-memory byte streams.
    """
    data = replay_fixture.fixture()
    calendar = data["bound"].holding_inputs.calendar
    formation, compact = calendar[60], calendar[60].strftime("%Y%m%d")
    identities = data["bound"].selections_d1[0].source_population
    targets = data["bound"].selections_d1[0].activity_targets
    population = dict(schema="activity-normal-population-audit-v1", rows=[
        dict(formation=compact, code=code, isin=isin, disposition="normal_window") for code, isin in identities])
    partition = [dict(formation=compact, code=code, isin=isin,
        prior_disposition="normal_window", control_disposition="numeric_possible",
        normal_input_disposition="eligible", normal_input_exclusion_reasons=[]) for code, isin in identities]
    normal = [dict(formation=formation.isoformat(), code=code, isin=isin, control_arms={
        arm: dict(eligible=True, exclusion_reasons=[], selection_at=SessionLagPolicy(lag, paired=True)
            .selection_at(calendar, 60).isoformat(), decision_on=SessionLagPolicy(lag, paired=True)
            .execution_on(calendar, 60).isoformat()) for arm, lag in (("D1", 1), ("D2", 2))})
        for code, isin in identities]
    scope = dict(schema="activity-control-scope-v1", status="complete_control_rank_metadata_planned",
        activity_snapshot_sha256="a" * 64, summary={"source_rows": len(identities)},
        population_partition=[{name: row[name] for name in ("formation", "code", "isin", "prior_disposition", "control_disposition")}
            for row in partition], formations=[dict(formation=compact,
                ranked_numeric_possible=[metadata_window(formation, code, isin, calendar, 60) for code, isin in identities],
                activity_preopening_targets={arm: list(targets) for arm in module.ARMS},
                activity_preopening_k={arm: len(targets) for arm in module.ARMS})],
        **dict.fromkeys(("frontier_complete", "compulsory_successor_closure_complete", "control_targets_frozen",
            "potential_holding_origins_bound_complete", "holding_selection_executed", "returns_computed"), False))
    bodies = dict(br_scope_result=scope, br_population_audit=population,
        br_control_dispositions=partition, br_normal_input_audit=normal,
        br_provisional_exit_scope={"entries": [], "calendar_sha256": "a" * 64})
    digest = lambda value: saved.digest(saved.encoded(value))
    producer = dict(br_scope_result=digest(scope), bl_population_audit=digest(population), normal_input_audit=digest(normal),
        br_scope_verification="1" * 64, br_adjudication="2" * 64, bi_result="3" * 64)
    bodies["br_read_scope"] = dict(calendar_dates=[day.strftime("%Y%m%d") for day in calendar],
        input_sha256=producer, normal_input_audit_sha256=digest(normal))
    outputs = {name: "a" * 64 for name in prior.BR_OUTPUTS}
    for role, name in prior.BR_FILES.items():
        if role in bodies: outputs[name] = digest(bodies[role])
    result = dict(schema=prior.diagnostic.SCHEMA, status="reviewed_nominee_inputs_audited",
        new_scope_required=False, database_access=True, api_access=False, input_sha256=producer,
        activity_snapshot_sha256="a" * 64, quote_snapshot_sha256="b" * 64,
        normal_summary={"synthetic": 6}, holding_summary={"synthetic": 6}, provisional_exit_entries=0,
        automatic_retries=0, new_source_requests=0, D1_completed_studies=0, D2_completed_studies=0,
        **dict.fromkeys(prior.diagnostic.FALSE_FLAGS, False))
    for key, filename in (("specification_sha256", "specification.json"), ("source_manifest_sha256", "source-manifest.json"),
        ("input_manifest_sha256", "input-manifest.json"), ("resource_manifest_sha256", "resource-manifest.json"),
        ("normal_input_audit_sha256", "normal-input-audit.json"), ("read_scope_sha256", "read-scope.json"),
        ("holding_input_audit_sha256", "holding-input-audit.json"), ("provisional_exit_scope_sha256", "provisional-exit-scope.json")):
        result[key] = outputs[filename]
    bodies["br_result"] = result
    outputs["result.json"] = digest(result)
    bodies["br_verification"] = dict(schema="br-control-inputs-independent-verification-v1", verified=True,
        scope="new_BR_nominee_normal_population_typed_holding_and_provisional_exit_consistency_only",
        output_sha256=outputs, evidence_sha256={role: producer[role] for role in
            ("br_scope_result", "br_scope_verification", "br_adjudication", "bi_result")},
        normal_summary=result["normal_summary"], holding_summary=result["holding_summary"],
        activity_snapshot_sha256="a" * 64, quote_snapshot_sha256="b" * 64, read_scope_sha256=result["read_scope_sha256"],
        provisional_exit_entries=0, automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0,
        original_input_files_read=4, new_output_files_read=14,
        **dict.fromkeys(("database_access", "api_access", "source_truth_certified", "price_basis_certified",
            "raw_source_bodies_opened", "source_body_truth_verified", "original_database_transaction_verified",
            "historical_eligibility_certified", "historical_publication_certified", "event_coverage_certified",
            "no_event_coverage_certified", "complete_control_prefix_proved", "compulsory_successor_closure_complete",
            "books_replayed", "returns_computed", "promotion_allowed"), False))
    upstream = {role: {"path": "/synthetic/upstream/" + role + ".json", "sha256": "a" * 64}
        for role in prior.paired.BASE_ROLES}
    for files in (prior.holding.BL_ROLES, prior.paired.paired.BM_ROLES):
        package = "bl" if "bl_result" in files else "bm"
        for role, filename in files.items(): upstream[role]["path"] = "/synthetic/upstream/" + package + "/" + filename
    upstream["bl_population_audit"]["sha256"] = digest(population)
    original = data["original_replay"]
    original.update(input_sha256={role: row["sha256"] for role, row in upstream.items()},
        activity_snapshot_sha256="a" * 64, quote_snapshot_sha256="b" * 64, original_quote_snapshot_sha256="c" * 64,
        read_scope_sha256="d" * 64, action_scope_sha256="e" * 64,
        actual_study_completed=False, **dict.fromkeys(prior.calibration.FALSE_FLAGS, False))
    bodies.update(original_activity_report=data["original_report"], original_activity_replay=original,
        original_activity_specification=dict(schema=prior.paired.SCHEMA, study_id=prior.paired.SCHEMA,
            mode=prior.paired.MODE, promotion_allowed=False, window=deepcopy(saved.WINDOW),
            reference_specification=saved.REFERENCE, parameters=deepcopy(PARAMETERS), inputs=upstream))
    files = {name: "a" * 64 for name in ("result.json", *prior.calibration.LINK_FILES.values())}
    for role, filename in (("original_activity_report", "report.json"), ("original_activity_replay", "paired-replay.json"),
        ("original_activity_specification", "specification.json")):
        files[filename] = digest(bodies[role])
    bodies["original_activity_verification"] = dict(schema="bq-raw-paired-independent-verification-v1", verified=True,
        scope="new_seven_outputs_links_and_five_saved_book_headline_metrics_only", file_sha256=files,
        specification_sha256=files["specification.json"], input_sha256=original["input_sha256"],
        **{key: original[key] for key in prior.calibration.SNAPSHOTS},
        metrics={"arms": {arm: {key: value for key, value in data["original_report"]["arms"][arm].items() if key != "path"}
            for arm in module.ARMS}}, published_D1_completed_studies=1, published_D2_completed_studies=1,
        D1_completed_studies=0, D2_completed_studies=0, automatic_retries=0, output_files_read=7,
        publication_files_read=2, original_input_files_read=0,
        **dict.fromkeys(prior.calibration.VERIFY_TRUE, True),
        **dict.fromkeys((*prior.calibration.FALSE_FLAGS, *prior.calibration.VERIFY_FALSE), False))
    bodies["control_bounds"] = dict(schema="br-control-bound-declarations-v1", parameters=deepcopy(PARAMETERS),
        calendar_sha256="a" * 64, reviewed_action_coverage_sha256="a" * 64,
        arms={arm: [dict(formation_on=formation.isoformat(), prior_potential_codes=[], successor_codes=[],
            evidence_sha256="a" * 64)] for arm in module.ARMS})
    spec, store = canonical_spec(), {}
    for role, row in spec["inputs"].items():
        row["path"] = "/synthetic/saved/" + role + ".json"
        row["sha256"] = digest(bodies[role]) if role in bodies else "a" * 64
        if role in bodies: store[Path(row["path"])] = saved.encoded(bodies[role])
    return spec, store


def test_real_saved_package_validation_reads_only_thirteen_roles_without_new_books(monkeypatch):
    spec, store = package_fixture()
    before, reads = deepcopy(store), []
    def reader(path):
        reads.append(path)
        return store[path]
    for owner, name in ((sqlite3, "connect"), (socket, "create_connection")):
        monkeypatch.setattr(owner, name, lambda *a, **k: pytest.fail("metadata validator attempted DB/network"))
    # Replace only this new module's alias. Existing validators remain real;
    # the actual replay and holding binder are absent from the local surface.
    monkeypatch.setattr(module, "original", SimpleNamespace(_verify_br=prior._verify_br,
        _bounds=prior._bounds, _verify_original=prior._verify_original))
    inputs = saved.PinnedInputs(reader)
    result = module.evaluate_inputs(spec, inputs)
    assert len(reads) == len(set(reads)) == 13 and store == before
    assert {row["role"] for row in inputs.manifest} == set(module.READ_ROLES)
    assert result["original_BR_partition_consistency_verified"] and result["original_ACTIVITY_saved_metrics_verified"]
    assert result["complete_target_prefix"] and result["summary"]["source_rows"] == 6
    assert result["declared_unread_roles"] == list(module.UNREAD_ROLES)
    assert result["books_replayed"] is result["returns_computed"] is False
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0


@pytest.mark.parametrize("role", ["br_verification", "br_normal_input_audit", "original_activity_verification"])
def test_real_package_metadata_corruption_is_rejected(role):
    spec, store = package_fixture()
    path = Path(spec["inputs"][role]["path"])
    value = saved.strict_json(store[path])
    if role == "br_normal_input_audit": value[0]["control_arms"]["D2"]["eligible"] = False
    else: value["verified"] = False
    store[path] = saved.encoded(value)
    spec["inputs"][role]["sha256"] = saved.digest(store[path])
    with pytest.raises(ValueError):
        module.evaluate_inputs(spec, saved.PinnedInputs(store.__getitem__))
