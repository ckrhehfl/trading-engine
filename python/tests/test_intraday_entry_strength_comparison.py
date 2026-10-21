"""Synthetic-only fixed eight-cell comparison and saved-report runner tests."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

from research import intraday_entry_strength_comparison as ch
from test_intraday_entry_strength import DAY, BREAKOUT, RECOVERY, fixture as calibration_fixture, finding


DAYS = (DAY, "2025-09-05")


def fixture():
    cf, chunks = calibration_fixture(days=DAYS)
    cg = json.loads(ch._canonical(ch.cg.build_calibration(cf, chunks)))
    # Supply two independently defined synthetic families with the same fixed
    # labels. Real inputs are never opened and production detectors are not run.
    for key in cf["variants"]:
        for row in cf["variants"][key]["days"]:
            row["families"][RECOVERY] = deepcopy(row["families"][BREAKOUT])
            if row["families"][RECOVERY]["event"] is not None:
                row["families"][RECOVERY]["event"]["family"] = RECOVERY
        for row in cg["variants"][key]["days"]:
            row["families"][RECOVERY] = deepcopy(row["families"][BREAKOUT])
        cf["variants"][key]["families"][RECOVERY] = deepcopy(cf["variants"][key]["families"][BREAKOUT])
        cg["variants"][key]["families"][RECOVERY] = deepcopy(cg["variants"][key]["families"][BREAKOUT])
        for horizon in ("60", "120"):
            own = cg["variants"][key]["families"][RECOVERY]["by_horizon"][horizon]
            own.update(mde_bps=34., requires_operator_choice=True,
                planning_constraint="own_label_mde_exceeds_assumed_cost_floor")
        cg["variants"][key]["families"][RECOVERY]["requires_operator_choice"] = True
    cg["requires_operator_choice"] = True
    return cf, cg


def response(cf, *, day=DAY, family=BREAKOUT, variant="D+1", horizon="5", symbol="KRX:000001"):
    return finding(cf, day=day, family=family, variant=variant, symbol=symbol)["responses"][horizon]


def set_labels(cf, event, peer, *, day=DAY, family=BREAKOUT, variant="D+1", horizon="5"):
    row = response(cf, day=day, family=family, variant=variant, horizon=horizon)
    row.update(event_price_change=event, control_mean_price_change=peer, excess_price_change=event - peer)


def test_exact_eight_cells_costs_pairing_baseline_and_fixed_mask():
    cf, cg = fixture()
    set_labels(cf, Fraction(1, 100), Fraction(1, 1000))
    set_labels(cf, Fraction(-1, 200), Fraction(1, 2000), day=DAYS[-1])
    output = ch.build_comparison(cf, cg)
    summary = output["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"]["5"]
    assert summary["measured_pairs"] == 2
    assert summary["event"]["gross"]["mean"] == pytest.approx(.0025)
    assert summary["event"]["gross"]["median"] == Fraction(1, 400)
    assert summary["event"]["gross"]["positive_fraction"] == Fraction(1, 2)
    assert summary["event"]["minus_33bp_assumption"]["mean"] == pytest.approx(-.0008)
    assert summary["control"]["gross"]["mean"] == pytest.approx(.00075)
    assert summary["control"]["minus_33bp_assumption"]["mean"] == pytest.approx(-.00255)
    assert summary["excess_mean"] == pytest.approx(.00175)
    assert summary["baseline_unfiltered_cf"] == {name: cf["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"]["5"][name]
        for name in ("measured_pairs", "event", "control", "excess_mean")}
    assert summary["own_label_dispersion"] == cg["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"]["5"]
    assert summary["by_month"]["2025-09"]["measured_pairs"] == 2
    assert summary["by_symbol"]["KRX:000001"]["measured_pairs"] == 2
    assert summary["by_symbol"]["KRX:000002"]["event"]["gross"]["mean"] is None
    for variant in output["variants"].values():
        assert len(variant["days"]) == 40
        assert sum(len(family["by_horizon"]) for family in variant["families"].values()) == 8
        for row in variant["days"]:
            for family in ch.FAMILIES:
                assert set(row["families"][family]["responses"]) <= {str(h) for h in ch.QUALIFIED_HORIZONS[family]}
    assert output["qualified_cells_power_prerequisite_met"] is True
    assert output["upstream_calibration_requires_operator_choice"] is True
    assert all(output[name] is False for name in ch.cc.FLAGS)
    assert output["actual_portfolio_pnl"] is False and output["family_adopted"] is False


def test_paired_uncertainty_uses_excess_all_reference_days_and_fixed_resampling():
    cf, cg = fixture()
    set_labels(cf, Fraction(1, 100), Fraction(1, 1000))
    set_labels(cf, Fraction(-1, 100), Fraction(-1, 1000), day=DAYS[-1])
    summary = ch.build_comparison(cf, cg)["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"]["5"]
    expected = ch.horizons._statistics([{"session": DAYS[0], "label": Fraction(9, 1000)},
        {"session": DAYS[1], "label": Fraction(-9, 1000)}], list(DAYS), lambda: None)
    own = summary["paired_mean_uncertainty"]
    assert own["reference_sessions"] == own["known_pair_sessions"] == 2
    for name in ("naive_se", "session_cluster_se", "bootstrap_se", "design_se", "corrected_to_naive_se_ratio"):
        assert own[name] == expected[name]
    assert own["bootstrap_zero_pair_draws"] == expected["bootstrap_zero_label_draws"]


@pytest.mark.parametrize("mutation", ["unknown", "over_cost", "waiver", "missing_mde", "choice", "count", "reference"])
def test_all_qualified_sizing_checks_precede_any_new_future_mean(monkeypatch, mutation):
    cf, cg = fixture()
    own = cg["variants"]["D+2"]["families"][RECOVERY]["by_horizon"]["30"]
    if mutation == "unknown":
        own["mde_bps"] = None
    elif mutation == "over_cost":
        own["mde_bps"] = 33.00001
    elif mutation == "waiver":
        own["underpower_exception_applied"] = True
    elif mutation == "missing_mde":
        own["mde_bps"] = float("nan")
    elif mutation == "choice":
        own["requires_operator_choice"] = True
    elif mutation == "count":
        own["measured_labels"] -= 1
    else:
        own["reference_sessions"] -= 1
    monkeypatch.setattr(ch.cd, "_distribution", lambda *args, **kwargs: pytest.fail("no new mean before every gate"))
    with pytest.raises(ch.ComparisonRefusal, match="power_prerequisite_failed"):
        ch.build_comparison(cf, cg)


def test_exact_cost_floor_qualifies_and_excluded_underpower_never_exposes_results():
    cf, cg = fixture()
    for variant in cg["variants"].values():
        for family, horizons in ch.QUALIFIED_HORIZONS.items():
            for horizon in horizons:
                variant["families"][family]["by_horizon"][str(horizon)]["mde_bps"] = 33.
    for variant in cf["variants"].values():
        for row in variant["days"]:
            if row["families"][RECOVERY]["status"] == "signal":
                for horizon in ("60", "120"):
                    for name in ("event_price_change", "control_mean_price_change", "excess_price_change"):
                        row["families"][RECOVERY]["responses"][horizon][name] = Fraction(7654321, 123456789)
        for horizon in ("60", "120"):
            variant["families"][RECOVERY]["by_horizon"][horizon]["excess_mean"] = "EXCLUDED_FUTURE_SENTINEL"
    output = ch.build_comparison(cf, cg)
    wire = ch._canonical(output)
    assert b"EXCLUDED_FUTURE_SENTINEL" not in wire and b"7654321" not in wire
    assert output["excluded_horizons_minutes"] == {RECOVERY: [60, 120]}
    assert output["upstream_calibration_requires_operator_choice"] is True


def test_selected_mask_unknowns_and_original_complete_cohort_never_change_with_outcomes():
    cf, cg = fixture()
    first = ch.build_comparison(cf, cg)
    for variant in cf["variants"].values():
        for family in ch.FAMILIES:
            for row in variant["days"]:
                if row["families"][family]["status"] == "signal":
                    for horizon in ch.QUALIFIED_HORIZONS[family]:
                        item = row["families"][family]["responses"][str(horizon)]
                        item.update(event_price_change=Fraction(-9, 10), control_mean_price_change=Fraction(9, 10), excess_price_change=Fraction(-9, 5))
    second = ch.build_comparison(cf, cg)
    for key in first["variants"]:
        for old, new in zip(first["variants"][key]["days"], second["variants"][key]["days"], strict=True):
            for family in ch.FAMILIES:
                assert all(old["families"][family][name] == new["families"][family][name]
                    for name in (*ch.ORIGINAL_FIELDS, *ch.SELECTION_FIELDS))
    # A valid old unknown pair remains visible and cannot supply even a 5m label.
    original = finding(cf, day=DAYS[-1])
    mask = finding(cg, day=DAYS[-1])
    original.update(original_pair_status="unknown", original_pair_unknown_reasons=["event_label_unknown"])
    original["original_event_label_state"] = {"status": "unknown", "missing_minute_buckets": 1,
        "unknown_reason": "incomplete_120minute_path"}
    for name in ("original_pair_status", "original_pair_unknown_reasons", "original_event_label_state"):
        mask[name] = deepcopy(original[name])
    mask["label_cohort_state"] = "outside_original_complete_cohort"
    for item in original["responses"].values():
        item.update(status="not_measured_outside_fixed_cohort", event_price_change=None,
            control_mean_price_change=None, excess_price_change=None)
    own_summary = cg["variants"]["D+1"]["families"][BREAKOUT]
    own_summary.update(ch.cg._counts([row["families"][BREAKOUT] for row in cg["variants"]["D+1"]["days"]]))
    for own in own_summary["by_horizon"].values():
        own["measured_labels"] = 1
    row = finding(ch.build_comparison(cf, cg), day=DAYS[-1])
    assert row["label_cohort_state"] == "outside_original_complete_cohort"
    assert row["responses"]["5"]["event_price_change"] is None


def test_reuses_saved_labels_and_selection_without_any_raw_replay(monkeypatch):
    cf, cg = fixture()
    def forbidden(*args, **kwargs):
        pytest.fail("CH may only consume the two saved reports")
    for module, name in ((ch.cg, "build_calibration"), (ch.cg, "_feature"),
            (ch.horizons, "_label"), (ch.horizons, "_archive_chunks"),
            (ch.cc, "aggregate_session"), (ch.cc, "calibrate_session"), (ch.cd, "build_comparison")):
        monkeypatch.setattr(module, name, forbidden)
    assert ch.build_comparison(cf, cg)["comparison_performed"] is True


@pytest.mark.parametrize("mutation", ["clock", "peers", "unknowns", "cohort", "state", "counts", "status", "vintage", "names", "scope", "reference", "pair_arithmetic"])
def test_rejects_scope_mask_and_paired_value_drift(mutation):
    cf, cg = fixture()
    mask = finding(cg)
    if mutation == "clock":
        mask["event"]["decision_at"] = DAY + "T09:38:00+09:00"
    elif mutation == "peers":
        mask["selected_controls"].pop()
    elif mutation == "unknowns":
        mask["original_pair_unknown_reasons"] = ["forged"]
    elif mutation == "cohort":
        mask["label_cohort_state"] = "outside_original_complete_cohort"
    elif mutation == "state":
        mask["selection_state"] = "unknown"
    elif mutation == "counts":
        cg["variants"]["D+1"]["families"][BREAKOUT]["selected_complete"] -= 1
    elif mutation == "status":
        mask["status"] = "known_no_signal"
    elif mutation == "vintage":
        cg["variants"]["D+1"]["days"][0]["data_vintage"] = "0" * 64
    elif mutation == "names":
        cg["variants"]["D+1"]["days"].pop()
    elif mutation == "scope":
        cg["mode"] = "waived"
    elif mutation == "reference":
        cg["reference_sessions"].append("2025-09-08")
    else:
        response(cf)["excess_price_change"] = Fraction(123)
    with pytest.raises((ch.ComparisonRefusal, ch.cg.EntryStrengthRefusal, ch.cd.ComparisonRefusal)):
        ch.build_comparison(cf, cg)


def specification():
    return {"id": ch.SPECIFICATION_ID, "mode": ch.MODE, "window": deepcopy(ch.WINDOW),
        "parameters": deepcopy(ch.PARAMETERS), "resources": deepcopy(ch.RESOURCES), **ch.REGISTERED_PATHS,
        "cf_report_sha256": ch.CF_SHA256, "cg_report_sha256": ch.CG_SHA256}


@pytest.mark.parametrize("mutation", ["cells", "cost", "budget", "cf_pin", "cg_pin", "path", "waiver", "extra"])
def test_exact_specification_rejects_scope_drift(mutation):
    spec = specification()
    if mutation == "cells":
        spec["parameters"]["response_horizons_minutes"][RECOVERY].append(60)
    elif mutation == "cost":
        spec["parameters"]["cost_floor_bps"] = "0"
    elif mutation == "budget":
        spec["resources"]["cpu_seconds"] = 301
    elif mutation in {"cf_pin", "cg_pin"}:
        spec[mutation[:2] + "_report_sha256"] = "0" * 64
    elif mutation == "path":
        spec["output_directory"] += "-retry"
    elif mutation == "waiver":
        spec["underpower_exception"] = True
    else:
        spec["archive_path"] = "forbidden"
    with pytest.raises(ch.ComparisonRefusal, match="specification_scope_mismatch"):
        ch.validate_spec(spec)


def test_committed_exact_spec_input_scope_and_registered_counts():
    ch.validate_spec(specification())
    ch.validate_spec(json.loads(ch.SPECIFICATION_PATH.read_bytes()))
    assert ch.EXPECTED_REFERENCE_SESSIONS == 257
    assert ch.EXPECTED_SELECTED_COMPLETE == {"D+1": {BREAKOUT: 787, RECOVERY: 377}, "D+2": {BREAKOUT: 785, RECOVERY: 376}}
    assert set(ch.REGISTERED_PATHS) == {"cf_report_path", "cg_report_path", "output_directory", "canonical_runs_path"}
    assert ch.RESOURCES["automatic_retries"] == 0 and ch.RESOURCES["cpu_seconds"] == 300


class SyntheticBudget:
    def __init__(self):
        self.stages, self.fail = [], False

    def checkpoint(self, stage):
        self.stages.append(stage)
        if self.fail:
            raise RuntimeError("synthetic_shared_budget_exhausted")

    def finish(self):
        pytest.fail("parent alone finishes the shared budget")


@pytest.fixture
def runner(tmp_path, monkeypatch):
    cf, cg = fixture()
    cf_path, cg_path, spec_path = [tmp_path / name for name in ("cf.json", "cg.json", "spec.json")]
    cf_path.write_bytes(ch._canonical(cf))
    cg_path.write_bytes(ch._canonical(cg))
    monkeypatch.setattr(ch, "CF_SHA256", hashlib.sha256(cf_path.read_bytes()).hexdigest())
    monkeypatch.setattr(ch, "CG_SHA256", hashlib.sha256(cg_path.read_bytes()).hexdigest())
    monkeypatch.setattr(ch, "EXPECTED_REFERENCE_SESSIONS", 2)
    monkeypatch.setattr(ch, "EXPECTED_SELECTED_COMPLETE", {key: {family: 2 for family in ch.FAMILIES} for key in ("D+1", "D+2")})
    paths = {"cf_report_path": str(cf_path), "cg_report_path": str(cg_path),
        "output_directory": str(tmp_path / "output"), "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(ch, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(ch, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(ch._canonical(specification()))
    return {"specification_path": spec_path, "cf_report_path": cf_path, "cg_report_path": cg_path,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"]),
        "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_runner_starts_before_both_reads_compacts_first_and_streams_exclusive_private_output(runner, monkeypatch):
    real_open, reads = Path.open, []
    def checked_open(path, *args, **kwargs):
        if path in {runner["cf_report_path"], runner["cg_report_path"]}:
            assert statuses(runner)[0] == "started"
            reads.append(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", checked_open)
    real_build = ch.build_comparison
    def compact_build(cf, cg, **kwargs):
        assert all("feature" not in row["families"][family] for variant in cg["variants"].values()
            for row in variant["days"] for family in ch.FAMILIES)
        return real_build(cf, cg, **kwargs)
    monkeypatch.setattr(ch, "build_comparison", compact_build)
    result = ch.run_comparison(**runner)
    path = runner["output_directory"] / "report.json"
    report = json.loads(path.read_bytes())
    assert statuses(runner) == ["started", "completed"] and result["status"] == "completed"
    assert reads[:2] == [runner["cg_report_path"], runner["cf_report_path"]]
    assert path.read_bytes() == ch._canonical(report) + b"\n"
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    assert len(report["source_sha256"]) == 13 and report["runtime"]["executable_sha256"]
    assert result["result"]["qualified_cells_power_prerequisite_met"] is True
    assert result["result"]["upstream_calibration_requires_operator_choice"] is True
    runner["budget"].checkpoint("parent_verification_still_shares_budget")
    with pytest.raises(ch.ComparisonRefusal, match="existing_output"):
        ch.run_comparison(**runner)


@pytest.mark.parametrize("target", ["source", "runtime", "cf_report_path", "cg_report_path", "report", "budget", "callback"])
def test_runner_keeps_failed_receipt_on_final_drift_or_shared_exhaustion(runner, monkeypatch, target):
    real_sha, changed = ch._sha, False
    def sha(path):
        drift = ((target == "source" and path == Path(ch.__file__))
            or (target == "runtime" and path == Path(ch.sys.executable).resolve()))
        return "f" * 64 if changed and drift else real_sha(path)
    monkeypatch.setattr(ch, "_sha", sha)
    def mutate():
        nonlocal changed
        changed = True
        if target == "callback":
            raise RuntimeError("synthetic_callback_failure")
        if target == "budget":
            runner["budget"].fail = True
        elif target not in {"source", "runtime"}:
            path = runner["output_directory"] / "report.json" if target == "report" else runner[target]
            with path.open("ab") as stream:
                stream.write(b" ")
    with pytest.raises((ch.ComparisonRefusal, RuntimeError)):
        ch.run_comparison(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]
    assert (runner["output_directory"] / "report.json").is_file()


@pytest.mark.parametrize("which", ["cf_report_path", "cg_report_path"])
def test_runner_bad_input_pin_never_reaches_build(runner, monkeypatch, which):
    runner[which].write_bytes(b"{}")
    monkeypatch.setattr(ch, "build_comparison", lambda *args, **kwargs: pytest.fail("bad saved evidence must not build"))
    with pytest.raises(ch.ComparisonRefusal, match="hash_mismatch"):
        ch.run_comparison(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("population", ["reference", "selected"])
def test_runner_registered_population_cannot_drift(runner, monkeypatch, population):
    if population == "reference":
        monkeypatch.setattr(ch, "EXPECTED_REFERENCE_SESSIONS", 256)
    else:
        monkeypatch.setattr(ch, "EXPECTED_SELECTED_COMPLETE", {key: {family: 3 for family in ch.FAMILIES} for key in ("D+1", "D+2")})
    with pytest.raises(ch.ComparisonRefusal, match="population_changed"):
        ch.run_comparison(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("mutation", ["spec_alias", "unregistered", "symlink", "hardlink"])
def test_runner_blocks_aliases_and_redirects_before_any_saved_read(runner, mutation):
    if mutation == "spec_alias":
        runner["specification_path"] = runner["specification_path"].parent / "absent" / ".." / "spec.json"
    elif mutation == "unregistered":
        runner["cf_report_path"] = runner["cf_report_path"].with_name("other.json")
    else:
        alias = runner["cg_report_path"].with_name("alias.json")
        if mutation == "symlink":
            alias.symlink_to(runner["cg_report_path"])
        else:
            alias.hardlink_to(runner["cg_report_path"])
        ch.REGISTERED_PATHS["cf_report_path"] = str(alias)
        runner["cf_report_path"] = alias
        runner["specification_path"].write_bytes(ch._canonical(specification()))
    with pytest.raises(ch.ComparisonRefusal, match="unregistered|symlinked|aliased"):
        ch.run_comparison(**runner)
    assert not runner["runs_path"].exists() and not runner["output_directory"].exists()


def test_shared_budget_checkpoints_fail_immediately():
    cf, cg = fixture()
    def exhausted():
        raise RuntimeError("synthetic_budget_exhausted")
    with pytest.raises(RuntimeError, match="synthetic_budget_exhausted"):
        ch.build_comparison(cf, cg, checkpoint=exhausted)
