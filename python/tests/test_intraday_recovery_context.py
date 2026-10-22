"""Invented-only recovery sign selection, inherited contracts and durable IO."""

from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path

import pytest

from research import intraday_recovery_context as cq
from test_intraday_peer_context import fixture as co_fixture, feature_summaries
from test_intraday_exit_policy import SyntheticBudget
from test_intraday_signal_paths import finding


OPENING, RECOVERY = cq.FAMILIES


def fixture(raw=None, *, sessions=2, zero_last=False, event_return=Fraction(-1, 100)):
    """Shift every invented causal return equally, preserving original CG states.

    Original selected returns become event_return; exact zero and positive
    values remain original CG passes and must fail only the new condition.
    The original rejecting signal is more negative and must never be rescued.
    """
    cm_report, cg_report = co_fixture(raw, sessions=sessions, zero_last=zero_last)
    shift = event_return - Fraction(3, 100)
    for variant in cg_report["variants"].values():
        for row in variant["days"]:
            for saved in row["families"].values():
                feature = saved["feature"]
                if feature is None:
                    continue
                for member in (feature["event"], *feature["peers"]):
                    value = cq._ratio(member["price_change"]) + shift
                    member.update(price_change=value, last_close=str(Decimal(100)
                        + Decimal(value.numerator * 100) / Decimal(value.denominator)))
                mean = sum((member["price_change"] for member in feature["peers"]), Fraction()) / len(feature["peers"])
                feature.update(peer_mean_price_change=mean, excess_price_change=feature["event"]["price_change"] - mean)
    return cm_report, json.loads(cq._canonical(cg_report))


def summary(report, variant="D+1"):
    return report["variants"][variant]["recovery_context"]


def specification():
    return {"id": cq.SPECIFICATION_ID, "mode": cq.MODE, "window": deepcopy(cq.WINDOW),
        "parameters": deepcopy(cq.PARAMETERS), "resources": deepcopy(cq.RESOURCES),
        **cq.REGISTERED_PATHS, "upstream_sha256": cq.UPSTREAM_SHA256, "feature_sha256": cq.FEATURE_SHA256}


@pytest.mark.parametrize("sign,expected", [(-1, "pass"), (0, "reject"), (1, "reject")])
def test_exact_negative_event_boundary_preserves_original_reject_and_unknown_cohort(sign, expected):
    cm_report, cg_report = fixture(event_return=Fraction(sign, 100))
    output = cq.build_calibration(cm_report, cg_report)
    selected = finding(output, family=RECOVERY)["recovery_context"]
    assert selected["selection_state"] == expected
    assert selected["label_cohort_state"] == ("selected_complete" if expected == "pass" else "not_selected")
    assert cq._ratio(selected["feature"]["event"]["price_change"]) == Fraction(sign, 100)
    rejected = finding(output, family=RECOVERY, index=1)
    assert cq._ratio(rejected["recovery_context"]["feature"]["event"]["price_change"]) < 0
    assert rejected["recovery_context"]["selection_state"] == "reject"
    outside = finding(output, family=RECOVERY, index=2)
    assert outside["original_pair_status"] == "unknown" and outside["labels"] is None
    assert outside["recovery_context"]["label_cohort_state"] == "outside_original_complete_cohort"
    assert summary(output)["selected_complete"] == (2 if sign == -1 else 0)
    assert summary(output)["own_label_dispersion"]["measured_labels"] == (2 if sign == -1 else 0)
    assert output["requires_operator_choice"] is (sign != -1)


def test_all_cm_rows_old_eight_sizing_cells_opening_and_metadata_retained(monkeypatch):
    cm_report, cg_report = fixture()
    before = deepcopy((cm_report, cg_report))
    for module, name in ((cq.cm, "build_calibration"), (cq.cn, "build_comparison"),
            (cq.cg, "build_calibration"), (cq.co, "build_calibration")):
        monkeypatch.setattr(module, name, lambda *args, **kwargs: pytest.fail("no raw replay or other study"))
    output = cq.build_calibration(cm_report, cg_report)
    assert (cm_report, cg_report) == before
    assert output["calibration_only"] is output["recovery_context_calibrated"] is True
    assert output["comparison_performed"] is output["exit_policy_compared"] is output["underpower_exception_applied"] is False
    assert output["requires_operator_choice"] is False
    assert output["upstream_legacy_calibration_requires_operator_choice"] is True
    assert "peer_context_calibrated" not in output
    for key, variant in output["variants"].items():
        assert variant["families"] == cm_report["variants"][key]["families"]
        assert sum(len(cell["by_arm"]) for cell in variant["families"].values()) == 4
        for original, row in zip(cm_report["variants"][key]["days"], variant["days"], strict=True):
            stripped = deepcopy(row)
            stripped["families"][RECOVERY].pop("recovery_context")
            assert stripped == original
            assert row["families"][OPENING] == original["families"][OPENING]
    wire = cq._canonical(output).decode()
    for forbidden in ("mean_after_assumed_cost", "sum_price_change", "mean_price_change", "paired", "winner", "win_rate"):
        assert ('"' + forbidden + '":') not in wire


def test_full_target_join_uses_identity_with_original_order_retained():
    cm_report, cg_report = fixture()
    original = cq.build_calibration(cm_report, cg_report)
    for variant in cg_report["variants"].values():
        reordered = []
        for day in cg_report["reference_sessions"]:
            reordered.extend(reversed([row for row in variant["days"] if row["session"] == day]))
        variant["days"] = reordered
    assert cq._canonical(cq.build_calibration(cm_report, cg_report)) == cq._canonical(original)


def test_own_labels_all_dates_zero_events_and_no_old_waiver(monkeypatch):
    cm_report, cg_report = fixture(sessions=7, zero_last=True)
    actual, calls = cq.cg._own_power, []
    def observed(records, reference, checkpoint):
        calls.append((deepcopy(records), list(reference)))
        return actual(records, reference, checkpoint)
    monkeypatch.setattr(cq.cg, "_own_power", observed)
    output = cq.build_calibration(cm_report, cg_report)
    assert len(calls) == 2
    assert all(len(records) == 6 and reference == cm_report["reference_sessions"] for records, reference in calls)
    own = summary(output)["own_label_dispersion"]
    assert own["reference_sessions"] == 7 and own["known_label_sessions"] == 6
    assert own == actual(*calls[0], lambda: None)
    assert own["mde_bps"] == 0 and own["underpower_exception_applied"] is False
    for field, period in (("by_week", cq._week), ("by_month", lambda day: day[:7])):
        assert summary(output)[field] == {value: cq._counts([
            {**row["families"][RECOVERY], **row["families"][RECOVERY]["recovery_context"]}
            for row in output["variants"]["D+1"]["days"] if period(row["session"]) == value])
            for value in sorted({period(day) for day in cm_report["reference_sessions"]})}
    monkeypatch.setattr(cq.cg, "_own_power", lambda *args: {**own, "mde_bps": 34, "requires_operator_choice": True})
    assert cq.build_calibration(cm_report, cg_report)["requires_operator_choice"] is True


def test_future_label_mutation_changes_power_but_never_selection():
    cm_report, cg_report = fixture()
    before = cq.build_calibration(cm_report, cg_report)
    for variant in cm_report["variants"].values():
        for row in variant["days"]:
            for saved in row["families"].values():
                if saved["labels"]:
                    for arm in cq.cm.ARMS:
                        saved["labels"][arm].update(exit_open="100.2", price_change=Fraction(1, 500))
    after = cq.build_calibration(cm_report, cg_report)
    for key in after["variants"]:
        assert [row["families"][RECOVERY]["recovery_context"] for row in after["variants"][key]["days"]] == [
            row["families"][RECOVERY]["recovery_context"] for row in before["variants"][key]["days"]]


def test_new_subset_uses_actual_own_dispersion_and_failed_power_has_no_old_rescue():
    cm_report, cg_report = fixture(sessions=7)
    reference = cm_report["reference_sessions"]
    for variant in cm_report["variants"].values():
        for row in variant["days"]:
            change = Fraction(1 if reference.index(row["session"]) % 2 else -1, 10)
            for saved in row["families"].values():
                if saved["labels"]:
                    for arm in cq.cm.ARMS:
                        saved["labels"][arm].update(exit_open=str(100 + change * 100), price_change=change)
    output = cq.build_calibration(cm_report, cg_report)
    expected = cq.cg._own_power([{"session": day, "label": Fraction(1 if index % 2 else -1, 10)}
        for index, day in enumerate(reference)], reference, lambda: None)
    assert summary(output)["own_label_dispersion"] == expected
    assert expected["mde_bps"] > 33 and expected["underpower_exception_applied"] is False
    assert output["requires_operator_choice"] is True
    assert all(not arm["requires_operator_choice"] for family in output["variants"]["D+1"]["families"].values()
        for arm in family["by_arm"].values())


@pytest.mark.parametrize("target", ["header", "reference", "target", "vintage", "peers", "cohort", "selection", "ratio",
    "mean", "excess", "window", "late", "clock", "member", "unknown_price", "count", "calendar", "lineage", "crossvariant", "legacy", "event"])
def test_cross_input_or_feature_contract_drift_refuses(target):
    cm_report, cg_report = fixture()
    selected = finding(cg_report, family=RECOVERY)
    feature = selected["feature"]
    if target == "header": cg_report["mode"] = "wrong"
    elif target == "reference": cg_report["reference_sessions"].reverse()
    elif target == "target": cg_report["variants"]["D+1"]["days"].pop()
    elif target == "vintage": cg_report["variants"]["D+1"]["days"][0]["data_vintage"] = "f" * 64
    elif target == "peers": selected["selected_controls"].pop()
    elif target == "cohort": selected["original_pair_status"] = "unknown"
    elif target == "selection": selected["selection_state"] = "reject"
    elif target == "ratio": feature["event"]["price_change"] = {"numerator": "4", "denominator": "100"}
    elif target == "mean": feature["peer_mean_price_change"] = Fraction()
    elif target == "excess": feature["excess_price_change"] = Fraction()
    elif target == "window": feature["start"] = feature["end"]
    elif target == "late": feature["available_at"] = (cq.horizons._instant(feature["decision_at"]) + cq.cc.MINUTE).isoformat()
    elif target == "clock": feature["decision_at"] = feature["end"]
    elif target == "member": feature["peers"][0]["symbol"] = "KRX:000001"
    elif target == "unknown_price": feature["event"].update(status="unknown", missing_minute_buckets=1)
    elif target == "count": cg_report["variants"]["D+1"]["families"][RECOVERY]["selected_complete"] += 1
    elif target == "calendar": cg_report["variants"]["D+1"]["families"][RECOVERY]["by_week"] = {}
    elif target == "lineage": cg_report["input_evidence"]["stream_sha256"] = "f" * 64
    elif target == "crossvariant": cg_report["variants"]["D+2"]["days"][0]["families"][RECOVERY]["feature"]["event"]["first_open"] = "99"
    elif target == "legacy": cm_report["upstream_calibration_requires_operator_choice"] = False
    elif target == "event": selected["event"]["signal_bar_end"] = selected["event"]["decision_at"]
    with pytest.raises(cq.RecoveryContextRefusal):
        cq.build_calibration(cm_report, cg_report)


@pytest.mark.parametrize("late", [False, True])
def test_original_unknown_or_late_feature_retained_and_forces_choice(late):
    cm_report, cg_report = fixture()
    reason = ["feature_unavailable_at_original_decision" if late else "event_feature_unknown"]
    for key in cm_report["variants"]:
        for family in cq.FAMILIES:
            original = finding(cm_report, variant=key, family=family, index=2)
            saved = finding(cg_report, variant=key, family=family, index=2)
            original.update(selection_state="unknown", selection_unknown_reasons=reason)
            saved.update(selection_state="unknown", selection_unknown_reasons=reason)
            if late:
                decision = (cq.horizons._instant(original["event"]["signal_bar_end"]) + cq.cc.MINUTE).isoformat()
                original["event"]["decision_at"] = saved["event"]["decision_at"] = decision
                saved["feature"]["decision_at"] = decision
            else:
                for name in original["event"]:
                    value = (cq.horizons._instant(original["event"][name]) + 5 * cq.cc.MINUTE).isoformat()
                    original["event"][name] = saved["event"][name] = value
                for name in ("start", "end", "available_at", "decision_at"):
                    saved["feature"][name] = (cq.horizons._instant(saved["feature"][name]) + 5 * cq.cc.MINUTE).isoformat()
                saved["feature"]["event"].update(status="unknown", missing_minute_buckets=1,
                    unknown_reason="incomplete_30minute_feature_path", first_open=None, last_close=None, price_change=None)
                saved["feature"]["excess_price_change"] = None
        for family in cq.FAMILIES:
            cm_report["variants"][key]["families"][family].update(cq.cg._counts([
                row["families"][family] for row in cm_report["variants"][key]["days"]]))
    feature_summaries(cg_report)
    output = cq.build_calibration(cm_report, cg_report)
    selected = finding(output, family=RECOVERY, index=2)["recovery_context"]
    assert selected["selection_state"] == "unknown" and selected["selection_unknown_reasons"] == reason
    assert selected["label_cohort_state"] == "outside_original_complete_cohort"
    assert summary(output)["feature_unknown"] == 1 and summary(output)["selected_complete"] == 2
    assert summary(output)["own_label_dispersion"]["requires_operator_choice"] is False
    assert output["requires_operator_choice"] is True


def test_arithmetically_consistent_cross_event_member_contradiction_refuses():
    cm_report, cg_report = fixture()
    for variant in cg_report["variants"].values():
        selected = variant["days"][0]["families"][RECOVERY]["feature"]["event"]
        selected.update(first_open="200", last_close="198")
    with pytest.raises(cq.RecoveryContextRefusal, match="cross_event_member"):
        cq.build_calibration(cm_report, cg_report)


def test_unknown_original_selected_label_refuses_without_silent_drop():
    cm_report, cg_report = fixture()
    finding(cm_report, family=RECOVERY)["labels"]["time_only"].update(
        status="unknown", unknown_reason="synthetic_missing", price_change=None)
    with pytest.raises(cq.RecoveryContextRefusal, match="invalid_upstream_contract"):
        cq.build_calibration(cm_report, cg_report)


def test_committed_specification_and_resource_limits():
    cq.validate_spec(json.loads(cq.SPECIFICATION_PATH.read_bytes()))
    assert cq.RESOURCES == cq.co.RESOURCES and cq.RESOURCES["automatic_retries"] == 0
    assert cq.EXPECTED_REFERENCE_SESSIONS == 257 and cq.RECOVERY == RECOVERY
    assert cq.UPSTREAM_SHA256 == "e03e53fb9389a461cd8d592d3335f3432df3b15b97e4d739e4535f34971ca379"
    assert cq.FEATURE_SHA256 == "b507b29998aa797d79f12e6da64ac6e39fd233a542fbedfb1f0ed98cee57d3f0"
    for key in ("parameters", "feature_sha256", "resources", "output_directory"):
        wrong = specification()
        wrong[key] = "wrong"
        with pytest.raises(cq.RecoveryContextRefusal, match="scope_mismatch"):
            cq.validate_spec(wrong)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    cm_report, cg_report = fixture()
    upstream, feature, spec = (tmp_path / name for name in ("cm.json", "cg.json", "spec.json"))
    upstream.write_bytes(cq._canonical(cm_report))
    feature.write_bytes(cq._canonical(cg_report))
    monkeypatch.setattr(cq, "UPSTREAM_SHA256", cq._sha(upstream))
    monkeypatch.setattr(cq, "FEATURE_SHA256", cq._sha(feature))
    monkeypatch.setattr(cq, "EXPECTED_REFERENCE_SESSIONS", 2)
    monkeypatch.setattr(cq, "EXPECTED_SELECTED_COMPLETE", {key: {family: 2 for family in cq.FAMILIES} for key in ("D+1", "D+2")})
    monkeypatch.setattr(cq, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 40, "queried_rows": 15600})
    paths = {"upstream_path": str(upstream), "feature_path": str(feature), "output_directory": str(tmp_path / "output"),
        "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(cq, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(cq, "SPECIFICATION_PATH", spec)
    spec.write_bytes(cq._canonical(specification()))
    return {"specification_path": spec, "upstream_path": upstream, "feature_path": feature,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"]), "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_durable_start_before_both_reads_private_output18sources_shared_budget(runner, monkeypatch):
    real_open, reads = Path.open, []
    def observed(path, *args, **kwargs):
        if path in (runner["upstream_path"], runner["feature_path"]):
            assert statuses(runner)[0] == "started"
            reads.append(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    receipt = cq.run_calibration(**runner)
    report_path = runner["output_directory"] / "report.json"
    report = json.loads(report_path.read_bytes())
    assert statuses(runner) == ["started", "completed"]
    assert set(reads) == {runner["upstream_path"], runner["feature_path"]}
    assert len(report["source_sha256"]) == 18 and str(Path(cq.co.__file__)) in report["source_sha256"]
    assert report_path.read_bytes() == cq._canonical(report) + b"\n"
    assert report_path.stat().st_mode & 0o777 == 0o600 and report_path.parent.stat().st_mode & 0o777 == 0o700
    assert receipt["result"]["calibration_only"] is True and receipt["result"]["requires_operator_choice"] is False
    runner["budget"].checkpoint("independent_verifier_shared_budget")
    with pytest.raises(cq.RecoveryContextRefusal, match="existing_output"):
        cq.run_calibration(**runner)


@pytest.mark.parametrize("target", ["source", "reused_source", "spec", "runtime", "upstream_path", "feature_path", "report", "budget", "callback"])
def test_final_drift_or_failure_retains_durable_failed_attempt(runner, monkeypatch, target):
    actual, changed = cq._sha, False
    def sha(path):
        drift = ((target == "source" and path == Path(cq.__file__))
            or (target == "reused_source" and path == Path(cq.co.__file__))
            or (target == "runtime" and path == Path(cq.sys.executable).resolve()))
        return "f" * 64 if changed and drift else actual(path)
    monkeypatch.setattr(cq, "_sha", sha)
    def mutate():
        nonlocal changed
        changed = True
        if target == "callback": raise RuntimeError("synthetic_callback_failure")
        if target == "budget": runner["budget"].fail = True
        elif target not in {"source", "reused_source", "runtime"}:
            path = runner["output_directory"] / "report.json" if target == "report" else runner["specification_path" if target == "spec" else target]
            with path.open("ab") as stream: stream.write(b" ")
    with pytest.raises((cq.RecoveryContextRefusal, RuntimeError)):
        cq.run_calibration(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("name", ["upstream_path", "feature_path"])
def test_bad_input_pin_refuses_before_calibration(runner, monkeypatch, name):
    runner[name].write_bytes(b"{}")
    monkeypatch.setattr(cq, "build_calibration", lambda *args, **kwargs: pytest.fail("pins precede calculations"))
    with pytest.raises(cq.RecoveryContextRefusal, match="hash_mismatch"):
        cq.run_calibration(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["reference", "selected", "raw", "disk", "size"])
def test_actual_registration_population_and_resource_runner_checks(runner, monkeypatch, target):
    if target == "reference": monkeypatch.setattr(cq, "EXPECTED_REFERENCE_SESSIONS", 257)
    elif target == "selected": monkeypatch.setattr(cq, "EXPECTED_SELECTED_COMPLETE", {key: {family: 3 for family in cq.FAMILIES} for key in ("D+1", "D+2")})
    elif target == "raw": monkeypatch.setattr(cq, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 41, "queried_rows": 15600})
    else:
        resources = {**cq.RESOURCES, "minimum_free_bytes": 10**30} if target == "disk" else {**cq.RESOURCES, "maximum_upstream_bytes": 10}
        monkeypatch.setattr(cq, "RESOURCES", resources)
        runner["specification_path"].write_bytes(cq._canonical(specification()))
    with pytest.raises(cq.RecoveryContextRefusal, match="population_changed|free_disk|size_or_hash"):
        cq.run_calibration(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["copied_spec", "unregistered", "symlink", "hardlink", "source_alias"])
def test_path_registration_and_aliases_refuse_before_any_input(runner, monkeypatch, target):
    if target == "copied_spec":
        copied = runner["specification_path"].with_name("copy.json")
        copied.write_bytes(runner["specification_path"].read_bytes())
        runner["specification_path"] = copied
    elif target == "unregistered": runner["feature_path"] = runner["feature_path"].with_name("other.json")
    else:
        source = runner["specification_path"]
        if target == "source_alias":
            source = runner["feature_path"].with_name("module.py")
            source.write_text("# invented source\n")
            monkeypatch.setattr(cq, "__file__", str(source))
        alias = runner["feature_path"].with_name("alias.json")
        if target == "symlink": alias.symlink_to(source)
        else: alias.hardlink_to(source)
        cq.REGISTERED_PATHS["feature_path"] = str(alias)
        runner["feature_path"] = alias
        runner["specification_path"].write_bytes(cq._canonical(specification()))
    actual = Path.open
    def observed(path, *args, **kwargs):
        if path in (runner["upstream_path"], runner["feature_path"]): pytest.fail("path refusal before input")
        return actual(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(cq.RecoveryContextRefusal, match="unregistered|symlinked|aliased"):
        cq.run_calibration(**runner)
    assert not runner["runs_path"].exists()
