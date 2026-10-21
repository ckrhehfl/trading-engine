"""Invented-data oracles for the fixed close-trigger/open-fill calibration."""

from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import stdev

import pytest

from research import intraday_exit_policy as cm
from test_intraday_entry_delay import fixture, rebind_all
from test_intraday_signal_paths import DAY, SYMBOL, minute_rows, finding


CONTINUATION, RECOVERY = cm.FAMILIES


def pair(raw=None):
    raw = minute_rows() if raw is None else raw
    minutes = cm.horizons._session_minutes(raw, DAY, SYMBOL, lambda: None)
    opening = cm.cc.session_open(DAY)
    return cm._label_pair(minutes, DAY, {"signal_bar_end": opening + 35 * cm.cc.MINUTE,
        "decision_at": opening + 37 * cm.cc.MINUTE})


def labels(report, **kwargs):
    return finding(report, **kwargs)["labels"]


def cell(report, family=CONTINUATION, variant="D+1", arm="managed"):
    return report["variants"][variant]["families"][family]["by_arm"][arm]


@pytest.mark.parametrize("close,reason,change", [("101", "take_profit", Fraction(1, 100)),
    ("99.5", "stop_loss", Fraction(-1, 200))])
def test_inclusive_threshold_equality_first_bar_close_lag_and_open_execution(close, reason, change):
    raw = minute_rows()
    raw[40].update(close=close, high="101", low="99.5")
    raw[44].update(open="97", low="97")
    result = pair(raw)
    opening = cm.cc.session_open(DAY)
    common, outcome = result["common"], result["managed"]
    assert common["entry_at"] == opening + 40 * cm.cc.MINUTE
    assert common["decision_at"] == opening + 37 * cm.cc.MINUTE
    assert common["entry_available_at"] == opening + 43 * cm.cc.MINUTE
    assert common["time_cap_decided_at"] == common["decision_at"]
    assert outcome["reason"] == reason
    assert outcome["exit_at"] == opening + 44 * cm.cc.MINUTE
    assert outcome["exit_open"] == Decimal("97")
    assert outcome["price_change"] == Fraction(-3, 100)
    assert outcome["label_available_at"] == opening + 47 * cm.cc.MINUTE
    assert outcome["trigger_trace"] == {"bar_start": opening + 40 * cm.cc.MINUTE,
        "close_at": opening + 41 * cm.cc.MINUTE, "close": Decimal(close), "price_change": change,
        "available_at": opening + 43 * cm.cc.MINUTE, "decision_at": opening + 43 * cm.cc.MINUTE,
        "scheduled_fill_at": opening + 44 * cm.cc.MINUTE}
    metadata = outcome["model_metadata"]
    assert metadata == {"evidence_level": "assumed", "execution_price": "minute_open",
        "entry_price_available_at": common["entry_available_at"],
        "outcome_label_available_at": outcome["label_available_at"], "actual_fill_certified": False}


def test_pending_exit_ignores_later_stop_and_fills_observed_open_after_reversal():
    raw = minute_rows()
    raw[40].update(close="101", high="101")
    raw[41].update(close="90", low="90")
    raw[44].update(open="98", low="98")
    outcome = pair(raw)["managed"]
    assert outcome["reason"] == "take_profit"
    assert outcome["trigger_trace"]["close"] == Decimal("101")
    assert outcome["price_change"] == Fraction(-1, 50)


def test_high_low_crossings_do_not_trigger_and_both_time_opens_are_exactly_equal():
    raw = minute_rows()
    for row in raw[40:70]:
        row.update(high="110", low="90")
    raw[70].update(open="100.7", high="100.7")
    result = pair(raw)
    managed = result["managed"]
    assert managed["reason"] == "time_cap" and managed["trigger_trace"] is None
    assert {name: managed[name] for name in result["time_only"]} == result["time_only"]
    assert managed["price_change"] == Fraction(7, 1000)
    assert managed["price_change"] - result["time_only"]["price_change"] == 0


@pytest.mark.parametrize("close_offset,managed", [(26, True), (27, False), (28, False), (29, False), (30, False)])
def test_last_eligible_trigger_late_closes_and_fill_tie_use_preplanned_time(close_offset, managed):
    raw = minute_rows()
    raw[40 + close_offset - 1].update(close="101", high="101")
    result = pair(raw)
    opening = cm.cc.session_open(DAY)
    assert result["managed"]["reason"] == ("take_profit" if managed else "time_cap")
    assert result["managed"]["exit_at"] == opening + (69 if managed else 70) * cm.cc.MINUTE


def test_preentry_close_not_monitored_and_values_strictly_inside_threshold_do_not_trigger():
    raw = minute_rows()
    raw[39].update(close="102", high="102")
    raw[40].update(close="100.999999999999999999999", high="101")
    raw[41].update(close="99.500000000000000000001", low="99.5")
    assert pair(raw)["managed"]["reason"] == "time_cap"


def test_open_time_baseline_is_distinct_from_old_close_bridge_without_aggregate_performance():
    raw = minute_rows()
    raw[69].update(close="100.4", high="100.4")
    raw[70].update(open="100.7", high="100.7")
    upstream, chunks = fixture(raw)
    output = cm.build_calibration(upstream, chunks)
    result = labels(output)
    opening = cm.cc.session_open(DAY)
    assert result["common"]["anchor_at"] == opening + 70 * cm.cc.MINUTE
    assert result["common"]["anchor_label_available_at"] == opening + 72 * cm.cc.MINUTE
    assert result["common"]["anchor_close"] == Decimal("100.4")
    assert result["common"]["anchor_price_change"] == Fraction(1, 250)
    assert result["time_only"]["price_change"] == Fraction(7, 1000)
    assert result["time_only"]["label_available_at"] == opening + 73 * cm.cc.MINUTE
    forbidden = {"mean", "gross_mean", "after_cost_mean", "positive_count", "take_profit_count", "stop_loss_count", "paired_mean"}
    def all_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from all_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from all_keys(child)
    assert not forbidden.intersection(all_keys(output))


def test_decimal_precision_survives_low_context_at_trigger_and_ratio_boundaries():
    raw = minute_rows()
    entry = "100.00000000000000000000000000000000000001"
    threshold = "101.0000000000000000000000000000000000000101"
    fill = "99.00000000000000000000000000000000000003"
    raw[40].update(open=entry, close=threshold, high=threshold)
    raw[44].update(open=fill, low=fill)
    with localcontext() as context:
        context.prec = 6
        result = pair(raw)
    assert result["managed"]["reason"] == "take_profit"
    assert result["managed"]["trigger_trace"]["price_change"] == Fraction(1, 100)
    assert result["managed"]["price_change"] == Fraction(fill) / Fraction(entry) - 1


@pytest.mark.parametrize("minute,mutation", [(41, "missing"), (70, "invalid"), (159, "duplicate"),
    (130, "missing"), (40, "invalid")])
def test_entire_original_120minute_path_must_stay_known_including_after_cap(minute, mutation):
    upstream, chunks = fixture()
    raw = chunks[DAY, SYMBOL]["rows"]
    if mutation == "missing":
        raw.pop(minute)
    elif mutation == "invalid":
        raw[minute]["open"] = "0"
    else:
        raw.append(deepcopy(raw[minute]))
    rebind_all(upstream, chunks)
    with pytest.raises(cm.ExitPolicyRefusal, match="120minute_path_invalid"):
        cm.build_calibration(upstream, chunks)


@pytest.mark.parametrize("mutation", ["bridge", "decision", "wait", "vintage", "metadata", "matrix"])
def test_pinned_bridge_information_boundary_vintage_metadata_and_matrix_contradictions_refuse(mutation):
    upstream, chunks = fixture()
    if mutation == "bridge":
        for name in ("event_price_change", "excess_price_change"):
            finding(upstream)["responses"]["30"][name] = Fraction(1, 10)
    elif mutation in {"decision", "wait"}:
        opening = cm.cc.session_open(DAY)
        event = finding(upstream)["event"]
        event["decision_at"] = (opening + 38 * cm.cc.MINUTE).isoformat()
        if mutation == "wait":
            event["signal_bar_end"] = (opening + 36 * cm.cc.MINUTE).isoformat()
    elif mutation == "vintage":
        finding(upstream)["status"] = "signal"
        upstream["variants"]["D+1"]["days"][0]["data_vintage"] = "f" * 64
    elif mutation == "metadata":
        chunks[DAY, SYMBOL]["evidence_level"] = "verified"
    else:
        upstream["variants"]["D+1"]["days"].pop()
    with pytest.raises((cm.ExitPolicyRefusal, cm.ci.PathRefusal), match="bridge_mismatch|entry_boundary|vintage|metadata|population"):
        cm.build_calibration(upstream, chunks)


def test_original_rows_masks_peers_unknowns_and_raw_metadata_are_retained_without_reselection(monkeypatch):
    upstream, chunks = fixture()
    def forbidden(*args, **kwargs):
        pytest.fail("no detector selection performance or original sizing reuse")
    monkeypatch.setattr(cm.ch, "build_comparison", forbidden)
    monkeypatch.setattr(cm.cg, "build_calibration", forbidden)
    monkeypatch.setattr(cm.cd, "_distribution", forbidden)
    monkeypatch.setattr(cm.cc, "calibrate_session", forbidden)
    output = cm.build_calibration(upstream, chunks)
    for key, variant in output["variants"].items():
        for original, row in zip(upstream["variants"][key]["days"], variant["days"], strict=True):
            assert {name: row[name] for name in ("symbol", "session", "data_vintage")} == {name: original[name] for name in ("symbol", "session", "data_vintage")}
            assert row["raw_chunk_metadata"] == {name: chunks[row["session"], row["symbol"]][name] for name in row["raw_chunk_metadata"]}
            for family in cm.FAMILIES:
                saved, actual = original["families"][family], row["families"][family]
                assert {name: actual[name] for name in cm.ORIGINAL_FIELDS} == {name: saved[name] for name in cm.ORIGINAL_FIELDS}
                assert (actual["labels"] is not None) == (saved["label_cohort_state"] == "selected_complete")
    assert all(output[name] is False for name in cm.cc.FLAGS)
    assert output["calibration_only"] is True
    assert output["comparison_performed"] is output["exit_policy_compared"] is False
    assert "excluded_horizons_minutes" not in output


def test_both_open_exit_arms_get_own_eight_sizing_cells_with_zero_event_reference_dates(monkeypatch):
    upstream, chunks = fixture(sessions=7)
    for variant in upstream["variants"].values():
        for row in variant["days"]:
            if row["session"] == upstream["reference_sessions"][-1]:
                for saved in row["families"].values():
                    if saved["label_cohort_state"] == "selected_complete":
                        saved["selection_state"], saved["label_cohort_state"] = "reject", "not_selected"
                        for response in saved["responses"].values():
                            response.update(status="not_measured_not_selected", event_price_change=None,
                                control_mean_price_change=None, excess_price_change=None)
        for family in cm.FAMILIES:
            variant["families"][family].update(cm.cg._counts([row["families"][family] for row in variant["days"]]))
    actual, calls = cm.cg._own_power, []
    def observed(records, reference, checkpoint):
        calls.append((len(records), list(reference)))
        return actual(records, reference, checkpoint)
    monkeypatch.setattr(cm.cg, "_own_power", observed)
    output = cm.build_calibration(upstream, chunks)
    assert len(calls) == 8
    assert all(count == 6 and dates == upstream["reference_sessions"] for count, dates in calls)
    for variant in output["variants"].values():
        for family in cm.FAMILIES:
            assert set(variant["families"][family]["by_arm"]) == set(cm.ARMS)
            for summary in variant["families"][family]["by_arm"].values():
                own = summary["own_label_dispersion"]
                assert own["measured_labels"] == own["known_label_sessions"] == 6
                assert own["reference_sessions"] == 7 and summary["unknown_labels"] == 0
    assert output["requires_operator_choice"] is False


@pytest.mark.parametrize("sessions", [1, 2])
def test_unknown_or_above_cost_sizing_requires_choice_and_has_no_transferred_exception(sessions):
    upstream, chunks = fixture(sessions=sessions)
    for index, day in enumerate(upstream["reference_sessions"]):
        raw = chunks[day, SYMBOL]["rows"]
        raw[70].update(open="50" if index else "150", high="150", low="50")
    rebind_all(upstream, chunks)
    output = cm.build_calibration(upstream, chunks)
    for arm in cm.ARMS:
        summary = cell(output, arm=arm)
        own = summary["own_label_dispersion"]
        assert own["mde_bps"] is None if sessions == 1 else own["mde_bps"] > 33
        assert own["underpower_exception_applied"] is False
        assert summary["requires_operator_choice"] is True
    assert output["requires_operator_choice"] is True


def test_shared_bootstrap_matches_direct_circular_five_session_oracle():
    reference = [str(index) for index in range(7)]
    records = [{"session": str(index), "label": Fraction(value, 100)}
        for index, value in [(0, 1), (0, 2), (1, -1), (3, 3), (4, -2), (6, 4)]]
    own = cm.cg._own_power(records, reference, lambda: None)
    totals = {day: [] for day in reference}
    for record in records:
        totals[record["session"]].append(float(record["label"]))
    values = [float(record["label"]) for record in records]
    mean, n = math.fsum(values) / len(values), len(values)
    cluster = math.sqrt(7 / 6 * math.fsum((math.fsum(values) - len(values) * mean) ** 2 for values in totals.values())) / n
    rng, draws = random.Random(0), []
    for _ in range(2000):
        picked = []
        while len(picked) < 7:
            start = rng.randrange(7)
            picked.extend((start + offset) % 7 for offset in range(min(5, 7 - len(picked))))
        sampled = [value for index in picked for value in totals[str(index)]]
        if sampled:
            draws.append(math.fsum(sampled) / len(sampled))
    assert own["session_cluster_se"] == pytest.approx(cluster, rel=1e-14)
    assert own["bootstrap_se"] == pytest.approx(stdev(draws), rel=1e-14)
    assert own["design_se"] == max(own[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))


def specification():
    return {"id": cm.SPECIFICATION_ID, "mode": cm.MODE, "window": deepcopy(cm.WINDOW),
        "parameters": deepcopy(cm.PARAMETERS), "resources": deepcopy(cm.RESOURCES), **cm.REGISTERED_PATHS,
        "upstream_sha256": cm.UPSTREAM_SHA256, "input_compressed_sha256": cm.INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": cm.INPUT_STREAM_SHA256}


def test_committed_registration_is_one_rule_no_sweep_and_original_inputs_budget_and_scope():
    cm.validate_spec(json.loads(cm.SPECIFICATION_PATH.read_bytes()))
    assert cm.TIME_CAP_MINUTES == 30 and cm.TAKE_PROFIT == Fraction(1, 100) and cm.STOP_LOSS == Fraction(-1, 200)
    assert cm.EXPECTED_REFERENCE_SESSIONS == 257
    assert cm.EXPECTED_SELECTED_COMPLETE == {"D+1": {CONTINUATION: 787, RECOVERY: 377}, "D+2": {CONTINUATION: 785, RECOVERY: 376}}
    assert cm.EXPECTED_INPUT_COUNTS == {"unique_target_days": 5158, "queried_rows": 1002211}
    assert cm.RESOURCES == cm.ci.RESOURCES
    for name in ("parameters", "resources", "upstream_sha256", "input_stream_sha256", "output_directory"):
        wrong = specification()
        wrong[name] = "drift"
        with pytest.raises(cm.ExitPolicyRefusal, match="scope_mismatch"):
            cm.validate_spec(wrong)


class SyntheticBudget:
    def __init__(self):
        self.stages, self.fail = [], False

    def checkpoint(self, stage):
        self.stages.append(stage)
        if self.fail:
            raise RuntimeError("synthetic_shared_budget_exhausted")

    def finish(self):
        pytest.fail("caller alone finishes shared budget")


@pytest.fixture
def runner(tmp_path, monkeypatch):
    upstream, chunks = fixture()
    upstream_path, archive, spec_path = (tmp_path / name for name in ("ch.json", "inputs.jsonl.gz", "spec.json"))
    upstream_path.write_bytes(cm._canonical(upstream))
    stream = b"".join(cm._canonical(chunk) + b"\n" for chunk in chunks.values())
    archive.write_bytes(gzip.compress(stream, mtime=0))
    monkeypatch.setattr(cm, "UPSTREAM_SHA256", cm._sha(upstream_path))
    monkeypatch.setattr(cm, "INPUT_COMPRESSED_SHA256", cm._sha(archive))
    monkeypatch.setattr(cm, "INPUT_STREAM_SHA256", hashlib.sha256(stream).hexdigest())
    monkeypatch.setattr(cm.horizons, "INPUT_STREAM_SHA256", cm.INPUT_STREAM_SHA256)
    monkeypatch.setattr(cm, "EXPECTED_REFERENCE_SESSIONS", 2)
    monkeypatch.setattr(cm, "EXPECTED_SELECTED_COMPLETE", {key: {family: 2 for family in cm.FAMILIES} for key in ("D+1", "D+2")})
    monkeypatch.setattr(cm, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 40, "queried_rows": 15600})
    paths = {"upstream_path": str(upstream_path), "input_archive_path": str(archive),
        "output_directory": str(tmp_path / "output"), "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(cm, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(cm, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(cm._canonical(specification()))
    return {"specification_path": spec_path, "upstream_path": upstream_path, "input_archive_path": archive,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"]), "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_durable_start_precedes_inputs_private_output_source15_and_single_unfinished_budget(runner, monkeypatch):
    real_open, reads = Path.open, []
    def observed(path, *args, **kwargs):
        if path in {runner["upstream_path"], runner["input_archive_path"]}:
            assert statuses(runner)[0] == "started"
            reads.append(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    receipt = cm.run_calibration(**runner)
    report_path = runner["output_directory"] / "report.json"
    report = json.loads(report_path.read_bytes())
    assert statuses(runner) == ["started", "completed"] and receipt["status"] == "completed" and reads
    assert len(report["source_sha256"]) == 15
    assert report["source_sha256"][str(runner["specification_path"])] == cm._sha(runner["specification_path"])
    assert str(Path(cm.ci.__file__)) in report["source_sha256"]
    assert report_path.read_bytes() == cm._canonical(report) + b"\n"
    assert report_path.stat().st_mode & 0o777 == 0o600 and report_path.parent.stat().st_mode & 0o777 == 0o700
    assert receipt["result"]["calibration_only"] is True
    assert receipt["result"]["comparison_performed"] is receipt["result"]["exit_policy_compared"] is False
    assert receipt["result"]["requires_operator_choice"] is report["requires_operator_choice"]
    runner["budget"].checkpoint("independent_verification_same_budget")
    with pytest.raises(cm.ExitPolicyRefusal, match="existing_output"):
        cm.run_calibration(**runner)


@pytest.mark.parametrize("target", ["source", "runtime", "upstream_path", "input_archive_path", "report", "budget", "callback"])
def test_final_drift_and_shared_budget_exhaustion_preserve_failed_receipt(runner, monkeypatch, target):
    real_sha, changed = cm._sha, False
    def sha(path):
        drift = (target == "source" and path == Path(cm.__file__)) or (target == "runtime" and path == Path(cm.sys.executable).resolve())
        return "f" * 64 if changed and drift else real_sha(path)
    monkeypatch.setattr(cm, "_sha", sha)
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
    with pytest.raises((cm.ExitPolicyRefusal, RuntimeError)):
        cm.run_calibration(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["upstream_path", "input_archive_path"])
def test_input_pins_fail_before_any_calculation(runner, monkeypatch, target):
    runner[target].write_bytes(b"{}")
    monkeypatch.setattr(cm, "build_calibration", lambda *args, **kwargs: pytest.fail("bad pin must refuse"))
    with pytest.raises(cm.ExitPolicyRefusal, match="hash_mismatch"):
        cm.run_calibration(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["reference", "selected", "raw"])
def test_registered_actual_population_cannot_silently_change(runner, monkeypatch, target):
    if target == "reference":
        monkeypatch.setattr(cm, "EXPECTED_REFERENCE_SESSIONS", 257)
    elif target == "selected":
        monkeypatch.setattr(cm, "EXPECTED_SELECTED_COMPLETE", {key: {family: 3 for family in cm.FAMILIES} for key in ("D+1", "D+2")})
    else:
        monkeypatch.setattr(cm, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 41, "queried_rows": 15600})
    with pytest.raises(cm.ExitPolicyRefusal, match="population_changed"):
        cm.run_calibration(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("mutation", ["copied_spec", "unregistered", "symlink", "hardlink"])
def test_path_registration_and_alias_refusal_precede_input_access(runner, monkeypatch, mutation):
    if mutation == "copied_spec":
        copied = runner["specification_path"].with_name("copy.json")
        copied.write_bytes(runner["specification_path"].read_bytes())
        runner["specification_path"] = copied
    elif mutation == "unregistered":
        runner["upstream_path"] = runner["upstream_path"].with_name("other.json")
    else:
        alias = runner["upstream_path"].with_name("alias.json")
        if mutation == "symlink":
            alias.symlink_to(runner["input_archive_path"])
        else:
            alias.hardlink_to(runner["input_archive_path"])
        cm.REGISTERED_PATHS["upstream_path"] = str(alias)
        runner["upstream_path"] = alias
        runner["specification_path"].write_bytes(cm._canonical(specification()))
    real_open = Path.open
    def observed(path, *args, **kwargs):
        if path in {runner["upstream_path"], runner["input_archive_path"]}:
            pytest.fail("refusal must precede saved input access")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(cm.ExitPolicyRefusal, match="unregistered|symlinked|aliased"):
        cm.run_calibration(**runner)
    assert not runner["runs_path"].exists()


@pytest.mark.parametrize("target", ["upstream_path", "input_archive_path", "output_directory", "runs_path"])
def test_source_hardlink_alias_is_rejected_before_pinning_logging_or_input_reads(runner, monkeypatch, target):
    fake_source = runner["specification_path"].with_name("source.py")
    fake_source.write_bytes(b"# synthetic source identity\n")
    monkeypatch.setattr(cm, "__file__", str(fake_source))
    alias = fake_source.with_name("source-alias")
    alias.hardlink_to(fake_source)
    runner[target] = alias
    registered = "canonical_runs_path" if target == "runs_path" else target
    cm.REGISTERED_PATHS[registered] = str(alias)
    runner["specification_path"].write_bytes(cm._canonical(specification()))
    monkeypatch.setattr(cm, "_sha", lambda *args: pytest.fail("aliases refuse before source pinning"))
    real_open = Path.open
    def observed(path, *args, **kwargs):
        if path in {runner["upstream_path"], runner["input_archive_path"]}:
            pytest.fail("aliases refuse before input reads")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(cm.ExitPolicyRefusal, match="aliased_source_or_runtime_path"):
        cm.run_calibration(**runner)
    assert not runner["runs_path"].exists() or target == "runs_path"
