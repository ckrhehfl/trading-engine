"""Streaming execution uses only fabricated immutable inputs and private temp IO."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
import os
from pathlib import Path
import socket
import sqlite3
import weakref

import pytest

from research import activity_cohort_comparison as comparison
from research import activity_cohort_execution as module
from research import activity_preflight as saved
from research import experiment_log
from research.activity_cohort_connection import prepare_cohort_scope
from research.activity_cohort_selection import activity_targets_fingerprint
from research.activity_paired_replay import _json_value
from research.activity_timing_report import _overlap
from test_activity_cohort_connection import package, raw_package
from test_activity_cohort_comparison import CODES


@pytest.fixture(autouse=True)
def no_actual_input_read_or_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("execution helper attempted actual input/DB/API access")
    monkeypatch.setattr(saved, "read_private", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(sqlite3, "connect", denied)
    monkeypatch.setattr(experiment_log, "_git_head_sha", lambda: "a" * 40)


def fixture(tmp_path, *, ks=(2, 1), changes=None):
    values = package(ks=ks, changes=changes)
    binding = prepare_cohort_scope(**values)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    return dict(declarations=values["declarations"], holding_inputs=values["quotes"], events=binding.events,
        parameters=deepcopy(values["parameters"]), scope_binding=binding, study_id="synthetic-bs-cohort-execution",
        source_manifest_sha256="1" * 64, specification_sha256="2" * 64,
        runs_path=tmp_path / "canonical" / "experiments.jsonl", output_dir=private / "cohorts")


def records(path):
    return [saved.strict_json(line) for line in path.read_bytes().splitlines()]


def read(path):
    return saved.strict_json(path.read_bytes())


def full(values):
    return comparison.replay_equal_capital_cohorts(values["declarations"], values["holding_inputs"], values["events"],
        parameters=values["parameters"], scope_binding=values["scope_binding"], raw_quote_view=values.get("raw_quote_view"))


def test_streamed_summary_equals_whole_engine_and_each_original_book_is_saved(tmp_path, monkeypatch):
    values = fixture(tmp_path)
    batch = full(values)
    real = comparison._prepare_cohort_replay
    refs, called, preparations = [], [], []
    def checked_prepare(*args, **kwargs):
        assert kwargs["scope_binding"] is values["scope_binding"]
        assert args[0] is values["declarations"] and args[1] is values["holding_inputs"]
        prepared, execute = real(*args, **kwargs)
        preparations.append(prepared)
        def checked(candidate, keys):
            assert all(reference() is None for reference in refs), "a prior full replay survived into the next cohort"
            log = records(values["runs_path"])
            key, = keys
            assert log[-1]["status"] == "started" and log[-1]["parameters"]["cohort_key"] == module._key_value(key)
            assert candidate is prepared
            called.append(key)
            result = execute(candidate, keys)
            refs.append(weakref.ref(result["cohorts"][0]["replay"]))
            return result
        return prepared, checked
    monkeypatch.setattr(comparison, "_prepare_cohort_replay", checked_prepare)
    result = module.run_cohort_family(**values)
    report = result["report"]
    assert all(reference() is None for reference in refs)
    assert len(preparations) == 1
    assert called == sorted(row["key"] for row in batch["cohorts"])
    assert report["aggregates"] == _json_value(batch["aggregates"])
    assert report["comparisons"] == _json_value(batch["comparisons"])
    assert report["cohort_evaluation_counts"] == {"D1": 12, "D2": 12}
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 12
    log = records(values["runs_path"])
    assert len(log) == 2 * len(called) and [row["status"] for row in log] == ["started", "completed"] * len(called)
    assert all(row["record_type"] == "discovery_trial" and row["promotion_trial_increment"] == 0
        and row["promotion_allowed"] is False for row in log)
    by_key = {tuple([row["arm"], row["group_id"], row["formation_on"].isoformat()]): row for row in batch["cohorts"]}
    for receipt in report["cohort_receipts"]:
        key = receipt["cohort_key"]
        expected = by_key[key["arm"], key["group_id"], key["formation_on"]]
        path = values["output_dir"] / receipt["cohort_output"]
        document = read(path)
        assert document["engine_result"]["cohorts"] == [_json_value(expected)]
        assert document["navs"] == _json_value(expected["replay"].navs)
        assert saved.digest(path.read_bytes()) == receipt["cohort_sha256"]
        receipt_path = values["output_dir"] / receipt["receipt_file"]
        assert saved.digest(receipt_path.read_bytes()) == receipt["receipt_sha256"]
        completed = read(receipt_path)
        assert completed["status"] == "completed" and completed["run_id"] == receipt["run_id"]
        assert completed["result"]["cohort_sha256"] == receipt["cohort_sha256"]
        assert "replay" not in receipt["statistics"] and "exposure" not in receipt["statistics"]
        assert "path" not in receipt["statistics"]["metrics"]
        assert receipt["statistics"]["exposure_statistics"] == _json_value(
            {key: value for key, value in expected["exposure"].items() if key != "path"})
    assert saved.digest((values["output_dir"] / "report.json").read_bytes()) == result["report_sha256"]
    assert saved.digest((values["output_dir"] / "completion.json").read_bytes()) == result["completion_sha256"]
    assert report["independent_output_verification"] == result["independent_output_verification"] == "pending"
    assert "actual_study_completed" not in report and "source_truth_certified" not in report


def test_cash_only_zero_budget_cohorts_are_all_logged_with_undefined_denominator(tmp_path):
    values = fixture(tmp_path, ks=(0, 0))
    batch = full(values)
    result = module.run_cohort_family(**values)
    report = result["report"]
    assert report["aggregates"] == _json_value(batch["aggregates"])
    assert report["comparisons"] == _json_value(batch["comparisons"])
    assert report["cohort_evaluation_counts"] == {"D1": 12, "D2": 12}
    assert len(records(values["runs_path"])) == 48
    for receipt in report["cohort_receipts"]:
        stats = receipt["statistics"]
        assert Decimal(stats["assigned_capital"]) == Decimal(stats["filled_principal"]) == 0
        assert stats["metrics"]["total_return"] is None
        document = read(values["output_dir"] / receipt["cohort_output"])
        assert set(document["navs"]) == {"1"}
        assert all(book["lots"] == [] for book in document["engine_result"]["cohorts"][0]["replay"]["books"])
    assert all(row["rate"] is None and row["union_count"] == 0 for row in report["target_selection_change"].values())


def test_streaming_preserves_existing_bounded_raw_component_binding_and_original_books(tmp_path):
    prepared = raw_package()
    binding = prepare_cohort_scope(**prepared)
    raw = prepared["quotes"]
    private = tmp_path / "private"; private.mkdir(mode=0o700)
    values = dict(declarations=prepared["declarations"], holding_inputs=raw.base, events=binding.events,
        raw_quote_view=raw, parameters=deepcopy(prepared["parameters"]), scope_binding=binding,
        study_id="synthetic-bounded-raw-stream", source_manifest_sha256="1" * 64, specification_sha256="2" * 64,
        runs_path=tmp_path / "log.jsonl", output_dir=private / "cohorts")
    batch = full(values)
    result = module.run_cohort_family(**values)
    assert result["report"]["aggregates"] == _json_value(batch["aggregates"])
    assert result["report"]["comparisons"] == _json_value(batch["comparisons"])
    assert result["report"]["quote_snapshot_sha256"] == raw.quote_snapshot_sha256
    expected = {(row["arm"], row["group_id"], row["formation_on"].isoformat()): row for row in batch["cohorts"]}
    for receipt in result["report"]["cohort_receipts"]:
        key = receipt["cohort_key"]
        original = expected[key["arm"], key["group_id"], key["formation_on"]]
        document = read(values["output_dir"] / receipt["cohort_output"])
        assert document["engine_result"]["cohorts"] == [_json_value(original)]
        assert document["engine_result"]["conditional_exit_bounds_consumed"] is True
    assert binding.original.requested_coordinates == prepared["original"].requested_coordinates
    assert max(day for _, day in raw.requested_coordinates) == raw.calendar[188]


def test_caller_decimal_precision_cannot_change_registered_budgets_or_summary(tmp_path):
    values = fixture(tmp_path, ks=(7, 1))
    batch = full(values)
    with localcontext() as context:
        context.prec = 1
        result = module.run_cohort_family(**values)
    assert result["report"]["aggregates"] == _json_value(batch["aggregates"])
    assert result["report"]["comparisons"] == _json_value(batch["comparisons"])
    plan = read(values["output_dir"] / "cohort-plan.json")
    assert {row["assigned_capital"] for row in plan["cohorts"] if row["target_ceiling"] == 7} == {"0.35"}


def test_absent_entries_keep_budget_cost_cash_and_original_targets_without_substitution(tmp_path):
    seed = package(ks=(2, 1))
    days = seed["original"].calendar
    changes = {(code, day): None for code in CODES for day in days[61:63]}
    values = fixture(tmp_path, changes=changes)
    result = module.run_cohort_family(**values)
    first = min(row.formation_on for row in values["declarations"]).isoformat()
    for receipt in result["report"]["cohort_receipts"]:
        if receipt["cohort_key"]["formation_on"] == first:
            stats = receipt["statistics"]
            assert stats["chosen_codes"] and stats["filled_entries"] == 0
            assert Decimal(stats["assigned_capital"]) == Decimal("0.1")
            assert Decimal(stats["filled_principal"]) == Decimal(stats["entry_cost_amount"]) == 0
    for groups in result["report"]["aggregates"].values():
        assert all(Decimal(row["assigned_capital"]) == Decimal("0.15") for row in groups.values())


def test_lag_specific_zero_targets_have_declared_selection_change_and_keep_proof_union(tmp_path):
    values = package(ks=(1, 0))
    rows = []
    first = min(row.formation_on for row in values["declarations"])
    for row in values["declarations"]:
        if row.arm == "D2" and row.formation_on == first:
            row = replace(row, activity_targets=(), resolved_prefix=(),
                activity_targets_sha256=activity_targets_fingerprint(arm=row.arm, formation_on=row.formation_on,
                    decision_on=row.decision_on, selection_at=row.selection_at, targets=(), seed=row.seed))
        rows.append(row)
    values["declarations"] = tuple(rows)
    binding = prepare_cohort_scope(**values)
    private = tmp_path / "private"; private.mkdir(mode=0o700)
    result = module.run_cohort_family(values["declarations"], values["quotes"], binding.events,
        parameters=values["parameters"], scope_binding=binding, study_id="synthetic-zero-lag",
        source_manifest_sha256="1" * 64, specification_sha256="2" * 64,
        runs_path=tmp_path / "log.jsonl", output_dir=private / "cohorts")
    for group, changed in result["report"]["target_selection_change"].items():
        paired = {arm: tuple((row.formation_on, row.chosen_codes) for row in sorted(
            (row for row in rows if row.arm == arm and row.group_id == group), key=lambda row: row.formation_on))
            for arm in comparison.ARMS}
        assert changed == _overlap(paired["D1"], paired["D2"])
        assert changed["rate"] == "1" and changed["symmetric_difference_count"] == changed["union_count"] == 1


def test_first_failure_preserves_completed_books_and_canonical_failure_and_refuses_retry(tmp_path, monkeypatch):
    values = fixture(tmp_path)
    real = comparison._prepare_cohort_replay
    calls = []
    def fail_third_prepare(*args, **kwargs):
        prepared, execute = real(*args, **kwargs)
        def fail_third(candidate, keys):
            calls.append(keys[0])
            if len(calls) == 3:
                raise ValueError("synthetic third-cohort failure")
            return execute(candidate, keys)
        return prepared, fail_third
    monkeypatch.setattr(comparison, "_prepare_cohort_replay", fail_third_prepare)
    with pytest.raises(ValueError, match="third-cohort"):
        module.run_cohort_family(**values)
    log = records(values["runs_path"])
    assert [row["status"] for row in log] == ["started", "completed", "started", "completed", "started", "failed"]
    failure = read(values["output_dir"] / "failure.json")
    assert len(failure["engine_callbacks_started"]) == 3 and len(failure["canonically_completed_cohorts"]) == 2
    assert failure["completed_cohort_studies_by_arm"] == {"D1": 2, "D2": 0}
    assert failure["partial_package_preserved"] and failure["automatic_retries"] == 0
    assert len(list(values["output_dir"].glob("*.receipt.json"))) == 2
    assert len(list(values["output_dir"].glob("cohort-*.failure.json"))) == 1
    assert not (values["output_dir"] / "report.json").exists()
    before = values["runs_path"].read_bytes()
    with pytest.raises(ValueError, match="fresh private output"):
        module.run_cohort_family(**values)
    assert len(calls) == 3 and values["runs_path"].read_bytes() == before


def test_partial_output_write_is_preserved_without_counting_a_completion_or_retry(tmp_path, monkeypatch):
    values = fixture(tmp_path)
    real = saved.write_exclusive
    def partial(path, raw):
        if path.name.startswith("cohort-0002-") and path.name.endswith(".json") and ".receipt." not in path.name and ".failure." not in path.name:
            real(path, b"preserved partial synthetic output")
            raise OSError("synthetic write failure")
        return real(path, raw)
    monkeypatch.setattr(saved, "write_exclusive", partial)
    with pytest.raises(OSError, match="write failure"):
        module.run_cohort_family(**values)
    assert [row["status"] for row in records(values["runs_path"])] == ["started", "completed", "started", "failed"]
    failure = read(values["output_dir"] / "failure.json")
    assert failure["completed_cohort_studies_by_arm"] == {"D1": 1, "D2": 0}
    partials = [path for path in values["output_dir"].glob("cohort-0002-*.json")
        if ".receipt." not in path.name and ".failure." not in path.name]
    assert len(partials) == 1 and partials[0].read_bytes() == b"preserved partial synthetic output"
    assert not (values["output_dir"] / "completion.json").exists()


def test_canonical_completion_append_failure_keeps_start_and_book_but_claims_no_completion(tmp_path, monkeypatch):
    values = fixture(tmp_path)
    real = experiment_log._append_record
    def refuse(record, runs_path):
        if record["status"] == "completed":
            raise OSError("synthetic completion append failure")
        return real(record, runs_path)
    monkeypatch.setattr(experiment_log, "_append_record", refuse)
    with pytest.raises(OSError, match="completion append"):
        module.run_cohort_family(**values)
    assert [row["status"] for row in records(values["runs_path"])] == ["started"]
    assert len(list(values["output_dir"].glob("cohort-0001-*.json"))) == 1
    assert read(values["output_dir"] / "failure.json")["completed_cohort_studies_by_arm"] == {"D1": 0, "D2": 0}


@pytest.mark.parametrize("mutation", ["parameter", "source_pin", "spec_pin", "binding", "relative_output", "relative_log", "log_inside", "existing_root", "public_parent"])
def test_invalid_frozen_contract_or_output_boundary_never_starts_engine(tmp_path, monkeypatch, mutation):
    values = fixture(tmp_path)
    if mutation == "parameter": values["parameters"]["threshold"] = 3
    elif mutation == "source_pin": values["source_manifest_sha256"] = "bad"
    elif mutation == "spec_pin": values["specification_sha256"] = "bad"
    elif mutation == "binding": values["scope_binding"] = None
    elif mutation == "relative_output": values["output_dir"] = Path("relative")
    elif mutation == "relative_log": values["runs_path"] = Path("relative.jsonl")
    elif mutation == "log_inside": values["runs_path"] = values["output_dir"] / "log.jsonl"
    elif mutation == "existing_root": values["output_dir"].mkdir(mode=0o700)
    else: values["output_dir"].parent.chmod(0o755)
    monkeypatch.setattr(comparison, "_prepare_cohort_replay", lambda *args, **kwargs: pytest.fail("invalid input reached engine"))
    with pytest.raises(ValueError):
        module.run_cohort_family(**values)
    if values["runs_path"].is_absolute():
        assert not values["runs_path"].exists()


def test_outputs_are_private_existing_canonical_log_mode_and_umask_are_preserved(tmp_path):
    values = fixture(tmp_path, ks=(0, 0))
    values["runs_path"].parent.mkdir()
    values["runs_path"].write_bytes(b"")
    values["runs_path"].chmod(0o644)
    previous = os.umask(0o022)
    try:
        result = module.run_cohort_family(**values)
        after = os.umask(0o022)
        assert after == 0o022
    finally:
        os.umask(previous)
    assert result["status"] == "logged_cohort_family_executed"
    assert values["output_dir"].stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in values["output_dir"].iterdir())
    assert values["runs_path"].stat().st_mode & 0o777 == 0o644


def test_all_168_policy_books_plans_trials_receipts_and_aggregates_keep_identical_pin(tmp_path, monkeypatch):
    from research.activity_cohort_return_attribution import ATTRIBUTION_KEY
    from research.activity_cohort_return_policy import policy_report
    from test_activity_cohort_return_policy import synthetic_policy

    values = package(ks=(0,) * 14)
    policy = saved.encoded(synthetic_policy())
    values["expected_pins"]["return_policy"] = saved.digest(policy)
    binding = prepare_cohort_scope(**values, return_policy_json=policy)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    calls, real = [], comparison.replay_synthetic
    def counted(*args, **kwargs):
        calls.append(True)
        return real(*args, **kwargs)
    monkeypatch.setattr(comparison, "replay_synthetic", counted)
    output = private / "cohorts"
    log_path = tmp_path / "log.jsonl"
    result = module.run_cohort_family(values["declarations"], values["quotes"], binding.events,
        parameters=values["parameters"], scope_binding=binding, study_id="synthetic-168-policy",
        source_manifest_sha256="1" * 64, specification_sha256="2" * 64,
        runs_path=log_path, output_dir=output)
    flags = policy_report(policy)
    def flagged(row):
        assert all(row[key] == value for key, value in flags.items())
    assert len(calls) == 168 and result["completed_cohort_studies_by_arm"] == {"D1": 84, "D2": 84}
    plan = read(output / "cohort-plan.json")
    flagged(plan)
    assert len(plan["cohorts"]) == 168
    for row in plan["cohorts"]: flagged(row)
    report = result["report"]
    flagged(report)
    flagged(read(output / "completion.json"))
    assert len(report["cohort_receipts"]) == 168
    completed_record_bytes = {row["run_id"]: saved.encoded(row) for line in log_path.read_bytes().splitlines()
        if (row := saved.strict_json(line))["status"] == "completed"}
    for receipt in report["cohort_receipts"]:
        flagged(receipt)
        flagged(receipt["statistics"])
        document = read(output / receipt["cohort_output"])
        flagged(document)
        flagged(document["engine_result"])
        cohort, = document["engine_result"]["cohorts"]
        flagged(cohort)
        assert cohort[ATTRIBUTION_KEY]["automatic_subscription_rights_cash"] is None
        assert cohort[ATTRIBUTION_KEY]["known_issuer_pnl"] == "0"
        assert cohort["metrics"]["total_return"] is None and set(document["navs"]) == {"1"}
        saved_receipt = read(output / receipt["receipt_file"])
        flagged(saved_receipt["parameters"])
        flagged(saved_receipt["result"])
        assert (output / receipt["receipt_file"]).read_bytes() == completed_record_bytes[receipt["run_id"]]
    log = records(log_path)
    assert len(log) == 336
    for row in log: flagged(row["parameters"])
    for row in log[1::2]: flagged(row["result"])
    for groups in report["aggregates"].values():
        for row in groups.values():
            flagged(row)
            assert row[ATTRIBUTION_KEY]["assigned_capital"] == "0"
            assert row[ATTRIBUTION_KEY]["known_issuer_return_contribution"] is None
