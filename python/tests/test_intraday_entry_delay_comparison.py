"""Synthetic-only fixed-exit cash-unit comparison and durable runner tests."""

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
from statistics import NormalDist

import pytest

from research import intraday_entry_delay_comparison as cl
from test_intraday_entry_delay import fixture as calibration_fixture, minute_rows, cell, finding, SyntheticBudget


CONTINUATION, RECOVERY = cl.FAMILIES


def fixture(raw=None, *, sessions=2):
    upstream, chunks = calibration_fixture(raw, sessions=sessions)
    return json.loads(cl._canonical(cl.ck.build_calibration(upstream, chunks)))


def specification():
    return {"id": cl.SPECIFICATION_ID, "mode": cl.MODE, "window": deepcopy(cl.WINDOW),
        "parameters": deepcopy(cl.PARAMETERS), "resources": deepcopy(cl.RESOURCES),
        **cl.REGISTERED_PATHS, "upstream_sha256": cl.UPSTREAM_SHA256}


def test_hand_oracle_uses_same_cash_own_entry_fixed_exit_and_exact_cost():
    raw = minute_rows()
    raw[38].update(open="99", high="100", low="99")
    raw[44].update(high="102", close="102")
    upstream = fixture(raw)
    output = cl.build_comparison(upstream)
    summary = cell(output)
    assert summary["current"] == {"events": 2, "sum_price_change": Fraction(1, 25),
        "mean_price_change": .02, "mean_after_assumed_cost": .0167,
        "gross_positive_events": 2, "cost_exceeding_events": 2}
    early, difference = Fraction(1, 33), Fraction(1, 33) - Fraction(1, 50)
    assert summary["earlier"]["sum_price_change"] == 2 * early
    assert summary["earlier"]["mean_after_assumed_cost"] == float(early - Fraction(33, 10000))
    assert summary["paired"]["sum_change"] == 2 * difference
    assert summary["paired"]["mean_change"] == float(difference)
    assert (early - cl.COST) - (Fraction(1, 50) - cl.COST) == difference
    assert summary["paired"]["improved_events"] == 2
    assert summary["paired"]["equal_events"] == summary["paired"]["worse_events"] == 0
    assert summary["paired"]["descriptive_normal_95_interval"] == [float(difference)] * 2
    for key, variant in output["variants"].items():
        assert variant["days"] == upstream["variants"][key]["days"]
        for family in cl.FAMILIES:
            for h, item in variant["families"][family]["by_horizon"].items():
                assert {name: item[name] for name in upstream["variants"][key]["families"][family]["by_horizon"][h]} == upstream["variants"][key]["families"][family]["by_horizon"][h]
    assert output["upstream_legacy_calibration_requires_operator_choice"] is True
    assert output["upstream_calibration_requires_operator_choice"] is output["requires_operator_choice"] is False


def test_all_cells_masks_unknowns_metadata_flags_preserved_without_producer_or_own_power(monkeypatch):
    upstream = fixture()
    original = deepcopy(upstream)
    def forbidden(*args, **kwargs):
        pytest.fail("comparison may not reconstruct raw labels, selection or own-label sizing")
    monkeypatch.setattr(cl.ck, "build_calibration", forbidden)
    monkeypatch.setattr(cl.ci, "build_paths", forbidden)
    monkeypatch.setattr(cl.ch, "build_comparison", forbidden)
    monkeypatch.setattr(cl.cg, "_own_power", forbidden)
    output = cl.build_comparison(upstream)
    assert upstream == original
    assert sum(len(f["by_horizon"]) for v in output["variants"].values() for f in v["families"].values()) == 16
    assert all(output[name] is False for name in cl.cc.FLAGS)
    assert output["comparison_performed"] is output["earlier_entry_policy_compared"] is True
    assert output["calibration_only"] is output["family_adopted"] is output["horizon_selected"] is False
    assert output["input_evidence"]["calibration_input_evidence"] == upstream["input_evidence"]
    assert cell(output)["current"]["mean_after_assumed_cost"] == -.0033
    assert cell(output)["paired"]["equal_events"] == 2


def test_strict_cost_equality_negative_labels_and_worse_event_counts():
    upstream = fixture()
    for variant in upstream["variants"].values():
        rows = [row for row in variant["days"] if row["families"][CONTINUATION]["labels"] is not None]
        first, second = [row["families"][CONTINUATION]["labels"]["5"] for row in rows]
        for arm in ("current", "earlier"):
            first[arm].update(entry_open="100", terminal_close="100.33", price_change=cl.COST)
        second["current"].update(entry_open="100", terminal_close="99", price_change=Fraction(-1, 100))
        second["earlier"].update(entry_open="110", terminal_close="99", price_change=Fraction(-1, 10))
    summary = cell(cl.build_comparison(upstream))
    for arm in ("current", "earlier"):
        assert summary[arm]["gross_positive_events"] == 1
        assert summary[arm]["cost_exceeding_events"] == 0
    assert summary["paired"]["sum_change"] == Fraction(-9, 100)
    assert summary["paired"]["worse_events"] == summary["paired"]["equal_events"] == 1
    assert summary["paired"]["improved_events"] == 0


def test_paired_uncertainty_uses_all_sessions_and_descriptive_interval(monkeypatch):
    upstream = fixture(sessions=7)
    # Preserve the original matrix but make the last date unselected everywhere.
    for variant in upstream["variants"].values():
        for row in variant["days"]:
            if row["session"] == upstream["reference_sessions"][-1]:
                for family in cl.FAMILIES:
                    finding_ = row["families"][family]
                    if finding_["label_cohort_state"] == "selected_complete":
                        finding_.update(selection_state="reject", label_cohort_state="not_selected", labels=None)
        for family in cl.FAMILIES:
            variant["families"][family].update(cl.cg._counts([row["families"][family] for row in variant["days"]]))
            for summary in variant["families"][family]["by_horizon"].values():
                summary.update(selected_complete=6, earlier_known_labels=6)
                for arm in ("current_own_power", "earlier_own_power"):
                    summary[arm].update(measured_labels=6, known_label_sessions=6)
    actual, calls = cl.horizons._statistics, []
    def observed(records, reference, checkpoint):
        calls.append((len(records), list(reference)))
        return actual(records, reference, checkpoint)
    monkeypatch.setattr(cl.horizons, "_statistics", observed)
    output = cl.build_comparison(upstream)
    assert len(calls) == 16 and all(n == 6 and days == upstream["reference_sessions"] for n, days in calls)
    assert cell(output)["paired"]["uncertainty"]["reference_sessions"] == 7
    assert cell(output)["paired"]["uncertainty"]["known_label_sessions"] == 6
    # A nonconstant pair obtains the exact registered normal interval.
    for variant in upstream["variants"].values():
        pair = variant["days"][0]["families"][CONTINUATION]["labels"]["5"]
        pair["earlier"].update(entry_open="99.99", price_change=Fraction(100) / Fraction("99.99") - 1)
    output = cl.build_comparison(upstream)
    paired = cell(output)["paired"]
    margin = NormalDist().inv_cdf(.975) * paired["uncertainty"]["design_se"]
    assert paired["descriptive_normal_95_interval"] == [paired["mean_change"] - margin, paired["mean_change"] + margin]
    assert paired["uncertainty"]["design_se"] == max(paired["uncertainty"][name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))


@pytest.mark.parametrize("target", ["header", "parameters", "flag", "choice", "reference", "target", "counts", "mask", "horizon", "clock", "holding", "ratio", "terminal", "label_unknown", "original_unknown", "original_label", "metadata", "vintage", "cell_choice", "current_status", "current_power", "earlier_power", "earlier_unknown"])
def test_upstream_drift_or_unresolved_prerequisite_refuses_before_means(monkeypatch, target):
    upstream = fixture()
    pair = finding(upstream)["labels"]["5"]
    summary = cell(upstream)
    if target == "header": upstream["mode"] = "wrong"
    elif target == "parameters": upstream["parameters"]["cost_floor_bps"] = "34"
    elif target == "flag": upstream["performance_certified"] = True
    elif target == "choice": upstream["requires_operator_choice"] = True
    elif target == "reference": upstream["reference_sessions"].reverse()
    elif target == "target": upstream["variants"]["D+1"]["days"].pop()
    elif target == "counts": upstream["variants"]["D+1"]["families"][CONTINUATION]["selected_complete"] = 1
    elif target == "mask": finding(upstream)["selection_state"] = "reject"
    elif target == "horizon": finding(upstream)["labels"].pop("120")
    elif target == "clock": pair["exit_at"] = pair["current_entry_at"]
    elif target == "holding": pair["earlier_holding_minutes"] = 5
    elif target == "ratio": pair["earlier"]["price_change"] = Fraction(1, 100)
    elif target == "terminal": pair["earlier"].update(terminal_close="101", price_change=Fraction(1, 100))
    elif target == "label_unknown": pair["earlier"].update(status="unknown", price_change=None, missing_minute_buckets=1, unknown_reason="incomplete_earlier_entry_path")
    elif target == "original_unknown": finding(upstream)["original_unknown_control_symbols"] = ["KRX:000002"]
    elif target == "original_label": finding(upstream)["original_event_label_state"]["status"] = "unknown"
    elif target == "metadata": upstream["variants"]["D+1"]["days"][0]["raw_chunk_metadata"]["available_at"] = "2025-09-04T00:00:00Z"
    elif target == "vintage": upstream["variants"]["D+1"]["days"][0]["data_vintage"] = "x"
    elif target == "cell_choice": summary["requires_operator_choice"] = True
    elif target == "current_status": summary["current_power_prerequisite_met"] = False
    elif target in {"current_power", "earlier_power"}: summary[target.replace("power", "own_power")]["mde_bps"] = 34
    elif target == "earlier_unknown": summary["earlier_unknown_labels"] = 1
    monkeypatch.setattr(cl, "_describe", lambda *args: pytest.fail("all upstream gates precede means"))
    with pytest.raises(cl.ComparisonRefusal):
        cl.build_comparison(upstream)


@pytest.mark.parametrize("arm", ["current_own_power", "earlier_own_power"])
@pytest.mark.parametrize("name,value", [("event_sd", 1), ("design_se", None), ("known_label_sessions", 3),
    ("requires_operator_choice", True), ("underpower_exception_applied", True), ("planning_constraint", "unknown")])
def test_each_own_power_contract_is_checked_not_paired_difference(arm, name, value):
    upstream = fixture()
    cell(upstream)[arm][name] = value
    with pytest.raises(cl.ComparisonRefusal, match="power_prerequisite"):
        cl.build_comparison(upstream)


def test_committed_registration_and_resource_limits():
    cl.validate_spec(json.loads(cl.SPECIFICATION_PATH.read_bytes()))
    assert cl.RESOURCES == {"maximum_upstream_bytes": 67108864, "minimum_free_bytes": 1073741824,
        "address_space_mib": 512, "cpu_seconds": 300, "wall_seconds": 1200, "nice": 10, "automatic_retries": 0}
    for name in ("parameters", "resources", "upstream_sha256", "output_directory"):
        spec = specification()
        spec[name] = "drift"
        with pytest.raises(cl.ComparisonRefusal, match="scope_mismatch"):
            cl.validate_spec(spec)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    upstream = fixture()
    upstream_path, spec_path = tmp_path / "ck.json", tmp_path / "spec.json"
    upstream_path.write_bytes(cl._canonical(upstream))
    monkeypatch.setattr(cl, "UPSTREAM_SHA256", cl._sha(upstream_path))
    monkeypatch.setattr(cl, "EXPECTED_REFERENCE_SESSIONS", 2)
    monkeypatch.setattr(cl, "EXPECTED_SELECTED_COMPLETE", {key: {family: 2 for family in cl.FAMILIES} for key in ("D+1", "D+2")})
    monkeypatch.setattr(cl, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 40, "queried_rows": 15600})
    paths = {"upstream_path": str(upstream_path), "output_directory": str(tmp_path / "output"),
        "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(cl, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(cl, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(cl._canonical(specification()))
    return {"specification_path": spec_path, "upstream_path": upstream_path,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"]), "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_durable_start_before_input_private_output_and_single_shared_budget(runner, monkeypatch):
    real_open, reads = Path.open, []
    def observed(path, *args, **kwargs):
        if path == runner["upstream_path"]:
            assert statuses(runner)[0] == "started"
            reads.append(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    receipt = cl.run_comparison(**runner)
    report_path = runner["output_directory"] / "report.json"
    report = json.loads(report_path.read_bytes())
    assert statuses(runner) == ["started", "completed"] and receipt["status"] == "completed" and reads
    assert len(report["source_sha256"]) == 16
    assert str(Path(cl.ck.__file__)) in report["source_sha256"]
    assert report_path.read_bytes() == cl._canonical(report) + b"\n"
    assert report_path.stat().st_mode & 0o777 == 0o600 and report_path.parent.stat().st_mode & 0o777 == 0o700
    assert receipt["result"]["calibration_only"] is False and receipt["result"]["comparison_performed"] is True
    runner["budget"].checkpoint("independent_verification_same_budget")
    with pytest.raises(cl.ComparisonRefusal, match="existing_output"):
        cl.run_comparison(**runner)


@pytest.mark.parametrize("target", ["source", "spec", "runtime", "upstream_path", "report", "budget", "callback"])
def test_final_drift_or_shared_budget_failure_leaves_failed_receipt(runner, monkeypatch, target):
    real_sha, changed = cl._sha, False
    def sha(path):
        drift = (target == "source" and path == Path(cl.__file__)) or (target == "runtime" and path == Path(cl.sys.executable).resolve())
        return "f" * 64 if changed and drift else real_sha(path)
    monkeypatch.setattr(cl, "_sha", sha)
    def mutate():
        nonlocal changed
        changed = True
        if target == "callback": raise RuntimeError("synthetic_callback_failure")
        if target == "budget": runner["budget"].fail = True
        elif target not in {"source", "runtime"}:
            path = runner["output_directory"] / "report.json" if target == "report" else runner["specification_path" if target == "spec" else target]
            with path.open("ab") as stream: stream.write(b" ")
    with pytest.raises((cl.ComparisonRefusal, RuntimeError)):
        cl.run_comparison(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]


def test_bad_upstream_pin_fails_before_calculation(runner, monkeypatch):
    runner["upstream_path"].write_bytes(b"{}")
    monkeypatch.setattr(cl, "build_comparison", lambda *args, **kwargs: pytest.fail("bad pin precedes calculation"))
    with pytest.raises(cl.ComparisonRefusal, match="hash_mismatch"):
        cl.run_comparison(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["reference", "selected", "raw", "disk", "size"])
def test_registered_populations_and_resource_bounds(runner, monkeypatch, target):
    if target == "reference": monkeypatch.setattr(cl, "EXPECTED_REFERENCE_SESSIONS", 257)
    elif target == "selected": monkeypatch.setattr(cl, "EXPECTED_SELECTED_COMPLETE", {key: {family: 3 for family in cl.FAMILIES} for key in ("D+1", "D+2")})
    elif target == "raw": monkeypatch.setattr(cl, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 41, "queried_rows": 15600})
    else:
        resources = {**cl.RESOURCES, "minimum_free_bytes": 10**30} if target == "disk" else {**cl.RESOURCES, "maximum_upstream_bytes": 10}
        monkeypatch.setattr(cl, "RESOURCES", resources)
        runner["specification_path"].write_bytes(cl._canonical(specification()))
    with pytest.raises(cl.ComparisonRefusal, match="population_changed|free_disk|size_or_hash"):
        cl.run_comparison(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("mutation", ["copied_spec", "unregistered", "symlink", "hardlink"])
def test_registration_and_alias_refusal_precede_input(runner, monkeypatch, mutation):
    if mutation == "copied_spec":
        copied = runner["specification_path"].with_name("copy.json")
        copied.write_bytes(runner["specification_path"].read_bytes())
        runner["specification_path"] = copied
    elif mutation == "unregistered": runner["upstream_path"] = runner["upstream_path"].with_name("other.json")
    else:
        alias = runner["upstream_path"].with_name("alias.json")
        if mutation == "symlink": alias.symlink_to(runner["specification_path"])
        else: alias.hardlink_to(runner["specification_path"])
        cl.REGISTERED_PATHS["upstream_path"] = str(alias)
        runner["upstream_path"] = alias
        runner["specification_path"].write_bytes(cl._canonical(specification()))
    real_open = Path.open
    def observed(path, *args, **kwargs):
        if path == runner["upstream_path"]: pytest.fail("registration refusal must precede CK input")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(cl.ComparisonRefusal, match="unregistered|symlinked|aliased"):
        cl.run_comparison(**runner)
    assert not runner["runs_path"].exists()


def test_log_alias_to_source_is_rejected_before_any_log_write(runner, monkeypatch):
    # A harmless temporary file stands in for a pinned source; do not alter it.
    source = runner["specification_path"].with_name("module.py")
    source.write_text("# synthetic source\n")
    runner["runs_path"].hardlink_to(source)
    monkeypatch.setattr(cl, "__file__", str(source))
    with pytest.raises(cl.ComparisonRefusal, match="aliased_source"):
        cl.run_comparison(**runner)
    assert source.read_text() == "# synthetic source\n"
