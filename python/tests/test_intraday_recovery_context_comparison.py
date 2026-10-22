"""Invented-only nested recovery arithmetic, retained power failure and private IO."""

from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import json
import math
from pathlib import Path
import random
from statistics import NormalDist, stdev

import pytest

from research import intraday_recovery_context_comparison as cr
from test_intraday_recovery_context import fixture as cq_fixture
from test_intraday_exit_policy import SyntheticBudget
from test_intraday_signal_paths import finding


def fixture(*, sessions=5, zero_last=False, all_selected=False, fail_power=True):
    """Fully invented CQ evidence; exact-zero event days reject the new filter.

    CQ computes its new sizing from the invented labels. The eight saved CM
    own-sizing cells remain unchanged, as required for the saved-input contract.
    """
    cm_report, cg_report = cq_fixture(sessions=sessions, zero_last=zero_last)
    if not all_selected:
        _, zero = cq_fixture(sessions=sessions, zero_last=zero_last, event_return=Fraction())
        for key, variant in cg_report["variants"].items():
            index = {(row["session"], row["symbol"]): row for row in zero["variants"][key]["days"]}
            variant["days"] = [index[row["session"], row["symbol"]]
                if cg_report["reference_sessions"].index(row["session"]) % 2 else row for row in variant["days"]]
    for variant in cm_report["variants"].values():
        for row in variant["days"]:
            index = cm_report["reference_sessions"].index(row["session"])
            value = Fraction((-1) ** (index // 2), 10) if fail_power else Fraction(1, 1000)
            for saved in row["families"].values():
                if saved["labels"]:
                    for arm in cr.cm.ARMS:
                        saved["labels"][arm].update(exit_open=str(Decimal(100) * (1 + Decimal(value.numerator)
                            / Decimal(value.denominator))), price_change=value)
    report = cr.cq.build_calibration(cm_report, cg_report)
    report["specification_sha256"] = cr.UPSTREAM_SPECIFICATION_SHA256
    report["source_sha256"] = {cr.UPSTREAM_SOURCE_ROOT + "/" + name: "a" * 64 for name in cr.UPSTREAM_SOURCE_PATHS}
    report["source_sha256"][cr.UPSTREAM_SOURCE_ROOT + "/" + cr.UPSTREAM_SOURCE_PATHS[0]] = cr.UPSTREAM_SPECIFICATION_SHA256
    report["runtime"] = {"python": "3.12 invented", "implementation": "cpython",
        "executable": "/synthetic/python3.12", "executable_sha256": "b" * 64}
    return json.loads(cr._canonical(report))


def comparison(report, variant="D+1"):
    return report["variants"][variant]["comparison"]


def specification():
    return {"id": cr.SPECIFICATION_ID, "mode": cr.MODE, "window": deepcopy(cr.WINDOW),
        "parameters": deepcopy(cr.PARAMETERS), "resources": deepcopy(cr.RESOURCES), **cr.REGISTERED_PATHS,
        "upstream_sha256": cr.UPSTREAM_SHA256, "upstream_specification_sha256": cr.UPSTREAM_SPECIFICATION_SHA256,
        "exploratory_authorization": cr.exploratory_authorization()}


def test_exact_retention_ten_own_cells_failed_power_and_scoped_exception_without_remeasurement(monkeypatch):
    upstream = fixture()
    before = deepcopy(upstream)
    def forbidden(*args, **kwargs):
        pytest.fail("no raw replay or own sizing remeasurement")
    for module, name in ((cr.cq, "build_calibration"), (cr.cm, "build_calibration"), (cr.cg, "_own_power"),
            (cr.cm, "_label_pair"), (cr.ci, "build_paths"), (cr.horizons, "_statistics")):
        monkeypatch.setattr(module, name, forbidden)
    output = cr.build_comparison(upstream)
    assert upstream == before
    assert all(output[name] is False for name in cr.cc.FLAGS)
    assert output["comparison_performed"] is output["recovery_context_compared"] is output["underpower_exception_applied"] is True
    assert output["calibration_only"] is output["exit_policy_compared"] is output["requires_operator_choice"] is False
    assert output["qualified_cells_power_prerequisite_met"] is False
    assert output["upstream_calibration_requires_operator_choice"] is True
    assert output["upstream_original_calibration_requires_operator_choice"] is False
    assert output["upstream_legacy_calibration_requires_operator_choice"] is True
    assert output["exploratory_authorization"] == cr.exploratory_authorization()
    assert output["input_evidence"]["calibration_input_evidence"] == upstream["input_evidence"]
    assert output["input_evidence"]["upstream_provenance"] == {name: upstream[name]
        for name in ("specification_sha256", "source_sha256", "runtime")}
    for key, variant in output["variants"].items():
        assert {name: variant[name] for name in ("days", "families", "recovery_context")} == upstream["variants"][key]
        own = variant["recovery_context"]["own_label_dispersion"]
        assert own["mde_bps"] > 33 and own["requires_operator_choice"] is True
        assert own["underpower_exception_applied"] is False
        assert own["planning_constraint"] == "own_label_mde_exceeds_assumed_cost_floor"
        assert variant["recovery_context"]["requires_operator_choice"] is True
        stats = comparison(output, key)
        assert stats["original"]["events"] == 5 and stats["filtered"]["events"] == 3
        assert stats["selection"]["removed_events"] == 2 and stats["selection"]["retention_fraction"] == .6


def test_known_passing_synthetic_prerequisite_truth_is_derived_not_invented():
    upstream = fixture(fail_power=False)
    output = cr.build_comparison(upstream)
    assert output["qualified_cells_power_prerequisite_met"] is True
    assert output["upstream_calibration_requires_operator_choice"] is False
    assert output["requires_operator_choice"] is False
    assert output["underpower_exception_applied"] is True  # This CR authorization remains declared.


def test_exact_means_sums_cost_gross_positive_and_strict_cost_boundary():
    upstream = fixture()
    values = [Fraction(33, 10000), Fraction(-1, 10000), Fraction(35, 10000), Fraction(), Fraction(-2, 10000)]
    for variant in upstream["variants"].values():
        for row in variant["days"]:
            value = values[upstream["reference_sessions"].index(row["session"])]
            for saved in row["families"].values():
                if saved["labels"]:
                    for arm in cr.cm.ARMS:
                        saved["labels"][arm].update(exit_open=str(Decimal(100) * (1 + Decimal(value.numerator)
                            / Decimal(value.denominator))), price_change=value)
    cell = comparison(cr.build_comparison(upstream))
    assert cell["original"]["sum_price_change"] == Fraction(65, 10000)
    assert cell["filtered"]["sum_price_change"] == Fraction(66, 10000)
    assert cell["original"]["mean_price_change"] == .0013
    assert cell["filtered"]["mean_after_assumed_cost"] == -.0011
    assert cell["original"]["gross_positive_events"] == cell["filtered"]["gross_positive_events"] == 2
    assert cell["original"]["gross_positive_fraction"] == 2 / 5
    assert cell["filtered"]["gross_positive_fraction"] == 2 / 3
    assert cell["original"]["cost_exceeding_events"] == cell["filtered"]["cost_exceeding_events"] == 1
    assert cell["original"]["cost_exceeding_fraction"] == 1 / 5
    assert cell["filtered"]["cost_exceeding_fraction"] == 1 / 3
    assert cell["difference"]["mean_change"] == .0009
    assert set(cell) == {"original", "filtered", "selection", "difference"}


def test_exact_zero_condition_rejects_and_original_cg_reject_is_not_rescued(monkeypatch):
    upstream = fixture()
    output = cr.build_comparison(upstream)
    days = output["variants"]["D+1"]["days"]
    zero = [row["families"][cr.RECOVERY] for row in days if row["session"] == output["reference_sessions"][1]
        and row["families"][cr.RECOVERY]["label_cohort_state"] == "selected_complete"]
    assert zero and all(cr.co._ratio(saved["recovery_context"]["feature"]["event"]["price_change"]) == 0
        and saved["recovery_context"]["selection_state"] == "reject" for saved in zero)
    original_reject = finding(output, family=cr.RECOVERY, index=1)
    assert original_reject["recovery_context"]["selection_state"] == "reject"
    tampered = deepcopy(upstream)
    finding(tampered, family=cr.RECOVERY)["selection_state"] = "reject"
    monkeypatch.setattr(cr, "_describe", lambda *args: pytest.fail("CG derivation must fail before means"))
    with pytest.raises(cr.ComparisonRefusal):
        cr.build_comparison(tampered)


def test_hand_nested_influence_cluster_joint_ratio_bootstrap_with_zero_event_dates():
    reference = [f"2025-09-{index + 4:02d}" for index in range(11)]
    records = [{"session": reference[0], "label": Fraction(1, 100), "selected": True},
        {"session": reference[0], "label": Fraction(-2, 100), "selected": False},
        {"session": reference[1], "label": Fraction(3, 100), "selected": True},
        {"session": reference[6], "label": Fraction(4, 100), "selected": False}]
    influences = [-.015, .035, .005, -.025]
    stats = cr._statistics(records, reference, lambda: None)
    assert stats["influence_sd"] == pytest.approx(stdev(influences))
    assert stats["naive_se"] == pytest.approx(stdev(influences) / 2)
    assert stats["session_cluster_se"] == pytest.approx(math.sqrt(11 / 10 * (.02 ** 2 + .005 ** 2 + .025 ** 2)) / 4)
    rng, draws, zero = random.Random(0), [], 0
    for _ in range(2000):
        indices = []
        for size in (5, 5, 1):
            start = rng.randrange(len(reference))
            indices.extend((start + offset) % len(reference) for offset in range(size))
        sampled = [row for index in indices for row in records if row["session"] == reference[index]]
        chosen = [row["label"] for row in sampled if row["selected"]]
        if not sampled or not chosen:
            zero += 1
        else:
            draws.append(float(sum(chosen, Fraction()) / len(chosen)
                - sum((row["label"] for row in sampled), Fraction()) / len(sampled)))
    assert stats["bootstrap_se"] == pytest.approx(stdev(draws))
    assert stats["bootstrap_zero_denominator_draws"] == zero and zero > 0
    assert stats["bootstrap_valid_draws"] == 2000 - zero
    assert stats["design_se"] == max(stats[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
    assert stats["corrected_to_naive_se_ratio"] == stats["design_se"] / stats["naive_se"]
    assert stats["reference_sessions"] == 11 and stats["known_label_sessions"] == 3


def test_identical_nested_population_zero_contrast_and_calendar_opportunities():
    output = cr.build_comparison(fixture(sessions=7, zero_last=True, all_selected=True))
    cell = comparison(output)
    assert cell["original"] == cell["filtered"]
    assert cell["difference"]["mean_change"] == 0 and cell["difference"]["descriptive_normal_95_interval"] == [0, 0]
    stats = cell["difference"]["uncertainty"]
    assert stats["design_se"] == 0 and stats["corrected_to_naive_se_ratio"] is None
    assert stats["reference_sessions"] == 7 and stats["known_label_sessions"] == 6
    selection = cell["selection"]
    assert set(selection["by_week"]) == {cr.cq._week(day) for day in output["reference_sessions"]}
    assert selection["reference_weeks"] == 2
    assert selection["original_opportunities_per_week"] == selection["filtered_opportunities_per_week"] == 3
    assert selection["removed_opportunities_per_week"] == 0


def test_interval_and_week_counts_use_the_same_underlying_population_check(monkeypatch):
    real, calls = cr.cd.conclusion_check.check_same_population, []
    def observed(samples):
        calls.append(samples)
        return real(samples)
    monkeypatch.setattr(cr.cd.conclusion_check, "check_same_population", observed)
    output = cr.build_comparison(fixture(sessions=7, zero_last=True))
    assert len(calls) == 2 and all(len(set(map(tuple, samples.values()))) == 1 for samples in calls)
    cell = comparison(output)
    difference, error = cell["difference"], cell["difference"]["uncertainty"]["design_se"]
    margin = NormalDist().inv_cdf(.975) * error
    assert difference["descriptive_normal_95_interval"] == [difference["mean_change"] - margin, difference["mean_change"] + margin]
    assert sum(week["original_events"] for week in cell["selection"]["by_week"].values()) == 6
    assert sum(week["filtered_events"] for week in cell["selection"]["by_week"].values()) == 3


@pytest.mark.parametrize("target", ["header", "parameters", "flag", "choice", "upstream_exception", "spec", "source", "runtime", "lineage",
    "reference", "matrix", "vintage", "old_count", "old_own", "own_choice", "own_waiver", "own_constraint", "mde", "ratio",
    "naive", "design", "bootstrap", "known_sessions", "unknown_sizing", "feature", "context", "count", "calendar", "labels", "unknown_label", "clock", "unknown", "opening"])
def test_contract_drift_unknowns_or_failure_masking_refuses_before_means(monkeypatch, target):
    upstream = fixture()
    saved = finding(upstream, family=cr.RECOVERY)
    variant = upstream["variants"]["D+1"]
    own = variant["recovery_context"]["own_label_dispersion"]
    if target == "header": upstream["mode"] = "wrong"
    elif target == "parameters": upstream["parameters"]["boundary"] = "zero_pass"
    elif target == "flag": upstream["performance_certified"] = True
    elif target == "choice": upstream["requires_operator_choice"] = False
    elif target == "upstream_exception": upstream["underpower_exception_applied"] = True
    elif target == "spec": upstream["specification_sha256"] = "a" * 64
    elif target == "source": upstream["source_sha256"].pop(next(iter(upstream["source_sha256"])))
    elif target == "runtime": upstream["runtime"]["executable_sha256"] = "bad"
    elif target == "lineage": upstream["input_evidence"]["feature_report_sha256"] = "a" * 64
    elif target == "reference": upstream["reference_sessions"].reverse()
    elif target == "matrix": variant["days"].pop()
    elif target == "vintage": variant["days"][0]["data_vintage"] = "b" * 64
    elif target == "old_count": variant["families"][cr.RECOVERY]["selected_complete"] += 1
    elif target == "old_own": variant["families"][cr.RECOVERY]["by_arm"]["time_only"]["own_label_dispersion"]["requires_operator_choice"] = True
    elif target == "own_choice": own["requires_operator_choice"] = False
    elif target == "own_waiver": own["underpower_exception_applied"] = True
    elif target == "own_constraint": own["planning_constraint"] = "cost_floor_power_prerequisite_met"
    elif target == "mde": own["mde_bps"] = 32
    elif target == "ratio": own["corrected_to_naive_se_ratio"] = 3
    elif target == "naive": own["naive_se"] += .001
    elif target == "design": own["design_se"] += .001
    elif target == "bootstrap": own["bootstrap_zero_label_draws"] = 1999
    elif target == "known_sessions": own["known_label_sessions"] = 1
    elif target == "unknown_sizing": own["mde_bps"] = None
    elif target == "feature": saved["recovery_context"]["feature"]["excess_price_change"] = Fraction(1, 7)
    elif target == "context": saved["recovery_context"]["selection_state"] = "reject"
    elif target == "count": variant["recovery_context"]["known_labels"] += 1
    elif target == "calendar": variant["recovery_context"]["by_week"] = {}
    elif target == "labels": saved["labels"]["time_only"]["price_change"] = Fraction(1, 7)
    elif target == "unknown_label": saved["labels"]["time_only"].update(status="unknown", unknown_reason="synthetic_missing", price_change=None)
    elif target == "clock": saved["labels"]["time_only"]["exit_at"] = saved["labels"]["common"]["entry_at"]
    elif target == "unknown": variant["recovery_context"]["feature_unknown"] = 1
    elif target == "opening": variant["days"][0]["families"][cr.FAMILIES[0]]["labels"] = None
    monkeypatch.setattr(cr, "_describe", lambda *args: pytest.fail("contract refusal must precede means"))
    with pytest.raises(cr.ComparisonRefusal):
        cr.build_comparison(upstream)


def test_registered_specification_resources_and_exception_identity():
    cr.validate_spec(json.loads(cr.SPECIFICATION_PATH.read_bytes()))
    assert cr.EXPECTED_REFERENCE_SESSIONS == 257 and cr.EXPECTED_FILTERED_COMPLETE == {"D+1": 130, "D+2": 130}
    assert len(cr.UPSTREAM_SOURCE_PATHS) == 18
    assert cr.RESOURCES == {"maximum_upstream_bytes": 67108864, "minimum_free_bytes": 1073741824,
        "address_space_mib": 512, "cpu_seconds": 300, "wall_seconds": 1200, "nice": 10, "automatic_retries": 0}
    for name in ("parameters", "upstream_sha256", "upstream_specification_sha256", "resources", "output_directory", "exploratory_authorization"):
        wrong = specification()
        wrong[name] = "drift"
        with pytest.raises(cr.ComparisonRefusal, match="scope_mismatch"):
            cr.validate_spec(wrong)
    for name in cr.exploratory_authorization():
        wrong = specification()
        wrong["exploratory_authorization"][name] = "another_family_or_source"
        with pytest.raises(cr.ComparisonRefusal, match="scope_mismatch"):
            cr.validate_spec(wrong)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    upstream_path, spec_path = tmp_path / "cq.json", tmp_path / "spec.json"
    upstream_path.write_bytes(cr._canonical(fixture()))
    monkeypatch.setattr(cr, "UPSTREAM_SHA256", cr._sha(upstream_path))
    monkeypatch.setattr(cr, "EXPECTED_REFERENCE_SESSIONS", 5)
    monkeypatch.setattr(cr, "EXPECTED_SELECTED_COMPLETE", {key: {family: 5 for family in cr.FAMILIES} for key in ("D+1", "D+2")})
    monkeypatch.setattr(cr, "EXPECTED_FILTERED_COMPLETE", {"D+1": 3, "D+2": 3})
    monkeypatch.setattr(cr, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 100, "queried_rows": 39000})
    paths = {"upstream_path": str(upstream_path), "output_directory": str(tmp_path / "output"), "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(cr, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(cr, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(cr._canonical(specification()))
    return {"specification_path": spec_path, "upstream_path": upstream_path, "output_directory": Path(paths["output_directory"]),
        "runs_path": Path(paths["canonical_runs_path"]), "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_durable_start_sole_input_source20_private_output_shared_callback_and_no_retry(runner, monkeypatch):
    real_open, reads, callbacks = Path.open, [], []
    def observed(path, *args, **kwargs):
        if path == runner["upstream_path"]:
            assert statuses(runner)[0] == "started"
            reads.append(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    def verify():
        runner["budget"].checkpoint("independent_verification_same_budget")
        callbacks.append(True)
    receipt = cr.run_comparison(**runner, before_completion=verify)
    path = runner["output_directory"] / "report.json"
    report = json.loads(path.read_bytes())
    assert statuses(runner) == ["started", "completed"] and receipt["status"] == "completed" and reads and callbacks == [True]
    assert len(report["source_sha256"]) == 20
    assert all(str(Path(module.__file__)) in report["source_sha256"] for module in (cr.cp, cr.cq, cr.co))
    assert path.read_bytes() == cr._canonical(report) + b"\n"
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    assert receipt["result"]["recovery_context_compared"] is receipt["result"]["underpower_exception_applied"] is True
    assert receipt["result"]["qualified_cells_power_prerequisite_met"] is False
    assert report["exploratory_authorization"]["upstream_report_sha256"] == cr.UPSTREAM_SHA256
    with pytest.raises(cr.ComparisonRefusal, match="existing_output"):
        cr.run_comparison(**runner)


@pytest.mark.parametrize("target", ["source", "shared_math_source", "spec", "runtime", "upstream_path", "report", "budget", "callback"])
def test_final_mutation_or_shared_budget_failure_retains_failure_receipt(runner, monkeypatch, target):
    real_sha, changed = cr._sha, False
    def sha(path):
        drift = (target == "source" and path == Path(cr.__file__)) or (target == "shared_math_source" and path == Path(cr.cp.__file__)) or (target == "runtime" and path == Path(cr.sys.executable).resolve())
        return "f" * 64 if changed and drift else real_sha(path)
    monkeypatch.setattr(cr, "_sha", sha)
    def mutate():
        nonlocal changed
        changed = True
        if target == "callback": raise RuntimeError("synthetic_callback_failure")
        if target == "budget": runner["budget"].fail = True
        elif target not in {"source", "runtime", "shared_math_source"}:
            path = runner["output_directory"] / "report.json" if target == "report" else runner["specification_path" if target == "spec" else target]
            with path.open("ab") as stream: stream.write(b" ")
    with pytest.raises((cr.ComparisonRefusal, RuntimeError)):
        cr.run_comparison(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["reference", "selected", "filtered", "raw", "disk", "size", "pin"])
def test_registered_population_resources_and_input_pin_fail_closed(runner, monkeypatch, target):
    if target == "reference": monkeypatch.setattr(cr, "EXPECTED_REFERENCE_SESSIONS", 257)
    elif target == "selected": monkeypatch.setattr(cr, "EXPECTED_SELECTED_COMPLETE", {key: {family: 99 for family in cr.FAMILIES} for key in ("D+1", "D+2")})
    elif target == "filtered": monkeypatch.setattr(cr, "EXPECTED_FILTERED_COMPLETE", {"D+1": 99, "D+2": 99})
    elif target == "raw": monkeypatch.setattr(cr, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 101, "queried_rows": 39000})
    elif target == "pin": runner["upstream_path"].write_bytes(b"{}")
    else:
        resources = {**cr.RESOURCES, "minimum_free_bytes": 10**30} if target == "disk" else {**cr.RESOURCES, "maximum_upstream_bytes": 10}
        monkeypatch.setattr(cr, "RESOURCES", resources)
        runner["specification_path"].write_bytes(cr._canonical(specification()))
    with pytest.raises(cr.ComparisonRefusal, match="population_changed|free_disk|size_or_hash"):
        cr.run_comparison(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("mutation", ["copied_spec", "unregistered", "symlink", "hardlink", "source_alias"])
def test_path_registration_or_alias_refuses_before_input(runner, monkeypatch, mutation):
    if mutation == "copied_spec":
        copied = runner["specification_path"].with_name("copy.json")
        copied.write_bytes(runner["specification_path"].read_bytes())
        runner["specification_path"] = copied
    elif mutation == "unregistered": runner["upstream_path"] = runner["upstream_path"].with_name("other.json")
    else:
        source = runner["specification_path"]
        if mutation == "source_alias":
            source = source.with_name("module.py")
            source.write_text("# synthetic source\n")
            monkeypatch.setattr(cr, "__file__", str(source))
        alias = source.with_name("alias.json")
        if mutation == "symlink": alias.symlink_to(source)
        else: alias.hardlink_to(source)
        cr.REGISTERED_PATHS["upstream_path"] = str(alias)
        runner["upstream_path"] = alias
        runner["specification_path"].write_bytes(cr._canonical(specification()))
    real_open = Path.open
    def observed(path, *args, **kwargs):
        if path == runner["upstream_path"]: pytest.fail("registration refusal precedes CQ input")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(cr.ComparisonRefusal, match="unregistered|symlinked|aliased"):
        cr.run_comparison(**runner)
    assert not runner["runs_path"].exists()
