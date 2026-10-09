from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import stat

import pytest

from research import activity_scope_preflight as module
from research import activity_preflight as saved


def fixture():
    # Uneven calendar gaps expose weekday/calendar-day lag substitutions.
    start = date(2020, 1, 2)
    days = [start + timedelta(days=i) for i in range(35)
            if (start + timedelta(days=i)).weekday() < 5
            and start + timedelta(days=i) != date(2020, 1, 6)][:20]
    calendar = [day.strftime("%Y%m%d") for day in days]
    params = {"lookback": 2, "holding_sessions": 4, "end": days[16].isoformat()}
    schedule = [{"formation": calendar[i], "arms": [
        {"session_lag": lag, "execution_on": days[i + lag].isoformat(),
         "selection_at": days[i + lag].isoformat() + "T08:30:00+09:00",
         "due_on": days[i + lag + 4].isoformat(), "status": "input_coverage_only_not_executed"}
        for lag in (1, 2)]} for i in (2, 6, 10)]
    prior = [
        {"formation": calendar[2], "code": "111111", "proven_numeric_failures": [],
         "potentially_influential_unresolved": True},
        {"formation": calendar[2], "code": "222222", "proven_numeric_failures": [],
         "potentially_influential_unresolved": True},
        {"formation": calendar[6], "code": "0126Z0",
         "proven_numeric_failures": ["BB_verified_observed_history_less_than_60"],
         "potentially_influential_unresolved": False},
        {"formation": calendar[10], "code": "111111", "proven_numeric_failures": [],
         "potentially_influential_unresolved": True},
    ]
    counts = {"formation_totals": {"trade_rows": 8, "cap_pass_rows": 5,
                "cap_below_rows": 3, "cap_pass_common_label_rows": 4},
              "bb_population": {"population_rows": 5, "target_windows": 4,
                "target_codes": 3, "retained_controls": 1},
              "prior_windows": 4, "bh_failures": 1, "bi_windows": 3,
              "bi_failures": 1, "potential_windows": 2, "potential_codes": 1}
    bh = {"schema": saved.SCHEMA, "status": "saved_input_coverage_audited",
          "returns_computed": False, "holding_selection_executed": False,
          "paired_schedule": deepcopy(schedule), "formation_totals": deepcopy(counts["formation_totals"]),
          "bb_population": deepcopy(counts["bb_population"]), "coverage": {
              "windows": prior, "unresolved_potentially_influential_windows": 3,
              "windows_with_proven_numeric_failure": 1}}
    rows = []
    for formation, code in ((calendar[2], "111111"), (calendar[2], "222222"), (calendar[10], "111111")):
        proven = code == "222222"
        arms = next(row["arms"] for row in schedule if row["formation"] == formation)
        rows.append({"formation": formation, "code": code, "proven_failure": proven,
                     "reason": "activity_below_threshold" if proven else "numeric_pass_unresolved",
                     "unresolved": [] if proven else ["operating_period_and_eligibility_uncertified"],
                     "selection_at": {f"D{arm['session_lag']}": arm["selection_at"] for arm in arms},
                     "historical_eligibility_certified": False,
                     "baseline": "not an input to this planner", "formation_turnover": "do not extract"})
    bi = {"schema": "activity-failure-preflight-v1", "status": "numeric_failures_audited",
          "returns_computed": False, "holding_selection_executed": False,
          "historical_eligibility_certified": False, "promotion_allowed": False,
          "paired_schedule": deepcopy(schedule), "prior_dispositions": deepcopy(prior), "windows": rows,
          "summary": {"input_windows": 3, "proven_activity_failure_windows": 1,
              "remaining_potential_windows": 2, "remaining_distinct_codes": 1,
              "unresolved_input_windows": 0, "failure_reasons": {"activity_below_threshold": 1}}}
    return calendar, params, schedule, counts, bh, bi


def plan(data):
    calendar, params, schedule, counts, bh, bi = data
    population = module.population_snapshot(bh, schedule, counts=counts)
    return module.plan_scope(population, bi, calendar, schedule, params, counts=counts)


def test_union_includes_every_session_to_fixed_end_after_due_and_deduplicates():
    data = fixture()
    calendar, params, schedule, _, _, _ = data
    result = plan(data)
    expected = [date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}").isoformat()
                for day in calendar[3:17]]
    assert result["requested_code_dates"] == {"111111": expected}
    assert result["summary"]["required_unique_code_dates"] == 14
    assert result["summary"]["potential_windows"] == 2
    assert result["summary"]["arm_windows"] == {"D1": 2, "D2": 2}
    assert result["calendar_dates"] == calendar
    assert calendar[-1] > params["end"].replace("-", "")
    for window in result["possible_windows"]:
        registered = next(row["arms"] for row in schedule if row["formation"] == window["formation"])
        for arm, original in zip(window["arms"], registered, strict=True):
            assert arm["possible_entry_on"] == original["execution_on"]
            assert arm["selection_at"] == original["selection_at"]
            assert arm["scheduled_due_on"] == original["due_on"] <= params["end"]
            assert arm["investigation_end"] == params["end"]
            assert arm["investigation_start"] == arm["possible_entry_on"]
        assert window["arms"][0]["investigation_sessions"] == window["arms"][1]["investigation_sessions"] + 1
    assert "2020-01-06" not in expected
    assert expected[-1] > result["possible_windows"][0]["arms"][0]["scheduled_due_on"]


def test_population_partition_and_alphanumeric_prior_failure_preserved():
    data = fixture()
    result = plan(data)
    assert result["formation_totals"] == data[4]["formation_totals"]
    assert result["bb_population"] == data[4]["bb_population"]
    assert result["retained_controls_reference"]["count"] == 1
    assert result["prior_dispositions_reference"]["count"] == 4
    assert result["bh_failure_windows"] == [{"formation": data[0][6], "code": "0126Z0",
        "proven_numeric_failures": ["BB_verified_observed_history_less_than_60"]}]
    assert result["bi_failure_windows"] == [{"formation": data[0][2], "code": "222222",
        "reason": "activity_below_threshold"}]
    assert result["scope_kind"] == "upper_bound_for_BI_potential_subset_only"
    assert result["holding_lot_denominator"] is result["action_lot_denominator"] is None
    for key in ("successor_closure_complete", "no_event_coverage_reviewed",
                "baseline_formation_price_basis_certified", "settlement_coverage_certified",
                "holding_selection_executed", "returns_computed", "database_access", "promotion_allowed"):
        assert result[key] is False
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert result["new_source_requests"] == 0
    assert b"turnover" not in saved.encoded(result) and b"baseline\"" not in saved.encoded(result)


@pytest.mark.parametrize("mutate", [
    lambda bh: bh["coverage"]["windows"].append(deepcopy(bh["coverage"]["windows"][0])),
    lambda bh: bh["coverage"]["windows"][0].update(code="11111!"),
    lambda bh: bh["coverage"]["windows"][0].update(potentially_influential_unresolved=1),
    lambda bh: bh["coverage"]["windows"][0].update(proven_numeric_failures=["unknown_failure"]),
    lambda bh: bh["coverage"]["windows"][0].update(potentially_influential_unresolved=False),
    lambda bh: bh["coverage"].update(unresolved_potentially_influential_windows=2),
    lambda bh: bh["formation_totals"].update(trade_rows=9),
    lambda bh: bh["bb_population"].update(retained_controls=2),
    lambda bh: bh["bb_population"].update(retained_controls=True),
    lambda bh: bh["paired_schedule"][0]["arms"][1].update(due_on="2099-01-01"),
])
def test_invalid_bh_population_reason_count_or_schedule_rejected(mutate):
    data = fixture()
    mutate(data[4])
    with pytest.raises(ValueError):
        plan(data)


@pytest.mark.parametrize("mutate", [
    lambda bi: bi["windows"].append(deepcopy(bi["windows"][0])),
    lambda bi: bi["windows"].pop(),
    lambda bi: bi["windows"][0].update(code="999999"),
    lambda bi: bi["windows"][0].update(proven_failure=0),
    lambda bi: bi["windows"][1].update(reason="unknown_failure"),
    lambda bi: bi["windows"][1].update(unresolved=["unavailable"]),
    lambda bi: bi["windows"][0].update(reason="unresolved_input"),
    lambda bi: bi["windows"][0].update(unresolved=[]),
    lambda bi: bi["windows"][0].update(unresolved=["operating_period_and_eligibility_uncertified", "extra"]),
    lambda bi: bi["windows"][0]["selection_at"].update(D2="2099-01-01T08:30:00+09:00"),
    lambda bi: bi["windows"][0].update(historical_eligibility_certified=True),
    lambda bi: bi["summary"].update(remaining_distinct_codes=2),
    lambda bi: bi["summary"].update(unresolved_input_windows=1),
    lambda bi: bi["summary"]["failure_reasons"].update(activity_below_threshold=True),
    lambda bi: bi["prior_dispositions"][0].update(required_dates=61),
    lambda bi: bi["paired_schedule"][0]["arms"][0].update(session_lag=2),
])
def test_invalid_bi_key_reason_count_prior_disposition_or_schedule_rejected(mutate):
    data = fixture()
    mutate(data[5])
    with pytest.raises(ValueError):
        plan(data)


@pytest.mark.parametrize("change", ["missing_intervening", "missing_end", "duplicate", "wrong_due", "wrong_cutoff"])
def test_incomplete_calendar_or_forged_recomputed_schedule_refused(change):
    data = fixture()
    calendar, params, schedule, _, bh, bi = data
    if change == "missing_intervening":
        calendar.pop(4)
    elif change == "missing_end":
        calendar.remove(params["end"].replace("-", ""))
    elif change == "duplicate":
        calendar.insert(4, calendar[4])
    else:
        field = "due_on" if change == "wrong_due" else "selection_at"
        for receipt in (schedule, bh["paired_schedule"], bi["paired_schedule"]):
            receipt[0]["arms"][0][field] = "2099-01-01"
    with pytest.raises(ValueError):
        plan(data)


def test_fixed_production_counts_and_exact_spec_inputs():
    root = Path(__file__).resolve().parents[2]
    spec = json.loads((root / "configs/research/discovery/activity-price-action-scope-v1.json").read_text())
    reference = json.loads((root / saved.REFERENCE).read_text())
    module.validate_spec(spec, reference)
    assert spec["parameters"]["holding_sessions"] == 126
    assert spec["parameters"]["end"] == "2026-09-18"
    assert module.REGISTERED_COUNTS["formation_totals"] == {
        "trade_rows": 35271, "cap_pass_rows": 944, "cap_below_rows": 34327,
        "cap_pass_common_label_rows": 926}
    assert tuple(module.REGISTERED_COUNTS[key] for key in (
        "prior_windows", "bh_failures", "bi_windows", "bi_failures", "potential_windows", "potential_codes")) == (
        917, 41, 876, 840, 36, 31)
    for change in ("path", "pin", "extra_input", "threshold", "end", "promotion"):
        changed = deepcopy(spec)
        if change == "path":
            changed["inputs"]["bi_result"]["path"] = "/tmp/unregistered.json"
        elif change == "pin":
            changed["inputs"]["bi_result"]["sha256"] = "0" * 64
        elif change == "extra_input":
            changed["inputs"]["scan_database"] = {"path": "/tmp/scan.sqlite3"}
        elif change == "threshold":
            changed["parameters"]["threshold"] = 2.9
        elif change == "end":
            changed["parameters"]["end"] = "2026-09-23"
        else:
            changed["promotion_allowed"] = True
        with pytest.raises(ValueError):
            module.validate_spec(changed, reference)


def test_evaluate_reads_only_three_pinned_inputs_and_emits_no_market_values(tmp_path, monkeypatch):
    calendar, params, schedule, counts, bh, bi = fixture()
    objects = {"calendar_manifest": {"calendar_dates": calendar}, "bh_result": bh, "bi_result": bi}
    store, declarations = {}, {}
    for role, value in objects.items():
        path = tmp_path / (role + ".json")
        store[path] = saved.encoded(value)
        declarations[role] = {"path": str(path), "sha256": saved.digest(store[path])}
    original_population, original_plan = module.population_snapshot, module.plan_scope
    monkeypatch.setattr(saved, "calendar_schedule", lambda *args: (calendar, {}, schedule))
    monkeypatch.setattr(module, "population_snapshot", lambda value, arms: original_population(value, arms, counts=counts))
    monkeypatch.setattr(module, "plan_scope", lambda *args: original_plan(*args, counts=counts))
    attempted = []

    def read(path):
        attempted.append(path)
        return store[path]

    result = module.evaluate_inputs({"inputs": declarations, "parameters": params}, saved.PinnedInputs(read))
    assert attempted == [Path(declarations[role]["path"]) for role in ("calendar_manifest", "bh_result", "bi_result")]
    assert [row["role"] for row in result["input_manifest"]] == ["calendar_manifest", "bh_result", "bi_result"]
    assert all(row["read_status"] == "hash_verified" for row in result["input_manifest"])
    assert b"do not extract" not in saved.encoded(result)


def runner_fixture(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    source = {"files": [{"path": "python/research/synthetic.py", "sha256": "a" * 64}], "code_version": "a" * 40}
    spec = {"study_id": module.SCHEMA, "parameters": {}, "window": saved.WINDOW,
            "inputs": {role: {"path": str(evidence / (role + ".json")), "sha256": "0" * 64}
                       for role in module.INPUTS}}
    monkeypatch.chdir(root)
    monkeypatch.setattr(saved, "freeze_sources", lambda *args, **kwargs: (source, b"{}", spec))
    return root, parent / "run", tmp_path / "runs.jsonl", source, spec


def test_durable_started_before_input_and_preserved_hash_failure(tmp_path, monkeypatch):
    root, output, runs, _, _ = runner_fixture(tmp_path, monkeypatch)
    attempted = []

    def bad_reader(path):
        attempted.append(path)
        rows = [json.loads(line) for line in runs.read_text().splitlines()]
        assert [row["status"] for row in rows] == ["started"]
        assert (output / "source-manifest.json").exists() and (output / "specification.json").exists()
        return b"{}"

    with pytest.raises(ValueError, match="actual input hash mismatch"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=bad_reader)
    rows = [json.loads(line) for line in runs.read_text().splitlines()]
    assert [row["status"] for row in rows] == ["started", "failed"]
    assert len(attempted) == 1
    failure = json.loads((output / "failure.json").read_text())
    assert failure["automatic_retries"] == 0 and failure["partial_package_preserved"] is True
    assert failure["actual_inputs_attempted"][0]["read_status"] == "hash_mismatch"
    assert json.loads((output / "input-manifest.json").read_text()) == failure["actual_inputs_attempted"]
    assert not (output / "result.json").exists()
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


@pytest.mark.parametrize("alias", ["input_log", "source_log", "input_output", "checkout_output", "output_log"])
def test_aliases_rejected_before_logging_or_actual_reads(tmp_path, monkeypatch, alias):
    root, output, runs, _, spec = runner_fixture(tmp_path, monkeypatch)
    if alias == "input_log":
        runs = Path(spec["inputs"]["bi_result"]["path"])
    elif alias == "source_log":
        runs = root / "python/research/synthetic.py"
    elif alias == "input_output":
        output = Path(spec["inputs"]["bi_result"]["path"]).parent / "run"
    elif alias == "checkout_output":
        output = root / "output"
    else:
        runs = output / "log.jsonl"
    attempted = []
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs,
                             reader=lambda path: attempted.append(path))
    assert attempted == [] and not runs.exists() and not output.exists()


def test_existing_exclusive_output_preserved_without_actual_reads(tmp_path, monkeypatch):
    root, output, runs, _, _ = runner_fixture(tmp_path, monkeypatch)
    output.mkdir(mode=0o700)
    sentinel = output / "sentinel"
    sentinel.write_bytes(b"original")
    attempted = []
    with pytest.raises(FileExistsError):
        module.run_preflight(root, root / "spec.json", output, runs,
                             reader=lambda path: attempted.append(path))
    assert attempted == [] and sentinel.read_bytes() == b"original"
    assert list(output.iterdir()) == [sentinel]
    assert [json.loads(line)["status"] for line in runs.read_text().splitlines()] == ["started", "failed"]


def test_changed_source_after_plan_preserves_failure_and_no_result(tmp_path, monkeypatch):
    root, output, runs, source, spec = runner_fixture(tmp_path, monkeypatch)
    manifests = iter([source, {**source, "code_version": "b" * 40}])
    monkeypatch.setattr(saved, "freeze_sources", lambda *args, **kwargs: (next(manifests), b"{}", spec))
    monkeypatch.setattr(module, "evaluate_inputs", lambda *args: {
        "status": "conservative_original_issue_scope_planned", "summary": {"potential_windows": 2}})
    with pytest.raises(ValueError, match="source/runtime changed"):
        module.run_preflight(root, root / "spec.json", output, runs)
    assert (output / "failure.json").exists() and not (output / "result.json").exists()
    assert [json.loads(line)["status"] for line in runs.read_text().splitlines()] == ["started", "failed"]


def test_completed_runner_hashes_private_outputs_and_logs_after_all_inputs(tmp_path, monkeypatch):
    root, output, runs, _, spec = runner_fixture(tmp_path, monkeypatch)
    body = b"{}"
    for declaration in spec["inputs"].values():
        declaration["sha256"] = saved.digest(body)
    attempted = []

    def reader(path):
        attempted.append(path)
        assert [json.loads(line)["status"] for line in runs.read_text().splitlines()] == ["started"]
        return body

    def evaluate(declared, inputs):
        for role, declaration in declared["inputs"].items():
            inputs.json(Path(declaration["path"]), declaration["sha256"], role)
        return {"status": "conservative_original_issue_scope_planned", "summary": {"potential_windows": 2},
                "input_manifest": inputs.manifest, "holding_lot_denominator": None,
                "action_lot_denominator": None, "D1_completed_studies": 0, "D2_completed_studies": 0}

    monkeypatch.setattr(module, "evaluate_inputs", evaluate)
    record = module.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    assert [json.loads(line)["status"] for line in runs.read_text().splitlines()] == ["started", "completed"]
    assert len(attempted) == 3
    assert record["result"]["result_sha256"] == saved.digest((output / "result.json").read_bytes())
    assert record["result"]["input_manifest_sha256"] == saved.digest((output / "input-manifest.json").read_bytes())
    assert {path.name for path in output.iterdir()} == {
        "specification.json", "source-manifest.json", "input-manifest.json", "result.json"}
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())
