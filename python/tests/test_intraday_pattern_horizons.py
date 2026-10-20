"""Synthetic-only CE fixed-cohort arithmetic, missingness and IO checks."""

from copy import deepcopy
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from research import intraday_pattern_horizons as ce
from research import intraday_pattern_comparison as cd
from test_intraday_pattern_comparison import DAY, BREAKOUT, RECOVERY, fixture, minute_rows


def inputs(*, overrides=None, days=(DAY,), only_event=False):
    report, chunks = fixture(days=days, overrides=overrides, only_event=only_event)
    saved = json.loads(cd._canonical(cd.build_comparison(report, chunks)))
    return saved, chunks


def finding(report, *, family=BREAKOUT, symbol="KRX:000001", day=DAY, variant="D+1"):
    return next(row["families"][family] for row in report["variants"][variant]["days"]
        if row["symbol"] == symbol and row["session"] == day)


def curve_rows():
    raw = minute_rows(breakout=True, terminal="105")
    for index, close in ((44, "101"), (54, "102"), (69, "103"), (99, "104")):
        raw[index].update(high=close, close=close)
    return raw


def test_fixed_horizons_same_entry_exact_prices_peer_mean_cost_and_120_anchor():
    saved, chunks = inputs(overrides={(DAY, "KRX:000001"): curve_rows()})
    output = ce.build_horizons(saved, chunks)
    row = finding(output)
    assert row["original_pair_status"] == "complete"
    assert row["event_prices"]["entry_label_start"] == ce.cc.session_open(DAY) + 40 * ce.cc.MINUTE
    assert row["event_prices"]["entry_open"] == 100
    assert len(row["selected_controls"]) == 19
    assert len(output["variants"]["D+1"]["days"]) == 20
    for horizon, percent in zip(ce.HORIZONS, range(1, 6), strict=True):
        response = row["responses"][str(horizon)]
        assert response == {"status": "measured", "event_price_change": Fraction(percent, 100),
            "control_mean_price_change": Fraction(0), "excess_price_change": Fraction(percent, 100)}
        assert row["event_prices"]["terminal_closes"][str(horizon)] == 100 + percent
        assert row["selected_controls"][0]["terminal_closes"][str(horizon)] == 100
        summary = output["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"][str(horizon)]
        assert summary["measured_pairs"] == 1
        assert summary["event"]["minus_33bp_assumption"]["mean"] == pytest.approx(percent / 100 - .0033)
        assert summary["control"]["minus_33bp_assumption"]["mean"] == pytest.approx(-.0033)
        assert summary["by_month"]["2025-09"]["excess_mean"] == pytest.approx(percent / 100)
        assert summary["by_symbol"]["KRX:000001"]["measured_pairs"] == 1
        assert summary["by_symbol"]["KRX:000002"]["measured_pairs"] == 0
    assert output["variants"]["D+1"]["families"][BREAKOUT]["original_cd_summary"] == saved["variants"]["D+1"]["families"][BREAKOUT]
    assert all(output[key] is False for key in ce.cc.FLAGS)
    assert output["horizon_selected"] is False and output["family_adopted"] is False


@pytest.mark.parametrize("gap_symbol,gap_index", [("KRX:000001", 100), ("KRX:000002", 100)])
def test_short_future_available_never_rescues_original_unknown_pair(gap_symbol, gap_index):
    overrides = {(DAY, "KRX:000001"): minute_rows(breakout=True)}
    raw = overrides.get((DAY, gap_symbol), minute_rows(symbol=gap_symbol))
    raw.pop(gap_index)
    overrides[DAY, gap_symbol] = raw
    saved, chunks = inputs(overrides=overrides)
    original = finding(saved)["comparison"]
    assert original["pair_status"] == "unknown"
    assert original["event_label"]["status"] == ("unknown" if gap_symbol == "KRX:000001" else "known")
    output = ce.build_horizons(saved, chunks)
    row = finding(output)
    assert row["event"] == finding(saved)["event"]
    assert row["original_pair_unknown_reasons"] == original["pair_unknown_reasons"]
    assert [item["symbol"] for item in row["selected_controls"]] == [item["symbol"] for item in original["selected_controls"]]
    assert row["event_prices"] is None
    assert row["original_unknown_control_symbols"] == original["unknown_control_symbols"]
    assert row["original_event_label_state"] == {name: original["event_label"][name]
        for name in ("status", "missing_minute_buckets", "unknown_reason")}
    assert all("entry_open" not in item for item in row["selected_controls"])
    for actual, before in zip(row["selected_controls"], original["selected_controls"], strict=True):
        assert actual["original_label_state"] == {name: before["label"][name]
            for name in ("status", "missing_minute_buckets", "unknown_reason")}
    for horizon in ce.HORIZONS:
        assert row["responses"][str(horizon)] == {"status": "not_measured_outside_fixed_cohort",
            "event_price_change": None, "control_mean_price_change": None, "excess_price_change": None}
        summary = output["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"][str(horizon)]
        assert summary["measured_pairs"] == 0
        assert summary["own_label_dispersion"]["measured_labels"] == 0
        assert summary["own_label_dispersion"]["not_measured_outside_fixed_cohort"] == 1
        assert "unknown_labels" not in summary["own_label_dispersion"]


def test_unknown_and_known_no_signal_and_no_control_states_retained():
    saved, chunks = inputs(overrides={(DAY, "KRX:000001"): minute_rows(breakout=True)}, only_event=True)
    output = ce.build_horizons(saved, chunks)
    assert finding(output)["original_pair_unknown_reasons"] == ["no_selected_controls"]
    assert finding(output, symbol="KRX:000002")["status"] == "unknown"
    assert finding(output, symbol="KRX:000002")["responses"] == {}
    assert finding(output, family=RECOVERY)["status"] == "known_no_signal"


def test_control_that_also_signals_keeps_original_event_entry():
    other = minute_rows(symbol="KRX:000002", recovery=True)
    saved, chunks = inputs(overrides={(DAY, "KRX:000001"): curve_rows(), (DAY, "KRX:000002"): other})
    output = ce.build_horizons(saved, chunks)
    assert finding(output, family=RECOVERY, symbol="KRX:000002")["status"] == "signal"
    first = finding(output)
    control = next(item for item in first["selected_controls"] if item["symbol"] == "KRX:000002")
    assert control["entry_open"] == 100
    assert finding(output, family=RECOVERY, symbol="KRX:000002")["event_prices"]["entry_label_start"] == ce.cc.session_open(DAY) + 45 * ce.cc.MINUTE


@pytest.mark.parametrize("mutation", ["zero", "invalid", "duplicate"])
def test_invalid_zero_duplicate_prices_remain_original_unknown(mutation):
    raw = minute_rows(breakout=True)
    if mutation == "zero":
        raw[70].update(open="0", low="0")
    elif mutation == "invalid":
        raw[70]["close"] = "NaN"
    else:
        raw.append(dict(raw[70]))
    saved, chunks = inputs(overrides={(DAY, "KRX:000001"): raw})
    output = ce.build_horizons(saved, chunks)
    assert finding(output)["status"] == "signal"
    assert finding(output)["original_pair_status"] == "unknown"
    assert finding(output)["responses"]["5"]["status"] == "not_measured_outside_fixed_cohort"


def test_full_union_variants_preserved_without_detector_or_calibration_calls(monkeypatch):
    saved, chunks = inputs(overrides={(DAY, "KRX:000001"): curve_rows()})
    def forbidden(*args, **kwargs):
        pytest.fail("CE must not repeat detector, calibration or old bootstrap")
    monkeypatch.setattr(ce.cc, "aggregate_session", forbidden)
    monkeypatch.setattr(ce.cc, "calibrate_session", forbidden)
    monkeypatch.setattr(ce.cc, "dispersion", forbidden)
    monkeypatch.setattr(cd, "build_comparison", forbidden)
    monkeypatch.setattr(cd, "_uncertainty", forbidden)
    output = ce.build_horizons(saved, chunks)
    assert set(output["variants"]) == {"D+1", "D+2"}
    assert finding(output)["responses"] == finding(output, variant="D+2")["responses"]


def test_multi_session_120minute_anchor_reproduces_uncertainty_and_zero_event_dates():
    days = (DAY, "2025-09-05", "2025-09-08")
    saved, chunks = inputs(days=days, overrides={
        (DAY, "KRX:000001"): curve_rows(),
        (days[-1], "KRX:000001"): minute_rows(days[-1], breakout=True, terminal="101")})
    output = ce.build_horizons(saved, chunks)
    old = saved["variants"]["D+1"]["families"][BREAKOUT]["paired_complete_conditional"]
    summary = output["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"]["120"]
    assert summary["measured_pairs"] == old["events"] == 2
    assert summary["paired_mean_uncertainty"] == old["paired_mean_uncertainty"]
    assert summary["paired_mean_uncertainty"]["reference_sessions"] == 3
    assert summary["paired_mean_uncertainty"]["known_pair_sessions"] == 2
    assert summary["own_label_dispersion"]["event_sd"] > 0
    assert summary["own_label_dispersion"]["mde_bps"] > 33
    for horizon in ce.HORIZONS:
        assert output["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"][str(horizon)]["measured_pairs"] == 2


def test_all_preselected_peers_use_exact_fraction_mean_before_float_boundary():
    control = minute_rows(symbol="KRX:000002")
    for index in range(40, 160):
        control[index].update(open="101", high="103", low="101", close="103")
    saved, chunks = inputs(overrides={(DAY, "KRX:000001"): curve_rows(), (DAY, "KRX:000002"): control})
    response = finding(ce.build_horizons(saved, chunks))["responses"]["5"]
    assert response["control_mean_price_change"] == Fraction(2, 101 * 19)
    assert response["excess_price_change"] == Fraction(1, 100) - Fraction(2, 101 * 19)


@pytest.mark.parametrize("mutation", ["raw_column", "raw_timestamp", "vintage", "raw_count", "chunk_order", "extra_chunk", "missing_chunk", "cohort_value", "entry", "summary", "parameters", "state"])
def test_rejects_scope_input_and_120minute_anchor_drift(mutation):
    saved, chunks = inputs(overrides={(DAY, "KRX:000001"): curve_rows()})
    provided = chunks
    if mutation == "raw_column":
        chunks[DAY, "KRX:000001"]["rows"][0]["volume"] = "1"
    elif mutation == "raw_timestamp":
        chunks[DAY, "KRX:000001"]["rows"][0]["open_time_ms"] = True
    elif mutation == "vintage":
        saved["variants"]["D+1"]["days"][0]["data_vintage"] = "0" * 64
    elif mutation == "raw_count":
        saved["input_evidence"]["queried_rows"] -= 1
    elif mutation == "chunk_order":
        provided = list(reversed(list(chunks.values())))
    elif mutation == "extra_chunk":
        provided = [*chunks.values(), next(iter(chunks.values()))]
    elif mutation == "missing_chunk":
        provided = list(chunks.values())[:-1]
    elif mutation == "cohort_value":
        finding(saved)["comparison"]["excess_price_change"]["numerator"] = "99"
    elif mutation == "entry":
        finding(saved)["comparison"]["event_label"]["entry_label_start"] = DAY + "T09:35:00+09:00"
    elif mutation == "summary":
        saved["variants"]["D+1"]["families"][BREAKOUT]["paired_complete_conditional"]["excess_mean"] = .999
    elif mutation == "parameters":
        saved["parameters"]["label_minutes"] = 5
    else:
        finding(saved)["status"] = "profit"
    with pytest.raises((ce.HorizonRefusal, cd.ComparisonRefusal)):
        ce.build_horizons(saved, provided)


def test_own_horizon_dispersion_mde_zero_days_checkpoint_and_repeatability():
    reference = ["2025-09-04", "2025-09-05", "2025-09-08", "2025-09-09", "2025-09-10", "2025-09-11"]
    rows = [{"session": reference[0], "label": Fraction(1, 100)},
        {"session": reference[-1], "label": Fraction(-1, 100)}]
    checkpoints = []
    result = ce._statistics(rows, reference, lambda: checkpoints.append(True))
    assert result == ce._statistics(rows, reference, lambda: None)
    assert result["reference_sessions"] == 6 and result["known_label_sessions"] == 2
    assert result["event_sd"] > 0 and result["design_se"] > 0
    assert result["corrected_to_naive_se_ratio"] >= 1
    assert len(checkpoints) >= 60
    summary = ce._summary([{"session": row["session"], "symbol": "KRX:000001", "status": "signal",
        "response": {"status": "measured", "event_price_change": row["label"],
            "control_mean_price_change": Fraction(0), "excess_price_change": row["label"]}} for row in rows],
        reference, uncertainty=True, checkpoint=lambda: None)
    own = summary["own_label_dispersion"]
    assert own["mde_bps"] > 33 and own["would_require_operator_underpower_choice"] is True
    assert own["underpower_exception_applied"] is True
    assert own["descriptive_extension_authorized"] is True
    assert own["planning_constraint"] == "own_label_mde_exceeds_assumed_cost_floor"
    assert own["family_adopted"] is False


def test_one_known_session_never_creates_independent_date_replication():
    rows = [{"session": DAY, "label": Fraction(value, 100)} for value in (1, 2)]
    result = ce._statistics(rows, [DAY, "2025-09-05"], lambda: None)
    assert result["naive_se"] is not None and result["bootstrap_se"] is None
    assert result["design_se"] is None


def test_own_horizon_sizing_precedes_aggregate_mean_comparison(monkeypatch):
    stages = []
    real_statistics, real_distribution = ce._statistics, cd._distribution
    def statistics(*args, **kwargs):
        stages.append("sizing")
        return real_statistics(*args, **kwargs)
    def distribution(*args, **kwargs):
        stages.append("mean")
        return real_distribution(*args, **kwargs)
    monkeypatch.setattr(ce, "_statistics", statistics)
    monkeypatch.setattr(cd, "_distribution", distribution)
    ce._summary([{"session": DAY, "symbol": "KRX:000001", "status": "signal",
        "response": {"status": "measured", "event_price_change": Fraction(1, 100),
            "control_mean_price_change": Fraction(0), "excess_price_change": Fraction(1, 100)}}],
        [DAY], uncertainty=True, checkpoint=lambda: None)
    assert stages == ["sizing", "sizing", "mean", "mean"]


def test_build_and_bootstrap_budget_failures_propagate_immediately():
    saved, chunks = inputs(overrides={(DAY, "KRX:000001"): curve_rows()})
    def exhausted():
        raise RuntimeError("synthetic_budget_exhausted")
    with pytest.raises(RuntimeError, match="synthetic_budget_exhausted"):
        ce.build_horizons(saved, chunks, checkpoint=exhausted)
    with pytest.raises(RuntimeError, match="synthetic_budget_exhausted"):
        ce._statistics([{"session": DAY, "label": Fraction(1)},
            {"session": "2025-09-05", "label": Fraction(0)}], [DAY, "2025-09-05"], exhausted)


def specification():
    return {"id": ce.SPECIFICATION_ID, "mode": ce.MODE, "window": deepcopy(ce.WINDOW),
        "parameters": deepcopy(ce.PARAMETERS), "resources": deepcopy(ce.RESOURCES),
        **ce.REGISTERED_PATHS, "upstream_sha256": ce.UPSTREAM_SHA256,
        "input_compressed_sha256": ce.INPUT_COMPRESSED_SHA256, "input_stream_sha256": ce.INPUT_STREAM_SHA256}


@pytest.mark.parametrize("mutation", ["horizons", "cohort", "budget", "cost", "pin", "extra"])
def test_exact_specification_rejects_scope_drift(mutation):
    spec = specification()
    if mutation == "horizons":
        spec["parameters"]["response_horizons_minutes"].append(240)
    elif mutation == "cohort":
        spec["parameters"]["cohort"] = "horizon_specific"
    elif mutation == "budget":
        spec["resources"]["cpu_seconds"] = 301
    elif mutation == "cost":
        spec["parameters"]["cost_floor_bps"] = "0"
    elif mutation == "pin":
        spec["upstream_sha256"] = "0" * 64
    else:
        spec["waiver"] = True
    with pytest.raises(ce.HorizonRefusal, match="specification_scope_mismatch"):
        ce.validate_spec(spec)


def test_expected_specification_and_fixed_actual_cohort_counts():
    ce.validate_spec(specification())
    ce.validate_spec(json.loads(ce.SPECIFICATION_PATH.read_bytes()))
    assert ce.EXPECTED_COMPLETE_PAIRS == {"D+1": {BREAKOUT: 884, RECOVERY: 1109},
        "D+2": {BREAKOUT: 881, RECOVERY: 1106}}
    assert ce.RESOURCES["cpu_seconds"] == 300 and ce.RESOURCES["address_space_mib"] == 512
    assert ce.RESOURCES["wall_seconds"] == 1200


def test_recovery_registration_changes_only_exclusive_output_and_keeps_original_immutable():
    original = json.loads(ce.SPECIFICATION_PATH.read_bytes())
    recovery = json.loads(ce.RECOVERY_SPECIFICATION_PATH.read_bytes())
    assert {key for key in original if original[key] != recovery[key]} == {"output_directory"}
    assert recovery["output_directory"] == ce.RECOVERY_REGISTERED_PATHS["output_directory"]
    ce.validate_spec(recovery, recovery=True)
    with pytest.raises(ce.HorizonRefusal, match="scope_mismatch"):
        ce.validate_spec(recovery)
    with pytest.raises(ce.HorizonRefusal, match="scope_mismatch"):
        ce.validate_spec(original, recovery=True)


@pytest.mark.parametrize("change", ["cpu", "bootstrap", "horizons", "output", "pin"])
def test_recovery_has_no_scope_or_resource_waiver(change):
    spec = json.loads(ce.RECOVERY_SPECIFICATION_PATH.read_bytes())
    if change == "cpu":
        spec["resources"]["cpu_seconds"] = 301
    elif change == "bootstrap":
        spec["parameters"]["bootstrap_draws"] = 1999
    elif change == "horizons":
        spec["parameters"]["response_horizons_minutes"] = [5]
    elif change == "output":
        spec["output_directory"] = ce.REGISTERED_PATHS["output_directory"]
    else:
        spec["upstream_sha256"] = "f" * 64
    with pytest.raises(ce.HorizonRefusal, match="scope_mismatch"):
        ce.validate_spec(spec, recovery=True)


def test_registration_selector_is_not_a_truthy_scope_override():
    with pytest.raises(ce.HorizonRefusal, match="invalid_registration_selector"):
        ce.validate_spec(specification(), recovery=1)


def test_archive_stream_hash_framing_and_checkpoints(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.jsonl.gz"
    line = b'{"synthetic":true}\n'
    with gzip.open(path, "wb") as stream:
        stream.write(line)
    monkeypatch.setattr(ce, "INPUT_STREAM_SHA256", hashlib.sha256(line).hexdigest())
    checkpoints = []
    assert list(ce._archive_chunks(path, lambda: checkpoints.append(True))) == [{"synthetic": True}]
    assert len(checkpoints) == 2
    monkeypatch.setattr(ce, "INPUT_STREAM_SHA256", "0" * 64)
    with pytest.raises(ce.HorizonRefusal, match="input_stream_hash_mismatch"):
        list(ce._archive_chunks(path, lambda: None))
    with gzip.open(path, "wb") as stream:
        stream.write(line.rstrip())
    with pytest.raises(ce.HorizonRefusal, match="input_stream_budget_or_framing_mismatch"):
        list(ce._archive_chunks(path, lambda: None))


class SyntheticBudget:
    def __init__(self):
        self.stages = []
        self.fail = False

    def checkpoint(self, stage):
        self.stages.append(stage)
        if self.fail:
            raise RuntimeError("synthetic_shared_budget_exhausted")

    def finish(self):
        pytest.fail("only the parent can finish the shared producer/verifier budget")


@pytest.fixture
def runner(tmp_path, monkeypatch):
    saved, chunks = inputs(overrides={(DAY, "KRX:000001"): curve_rows()})
    archive, upstream, spec_path = [tmp_path / name for name in ("synthetic.jsonl.gz", "synthetic.json", "spec.json")]
    stream = b"".join(ce._canonical(chunk) + b"\n" for chunk in chunks.values())
    archive.write_bytes(gzip.compress(stream, mtime=0))
    monkeypatch.setattr(ce, "INPUT_COMPRESSED_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(ce, "INPUT_STREAM_SHA256", hashlib.sha256(stream).hexdigest())
    saved["input_evidence"].update(compressed_sha256=ce.INPUT_COMPRESSED_SHA256,
        stream_sha256=ce.INPUT_STREAM_SHA256)
    upstream.write_bytes(ce._canonical(saved))
    monkeypatch.setattr(ce, "UPSTREAM_SHA256", hashlib.sha256(upstream.read_bytes()).hexdigest())
    monkeypatch.setattr(ce, "EXPECTED_REFERENCE_SESSIONS", 1)
    monkeypatch.setattr(ce, "EXPECTED_COMPLETE_PAIRS", {key: {family:
        saved["variants"][key]["families"][family]["pair_counts"]["complete"]
        for family in ce.FAMILIES} for key in ("D+1", "D+2")})
    paths = {"upstream_path": str(upstream), "input_archive_path": str(archive),
        "output_directory": str(tmp_path / "output"), "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(ce, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(ce, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(ce._canonical(specification()))
    return {"specification_path": spec_path, "upstream_path": upstream, "input_archive_path": archive,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"]),
        "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_runner_starts_durably_before_saved_read_streams_private_output_and_keeps_budget_open(runner, monkeypatch):
    original_open = Path.open
    evidence = {runner["upstream_path"], runner["input_archive_path"]}
    def checked_open(path, *args, **kwargs):
        if path in evidence:
            assert statuses(runner)[0] == "started"
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", checked_open)
    result = ce.run_horizons(**runner)
    assert result["status"] == "completed" and statuses(runner) == ["started", "completed"]
    report_path = runner["output_directory"] / "report.json"
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert runner["output_directory"].stat().st_mode & 0o777 == 0o700
    report = json.loads(report_path.read_bytes())
    assert report["input_evidence"]["queried_rows"] == 7800
    assert report["source_sha256"] and report["runtime"]["executable_sha256"]
    assert result["result"]["output_sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    # The streaming serializer has exactly the same canonical wire encoding.
    assert report_path.read_bytes() == ce._canonical(report) + b"\n"
    assert len(runner["budget"].stages) > 100
    runner["budget"].checkpoint("parent_verifier_still_has_same_budget")
    with pytest.raises(ce.HorizonRefusal, match="existing_output"):
        ce.run_horizons(**runner)


@pytest.mark.parametrize("target", ["upstream_path", "input_archive_path", "report", "source", "callback", "budget"])
def test_runner_completion_rejects_source_input_output_and_shared_budget_drift(runner, monkeypatch, target):
    real_sha = ce._sha
    changed = False
    def sha(path):
        if changed and target == "source" and path == Path(ce.__file__):
            return "f" * 64
        return real_sha(path)
    monkeypatch.setattr(ce, "_sha", sha)
    def mutate():
        nonlocal changed
        changed = True
        if target == "callback":
            raise RuntimeError("synthetic_callback_failure")
        if target == "budget":
            runner["budget"].fail = True
        elif target != "source":
            path = runner["output_directory"] / "report.json" if target == "report" else runner[target]
            with path.open("ab") as stream:
                stream.write(b" ")
    with pytest.raises((ce.HorizonRefusal, RuntimeError)):
        ce.run_horizons(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]
    assert (runner["output_directory"] / "report.json").is_file()


def test_runner_input_pin_failure_records_failed_without_replay(runner, monkeypatch):
    runner["upstream_path"].write_bytes(b"{}")
    monkeypatch.setattr(ce, "build_horizons", lambda *args, **kwargs: pytest.fail("invalid evidence must not replay"))
    with pytest.raises(ce.HorizonRefusal, match="hash_mismatch"):
        ce.run_horizons(**runner)
    assert statuses(runner) == ["started", "failed"]


def test_runner_registered_cohort_is_not_silently_changed(runner, monkeypatch):
    monkeypatch.setattr(ce, "EXPECTED_COMPLETE_PAIRS", {key: {BREAKOUT: 2, RECOVERY: 0} for key in ("D+1", "D+2")})
    with pytest.raises(ce.HorizonRefusal, match="registered_complete_pair_population_changed"):
        ce.run_horizons(**runner)
    assert statuses(runner) == ["started", "failed"]


def test_runner_selects_only_the_registered_recovery_path(runner, monkeypatch):
    original_spec = runner["specification_path"]
    recovery_spec = original_spec.with_name("recovery.json")
    recovery_output = runner["output_directory"].with_name("recovery-output")
    paths = {**ce.REGISTERED_PATHS, "output_directory": str(recovery_output)}
    monkeypatch.setattr(ce, "RECOVERY_SPECIFICATION_PATH", recovery_spec)
    monkeypatch.setattr(ce, "RECOVERY_REGISTERED_PATHS", paths)
    recovery_spec.write_bytes(ce._canonical({**specification(), **paths}))
    runner.update(specification_path=recovery_spec, output_directory=recovery_output)
    result = ce.run_horizons(**runner)
    assert result["status"] == "completed" and statuses(runner) == ["started", "completed"]
    assert original_spec.exists() and not recovery_output.with_name("output").exists()
    with pytest.raises(ce.HorizonRefusal, match="existing_output"):
        ce.run_horizons(**runner)
