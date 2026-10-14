from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from research import activity_control_scope_preflight as module
from research import activity_preflight as saved
from research import activity_scope_preflight as prior


def fixture():
    days = [date(2020, 1, 2) + timedelta(days=i) for i in range(32)
            if (date(2020, 1, 2) + timedelta(days=i)).weekday() < 5][:20]
    calendar = [day.strftime("%Y%m%d") for day in days]
    params = {"lookback": 2, "holding_sessions": 4, "end": days[16].isoformat(), "seed": 20261005, "slots": 2}
    _, _, schedule = prior._paired_schedule(calendar, params)
    windows = [(2, "111111", "numeric_pass_unresolved"), (2, "222222", "activity_below_threshold"),
               (2, "333333", "zero_baseline"), (2, "444444", "formation_frozen"),
               (6, "0126Z0", "short"), (10, "111111", "numeric_pass_unresolved")]
    previous, numeric = [], []
    counts = {"formation_totals": {"trade_rows": 8, "cap_pass_rows": 7, "cap_below_rows": 1,
        "cap_pass_common_label_rows": 6}, "bb_population": {"population_rows": 7, "target_windows": 6,
        "target_codes": 5, "retained_controls": 1}, "prior_windows": 6, "bh_failures": 1,
        "bi_windows": 5, "bi_failures": 3, "potential_windows": 2, "potential_codes": 1}
    for index, code, reason in windows:
        previous.append({"formation": calendar[index], "code": code,
            "proven_numeric_failures": ["BB_verified_observed_history_less_than_60"] if reason == "short" else [],
            "potentially_influential_unresolved": reason != "short"})
        if reason == "short":
            continue
        arms = next(row["arms"] for row in schedule if row["formation"] == calendar[index])
        failed = reason != "numeric_pass_unresolved"
        numeric.append({"formation": calendar[index], "code": code, "proven_failure": failed, "reason": reason,
            "unresolved": [] if failed else ["operating_period_and_eligibility_uncertified"],
            "consumed_dates": [day.isoformat() for day in days[index - 2:index + 1]],
            "normal_dates": [day.isoformat() for day in days[index - 2:index]], "frozen_dates": [],
            "selection_at": {f"D{arm['session_lag']}": arm["selection_at"] for arm in arms},
            "historical_eligibility_certified": False, "baseline": "MUST NOT READ",
            "formation_turnover": "MUST NOT READ", "required_turnover": "MUST NOT READ"})
    bh = {"schema": saved.SCHEMA, "status": "saved_input_coverage_audited", "returns_computed": False,
        "holding_selection_executed": False, "paired_schedule": deepcopy(schedule),
        "formation_totals": deepcopy(counts["formation_totals"]), "bb_population": deepcopy(counts["bb_population"]),
        "coverage": {"windows": previous, "unresolved_potentially_influential_windows": 5,
            "windows_with_proven_numeric_failure": 1}}
    states, provenance = [], []
    snapshot_pin = "a" * 64
    def metadata(day):
        return {"observation_date": day.isoformat(), "available_at": days[days.index(day) + 1].isoformat() + "T08:30:00+09:00",
            "retrieved_at": "unknown", "source": "synthetic stored source", "is_final": None,
            "data_vintage": None, "evidence_level": "assumed", "source_public_available_at": None,
            "availability_policy": "synthetic", "evidence_reference": "synthetic", "snapshot_sha256": snapshot_pin}
    for row in numeric:
        for observed in row["consumed_dates"]:
            state = "frozen" if row["reason"] == "formation_frozen" and observed == row["consumed_dates"][-1] else "observed"
            states.append({"code": row["code"], "observation_date": observed, "state": state, "issues": []})
            provenance.append({"code": row["code"], **metadata(date.fromisoformat(observed)), "state": state,
                "issues": [], "turnover": "MUST NOT READ"})
    states.sort(key=lambda row: (row["code"], row["observation_date"]))
    provenance.sort(key=lambda row: (row["code"], row["observation_date"]))
    read_scope = [{"code": code, "dates": sorted(row["observation_date"] for row in states if row["code"] == code)}
                  for code in sorted({row["code"] for row in states})]
    bi = {"schema": "activity-failure-preflight-v1", "status": "numeric_failures_audited",
        "returns_computed": False, "holding_selection_executed": False, "historical_eligibility_certified": False,
        "promotion_allowed": False, "paired_schedule": deepcopy(schedule), "prior_dispositions": deepcopy(previous),
        "windows": numeric, "summary": {"input_windows": 5, "proven_activity_failure_windows": 3,
            "remaining_potential_windows": 2, "remaining_distinct_codes": 1, "unresolved_input_windows": 0,
            "failure_reasons": {"activity_below_threshold": 1, "formation_frozen": 1, "zero_baseline": 1}},
        "scan_snapshot": {"schema": "task-bi-activity-failure-snapshot-v1", "dataset_sha256": snapshot_pin,
            "whole_database_hash": False, "original_source_vintage_certified": False,
            "row_states": states, "row_provenance": provenance, "read_scope": read_scope}}
    table = {f"m{index:04d}": metadata(date.fromisoformat(row["formation"])) for index, row in enumerate(schedule)}
    rows = []
    for index, code, reason in [*windows, (2, "555555", "cap"), (2, "005935", "negative")]:
        formation = calendar[index]
        reference = next(f"m{position:04d}" for position, row in enumerate(schedule) if row["formation"] == formation)
        cap = "1000" if reason == "cap" else "5000000000000"
        disposition = {"cap": "cap_below", "negative": "negative_type", "short": "bh_failure",
                       "numeric_pass_unresolved": "normal_window"}.get(reason, "bi_failure")
        if reason == "short":
            proof = next(row for row in previous if row["code"] == code)
        elif reason in {"formation_frozen", "activity_below_threshold", "zero_baseline"}:
            proof = next(row for row in numeric if row["code"] == code)
        else:
            proof = {"capitalization": cap} if reason == "cap" else {"literal_type": "preferred_share"} if reason == "negative" else {"reason": reason}
        rows.append({"formation": formation, "code": code, "isin": "KR7" + code + "005", "capitalization": cap,
            "capitalization_raw": cap, "cap_pass": reason != "cap", "negative_type": "preferred_share" if reason == "negative" else None,
            "literal_source_labels": {"market": "KOSPI", "security_group": "주권",
                "share_class": "구형우선주" if reason == "negative" else "보통주", "basic_section": "", "trade_section": ""},
            "listing_date": days[0].isoformat(), "disposition": disposition, "proof": deepcopy(proof),
            "capitalization_availability": reference, "classification_availability": reference, "trade_section_availability": reference,
            "krx_metadata_provenance": {"formation": formation, "code": code, "source_response_sha256": "b" * 64,
                "source_row_position": 1, "source_observation_position": 1, "source_service": "synthetic"}})
    partitions = {"cap_below": 1, "negative_type": 1, "bh_failure": 1, "bi_failure": 3, "normal_window": 2}
    summary = {"source_rows": 8, "disposition_counts": partitions}
    audit = {"schema": "activity-normal-population-audit-v1", "availability_metadata": table, "rows": rows}
    targets = {arm: {row["formation"]: ["111111"] if position in (0, 2) else []
                    for position, row in enumerate(schedule)} for arm in ("D1", "D2")}
    targets["D2"][calendar[2]] = []
    return calendar, params, schedule, counts, bh, bi, audit, targets, summary, partitions


def plan(data):
    calendar, params, schedule, counts, bh, bi, audit, targets, summary, partitions = data
    population = prior.population_snapshot(bh, schedule, counts=counts)
    metadata = module.bi_metadata(population, bi, calendar, schedule, params, counts=counts)
    return module.plan_control(audit, population, metadata, targets, summary, schedule, params,
                               partition_counts=partitions, expected_possible=4)


def test_full_pool_keeps_activity_and_zero_failures_and_separate_declared_ceilings():
    data = fixture()
    result = plan(data)
    assert result["summary"]["source_rows"] == 8
    assert result["summary"]["control_disposition_counts"] == {
        "bh_failure": 1, "cap_below": 1, "formation_frozen": 1, "negative_type": 1, "numeric_possible": 4}
    first = result["formations"][0]
    assert {row["code"] for row in first["ranked_numeric_possible"]} == {"111111", "222222", "333333"}
    assert first["activity_preopening_k"] == {"D1": 1, "D2": 0}
    expected = sorted(first["ranked_numeric_possible"], key=lambda row: hashlib.sha256(
        f"{data[1]['seed']}:{date.fromisoformat(first['formation'])}:{row['code']}".encode()).digest())
    assert first["ranked_numeric_possible"] == expected
    assert [row["rank"] for row in expected] == [1, 2, 3]
    assert all(row["operating_period"] is None for row in expected)
    assert result["formations"][1]["ranked_numeric_possible"] == []
    for name in ("frontier_complete", "compulsory_successor_closure_complete", "control_targets_frozen",
                 "potential_holding_origins_bound_complete", "historical_eligibility_certified", "books_replayed", "returns_computed"):
        assert result[name] is False
    assert "MUST NOT READ" not in saved.encoded(result).decode()
    assert b'"turnover"' not in saved.encoded(result)


@pytest.mark.parametrize("mutate", [
    lambda data: data[6]["rows"].pop(),
    lambda data: data[6]["rows"][0].update(cap_pass=False),
    lambda data: data[6]["rows"][1]["proof"].update(reason="formation_frozen"),
    lambda data: data[6]["rows"][-1]["literal_source_labels"].update(share_class="보통주"),
    lambda data: data[5]["windows"][0].update(reason="unknown_reason"),
    lambda data: data[5]["windows"][0]["consumed_dates"].pop(1),
    lambda data: data[5]["windows"][0]["normal_dates"].reverse(),
    lambda data: data[5]["scan_snapshot"]["row_states"][0].update(state="missing"),
    lambda data: data[5]["scan_snapshot"]["row_provenance"][0].update(snapshot_sha256="c" * 64),
    lambda data: data[7]["D1"][data[0][2]].append("444444"),
    lambda data: data[6]["availability_metadata"]["m0000"].update(available_at="2099-01-01T08:30:00+09:00"),
    lambda data: data[6]["rows"][0]["krx_metadata_provenance"].update(code="999999"),
])
def test_bad_population_proof_reason_date_metadata_or_target_rejected(mutate):
    data = fixture()
    mutate(data)
    with pytest.raises(ValueError):
        plan(data)


def pair_fixture():
    data = fixture()
    schedule, targets, summary = data[2], data[7], data[8]
    report = {"schema": "activity-paired-timing-report-v1", "target_overlap": {"formations": [
        {"formation": date.fromisoformat(row["formation"]).isoformat(),
         **{arm + "_codes": targets[arm][row["formation"]] for arm in ("D1", "D2")}} for row in schedule]},
        "arms": {"D1": {"filled_entries": 0, "path": "MUST NOT READ"}}}
    replay = {"schema": "activity-paired-replay-v1", "status": "pure_paired_replay", "report": report,
        "replays": {arm: {"selections": [[row["formation"], row[arm + "_codes"]]
                      for row in report["target_overlap"]["formations"]], "books": "MUST NOT READ"} for arm in ("D1", "D2")}}
    pins = {role: "b" * 64 for role in module.INPUTS}
    pins["paired_report"] = saved.digest(saved.encoded(report))
    pins["paired_replay"] = saved.digest(saved.encoded(replay))
    bl = {"status": "normal_inputs_assembled", "paired_schedule": schedule, "returns_computed": False,
          "summary": summary, "audit_sha256": {"population-audit.json": pins["bl_population_audit"]}}
    bl_verified = {"verified": True, "returns_computed": False, "summary": summary,
        "file_sha256": {"population-audit.json": pins["bl_population_audit"], "result.json": pins["bl_result"]}}
    original = {role: pins[role] for role in ("bl_result", "bl_population_audit", "bl_verification")}
    result = {"status": "descriptive_timing_pair_executed", "logged_paired_execution_completed": True,
        "report_sha256": pins["paired_report"], "paired_replay_sha256": pins["paired_replay"], "input_sha256": original}
    verified = {"verified": True, "hash_links_verified": True, "input_sha256": original,
        "file_sha256": {"report.json": pins["paired_report"], "paired-replay.json": pins["paired_replay"], "result.json": pins["paired_result"]}}
    return {"bl_result": bl, "bl_verification": bl_verified, "paired_report": report,
        "paired_result": result, "paired_verification": verified, "paired_replay": replay}, schedule, pins, targets


def test_saved_targets_use_declarations_even_when_no_entries_filled():
    docs, schedule, pins, expected = pair_fixture()
    actual, _ = module.saved_targets(docs, schedule, pins)
    assert actual == expected
    assert len(actual["D1"][schedule[0]["formation"]]) == 1
    assert docs["paired_report"]["arms"]["D1"]["filled_entries"] == 0


@pytest.mark.parametrize("mutate", [
    lambda docs: docs["paired_report"]["target_overlap"]["formations"].pop(),
    lambda docs: docs["paired_replay"]["replays"]["D1"]["selections"][0][1].append("222222"),
    lambda docs: docs["paired_result"].update(report_sha256="c" * 64),
    lambda docs: docs["paired_verification"]["file_sha256"].update(**{"paired-replay.json": "c" * 64}),
    lambda docs: docs["bl_verification"]["file_sha256"].update(**{"population-audit.json": "c" * 64}),
    lambda docs: docs["paired_result"]["input_sha256"].update(bl_result="c" * 64),
])
def test_saved_target_missing_schedule_or_hash_links_refused(mutate):
    docs, schedule, pins, _ = pair_fixture()
    mutate(docs)
    with pytest.raises(ValueError):
        module.saved_targets(docs, schedule, pins)


def test_exact_registered_inputs_and_json_parameters():
    root = Path(__file__).resolve().parents[2]
    spec = json.loads((root / "configs/research/discovery/activity-control-scope-v1.json").read_text())
    reference = json.loads((root / saved.REFERENCE).read_text())
    module.validate_spec(spec, reference)
    assert len(spec["inputs"]) == 10
    for change in ("threshold", "path", "pin", "extra"):
        bad = deepcopy(spec)
        if change == "threshold":
            bad["parameters"]["threshold"] = 3
        elif change == "path":
            bad["inputs"]["paired_replay"]["path"] = "/tmp/other.json"
        elif change == "pin":
            bad["inputs"]["paired_replay"]["sha256"] = "c" * 64
        else:
            bad["inputs"]["database"] = {"path": "/tmp/other.sqlite3", "sha256": "c" * 64}
        with pytest.raises(ValueError):
            module.validate_spec(bad, reference)


def test_connected_pinned_reader_projects_metadata_and_reads_only_ten_declared_inputs(tmp_path, monkeypatch):
    data = fixture()
    docs, schedule, pins, _ = pair_fixture()
    calendar, params, _, counts, bh, bi, audit, _, _, partitions = data
    objects = {"calendar_manifest": {"calendar_dates": calendar}, "bh_result": bh,
               "bi_result": bi, "bl_population_audit": audit, **docs}
    for role in ("calendar_manifest", "bh_result", "bi_result", "bl_population_audit"):
        pins[role] = saved.digest(saved.encoded(objects[role]))
    docs["bl_result"]["audit_sha256"]["population-audit.json"] = pins["bl_population_audit"]
    docs["bl_result"]["activity_input"] = {"source_sha256": pins["bi_result"], "upstream_dataset_sha256": "a" * 64}
    pins["bl_result"] = saved.digest(saved.encoded(docs["bl_result"]))
    docs["bl_verification"]["file_sha256"].update({"result.json": pins["bl_result"], "population-audit.json": pins["bl_population_audit"]})
    pins["bl_verification"] = saved.digest(saved.encoded(docs["bl_verification"]))
    upstream = {role: pins[role] for role in ("bl_result", "bl_verification", "bl_population_audit")}
    docs["paired_result"]["input_sha256"] = upstream
    pins["paired_result"] = saved.digest(saved.encoded(docs["paired_result"]))
    docs["paired_verification"]["input_sha256"] = upstream
    docs["paired_verification"]["file_sha256"]["result.json"] = pins["paired_result"]
    pins["paired_verification"] = saved.digest(saved.encoded(docs["paired_verification"]))
    declarations, stored, attempted = {}, {}, []
    for role, value in objects.items():
        path = tmp_path / (role + ".json")
        stored[path] = saved.encoded(value)
        declarations[role] = {"path": str(path), "sha256": saved.digest(stored[path])}
    metadata_planner, control_planner = module.bi_metadata, module.plan_control
    population_planner = prior.population_snapshot
    monkeypatch.setattr(saved, "calendar_schedule", lambda *args: (calendar, {}, schedule))
    monkeypatch.setattr(prior, "population_snapshot", lambda *args: population_planner(*args, counts=counts))
    monkeypatch.setattr(module, "bi_metadata", lambda *args: metadata_planner(*args, counts=counts))
    monkeypatch.setattr(module, "plan_control", lambda *args: control_planner(*args, partition_counts=partitions, expected_possible=4))
    def reader(path):
        attempted.append(path)
        return stored[path]
    inputs = saved.PinnedInputs(reader)
    output = module.evaluate_inputs({"parameters": params, "inputs": declarations}, inputs)
    assert len(attempted) == len(set(attempted)) == len(inputs.manifest) == 10
    assert set(attempted) == set(stored)
    assert output["summary"]["numeric_possible_windows"] == 4
    assert "MUST NOT READ" not in saved.encoded(output).decode()


@pytest.mark.parametrize("fail", [False, True])
def test_logged_attempt_starts_before_reader_preserves_failure_and_refuses_reuse(tmp_path, monkeypatch, fail):
    root = Path(__file__).resolve().parents[2]
    spec_path = root / "configs/research/discovery/activity-control-scope-v1.json"
    raw = spec_path.read_bytes()
    spec = saved.strict_json(raw)
    sources = {"files": [], "code_version": "synthetic"}
    monkeypatch.chdir(root)
    monkeypatch.setattr(saved, "freeze_sources", lambda *args, **kwargs: (sources, raw, spec))
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    output, runs = parent / "run", tmp_path / "runs.jsonl"
    def evaluate(specification, inputs):
        entries = [json.loads(line) for line in runs.read_text().splitlines()]
        assert entries[-1]["status"] == "started"
        assert (output / "source-manifest.json").is_file()
        if fail:
            raise ValueError("synthetic input failure")
        return {"status": "complete_control_rank_metadata_planned", "summary": {}, "D1_completed_studies": 0, "D2_completed_studies": 0}
    monkeypatch.setattr(module, "evaluate_inputs", evaluate)
    if fail:
        with pytest.raises(ValueError, match="synthetic input failure"):
            module.run_preflight(root, spec_path, output, runs)
        assert json.loads((output / "failure.json").read_bytes())["partial_package_preserved"] is True
    else:
        module.run_preflight(root, spec_path, output, runs)
        assert (output / "result.json").is_file()
    assert (output / "resource-manifest.json").is_file()
    assert (output / "input-manifest.json").is_file()
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(FileExistsError):
        module.run_preflight(root, spec_path, output, runs)
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}
