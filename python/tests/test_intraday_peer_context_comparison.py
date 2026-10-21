"""Invented-only nested selection arithmetic, saved contracts and durable IO."""

from copy import deepcopy
from fractions import Fraction
import json
import math
from pathlib import Path
import random
from statistics import NormalDist, stdev

import pytest

from research import intraday_peer_context_comparison as cp
from test_intraday_peer_context import fixture as co_fixture, feature_summaries
from test_intraday_exit_policy import SyntheticBudget
from test_intraday_signal_paths import finding


def fixture(*, sessions=3, zero_last=False, all_selected=False):
    """A complete invented CO report; alternate positive/negative peer days."""
    cm_report, cg_report = co_fixture(sessions=sessions, zero_last=zero_last)
    if not all_selected:
        _, rejected = co_fixture(sessions=sessions, zero_last=zero_last, peer_sign=-1)
        for key, variant in cg_report["variants"].items():
            index = {(row["session"], row["symbol"]): row for row in rejected["variants"][key]["days"]}
            for position, row in enumerate(variant["days"]):
                if cg_report["reference_sessions"].index(row["session"]) % 2:
                    variant["days"][position] = index[row["session"], row["symbol"]]
        feature_summaries(cg_report)
    report = cp.co.build_calibration(cm_report, cg_report)
    report["specification_sha256"] = cp.UPSTREAM_SPECIFICATION_SHA256
    report["source_sha256"] = {cp.UPSTREAM_SOURCE_ROOT + "/" + name: "a" * 64 for name in cp.UPSTREAM_SOURCE_PATHS}
    report["source_sha256"][cp.UPSTREAM_SOURCE_ROOT + "/" + cp.UPSTREAM_SOURCE_PATHS[0]] = cp.UPSTREAM_SPECIFICATION_SHA256
    report["runtime"] = {"python": "3.12 invented", "implementation": "cpython",
        "executable": "/synthetic/python3.12", "executable_sha256": "b" * 64}
    return json.loads(cp._canonical(report))


def comparison(report, variant="D+1"):
    return report["variants"][variant]["comparison"]


def specification():
    return {"id": cp.SPECIFICATION_ID, "mode": cp.MODE, "window": deepcopy(cp.WINDOW),
        "parameters": deepcopy(cp.PARAMETERS), "resources": deepcopy(cp.RESOURCES), **cp.REGISTERED_PATHS,
        "upstream_sha256": cp.UPSTREAM_SHA256, "upstream_specification_sha256": cp.UPSTREAM_SPECIFICATION_SHA256}


def test_preserve_all_rows_ten_own_cells_and_qualifications_without_remeasurement(monkeypatch):
    upstream = fixture()
    before = deepcopy(upstream)
    def forbidden(*args, **kwargs):
        pytest.fail("no raw/feature replay or own sizing calculation")
    for module, name in ((cp.co, "build_calibration"), (cp.cm, "build_calibration"), (cp.cg, "_own_power"),
            (cp.cm, "_label_pair"), (cp.ci, "build_paths"), (cp.horizons, "_statistics")):
        monkeypatch.setattr(module, name, forbidden)
    output = cp.build_comparison(upstream)
    assert upstream == before
    assert all(output[name] is False for name in cp.cc.FLAGS)
    assert output["comparison_performed"] is output["peer_context_compared"] is True
    assert output["calibration_only"] is output["exit_policy_compared"] is output["underpower_exception_applied"] is False
    assert output["qualified_cells_power_prerequisite_met"] is True and output["requires_operator_choice"] is False
    assert output["upstream_legacy_calibration_requires_operator_choice"] is True
    assert output["input_evidence"]["calibration_input_evidence"] == upstream["input_evidence"]
    assert output["input_evidence"]["upstream_provenance"] == {name: upstream[name]
        for name in ("specification_sha256", "source_sha256", "runtime")}
    for key, variant in output["variants"].items():
        assert {name: variant[name] for name in ("days", "families", "peer_context")} == upstream["variants"][key]
        stats = comparison(output, key)
        assert stats["original"]["events"] == 3 and stats["filtered"]["events"] == 2
        assert stats["selection"]["removed_events"] == 1 and stats["selection"]["retention_fraction"] == 2 / 3


def test_exact_conditional_means_sum_cost_strict_boundary_and_no_cash_zeros():
    upstream = fixture()
    values = [Fraction(33, 10000), Fraction(-1, 10000), Fraction(35, 10000)]
    for variant in upstream["variants"].values():
        for row in variant["days"]:
            value = values[upstream["reference_sessions"].index(row["session"])]
            for saved in row["families"].values():
                if saved["labels"]:
                    for arm in cp.cm.ARMS:
                        saved["labels"][arm].update(exit_open=str(float(100 * (1 + value))), price_change=value)
    cell = comparison(cp.build_comparison(upstream))
    assert cell["original"]["sum_price_change"] == Fraction(67, 10000)
    assert cell["filtered"]["sum_price_change"] == Fraction(68, 10000)
    assert cell["original"]["mean_price_change"] == float(Fraction(67, 30000))
    assert cell["filtered"]["mean_after_assumed_cost"] == .0001
    assert cell["original"]["gross_positive_events"] == cell["filtered"]["gross_positive_events"] == 2
    assert cell["original"]["cost_exceeding_events"] == cell["filtered"]["cost_exceeding_events"] == 1
    assert cell["original"]["cost_exceeding_fraction"] == 1 / 3
    assert cell["filtered"]["cost_exceeding_fraction"] == .5
    assert cell["difference"]["mean_change"] == float(Fraction(7, 6000))
    assert set(cell) == {"original", "filtered", "selection", "difference"}


def test_hand_nested_influence_cluster_and_joint_varying_denominator_bootstrap():
    reference = [f"2025-09-{index + 4:02d}" for index in range(11)]
    records = [{"session": reference[0], "label": Fraction(1, 100), "selected": True},
        {"session": reference[0], "label": Fraction(-2, 100), "selected": False},
        {"session": reference[1], "label": Fraction(3, 100), "selected": True},
        {"session": reference[6], "label": Fraction(4, 100), "selected": False}]
    # n=4,m=2, means=.015/.02; exact influences=-.015,+.035,+.005,-.025.
    influences = [-.015, .035, .005, -.025]
    stats = cp._statistics(records, reference, lambda: None)
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


def test_identical_nested_population_is_exact_zero_and_keeps_zero_event_dates():
    output = cp.build_comparison(fixture(sessions=7, zero_last=True, all_selected=True))
    cell = comparison(output)
    assert cell["original"] == cell["filtered"]
    assert cell["difference"]["mean_change"] == 0 and cell["difference"]["descriptive_normal_95_interval"] == [0, 0]
    stats = cell["difference"]["uncertainty"]
    assert stats["design_se"] == 0 and stats["corrected_to_naive_se_ratio"] is None
    assert stats["reference_sessions"] == 7 and stats["known_label_sessions"] == 6
    assert set(cell["selection"]["by_week"]) == {cp.co._week(day) for day in output["reference_sessions"]}


def test_interval_and_week_counts_follow_same_underlying_population_check(monkeypatch):
    upstream = fixture(sessions=7, zero_last=True)
    real, calls = cp.cd.conclusion_check.check_same_population, []
    def observed(samples):
        calls.append(samples)
        return real(samples)
    monkeypatch.setattr(cp.cd.conclusion_check, "check_same_population", observed)
    output = cp.build_comparison(upstream)
    assert len(calls) == 2 and all(len(set(map(tuple, samples.values()))) == 1 for samples in calls)
    cell = comparison(output)
    difference, error = cell["difference"], cell["difference"]["uncertainty"]["design_se"]
    margin = NormalDist().inv_cdf(.975) * error
    assert difference["descriptive_normal_95_interval"] == [difference["mean_change"] - margin, difference["mean_change"] + margin]
    assert sum(week["original_events"] for week in cell["selection"]["by_week"].values()) == 6
    assert sum(week["filtered_events"] for week in cell["selection"]["by_week"].values()) == 3
    assert cp._selection_counts([]) == {"original_events": 0, "filtered_events": 0, "removed_events": 0, "retention_fraction": None}


@pytest.mark.parametrize("target", ["header", "parameters", "flag", "choice", "spec", "source", "runtime", "lineage",
    "reference", "matrix", "vintage", "old_count", "old_own", "new_own", "new_mde", "feature", "context", "count", "calendar", "labels", "clock", "unknown", "recovery"])
def test_contract_drift_refuses_before_means(monkeypatch, target):
    upstream = fixture()
    saved = finding(upstream)
    variant = upstream["variants"]["D+1"]
    if target == "header": upstream["mode"] = "wrong"
    elif target == "parameters": upstream["parameters"]["boundary"] = "zero_pass"
    elif target == "flag": upstream["performance_certified"] = True
    elif target == "choice": upstream["requires_operator_choice"] = True
    elif target == "spec": upstream["specification_sha256"] = "a" * 64
    elif target == "source": upstream["source_sha256"].pop(next(iter(upstream["source_sha256"])))
    elif target == "runtime": upstream["runtime"]["executable_sha256"] = "bad"
    elif target == "lineage": upstream["input_evidence"]["feature_report_sha256"] = "a" * 64
    elif target == "reference": upstream["reference_sessions"].reverse()
    elif target == "matrix": variant["days"].pop()
    elif target == "vintage": variant["days"][0]["data_vintage"] = "b" * 64
    elif target == "old_count": variant["families"][cp.OPENING]["selected_complete"] += 1
    elif target == "old_own": variant["families"][cp.OPENING]["by_arm"]["time_only"]["own_label_dispersion"]["requires_operator_choice"] = True
    elif target == "new_own": variant["peer_context"]["own_label_dispersion"]["requires_operator_choice"] = True
    elif target == "new_mde": variant["peer_context"]["own_label_dispersion"]["mde_bps"] = 34
    elif target == "feature": saved["peer_context"]["feature"]["excess_price_change"] = Fraction(1, 7)
    elif target == "context": saved["peer_context"]["selection_state"] = "reject"
    elif target == "count": variant["peer_context"]["known_labels"] += 1
    elif target == "calendar": variant["peer_context"]["by_week"] = {}
    elif target == "labels": saved["labels"]["time_only"]["price_change"] = Fraction(1, 7)
    elif target == "clock": saved["labels"]["time_only"]["exit_at"] = saved["labels"]["common"]["entry_at"]
    elif target == "unknown": variant["peer_context"]["feature_unknown"] = 1
    elif target == "recovery": variant["days"][0]["families"][cp.FAMILIES[1]]["labels"] = None
    monkeypatch.setattr(cp, "_describe", lambda *args: pytest.fail("contract refusal must precede means"))
    with pytest.raises(cp.ComparisonRefusal):
        cp.build_comparison(upstream)


def test_registered_specification_and_resources():
    cp.validate_spec(json.loads(cp.SPECIFICATION_PATH.read_bytes()))
    assert cp.EXPECTED_REFERENCE_SESSIONS == 257 and cp.EXPECTED_FILTERED_COMPLETE == {"D+1": 577, "D+2": 574}
    assert cp.RESOURCES == {"maximum_upstream_bytes": 67108864, "minimum_free_bytes": 1073741824,
        "address_space_mib": 512, "cpu_seconds": 300, "wall_seconds": 1200, "nice": 10, "automatic_retries": 0}
    for name in ("parameters", "upstream_sha256", "upstream_specification_sha256", "resources", "output_directory"):
        wrong = specification()
        wrong[name] = "drift"
        with pytest.raises(cp.ComparisonRefusal, match="scope_mismatch"):
            cp.validate_spec(wrong)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    upstream_path, spec_path = tmp_path / "co.json", tmp_path / "spec.json"
    upstream_path.write_bytes(cp._canonical(fixture()))
    monkeypatch.setattr(cp, "UPSTREAM_SHA256", cp._sha(upstream_path))
    monkeypatch.setattr(cp, "EXPECTED_REFERENCE_SESSIONS", 3)
    monkeypatch.setattr(cp, "EXPECTED_SELECTED_COMPLETE", {key: {family: 3 for family in cp.FAMILIES} for key in ("D+1", "D+2")})
    monkeypatch.setattr(cp, "EXPECTED_FILTERED_COMPLETE", {"D+1": 2, "D+2": 2})
    monkeypatch.setattr(cp, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 60, "queried_rows": 23400})
    paths = {"upstream_path": str(upstream_path), "output_directory": str(tmp_path / "output"), "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(cp, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(cp, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(cp._canonical(specification()))
    return {"specification_path": spec_path, "upstream_path": upstream_path, "output_directory": Path(paths["output_directory"]),
        "runs_path": Path(paths["canonical_runs_path"]), "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_durable_start_sole_input_source18_private_output_shared_callback_and_no_retry(runner, monkeypatch):
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
    receipt = cp.run_comparison(**runner, before_completion=verify)
    path = runner["output_directory"] / "report.json"
    report = json.loads(path.read_bytes())
    assert statuses(runner) == ["started", "completed"] and receipt["status"] == "completed" and reads and callbacks == [True]
    assert len(report["source_sha256"]) == 18 and str(Path(cp.co.__file__)) in report["source_sha256"]
    assert path.read_bytes() == cp._canonical(report) + b"\n"
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    assert receipt["result"]["peer_context_compared"] is True
    with pytest.raises(cp.ComparisonRefusal, match="existing_output"):
        cp.run_comparison(**runner)


@pytest.mark.parametrize("target", ["source", "spec", "runtime", "upstream_path", "report", "budget", "callback"])
def test_final_mutation_or_shared_budget_failure_retains_failure_receipt(runner, monkeypatch, target):
    real_sha, changed = cp._sha, False
    def sha(path):
        drift = (target == "source" and path == Path(cp.__file__)) or (target == "runtime" and path == Path(cp.sys.executable).resolve())
        return "f" * 64 if changed and drift else real_sha(path)
    monkeypatch.setattr(cp, "_sha", sha)
    def mutate():
        nonlocal changed
        changed = True
        if target == "callback": raise RuntimeError("synthetic_callback_failure")
        if target == "budget": runner["budget"].fail = True
        elif target not in {"source", "runtime"}:
            path = runner["output_directory"] / "report.json" if target == "report" else runner["specification_path" if target == "spec" else target]
            with path.open("ab") as stream: stream.write(b" ")
    with pytest.raises((cp.ComparisonRefusal, RuntimeError)):
        cp.run_comparison(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["reference", "selected", "filtered", "raw", "disk", "size", "pin"])
def test_registered_population_resources_and_input_pin_fail_closed(runner, monkeypatch, target):
    if target == "reference": monkeypatch.setattr(cp, "EXPECTED_REFERENCE_SESSIONS", 257)
    elif target == "selected": monkeypatch.setattr(cp, "EXPECTED_SELECTED_COMPLETE", {key: {family: 99 for family in cp.FAMILIES} for key in ("D+1", "D+2")})
    elif target == "filtered": monkeypatch.setattr(cp, "EXPECTED_FILTERED_COMPLETE", {"D+1": 99, "D+2": 99})
    elif target == "raw": monkeypatch.setattr(cp, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 61, "queried_rows": 23400})
    elif target == "pin": runner["upstream_path"].write_bytes(b"{}")
    else:
        resources = {**cp.RESOURCES, "minimum_free_bytes": 10**30} if target == "disk" else {**cp.RESOURCES, "maximum_upstream_bytes": 10}
        monkeypatch.setattr(cp, "RESOURCES", resources)
        runner["specification_path"].write_bytes(cp._canonical(specification()))
    with pytest.raises(cp.ComparisonRefusal, match="population_changed|free_disk|size_or_hash"):
        cp.run_comparison(**runner)
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
            monkeypatch.setattr(cp, "__file__", str(source))
        alias = source.with_name("alias.json")
        if mutation == "symlink": alias.symlink_to(source)
        else: alias.hardlink_to(source)
        cp.REGISTERED_PATHS["upstream_path"] = str(alias)
        runner["upstream_path"] = alias
        runner["specification_path"].write_bytes(cp._canonical(specification()))
    real_open = Path.open
    def observed(path, *args, **kwargs):
        if path == runner["upstream_path"]: pytest.fail("registration refusal precedes CO input")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(cp.ComparisonRefusal, match="unregistered|symlinked|aliased"):
        cp.run_comparison(**runner)
    assert not runner["runs_path"].exists()
