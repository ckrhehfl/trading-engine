"""Invented-only causal join, strict positive-peer selection and durable IO."""

from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path

import pytest

from research import intraday_peer_context as co
from test_intraday_exit_policy_comparison import fixture as cm_fixture
from test_intraday_exit_policy import SyntheticBudget
from test_intraday_signal_paths import finding


OPENING, RECOVERY = co.FAMILIES


def feature_summaries(report):
    """Refresh synthetic CG evidence summaries after a deliberate mutation."""
    reference = report["reference_sessions"]
    for variant in report["variants"].values():
        for family in co.FAMILIES:
            rows = [{"session": row["session"], **row["families"][family]} for row in variant["days"]]
            counts = co.cg._counts(rows)
            own = co.cg._own_power([{"session": row["session"], "label": Fraction()}
                for row in rows if row["label_cohort_state"] == "selected_complete"], reference, lambda: None)
            summary = {**counts, "by_horizon": {str(h): deepcopy(own) for h in co.cg.HORIZONS},
                "requires_operator_choice": own["requires_operator_choice"]}
            if family == RECOVERY:
                selected = [row for row in rows if row["label_cohort_state"] == "selected_complete"]
                weak = co.cg._own_power([{"session": row["session"], "label": Fraction((-1) ** index, 10)}
                    for index, row in enumerate(selected)], reference, lambda: None)
                for horizon in (60, 120): summary["by_horizon"][str(horizon)] = deepcopy(weak)
                summary["requires_operator_choice"] = any(cell["requires_operator_choice"] for cell in summary["by_horizon"].values())
            for field, period in (("by_month", lambda day: day[:7]), ("by_week", co._week)):
                summary[field] = {value: co.cg._counts([row for row in rows if period(row["session"]) == value])
                    for value in sorted({period(day) for day in reference})}
            variant["families"][family] = summary
    report["requires_operator_choice"] = any(summary["requires_operator_choice"]
        for variant in report["variants"].values() for summary in variant["families"].values())


def fixture(raw=None, *, sessions=2, zero_last=False, peer_sign=1):
    """Return fully invented CM/CG reports; safe for scale/verifier fixtures.

    Three original signals have returns +3%, -1% and +2%; others are flat.
    peer_sign=0/-1 changes the second return to -2%/-3%, preserving CG states.
    zero_last removes the original selected event on the final reference date.
    """
    cm_report = cm_fixture(raw, sessions=sessions, zero_last=zero_last)
    report = {"study_id": co.cg.SPECIFICATION_ID, "mode": co.cg.MODE, "window": deepcopy(co.WINDOW),
        "parameters": deepcopy(co.cg.PARAMETERS), "assumptions": deepcopy(co.cg.ASSUMPTIONS), **co.cg.FLAGS,
        "reference_sessions": list(cm_report["reference_sessions"]),
        "input_evidence": {"upstream_report_sha256": co.cg.UPSTREAM_SHA256,
            "archive_path": co.cg.REGISTERED_PATHS["input_archive_path"], "compressed_sha256": co.cg.INPUT_COMPRESSED_SHA256,
            "stream_sha256": co.cg.INPUT_STREAM_SHA256,
            **{name: cm_report["input_evidence"][name] for name in co.EXPECTED_INPUT_COUNTS}}, "variants": {}}
    for key, variant in cm_report["variants"].items():
        days = []
        for row in variant["days"]:
            day = row["session"]
            no_event = zero_last and day == report["reference_sessions"][-1]
            values = {f"KRX:{index:06d}": Fraction() for index in range(1, 21)}
            values.update({"KRX:000001": Fraction(-4 if no_event else 3, 100),
                "KRX:000002": Fraction(peer_sign - 2, 100), "KRX:000003": Fraction(2, 100)})
            def member(symbol):
                return {"symbol": symbol, "status": "known", "missing_minute_buckets": 0, "unknown_reason": None,
                    "first_open": "100", "last_close": str(Decimal(100) + Decimal(values[symbol].numerator * 100) / Decimal(values[symbol].denominator)),
                    "price_change": values[symbol]}
            membership = {"session": day, "observation_date": day[:8] + "01", "available_at": day + "T08:00:00+09:00",
                "selected_at": day + "T08:30:00+09:00"}
            output = {"symbol": row["symbol"], "session": day, "data_vintage": row["data_vintage"], "observation_date": day,
                "membership_source": membership, "membership_observation_date": membership["observation_date"],
                "membership_available_at": membership["available_at"], "families": {}}
            for family, saved in row["families"].items():
                feature = None
                if saved["status"] == "signal":
                    end = co.horizons._instant(saved["event"]["signal_bar_end"])
                    peers = [member(peer["symbol"]) for peer in saved["selected_controls"]]
                    mean = sum((item["price_change"] for item in peers), Fraction()) / len(peers)
                    event = member(row["symbol"])
                    feature = {"start": end - 30 * co.cc.MINUTE, "end": end, "available_at": end + 2 * co.cc.MINUTE,
                        "decision_at": saved["event"]["decision_at"], "event": event, "peers": peers,
                        "peer_mean_price_change": mean, "excess_price_change": event["price_change"] - mean}
                output["families"][family] = {**{name: deepcopy(saved[name]) for name in co.cm.ORIGINAL_FIELDS}, "feature": feature}
            days.append(output)
        report["variants"][key] = {"days": days, "families": {}}
    feature_summaries(report)
    return cm_report, json.loads(co._canonical(report))


def summary(report, variant="D+1"):
    return report["variants"][variant]["peer_context"]


def specification():
    return {"id": co.SPECIFICATION_ID, "mode": co.MODE, "window": deepcopy(co.WINDOW),
        "parameters": deepcopy(co.PARAMETERS), "resources": deepcopy(co.RESOURCES),
        **co.REGISTERED_PATHS, "upstream_sha256": co.UPSTREAM_SHA256, "feature_sha256": co.FEATURE_SHA256}


@pytest.mark.parametrize("sign,expected", [(1, "pass"), (0, "reject"), (-1, "reject")])
def test_exact_positive_peer_boundary_and_original_unknown_not_rescued(sign, expected):
    cm_report, cg_report = fixture(peer_sign=sign)
    output = co.build_calibration(cm_report, cg_report)
    selected = finding(output)["peer_context"]
    assert selected["selection_state"] == expected
    assert selected["label_cohort_state"] == ("selected_complete" if expected == "pass" else "not_selected")
    assert co._ratio(selected["feature"]["peer_mean_price_change"]) == Fraction(sign, 1900)
    assert finding(output, index=1)["peer_context"]["selection_state"] == "reject"
    outside = finding(output, index=2)
    assert outside["original_pair_status"] == "unknown"
    assert outside["peer_context"]["label_cohort_state"] == "outside_original_complete_cohort"
    assert outside["labels"] is None
    assert summary(output)["selected_complete"] == (2 if sign == 1 else 0)
    assert summary(output)["own_label_dispersion"]["measured_labels"] == (2 if sign == 1 else 0)


def test_all_cm_rows_old_sizing_recovery_feature_graph_and_metadata_retained(monkeypatch):
    cm_report, cg_report = fixture()
    before = deepcopy((cm_report, cg_report))
    for module, name in ((co.cm, "build_calibration"), (co.cn, "build_comparison"), (co.cg, "build_calibration")):
        monkeypatch.setattr(module, name, lambda *args, **kwargs: pytest.fail("no raw replay or comparison"))
    output = co.build_calibration(cm_report, cg_report)
    assert (cm_report, cg_report) == before
    assert output["calibration_only"] is True and output["comparison_performed"] is False
    assert output["exit_policy_compared"] is output["underpower_exception_applied"] is False
    assert output["requires_operator_choice"] is False
    assert output["upstream_legacy_calibration_requires_operator_choice"] is True
    for key, variant in output["variants"].items():
        assert variant["families"] == cm_report["variants"][key]["families"]
        for original, row in zip(cm_report["variants"][key]["days"], variant["days"], strict=True):
            stripped = deepcopy(row)
            stripped["families"][OPENING].pop("peer_context")
            assert stripped == original
            assert row["families"][RECOVERY] == original["families"][RECOVERY]
    wire = co._canonical(output).decode()
    for forbidden in ("mean_after_assumed_cost", "sum_price_change", "mean_price_change", "paired", "winner"):
        # Preregistered claim-boundary prose can contain winner; computed keys cannot.
        assert ('"' + forbidden + '":') not in wire


def test_full_target_join_uses_identity_with_original_order_retained():
    cm_report, cg_report = fixture()
    original = co.build_calibration(cm_report, cg_report)
    for variant in cg_report["variants"].values():
        reordered = []
        for day in cg_report["reference_sessions"]:
            reordered.extend(reversed([row for row in variant["days"] if row["session"] == day]))
        variant["days"] = reordered
    assert co._canonical(co.build_calibration(cm_report, cg_report)) == co._canonical(original)


def test_own_labels_all_dates_zero_events_and_no_old_waiver(monkeypatch):
    cm_report, cg_report = fixture(sessions=7, zero_last=True)
    actual, calls = co.cg._own_power, []
    def observed(records, reference, checkpoint):
        calls.append((deepcopy(records), list(reference)))
        return actual(records, reference, checkpoint)
    monkeypatch.setattr(co.cg, "_own_power", observed)
    output = co.build_calibration(cm_report, cg_report)
    assert len(calls) == 2
    assert all(len(records) == 6 and reference == cm_report["reference_sessions"] for records, reference in calls)
    own = summary(output)["own_label_dispersion"]
    assert own["reference_sessions"] == 7 and own["known_label_sessions"] == 6
    assert own == actual(*calls[0], lambda: None)
    assert own["mde_bps"] == 0 and own["underpower_exception_applied"] is False
    assert summary(output)["by_week"] == {period: co._counts([
        {**row["families"][OPENING], **row["families"][OPENING]["peer_context"]}
        for row in output["variants"]["D+1"]["days"] if co._week(row["session"]) == period])
        for period in sorted({co._week(day) for day in cm_report["reference_sessions"]})}
    monkeypatch.setattr(co.cg, "_own_power", lambda *args: {**own, "mde_bps": 34, "requires_operator_choice": True})
    assert co.build_calibration(cm_report, cg_report)["requires_operator_choice"] is True


def test_future_label_mutation_changes_power_but_never_selection():
    cm_report, cg_report = fixture()
    before = co.build_calibration(cm_report, cg_report)
    # Same coherent label in both families/variants: no first-trigger replay is invoked.
    for variant in cm_report["variants"].values():
        for row in variant["days"]:
            for saved in row["families"].values():
                if saved["labels"]:
                    for arm in co.cm.ARMS:
                        saved["labels"][arm].update(exit_open="100.2", price_change=Fraction(1, 500))
    after = co.build_calibration(cm_report, cg_report)
    for key in after["variants"]:
        assert [row["families"][OPENING]["peer_context"] for row in after["variants"][key]["days"]] == [
            row["families"][OPENING]["peer_context"] for row in before["variants"][key]["days"]]


def test_new_subset_uses_actual_own_label_dispersion_and_failed_power_has_no_old_rescue():
    cm_report, cg_report = fixture(sessions=7)
    reference = cm_report["reference_sessions"]
    for variant in cm_report["variants"].values():
        for row in variant["days"]:
            change = Fraction(1 if reference.index(row["session"]) % 2 else -1, 10)
            for saved in row["families"].values():
                if saved["labels"]:
                    for arm in co.cm.ARMS:
                        saved["labels"][arm].update(exit_open=str(100 + change * 100), price_change=change)
    output = co.build_calibration(cm_report, cg_report)
    cell = summary(output)
    expected = co.cg._own_power([{"session": day, "label": Fraction(1 if index % 2 else -1, 10)}
        for index, day in enumerate(reference)], reference, lambda: None)
    assert cell["own_label_dispersion"] == expected
    assert expected["mde_bps"] > 33 and expected["underpower_exception_applied"] is False
    assert output["requires_operator_choice"] is True
    assert all(not arm["requires_operator_choice"] for family in output["variants"]["D+1"]["families"].values()
        for arm in family["by_arm"].values())


@pytest.mark.parametrize("target", ["header", "reference", "target", "vintage", "peers", "cohort", "selection", "ratio",
    "mean", "excess", "window", "late", "clock", "member", "unknown_price", "count", "calendar", "lineage", "crossvariant", "legacy", "event"])
def test_cross_input_or_feature_contract_drift_refuses(target):
    cm_report, cg_report = fixture()
    selected = finding(cg_report)
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
    elif target == "late": feature["available_at"] = (co.horizons._instant(feature["decision_at"]) + co.cc.MINUTE).isoformat()
    elif target == "clock": feature["decision_at"] = feature["end"]
    elif target == "member": feature["peers"][0]["symbol"] = "KRX:000001"
    elif target == "unknown_price": feature["event"].update(status="unknown", missing_minute_buckets=1)
    elif target == "count": cg_report["variants"]["D+1"]["families"][OPENING]["selected_complete"] += 1
    elif target == "calendar": cg_report["variants"]["D+1"]["families"][OPENING]["by_week"] = {}
    elif target == "lineage": cg_report["input_evidence"]["stream_sha256"] = "f" * 64
    elif target == "crossvariant": cg_report["variants"]["D+2"]["days"][0]["families"][OPENING]["feature"]["event"]["first_open"] = "99"
    elif target == "legacy": cm_report["upstream_calibration_requires_operator_choice"] = False
    elif target == "event": selected["event"]["signal_bar_end"] = selected["event"]["decision_at"]
    with pytest.raises(co.PeerContextRefusal):
        co.build_calibration(cm_report, cg_report)


def test_original_unknown_feature_retained_and_forces_choice_without_rescue():
    cm_report, cg_report = fixture()
    # The third original signal is already outside the complete future cohort.
    for key in cm_report["variants"]:
        for family in co.FAMILIES:
            original = finding(cm_report, variant=key, family=family, index=2)
            original.update(selection_state="unknown", selection_unknown_reasons=["event_feature_unknown"])
            saved = finding(cg_report, variant=key, family=family, index=2)
            saved.update(selection_state="unknown", selection_unknown_reasons=["event_feature_unknown"])
            # A distinct causal window keeps this genuine feature gap separate
            # from the selected event's complete peer feature observations.
            for name in original["event"]:
                value = (co.horizons._instant(original["event"][name]) + 5 * co.cc.MINUTE).isoformat()
                original["event"][name] = saved["event"][name] = value
            for name in ("start", "end", "available_at", "decision_at"):
                saved["feature"][name] = (co.horizons._instant(saved["feature"][name]) + 5 * co.cc.MINUTE).isoformat()
            saved["feature"]["event"].update(status="unknown", missing_minute_buckets=1,
                unknown_reason="incomplete_30minute_feature_path", first_open=None, last_close=None, price_change=None)
            saved["feature"]["excess_price_change"] = None
        for family in co.FAMILIES:
            cm_report["variants"][key]["families"][family].update(co.cg._counts([
                row["families"][family] for row in cm_report["variants"][key]["days"]]))
    feature_summaries(cg_report)
    output = co.build_calibration(cm_report, cg_report)
    assert summary(output)["feature_unknown"] == 1
    assert summary(output)["selected_complete"] == 2
    assert summary(output)["own_label_dispersion"]["requires_operator_choice"] is False
    assert output["requires_operator_choice"] is True


def test_original_late_feature_is_unknown_not_available_selection():
    cm_report, cg_report = fixture()
    for key in cm_report["variants"]:
        for family in co.FAMILIES:
            original = finding(cm_report, variant=key, family=family, index=2)
            saved = finding(cg_report, variant=key, family=family, index=2)
            reason = ["feature_unavailable_at_original_decision"]
            original.update(selection_state="unknown", selection_unknown_reasons=reason)
            saved.update(selection_state="unknown", selection_unknown_reasons=reason)
            decision = (co.horizons._instant(original["event"]["signal_bar_end"]) + co.cc.MINUTE).isoformat()
            original["event"]["decision_at"] = saved["event"]["decision_at"] = decision
            saved["feature"]["decision_at"] = decision
        for family in co.FAMILIES:
            cm_report["variants"][key]["families"][family].update(co.cg._counts([
                row["families"][family] for row in cm_report["variants"][key]["days"]]))
    feature_summaries(cg_report)
    output = co.build_calibration(cm_report, cg_report)
    assert finding(output, index=2)["peer_context"]["selection_state"] == "unknown"
    assert summary(output)["feature_unknown"] == 1 and output["requires_operator_choice"] is True


def test_arithmetically_consistent_cross_event_member_contradiction_refuses():
    cm_report, cg_report = fixture()
    for variant in cg_report["variants"].values():
        # Keep this event's ratio and state valid while contradicting its price
        # endpoint reused as a peer in the other original signal graphs.
        selected = variant["days"][0]["families"][OPENING]["feature"]["event"]
        selected.update(first_open="200", last_close="206")
    with pytest.raises(co.PeerContextRefusal, match="cross_event_member"):
        co.build_calibration(cm_report, cg_report)


def test_unknown_original_selected_label_refuses_without_silent_drop():
    cm_report, cg_report = fixture()
    finding(cm_report)["labels"]["time_only"].update(status="unknown", unknown_reason="synthetic_missing", price_change=None)
    with pytest.raises(co.PeerContextRefusal, match="invalid_upstream_contract"):
        co.build_calibration(cm_report, cg_report)


def test_preopening_feature_cannot_fabricate_known_outside_session_minutes():
    cm_report, cg_report = fixture()
    for key in cm_report["variants"]:
        for family in co.FAMILIES:
            original = finding(cm_report, variant=key, family=family, index=2)
            saved = finding(cg_report, variant=key, family=family, index=2)
            for name in original["event"]:
                value = (co.horizons._instant(original["event"][name]) - 10 * co.cc.MINUTE).isoformat()
                original["event"][name] = saved["event"][name] = value
            for name in ("start", "end", "available_at", "decision_at"):
                saved["feature"][name] = (co.horizons._instant(saved["feature"][name]) - 10 * co.cc.MINUTE).isoformat()
    with pytest.raises(co.PeerContextRefusal, match="preopening_feature_missingness"):
        co.build_calibration(cm_report, cg_report)


def test_committed_specification_and_resource_limits():
    co.validate_spec(json.loads(co.SPECIFICATION_PATH.read_bytes()))
    assert co.RESOURCES == co.cn.RESOURCES and co.RESOURCES["automatic_retries"] == 0
    assert co.EXPECTED_REFERENCE_SESSIONS == 257
    for key in ("parameters", "feature_sha256", "resources", "output_directory"):
        wrong = specification()
        wrong[key] = "wrong"
        with pytest.raises(co.PeerContextRefusal, match="scope_mismatch"):
            co.validate_spec(wrong)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    cm_report, cg_report = fixture()
    upstream, feature, spec = (tmp_path / name for name in ("cm.json", "cg.json", "spec.json"))
    upstream.write_bytes(co._canonical(cm_report))
    feature.write_bytes(co._canonical(cg_report))
    monkeypatch.setattr(co, "UPSTREAM_SHA256", co._sha(upstream))
    monkeypatch.setattr(co, "FEATURE_SHA256", co._sha(feature))
    monkeypatch.setattr(co, "EXPECTED_REFERENCE_SESSIONS", 2)
    monkeypatch.setattr(co, "EXPECTED_SELECTED_COMPLETE", {key: {family: 2 for family in co.FAMILIES} for key in ("D+1", "D+2")})
    monkeypatch.setattr(co, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 40, "queried_rows": 15600})
    paths = {"upstream_path": str(upstream), "feature_path": str(feature), "output_directory": str(tmp_path / "output"),
        "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(co, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(co, "SPECIFICATION_PATH", spec)
    spec.write_bytes(co._canonical(specification()))
    return {"specification_path": spec, "upstream_path": upstream, "feature_path": feature,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"]), "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_durable_start_before_both_reads_private_output17sources_shared_budget(runner, monkeypatch):
    real_open, reads = Path.open, []
    def observed(path, *args, **kwargs):
        if path in (runner["upstream_path"], runner["feature_path"]):
            assert statuses(runner)[0] == "started"
            reads.append(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    receipt = co.run_calibration(**runner)
    report_path = runner["output_directory"] / "report.json"
    report = json.loads(report_path.read_bytes())
    assert statuses(runner) == ["started", "completed"]
    assert set(reads) == {runner["upstream_path"], runner["feature_path"]}
    assert len(report["source_sha256"]) == 17
    assert report_path.read_bytes() == co._canonical(report) + b"\n"
    assert report_path.stat().st_mode & 0o777 == 0o600 and report_path.parent.stat().st_mode & 0o777 == 0o700
    assert receipt["result"]["calibration_only"] is True and receipt["result"]["requires_operator_choice"] is False
    runner["budget"].checkpoint("independent_verifier_shared_budget")
    with pytest.raises(co.PeerContextRefusal, match="existing_output"):
        co.run_calibration(**runner)


@pytest.mark.parametrize("target", ["source", "spec", "runtime", "upstream_path", "feature_path", "report", "budget", "callback"])
def test_final_drift_or_failure_retains_durable_failed_attempt(runner, monkeypatch, target):
    actual, changed = co._sha, False
    def sha(path):
        drift = (target == "source" and path == Path(co.__file__)) or (target == "runtime" and path == Path(co.sys.executable).resolve())
        return "f" * 64 if changed and drift else actual(path)
    monkeypatch.setattr(co, "_sha", sha)
    def mutate():
        nonlocal changed
        changed = True
        if target == "callback": raise RuntimeError("synthetic_callback_failure")
        if target == "budget": runner["budget"].fail = True
        elif target not in {"source", "runtime"}:
            path = runner["output_directory"] / "report.json" if target == "report" else runner["specification_path" if target == "spec" else target]
            with path.open("ab") as stream: stream.write(b" ")
    with pytest.raises((co.PeerContextRefusal, RuntimeError)):
        co.run_calibration(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("name", ["upstream_path", "feature_path"])
def test_bad_input_pin_refuses_before_calibration(runner, monkeypatch, name):
    runner[name].write_bytes(b"{}")
    monkeypatch.setattr(co, "build_calibration", lambda *args, **kwargs: pytest.fail("pins precede calculations"))
    with pytest.raises(co.PeerContextRefusal, match="hash_mismatch"):
        co.run_calibration(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["reference", "selected", "raw", "disk", "size"])
def test_actual_registration_population_and_resource_runner_checks(runner, monkeypatch, target):
    if target == "reference": monkeypatch.setattr(co, "EXPECTED_REFERENCE_SESSIONS", 257)
    elif target == "selected": monkeypatch.setattr(co, "EXPECTED_SELECTED_COMPLETE", {key: {family: 3 for family in co.FAMILIES} for key in ("D+1", "D+2")})
    elif target == "raw": monkeypatch.setattr(co, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 41, "queried_rows": 15600})
    else:
        resources = {**co.RESOURCES, "minimum_free_bytes": 10**30} if target == "disk" else {**co.RESOURCES, "maximum_upstream_bytes": 10}
        monkeypatch.setattr(co, "RESOURCES", resources)
        runner["specification_path"].write_bytes(co._canonical(specification()))
    with pytest.raises(co.PeerContextRefusal, match="population_changed|free_disk|size_or_hash"):
        co.run_calibration(**runner)
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
            monkeypatch.setattr(co, "__file__", str(source))
        alias = runner["feature_path"].with_name("alias.json")
        if target == "symlink": alias.symlink_to(source)
        else: alias.hardlink_to(source)
        co.REGISTERED_PATHS["feature_path"] = str(alias)
        runner["feature_path"] = alias
        runner["specification_path"].write_bytes(co._canonical(specification()))
    actual = Path.open
    def observed(path, *args, **kwargs):
        if path in (runner["upstream_path"], runner["feature_path"]): pytest.fail("path refusal before input")
        return actual(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(co.PeerContextRefusal, match="unregistered|symlinked|aliased"):
        co.run_calibration(**runner)
    assert not runner["runs_path"].exists()
