"""Synthetic BS normal recomputation, whole partitions and one logged frontier."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from research import activity_cohort_normal_preflight as module
from research import activity_cohort_scope_preflight as frontier
from research import activity_preflight as saved
from research.activity_cohort_selection import CohortSelection, rank_control_codes
from research.activity_control_normal_inputs import assemble_control_normal_inputs
from research.activity_control_selection import activity_targets_fingerprint, population_fingerprint
from research.activity_pair_calibration import PARAMETERS
from research.activity_timing import SessionLagPolicy
from test_activity_control_normal_inputs import fixture as normal_fixture
from test_activity_normal_assembly import fixture as window_fixture


def fixture(*, zero=False):
    data = normal_fixture()
    calendar, params, original_scope, population = (data[key] for key in ("calendar", "params", "scope", "population_audit"))
    formation = calendar[60]
    for number in range(6, 5000):
        tail = f"{number:06d}"
        orders = [rank_control_codes(("000001", "000002", tail), control_id=control, formation_on=formation)
            for control in module.CONTROL_IDS]
        if all(order[0] != tail for order in orders) and any(order == ("000001", tail, "000002") for order in orders): break
    else: raise AssertionError("synthetic tail search exhausted")
    row = deepcopy(population["rows"][1]); row.update(code=tail, isin="KR7" + tail + "000")
    row["krx_metadata_provenance"]["code"] = tail
    population["rows"].append(row)
    partition = deepcopy(original_scope["population_partition"][1]); partition.update(code=tail, isin=row["isin"])
    original_scope["population_partition"].append(partition)
    numeric = deepcopy(original_scope["formations"][0]["ranked_numeric_possible"][1])
    numeric.update(code=tail, isin=row["isin"], rank=3); numeric["krx_metadata_provenance"]["code"] = tail
    original_scope["formations"][0]["ranked_numeric_possible"].append(numeric)
    original_scope["summary"].update(source_rows=7, numeric_possible_windows=3)
    original_scope["activity_snapshot_sha256"] = "a" * 64
    for candidate in original_scope["formations"][0]["ranked_numeric_possible"]:
        candidate.update(activity_reason="numeric_possible", first_required_observation=candidate["required_dates"][0],
            normal_candidate_start=candidate["normal_dates"][0], formation_state="observed",
            selection_at={arm: SessionLagPolicy(lag, paired=True).selection_at(calendar, 60).isoformat()
                for arm, lag in (("D1", 1), ("D2", 2))},
            source_metadata_refs={field: "m0000" for field in
                ("capitalization_availability", "classification_availability", "trade_section_availability")})
    record = deepcopy(data["accepted_records"][0]); record["code"] = "000002"
    accepted = record["required_windows"][0]
    accepted.update(window_id="20200301/000002", isin="KR7000002000")
    accepted["krx_metadata_provenance"]["code"] = "000002"
    source = deepcopy(data["potential_sources"][("20200301", "000001")])
    source.update(isin="KR7000002000"); source["krx_metadata_provenance"]["code"] = "000002"
    observations = deepcopy(data["observations"])
    observations["000002"] = deepcopy(observations["000001"])
    prior = assemble_control_normal_inputs(calendar, original_scope, population, [record], observations,
        {("20200301", "000002"): source}, params)
    identities = tuple(sorted((row["code"], row["isin"]) for row in population["rows"]))
    partition_sha = saved.digest(saved.encoded(prior["dispositions"]))
    declarations = {}
    for arm, lag in (("D1", 1), ("D2", 2)):
        policy = SessionLagPolicy(lag, paired=True)
        decision, cutoff = policy.execution_on(calendar, 60), policy.selection_at(calendar, 60)
        targets = () if zero else ("000002",)
        declarations[arm] = (CohortSelection("ACTIVITY", arm, formation, decision, cutoff, params["seed"], targets,
            activity_targets_fingerprint(arm=arm, formation_on=formation, decision_on=decision, selection_at=cutoff,
                targets=targets, seed=params["seed"]), identities, len(identities), population_fingerprint(identities), partition_sha, ()),)
    bs_scope = frontier.plan_frontiers(original_scope, population, prior["dispositions"], prior["normal_input_audit"], declarations, params)
    records = [] if zero else [deepcopy(data["accepted_records"][0]), record]
    sources = {} if zero else {**deepcopy(data["potential_sources"]), ("20200301", "000002"): source}
    return dict(calendar=calendar, original_scope=original_scope, population=population,
        previous_dispositions=prior["dispositions"], previous_normal_audits=prior["normal_input_audit"],
        bs_scope=bs_scope, ledger={"records": records}, observations=observations, potential_sources=sources, parameters=params), tail


def test_exact_needed_union_recomputed_with_original_activity_predicate_removed_only():
    data, tail = fixture(); before = deepcopy(data)
    delta = module.assemble_delta(**data)
    planned = module.updated_frontier(data["original_scope"], data["population"], data["bs_scope"], delta, data["parameters"])
    assert data == before and len(delta["normal_input_delta"]) == 2 and len(delta["dispositions"]) == 7
    assert next(row for row in delta["dispositions"] if row["code"] == tail)["normal_input_disposition"] == "unresolved"
    assert all(row["control_arms"]["D1"]["eligible"] for row in delta["normal_input_delta"])
    assert all(row["activity_screen"]["arms"]["D1"]["selected"] is False for row in delta["normal_input_delta"])
    assert delta["new_normal_exclusions"] == [] and planned["complete_target_prefix"]
    assert planned["normal_input_audit_sha256"] == saved.digest(saved.encoded(delta["normal_input_audit"]))
    assert planned["control_dispositions_sha256"] == saved.digest(saved.encoded(delta["dispositions"]))
    assert not planned["control_targets_frozen"] and not planned["books_replayed"] and not planned["returns_computed"]


def test_new_normal_exclusion_retained_and_exposes_unknown_frontier_without_retry():
    data, tail = fixture()
    source = data["potential_sources"][("20200301", "000001")]
    source["liquidity"] = {day: replace(row, turnover=Decimal(1)) for day, row in source["liquidity"].items()}
    delta = module.assemble_delta(**data)
    assert delta["new_normal_exclusions"] == [{"formation": "2020-03-01", "code": "000001", "isin": "KR7000001000",
        "exclusion_reasons": ["absolute_liquidity_fail"]}]
    planned = module.updated_frontier(data["original_scope"], data["population"], data["bs_scope"], delta, data["parameters"])
    assert not planned["complete_target_prefix"] and planned["new_normal_exclusions"] == delta["new_normal_exclusions"]
    assert any(tail in arms["D1"]["unknown_codes"] for arms in planned["formations"][0]["controls"].values())
    assert planned["formations"][0]["ACTIVITY"]["D1"]["target_codes"] == ["000002"]


def test_empty_needed_union_preserves_prior_known_audit_and_entire_unknown_tail():
    data, tail = fixture(zero=True)
    delta = module.assemble_delta(**data)
    assert delta["normal_input_delta"] == [] and delta["normal_input_audit"] == data["previous_normal_audits"]
    assert delta["dispositions"] == data["previous_dispositions"] and len(delta["dispositions"]) == 7
    assert delta["summary"]["preserved_prior_normal_windows"] == delta["summary"]["merged_normal_audit_windows"] == 1
    assert delta["summary"]["unresolved_numeric_windows"] == 2
    planned = module.updated_frontier(data["original_scope"], data["population"], data["bs_scope"], delta, data["parameters"])
    assert planned["needed_normal_windows"] == [] and planned["summary"]["normal_disposition_counts"]["unresolved"] == 2


def context_fixture():
    inputs, needed = [], {}
    for year, report_year, raw, readable in ((2019, 2018, "a" * 64, "b" * 64), (2024, 2023, "c" * 64, "d" * 64)):
        value = window_fixture(frozen=0)
        original_calendar = value["calendar"]
        calendar = tuple(date(year, 1, 1) + timedelta(days=index) for index in range(len(original_calendar)))
        mapped = dict(zip(original_calendar, calendar, strict=True)); formation = calendar[60]
        cutoff = SessionLagPolicy(1, paired=True).selection_at(calendar, 60)
        for field in ("observations", "liquidity"):
            value[field] = {mapped[day]: replace(row, availability=replace(row.availability,
                observation_date=mapped[day], available_at=cutoff)) for day, row in value[field].items()}
        value["capitalization"] = replace(value["capitalization"], availability=replace(value["capitalization"].availability,
            observation_date=formation, available_at=cutoff))
        value.update(calendar=calendar, formation=formation, code="005490", isin="KR7005490008")
        record = value["accepted_record"]
        receipt = f"{report_year}1114000001"
        record.update(code="005490", known_on_conservative_candidate=f"{report_year}-11-15", published_on=f"{report_year}-11-14",
            receipt=receipt, source_url="https://dart.fss.or.kr/report/viewer.do?rcpNo=" + receipt,
            raw_sha256=raw, readable_sha256=readable)
        record["passages"][0].update(raw_sha256=raw, readable_sha256=readable, source_url=record["source_url"], receipt=receipt)
        window = record["required_windows"][0]
        window.update(formation=formation.isoformat(), window_id=f"{formation:%Y%m%d}/005490", isin="KR7005490008",
            required_dates=[day.isoformat() for day in calendar[:61]], effective_start_for_this_adjudication=calendar[0].isoformat(),
            effective_end_exclusive=(formation + timedelta(days=1)).isoformat())
        window["krx_metadata_provenance"].update(code="005490", formation=formation.strftime("%Y%m%d"))
        needed[formation, "005490"] = {"isin": value["isin"], "required_dates": deepcopy(window["required_dates"]),
            "krx_metadata_provenance": deepcopy(window["krx_metadata_provenance"])}
        inputs.append(value)
    return inputs, needed, {"records": [value["accepted_record"] for value in inputs]}


def test_same_code_2019_and_2024_use_separate_reports_without_global_known_on_or_backdating():
    inputs, needed, ledger = context_fixture(); before = deepcopy(ledger)
    contexts = module.adjudication_contexts(ledger, needed)
    assert len(contexts) == 2 and ledger == before
    for value in inputs:
        record = contexts[value["formation"], "005490"]
        assert record is value["accepted_record"]
        result = module.assemble_control_window(**{**value, "accepted_record": record})
        year = value["formation"].year - 1
        assert result["activity_screen"]["identity_period"]["known_on"] == f"{year}-11-15"
        assert result["activity_screen"]["accepted_evidence"]["receipt"] == f"{year}1114000001"
        assert all(arm["eligible"] for arm in result["control_arms"].values())


def test_one_context_can_explicitly_cover_multiple_disjoint_windows():
    inputs, needed, ledger = context_fixture()
    ledger["records"][0]["required_windows"].append(deepcopy(ledger["records"][1]["required_windows"][0]))
    ledger["records"].pop()
    contexts = module.adjudication_contexts(ledger, needed)
    assert len(contexts) == 2 and len({id(record) for record in contexts.values()}) == 1


@pytest.mark.parametrize("mutation", ["overlap", "missing", "early_uses_late_context", "late_report_backdated", "dated_receipt_backdated",
    "cross_context_passage", "mixed_report_pair", "source_code", "provenance", "isin", "bound", "required_dates"])
def test_disjoint_context_union_timing_and_source_reference_guards(mutation):
    inputs, needed, ledger = context_fixture()
    early, later = ledger["records"]
    if mutation == "overlap": later["required_windows"] = deepcopy(early["required_windows"])
    elif mutation == "missing": ledger["records"].pop()
    elif mutation == "early_uses_late_context":
        later["required_windows"] = deepcopy(early["required_windows"]); ledger["records"] = [later]
    elif mutation == "late_report_backdated": later["known_on_conservative_candidate"] = "2018-11-15"
    elif mutation == "dated_receipt_backdated":
        later["known_on_conservative_candidate"] = "2018-11-15"; later.pop("published_on")
    elif mutation == "cross_context_passage": later["passages"] = deepcopy(early["passages"])
    elif mutation == "mixed_report_pair": later["passages"][0]["raw_sha256"] = early["raw_sha256"]
    elif mutation == "source_code": later["source_evidence"] = [{**deepcopy(later), "code": "000001"}]
    elif mutation == "provenance": later["required_windows"][0]["krx_metadata_provenance"]["source_row_position"] += 1
    elif mutation == "isin": later["required_windows"][0]["isin"] = "other"
    elif mutation == "bound": later["required_windows"][0]["effective_start_for_this_adjudication"] = "2024-01-02"
    else: later["required_windows"][0]["required_dates"].pop()
    with pytest.raises(ValueError): module.adjudication_contexts(ledger, needed)


@pytest.mark.parametrize("mutation", ["missing_ledger_window", "extra_ledger_window", "unknown_decision", "isin", "dates", "late_known", "missing_source",
    "initial_frontier", "original_targets", "original_partition", "known_normal_changed", "activity_unavailable"])
def test_missing_positive_evidence_and_changed_original_contracts_fail_closed(mutation):
    data, tail = fixture()
    if mutation == "missing_ledger_window": data["ledger"]["records"].pop()
    elif mutation == "extra_ledger_window": data["ledger"]["records"][0]["required_windows"].append(deepcopy(data["ledger"]["records"][0]["required_windows"][0]))
    elif mutation == "unknown_decision": data["ledger"]["records"][0]["decision"] = "unknown"
    elif mutation == "isin": data["ledger"]["records"][0]["required_windows"][0]["isin"] = "other"
    elif mutation == "dates": data["ledger"]["records"][0]["required_windows"][0]["required_dates"].pop()
    elif mutation == "late_known": data["ledger"]["records"][0]["known_on_conservative_candidate"] = "2020-03-02"
    elif mutation == "missing_source": data["potential_sources"].pop(("20200301", "000001"))
    elif mutation == "initial_frontier": data["bs_scope"]["needed_normal_windows"].pop()
    elif mutation == "original_targets": data["bs_scope"]["formations"][0]["ACTIVITY"]["D1"]["target_codes"] = []
    elif mutation == "original_partition": data["bs_scope"]["formations"][0]["ACTIVITY"]["D1"]["source_partition_sha256"] = "c" * 64
    elif mutation == "known_normal_changed":
        source = data["potential_sources"][("20200301", "000002")]
        source["liquidity"] = {day: replace(row, turnover=Decimal(1)) for day, row in source["liquidity"].items()}
    else:
        day = next(iter(data["observations"]["000001"]))
        data["observations"]["000001"][day] = replace(data["observations"]["000001"][day], state="missing", turnover=None)
    with pytest.raises(ValueError): module.assemble_delta(**data)


def canonical_spec():
    return dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode=module.MODE, promotion_allowed=False,
        comparison_power_prerequisite_met=False, power_design_passed=False, window=deepcopy(saved.WINDOW),
        reference_specification=saved.REFERENCE, parameters=deepcopy(PARAMETERS), controls=list(module.CONTROL_IDS),
        design=frontier.DESIGN, inputs={**deepcopy(module.FIXED_INPUTS), **{role: {"path": "/synthetic/" + role + ".json",
            "sha256": "a" * 64} for role in module.NEW_ROLES}})


@pytest.mark.parametrize("mutation", [None, "DB", "source_pin", "parameters", "power", "controls", "alias", "new_pin"])
def test_spec_allows_only_frozen_normal_sources_and_exact_new_pins(mutation):
    spec = canonical_spec()
    if mutation == "DB": spec["inputs"]["scan_database"] = {"path": "/synthetic/db", "mode": "ro"}
    elif mutation == "source_pin": spec["inputs"]["bi_result"]["sha256"] = "b" * 64
    elif mutation == "parameters": spec["parameters"]["threshold"] = 3
    elif mutation == "power": spec["power_design_passed"] = True
    elif mutation == "controls": spec["controls"].reverse()
    elif mutation == "alias": spec["inputs"]["bs_adjudication"]["path"] = spec["inputs"]["bs_scope_result"]["path"]
    elif mutation == "new_pin": spec["inputs"]["bs_adjudication"]["sha256"] = "bad"
    if mutation is None: module.validate_spec(spec, {"parameters": PARAMETERS})
    else:
        with pytest.raises(ValueError): module.validate_spec(spec, {"parameters": PARAMETERS})


def receipt_fixture():
    scope = module.frontier.plan_frontiers(*__import__("test_activity_cohort_scope_preflight").frontier_fixture())
    files = {name: "a" * 64 for name in module.SCOPE_FILES}
    scope.update(input_sha256={role: row["sha256"] for role, row in frontier.INPUTS.items()},
        specification_sha256=files["specification.json"], source_manifest_sha256=files["source-manifest.json"],
        input_manifest_sha256=files["input-manifest.json"], resource_manifest_sha256=files["resource-manifest.json"])
    receipt = dict(schema="bs-cohort-scope-independent-verification-v1", status="verified", verified=True,
        producer_code_version="a" * 40, expected_commit="a" * 40, new_output_files_read=5,
        saved_input_files_read=5, completed_reads=10, automatic_retries=0, performance_studies_added=0,
        scope="new_metadata_population_targets_salted_prefixes_unknown_tails_and_needed_windows_only", file_sha256=files,
        input_sha256={role: frontier.INPUTS[role]["sha256"] for role in module.OLD_RECEIPT_ROLES}, summary=deepcopy(scope["summary"]),
        D1_completed_studies=0, D2_completed_studies=0, **dict.fromkeys(("books_replayed", "returns_computed", "original_ACTIVITY_replayed",
            "original_books_or_metrics_inspected", "source_truth_certified", "promotion_allowed", *frontier.POWER_FLAGS), False))
    return scope, receipt, canonical_spec()["inputs"]


@pytest.mark.parametrize("mutation", [None, "verified", "result_pin", "old_pin", "manifest", "summary", "power", "completed", "source", "read_count"])
def test_independent_scope_receipt_and_manifest_links_are_required(mutation):
    scope, receipt, declared = receipt_fixture()
    if mutation == "verified": receipt["verified"] = False
    elif mutation == "result_pin": receipt["file_sha256"]["result.json"] = "b" * 64
    elif mutation == "old_pin": receipt["input_sha256"]["br_scope_result"] = "b" * 64
    elif mutation == "manifest": scope["source_manifest_sha256"] = "b" * 64
    elif mutation == "summary": receipt["summary"]["source_rows"] += 1
    elif mutation == "power": receipt["power_design_passed"] = True
    elif mutation == "completed": scope["D1_completed_studies"] = 1
    elif mutation == "source": receipt["producer_code_version"] = "b" * 40
    elif mutation == "read_count": receipt["completed_reads"] = 9
    if mutation is None: module.validate_scope(scope, receipt, declared)
    else:
        with pytest.raises(ValueError): module.validate_scope(scope, receipt, declared)


@pytest.mark.parametrize("normal_exclusion", [False, True])
def test_thin_saved_input_pipeline_freezes_normal_scope_before_cache_and_saves_updated_partition(tmp_path, monkeypatch, normal_exclusion):
    data, tail = fixture()
    if normal_exclusion:
        source = data["potential_sources"][("20200301", "000001")]
        source["liquidity"] = {day: replace(row, turnover=Decimal(1)) for day, row in source["liquidity"].items()}
    scope, receipt, _ = receipt_fixture()
    scope.update(data["bs_scope"]); receipt["summary"] = deepcopy(scope["summary"])
    compact = [day.strftime("%Y%m%d") for day in data["calendar"]]
    bodies = dict(bs_scope_result=scope, bs_scope_verification=receipt, br_scope_result=data["original_scope"],
        br_population_audit=data["population"], br_control_dispositions=data["previous_dispositions"],
        br_normal_input_audit=data["previous_normal_audits"], calendar_manifest={"synthetic": True},
        bb_result={"synthetic": True}, bh_result={"coverage": {"windows": []}}, au_result={"rows": []},
        bi_result={"scan_snapshot": {"dataset_sha256": "a" * 64}}, bs_adjudication=data["ledger"])
    # The PinnedInputs boundary still hashes every fabricated stream. The
    # upstream calendar/BI/cache seams have their separate production tests.
    spec, store = canonical_spec(), {}
    spec["parameters"] = deepcopy(data["parameters"])
    for role, value in bodies.items():
        if role == "bs_scope_verification":
            value["file_sha256"]["result.json"] = saved.digest(saved.encoded(scope))
        raw = saved.encoded(value); path = tmp_path / "inputs" / (role + ".json")
        spec["inputs"][role] = {"path": str(path), "sha256": saved.digest(raw)}; store[path] = raw
    output = tmp_path / "run"; output.mkdir(mode=0o700)
    calls = []
    monkeypatch.setattr(module, "saved", SimpleNamespace(require=saved.require, digest=saved.digest, encoded=saved.encoded,
        write_exclusive=saved.write_exclusive, PinnedInputs=saved.PinnedInputs, _pin=saved._pin,
        calendar_schedule=lambda manifest, params: (compact, {}, data["original_scope"]["paired_schedule"]),
        _bb_windows=lambda value, calendar: ([], [])))
    monkeypatch.setattr(module, "plan_windows", lambda *args: ([], {}))
    monkeypatch.setattr(module, "restore_activity_failure", lambda *args: SimpleNamespace(observations=data["observations"]))
    monkeypatch.setattr(module, "normal", SimpleNamespace(adjudication_records=module.normal.adjudication_records,
        replay_proofs=lambda *args: []))
    def source_reader(declared, inputs, schedule, calendar, needed, directory, **kwargs):
        assert (directory / "normal-read-scope.json").exists()
        assert needed == {("20200301", "000001"), ("20200301", "000002")} and kwargs["expected_window_count"] == 2
        calls.append("normal-cache-only")
        fresh = deepcopy(data["population"]["rows"])
        for row in fresh:
            for field in ("capitalization_availability", "classification_availability", "trade_section_availability"):
                row[field] = data["population"]["availability_metadata"][row[field]]
        raw_scope = saved.encoded({"synthetic_normal_source_scope": sorted(needed)})
        saved.write_exclusive(directory / "raw-source-read-scope.json", raw_scope)
        return {"population": fresh, "potential_sources": data["potential_sources"],
            "raw_source_read_scope_sha256": saved.digest(raw_scope)}
    monkeypatch.setattr(module, "load_sources", source_reader)
    for owner, name in ((sqlite3, "connect"), (socket, "create_connection")):
        monkeypatch.setattr(owner, name, lambda *a, **k: pytest.fail("normal pipeline attempted DB/network"))
    inputs = saved.PinnedInputs(store.__getitem__)
    result = module.evaluate_inputs(spec, inputs, output)
    planned = saved.strict_json((output / "frontier.json").read_bytes())
    assert calls == ["normal-cache-only"] and len(inputs.manifest) == len(bodies)
    assert result["summary"]["needed_windows_recomputed"] == 2 and result["summary"]["source_rows"] == 7
    assert result["summary"]["new_normal_exclusions"] == int(normal_exclusion)
    assert result["complete_target_prefix"] == (not normal_exclusion)
    assert all(result[field] is False for field in module.FALSE_FLAGS)
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    for filename, pin in result["artifact_sha256"].items(): assert pin == saved.digest((output / filename).read_bytes())
    assert planned["normal_input_audit_sha256"] == result["artifact_sha256"]["normal-input-audit.json"]
    assert planned["control_dispositions_sha256"] == result["artifact_sha256"]["control-dispositions.json"]
    assert result["raw_source_read_scope_sha256"] == saved.digest((output / "raw-source-read-scope.json").read_bytes())


def statuses(path): return [saved.strict_json(raw)["status"] for raw in path.read_bytes().splitlines()]


def logging_fixture(tmp_path, monkeypatch):
    root, private, evidence = (tmp_path / name for name in ("repo", "private", "evidence"))
    root.mkdir(); private.mkdir(mode=0o700); evidence.mkdir(mode=0o700)
    path, raw = evidence / "population.json", b"{}"
    spec = canonical_spec(); spec["inputs"] = {"br_population_audit": {"path": str(path), "sha256": saved.digest(raw)}}
    sources = {"code_version": "a" * 40, "files": [{"path": module.SOURCE_PATH, "sha256": "a" * 64}]}
    monkeypatch.chdir(root); monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_spec
        return deepcopy(sources), saved.encoded(spec), deepcopy(spec)
    def evaluator(spec, inputs, output):
        inputs.json(path, saved.digest(raw), "br_population_audit")
        return dict(schema=module.SCHEMA, status="synthetic_normal_assembled", summary={"synthetic": True},
            D1_completed_studies=0, D2_completed_studies=0, **dict.fromkeys(module.FALSE_FLAGS, False))
    return root, private / "run", tmp_path / "runs.jsonl", path, raw, freeze, evaluator


def test_logged_normal_start_precedes_read_and_never_adds_performance_studies(tmp_path, monkeypatch):
    root, output, runs, path, raw, freeze, evaluator = logging_fixture(tmp_path, monkeypatch)
    def reader(target):
        assert target == path and statuses(runs) == ["started"] and (output / "source-manifest.json").exists()
        return raw
    for owner, name in ((sqlite3, "connect"), (socket, "create_connection")):
        monkeypatch.setattr(owner, name, lambda *a, **k: pytest.fail("normal-only caller attempted DB/network"))
    record = module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze, evaluator=evaluator)
    result = saved.strict_json((output / "result.json").read_bytes())
    assert statuses(runs) == ["started", "completed"] and result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert result["books_replayed"] is result["returns_computed"] is False
    assert record["result"]["result_sha256"] == saved.digest((output / "result.json").read_bytes())
    assert (output.stat().st_mode & 0o777) == 0o700 and all((path.stat().st_mode & 0o777) == 0o600 for path in output.iterdir())


@pytest.mark.parametrize("failure", ["dirty", "alias", "hash", "runtime"])
def test_refusal_or_partial_failure_never_retries_or_drops_manifests(tmp_path, monkeypatch, failure):
    root, output, runs, path, raw, freeze, evaluator = logging_fixture(tmp_path, monkeypatch)
    calls = 0
    def freezer(*args, **kwargs):
        nonlocal calls
        calls += 1
        if failure == "dirty": raise ValueError("source dirty")
        source, body, spec = freeze(*args, **kwargs)
        if failure == "runtime" and calls == 2: source["code_version"] = "b" * 40
        return source, body, spec
    if failure == "alias": runs = path
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs, reader=lambda target: b"bad" if failure == "hash" else raw,
            freeze=freezer, evaluator=evaluator)
    if failure in ("dirty", "alias"):
        assert not output.exists() and not runs.exists()
    else:
        assert statuses(runs) == ["started", "failed"] and not (output / "result.json").exists()
        assert (output / "failure.json").exists() and (output / "resource-manifest.json").exists() and (output / "input-manifest.json").exists()
        with pytest.raises(ValueError, match="already exists"):
            module.run_preflight(root, root / "spec.json", output, runs, reader=lambda target: raw, freeze=freeze, evaluator=evaluator)
        assert statuses(runs) == ["started", "failed"]
