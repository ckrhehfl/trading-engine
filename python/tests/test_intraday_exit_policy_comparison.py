"""Invented-only exact accounting, saved CM contracts and durable CN IO."""

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
from statistics import NormalDist

import pytest

from research import intraday_exit_policy_comparison as cn
from test_intraday_entry_delay import fixture as ch_fixture
from test_intraday_exit_policy import SyntheticBudget
from test_intraday_signal_paths import minute_rows, finding


CONTINUATION, RECOVERY = cn.FAMILIES


def fixture(raw=None, *, sessions=2, zero_last=False):
    """Supply synthetic CH data to CM; never open a saved actual report."""
    upstream, chunks = ch_fixture(raw, sessions=sessions)
    if zero_last:
        for variant in upstream["variants"].values():
            for row in variant["days"]:
                if row["session"] == upstream["reference_sessions"][-1]:
                    for saved in row["families"].values():
                        if saved["label_cohort_state"] == "selected_complete":
                            saved.update(selection_state="reject", label_cohort_state="not_selected")
                            for response in saved["responses"].values():
                                response.update(status="not_measured_not_selected", event_price_change=None,
                                    control_mean_price_change=None, excess_price_change=None)
            for family in cn.FAMILIES:
                variant["families"][family].update(cn.cg._counts([row["families"][family] for row in variant["days"]]))
    return json.loads(cn._canonical(cn.cm.build_calibration(upstream, chunks)))


def cell(report, family=CONTINUATION, variant="D+1"):
    return report["variants"][variant]["families"][family]


def all_pairs(report, day=None):
    for variant in report["variants"].values():
        for row in variant["days"]:
            if day is None or row["session"] == day:
                for saved in row["families"].values():
                    if saved["labels"] is not None:
                        yield saved["labels"]


def specification():
    return {"id": cn.SPECIFICATION_ID, "mode": cn.MODE, "window": deepcopy(cn.WINDOW),
        "parameters": deepcopy(cn.PARAMETERS), "resources": deepcopy(cn.RESOURCES),
        **cn.REGISTERED_PATHS, "upstream_sha256": cn.UPSTREAM_SHA256}


def test_hand_oracle_exact_cost_same_cash_bridge_and_trigger_reversal():
    raw = minute_rows()
    raw[40].update(close="101", high="101")
    raw[41].update(close="99", low="99")
    raw[44].update(open="98", low="98")
    raw[69].update(close="100.4", high="100.4")
    raw[70].update(open="100.7", high="100.7")
    upstream = fixture(raw)
    output = cn.build_comparison(upstream)
    summary = cell(output)
    assert summary["time_only"] == {"events": 2, "sum_price_change": Fraction(7, 500),
        "mean_price_change": .007, "mean_after_assumed_cost": .0037,
        "gross_positive_events": 2, "cost_exceeding_events": 2}
    assert summary["managed"] == {"events": 2, "sum_price_change": Fraction(-1, 25),
        "mean_price_change": -.02, "mean_after_assumed_cost": -.0233,
        "gross_positive_events": 0, "cost_exceeding_events": 0}
    paired = summary["paired"]
    assert paired["sum_change"] == Fraction(-27, 500)
    assert paired["mean_change"] == -.027
    assert paired["worse_events"] == 2 and paired["equal_events"] == paired["improved_events"] == 0
    assert paired["descriptive_normal_95_interval"] == [-.027] * 2
    assert summary["old_close_bridge"]["old_close"]["sum_price_change"] == Fraction(1, 125)
    assert summary["old_close_bridge"]["time_open_minus_old_close"] == {
        "events": 2, "sum_change": Fraction(3, 500), "mean_change": .003}
    assert summary["exit_usage"] == {"take_profit": 2, "stop_loss": 0, "time_cap": 0,
        "holding_minutes": {"min": 4, "max": 4, "sum": 8, "mean": 4.0}}
    assert (Fraction(-1, 50) - cn.COST) - (Fraction(7, 1000) - cn.COST) == Fraction(-27, 1000)
    for pair in all_pairs(upstream):
        time, managed = (cn.horizons._ratio(pair[arm]["price_change"]) for arm in cn.ARMS)
        anchor = cn.horizons._ratio(pair["common"]["anchor_price_change"])
        assert managed - anchor == (managed - time) + (time - anchor)


def test_all_four_groups_rows_unknowns_metadata_own_sizing_and_flags_preserved(monkeypatch):
    upstream = fixture()
    original = deepcopy(upstream)
    def forbidden(*args, **kwargs):
        pytest.fail("CN must not reconstruct raw labels selection or own-label sizing")
    monkeypatch.setattr(cn.cm, "build_calibration", forbidden)
    monkeypatch.setattr(cn.cm, "_label_pair", forbidden)
    monkeypatch.setattr(cn.ci, "build_paths", forbidden)
    monkeypatch.setattr(cn.cg, "_own_power", forbidden)
    output = cn.build_comparison(upstream)
    assert upstream == original
    assert sum(len(variant["families"]) for variant in output["variants"].values()) == 4
    for key, variant in output["variants"].items():
        assert variant["days"] == upstream["variants"][key]["days"]
        for family, summary in variant["families"].items():
            assert {name: summary[name] for name in cell(upstream, family, key)} == cell(upstream, family, key)
    assert all(output[name] is False for name in cn.cc.FLAGS)
    assert output["comparison_performed"] is output["exit_policy_compared"] is True
    assert output["calibration_only"] is output["actual_portfolio_pnl"] is output["family_adopted"] is False
    assert output["upstream_calibration_requires_operator_choice"] is output["requires_operator_choice"] is False
    assert output["upstream_legacy_calibration_requires_operator_choice"] is True
    assert output["input_evidence"]["calibration_input_evidence"] == upstream["input_evidence"]
    assert cell(output)["paired"]["equal_events"] == 2
    assert cell(output)["exit_usage"] == {"take_profit": 0, "stop_loss": 0, "time_cap": 2,
        "holding_minutes": {"min": 30, "max": 30, "sum": 60, "mean": 30.0}}


def test_improved_and_equal_counts_stop_trigger_gap_and_exact_cost_equality():
    raw = minute_rows()
    raw[40].update(close="99.5", low="99.5")
    raw[44].update(open="100.33", high="100.33")
    raw[70].update(open="99", low="99")
    upstream = fixture(raw)
    # On the second day preserve a valid no-trigger fallback, with a cost-exact label.
    for pair in all_pairs(upstream, upstream["reference_sessions"][1]):
        time = pair["time_only"]
        time.update(exit_open="100.33", price_change=cn.COST)
        pair["managed"] = {**deepcopy(time), "reason": "time_cap", "trigger_trace": None}
    summary = cell(cn.build_comparison(upstream))
    assert summary["managed"]["gross_positive_events"] == 2
    assert summary["managed"]["cost_exceeding_events"] == 0
    assert summary["managed"]["mean_after_assumed_cost"] == 0
    assert summary["time_only"]["cost_exceeding_events"] == 0
    assert summary["paired"]["sum_change"] == Fraction(133, 10000)
    assert summary["paired"]["improved_events"] == summary["paired"]["equal_events"] == 1
    assert summary["paired"]["worse_events"] == 0
    assert summary["exit_usage"] == {"take_profit": 0, "stop_loss": 1, "time_cap": 1,
        "holding_minutes": {"min": 4, "max": 30, "sum": 34, "mean": 17.0}}


def test_all_reference_sessions_including_zero_dates_and_registered_interval(monkeypatch):
    upstream = fixture(sessions=7, zero_last=True)
    raw = minute_rows()
    raw[40].update(close="101", high="101")
    raw[44].update(open="100.1", high="100.1")
    alternate = fixture(raw, sessions=7, zero_last=True)
    for key, variant in upstream["variants"].items():
        for family in cn.FAMILIES:
            variant["days"][0]["families"][family]["labels"] = alternate["variants"][key]["days"][0]["families"][family]["labels"]
    actual, calls = cn.horizons._statistics, []
    def observed(records, reference, checkpoint):
        calls.append((len(records), list(reference)))
        return actual(records, reference, checkpoint)
    monkeypatch.setattr(cn.horizons, "_statistics", observed)
    paired = cell(cn.build_comparison(upstream))["paired"]
    assert len(calls) == 4 and all(count == 6 and dates == upstream["reference_sessions"] for count, dates in calls)
    assert paired["uncertainty"]["reference_sessions"] == 7 and paired["uncertainty"]["known_label_sessions"] == 6
    error = paired["uncertainty"]["design_se"]
    assert error == max(paired["uncertainty"][name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
    margin = NormalDist().inv_cdf(.975) * error
    assert paired["descriptive_normal_95_interval"] == [paired["mean_change"] - margin, paired["mean_change"] + margin]


@pytest.mark.parametrize("target", ["header", "parameters", "flag", "choice", "reference", "target", "counts", "mask", "labels", "common_clock", "entry_wait", "anchor_ratio", "price_ratio", "entry_price", "label_unknown", "original_unknown", "original_label", "metadata", "vintage", "source_crossvariant", "event_crossfamily", "own_choice", "known", "unknown", "input_lineage", "raw_count", "trace", "reason", "fill", "trigger_close", "trigger_available", "trigger_ratio", "fallback", "duration"])
def test_contract_drift_and_failed_prerequisites_refuse_before_means(monkeypatch, target):
    upstream = fixture()
    pair = finding(upstream)["labels"]
    summary = cell(upstream)
    if target == "header": upstream["mode"] = "wrong"
    elif target == "parameters": upstream["parameters"]["cost_floor_bps"] = "34"
    elif target == "flag": upstream["performance_certified"] = True
    elif target == "choice": upstream["requires_operator_choice"] = True
    elif target == "reference": upstream["reference_sessions"].reverse()
    elif target == "target": upstream["variants"]["D+1"]["days"].pop()
    elif target == "counts": summary["selected_complete"] = 1
    elif target == "mask": finding(upstream)["selection_state"] = "reject"
    elif target == "labels": pair.pop("time_only")
    elif target == "common_clock": pair["common"]["time_cap_decided_at"] = pair["common"]["entry_at"]
    elif target == "entry_wait": finding(upstream)["event"]["decision_at"] = pair["common"]["entry_at"]
    elif target == "anchor_ratio": pair["common"]["anchor_price_change"] = Fraction(1, 100)
    elif target == "price_ratio": pair["managed"]["price_change"] = Fraction(1, 100)
    elif target == "entry_price": pair["managed"]["entry_open"] = 100.0
    elif target == "label_unknown": pair["managed"].update(status="unknown", price_change=None, unknown_reason="missing")
    elif target == "original_unknown": finding(upstream)["original_unknown_control_symbols"] = ["KRX:000002"]
    elif target == "original_label": finding(upstream)["original_event_label_state"]["status"] = "unknown"
    elif target == "metadata": pair["time_only"]["model_metadata"]["actual_fill_certified"] = True
    elif target == "vintage": upstream["variants"]["D+1"]["days"][0]["data_vintage"] = "x"
    elif target == "source_crossvariant": upstream["variants"]["D+2"]["days"][0]["data_vintage"] = "f" * 64
    elif target == "event_crossfamily":
        pair["common"].update(anchor_close="100.01", anchor_price_change=Fraction(1, 10000))
    elif target == "own_choice": summary["by_arm"]["managed"]["requires_operator_choice"] = True
    elif target == "known": summary["by_arm"]["time_only"]["known_labels"] = 1
    elif target == "unknown": summary["by_arm"]["managed"]["unknown_labels"] = 1
    elif target == "input_lineage": upstream["input_evidence"]["compressed_sha256"] = "f" * 64
    elif target == "raw_count": upstream["input_evidence"]["unique_target_days"] += 1
    else:
        raw = minute_rows()
        raw[40].update(close="101", high="101")
        upstream = fixture(raw)
        pair = finding(upstream)["labels"]
        managed, trace = pair["managed"], pair["managed"]["trigger_trace"]
        if target == "trace": trace.pop("bar_start")
        elif target == "reason": managed["reason"] = "stop_loss"
        elif target == "fill": managed["exit_at"] = pair["common"]["time_cap_at"]
        elif target == "trigger_close": trace["close"] = "100"
        elif target == "trigger_available": trace["available_at"] = trace["close_at"]
        elif target == "trigger_ratio": trace["price_change"] = Fraction()
        elif target == "fallback": managed["reason"], managed["trigger_trace"] = "time_cap", None
        elif target == "duration": managed["exit_at"] = pair["common"]["entry_at"]
    monkeypatch.setattr(cn, "_describe", lambda *args: pytest.fail("all input contracts must precede means"))
    with pytest.raises(cn.ComparisonRefusal):
        cn.build_comparison(upstream)


@pytest.mark.parametrize("arm", cn.ARMS)
@pytest.mark.parametrize("name,value", [("event_sd", 1), ("design_se", None), ("known_label_sessions", 3),
    ("mde_bps", 34), ("requires_operator_choice", True), ("underpower_exception_applied", True),
    ("planning_constraint", "unknown"), ("corrected_to_naive_se_ratio", 1),
    ("bootstrap_zero_label_draws", 1999), ("bootstrap_zero_label_draws", False)])
def test_both_own_arm_prerequisites_are_retained_and_checked(arm, name, value):
    upstream = fixture()
    cell(upstream)["by_arm"][arm]["own_label_dispersion"][name] = value
    with pytest.raises(cn.ComparisonRefusal, match="power_prerequisite"):
        cn.build_comparison(upstream)


@pytest.mark.parametrize("offset,holding", [(1, 4), (26, 29), (27, 30)])
def test_first_and_last_eligible_trigger_and_cap_tie_contract(offset, holding):
    raw = minute_rows()
    raw[40 + offset - 1].update(close="101", high="101")
    usage = cell(cn.build_comparison(fixture(raw)))["exit_usage"]
    assert usage["holding_minutes"] == {"min": holding, "max": holding, "sum": holding * 2, "mean": float(holding)}
    assert usage["time_cap"] == (2 if holding == 30 else 0)


def test_committed_registration_and_fixed_resources():
    cn.validate_spec(json.loads(cn.SPECIFICATION_PATH.read_bytes()))
    assert cn.RESOURCES == {"maximum_upstream_bytes": 67108864, "minimum_free_bytes": 1073741824,
        "address_space_mib": 512, "cpu_seconds": 300, "wall_seconds": 1200, "nice": 10, "automatic_retries": 0}
    assert cn.EXPECTED_REFERENCE_SESSIONS == 257
    for name in ("parameters", "resources", "upstream_sha256", "output_directory"):
        wrong = specification()
        wrong[name] = "drift"
        with pytest.raises(cn.ComparisonRefusal, match="scope_mismatch"):
            cn.validate_spec(wrong)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    upstream = fixture()
    upstream_path, spec_path = tmp_path / "cm.json", tmp_path / "spec.json"
    upstream_path.write_bytes(cn._canonical(upstream))
    monkeypatch.setattr(cn, "UPSTREAM_SHA256", cn._sha(upstream_path))
    monkeypatch.setattr(cn, "EXPECTED_REFERENCE_SESSIONS", 2)
    monkeypatch.setattr(cn, "EXPECTED_SELECTED_COMPLETE", {key: {family: 2 for family in cn.FAMILIES} for key in ("D+1", "D+2")})
    monkeypatch.setattr(cn, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 40, "queried_rows": 15600})
    paths = {"upstream_path": str(upstream_path), "output_directory": str(tmp_path / "output"),
        "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(cn, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(cn, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(cn._canonical(specification()))
    return {"specification_path": spec_path, "upstream_path": upstream_path,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"]), "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_durable_start_before_sole_input_source16_private_output_and_shared_budget(runner, monkeypatch):
    real_open, reads = Path.open, []
    def observed(path, *args, **kwargs):
        if path == runner["upstream_path"]:
            assert statuses(runner)[0] == "started"
            reads.append(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    receipt = cn.run_comparison(**runner)
    report_path = runner["output_directory"] / "report.json"
    report = json.loads(report_path.read_bytes())
    assert statuses(runner) == ["started", "completed"] and receipt["status"] == "completed" and reads
    assert len(report["source_sha256"]) == 16 and str(Path(cn.cm.__file__)) in report["source_sha256"]
    assert report_path.read_bytes() == cn._canonical(report) + b"\n"
    assert report_path.stat().st_mode & 0o777 == 0o600 and report_path.parent.stat().st_mode & 0o777 == 0o700
    assert receipt["result"]["exit_policy_compared"] is True and receipt["result"]["calibration_only"] is False
    runner["budget"].checkpoint("independent_verification_same_budget")
    with pytest.raises(cn.ComparisonRefusal, match="existing_output"):
        cn.run_comparison(**runner)


@pytest.mark.parametrize("target", ["source", "spec", "runtime", "upstream_path", "report", "budget", "callback"])
def test_final_mutation_or_shared_budget_exhaustion_preserves_failure_receipt(runner, monkeypatch, target):
    real_sha, changed = cn._sha, False
    def sha(path):
        drift = (target == "source" and path == Path(cn.__file__)) or (target == "runtime" and path == Path(cn.sys.executable).resolve())
        return "f" * 64 if changed and drift else real_sha(path)
    monkeypatch.setattr(cn, "_sha", sha)
    def mutate():
        nonlocal changed
        changed = True
        if target == "callback": raise RuntimeError("synthetic_callback_failure")
        if target == "budget": runner["budget"].fail = True
        elif target not in {"source", "runtime"}:
            path = runner["output_directory"] / "report.json" if target == "report" else runner["specification_path" if target == "spec" else target]
            with path.open("ab") as stream: stream.write(b" ")
    with pytest.raises((cn.ComparisonRefusal, RuntimeError)):
        cn.run_comparison(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]


def test_bad_pin_refuses_before_calculation(runner, monkeypatch):
    runner["upstream_path"].write_bytes(b"{}")
    monkeypatch.setattr(cn, "build_comparison", lambda *args, **kwargs: pytest.fail("bad pin must precede calculation"))
    with pytest.raises(cn.ComparisonRefusal, match="hash_mismatch"):
        cn.run_comparison(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["reference", "selected", "raw", "disk", "size"])
def test_actual_population_and_resource_checks_are_runner_only(runner, monkeypatch, target):
    if target == "reference": monkeypatch.setattr(cn, "EXPECTED_REFERENCE_SESSIONS", 257)
    elif target == "selected": monkeypatch.setattr(cn, "EXPECTED_SELECTED_COMPLETE", {key: {family: 3 for family in cn.FAMILIES} for key in ("D+1", "D+2")})
    elif target == "raw": monkeypatch.setattr(cn, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 41, "queried_rows": 15600})
    else:
        resources = {**cn.RESOURCES, "minimum_free_bytes": 10**30} if target == "disk" else {**cn.RESOURCES, "maximum_upstream_bytes": 10}
        monkeypatch.setattr(cn, "RESOURCES", resources)
        runner["specification_path"].write_bytes(cn._canonical(specification()))
    with pytest.raises(cn.ComparisonRefusal, match="population_changed|free_disk|size_or_hash"):
        cn.run_comparison(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("mutation", ["copied_spec", "unregistered", "symlink", "hardlink"])
def test_registration_and_path_aliases_refuse_before_input(runner, monkeypatch, mutation):
    if mutation == "copied_spec":
        copied = runner["specification_path"].with_name("copy.json")
        copied.write_bytes(runner["specification_path"].read_bytes())
        runner["specification_path"] = copied
    elif mutation == "unregistered": runner["upstream_path"] = runner["upstream_path"].with_name("other.json")
    else:
        alias = runner["upstream_path"].with_name("alias.json")
        if mutation == "symlink": alias.symlink_to(runner["specification_path"])
        else: alias.hardlink_to(runner["specification_path"])
        cn.REGISTERED_PATHS["upstream_path"] = str(alias)
        runner["upstream_path"] = alias
        runner["specification_path"].write_bytes(cn._canonical(specification()))
    real_open = Path.open
    def observed(path, *args, **kwargs):
        if path == runner["upstream_path"]: pytest.fail("registration refusal precedes CM input")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(cn.ComparisonRefusal, match="unregistered|symlinked|aliased"):
        cn.run_comparison(**runner)
    assert not runner["runs_path"].exists()


@pytest.mark.parametrize("target", ["upstream_path", "output_directory", "runs_path"])
def test_source_alias_refuses_before_pins_log_or_input(runner, monkeypatch, target):
    source = runner["specification_path"].with_name("module.py")
    source.write_text("# synthetic source\n")
    alias = source.with_name("alias")
    alias.hardlink_to(source)
    monkeypatch.setattr(cn, "__file__", str(source))
    runner[target] = alias
    cn.REGISTERED_PATHS["canonical_runs_path" if target == "runs_path" else target] = str(alias)
    runner["specification_path"].write_bytes(cn._canonical(specification()))
    monkeypatch.setattr(cn, "_sha", lambda *args: pytest.fail("aliases refuse before pinning"))
    with pytest.raises(cn.ComparisonRefusal, match="aliased_source"):
        cn.run_comparison(**runner)
    assert source.read_text() == "# synthetic source\n"
