"""Synthetic-only causal selection, own-power and bounded caller checks."""

from copy import deepcopy
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
from statistics import NormalDist

import pytest

from research import intraday_entry_strength as cg
from research import intraday_pattern_horizons as ce
from test_intraday_pattern_horizons import inputs
from test_intraday_pattern_comparison import DAY, BREAKOUT, RECOVERY, minute_rows


def fixture(*, days=(DAY,), overrides=None, only_event=False):
    changes = {(day, "KRX:000001"): minute_rows(day, breakout=True) for day in days}
    changes.update(overrides or {})
    saved, chunks = inputs(days=days, overrides=changes, only_event=only_event)
    return json.loads(cg._canonical(ce.build_horizons(saved, chunks))), chunks


def finding(report, *, family=BREAKOUT, symbol="KRX:000001", day=DAY, variant="D+1"):
    return next(row["families"][family] for row in report["variants"][variant]["days"]
        if row["symbol"] == symbol and row["session"] == day)


def rebind_vintage(saved, chunks, *, day=DAY, symbol="KRX:000001"):
    digest = hashlib.sha256(cg._canonical(chunks[day, symbol]) + b"\n").hexdigest()
    for variant in saved["variants"].values():
        next(row for row in variant["days"] if row["session"] == day and row["symbol"] == symbol)["data_vintage"] = digest
    saved["input_evidence"]["queried_rows"] = sum(len(chunk["rows"]) for chunk in chunks.values())


def test_exact_prior_window_original_peers_availability_and_no_future_labels():
    saved, chunks = fixture()
    output = cg.build_calibration(saved, chunks)
    row = finding(output)
    feature = row["feature"]
    opening = cg.cc.session_open(DAY)
    assert feature["start"] == opening + 5 * cg.cc.MINUTE
    assert feature["end"] == opening + 35 * cg.cc.MINUTE
    assert feature["available_at"] == feature["decision_at"] == opening + 37 * cg.cc.MINUTE
    assert feature["event"]["price_change"] == Fraction(1, 50)
    assert len(feature["peers"]) == 19 and feature["peer_mean_price_change"] == 0
    assert feature["excess_price_change"] == Fraction(1, 50)
    assert row["event"] == {name: finding(saved)["event"][name] for name in ("decision_at", "signal_bar_end")}
    assert row["selected_controls"] == [{"symbol": peer["symbol"]} for peer in finding(saved)["selected_controls"]]
    assert row["selection_state"] == "pass" and row["label_cohort_state"] == "selected_complete"
    wire = cg._canonical(output).decode()
    for forbidden in ("event_price_change", "excess_mean", "terminal_closes", "positive_fraction", "median", "p_value"):
        assert forbidden not in wire
    assert output["comparison_performed"] is False and output["requires_operator_choice"] is True
    summary = output["variants"]["D+1"]["families"][BREAKOUT]
    assert summary["selection_counts"] == {"pass": 1, "reject": 0, "unknown": 0, "non_signal": 19}
    assert summary["by_month"]["2025-09"] == summary["by_week"]["2025-W36"] == cg._counts(
        [row["families"][BREAKOUT] for row in output["variants"]["D+1"]["days"]])


@pytest.mark.parametrize("first_open,expected", [("102", "reject"), ("103", "reject"), ("101", "pass")])
def test_strict_positive_excess_rejects_exact_zero_and_negative(first_open, expected):
    saved, chunks = fixture()
    chunks[DAY, "KRX:000001"]["rows"][5].update(open=first_open, high=first_open)
    rebind_vintage(saved, chunks)
    row = finding(cg.build_calibration(saved, chunks))
    assert row["feature"]["excess_price_change"] == Fraction(102, int(first_open)) - 1
    assert row["selection_state"] == expected


def test_equal_weight_all_original_peer_exact_fractions_and_no_replacement():
    saved, chunks = fixture()
    peer = chunks[DAY, "KRX:000002"]["rows"]
    peer[5].update(open="101", high="101")
    peer[34].update(high="103", close="103")
    rebind_vintage(saved, chunks, symbol="KRX:000002")
    row = finding(cg.build_calibration(saved, chunks))
    assert row["feature"]["peer_mean_price_change"] == Fraction(2, 101 * 19)
    assert row["feature"]["excess_price_change"] == Fraction(1, 50) - Fraction(2, 101 * 19)
    assert [item["symbol"] for item in row["selected_controls"]] == [item["symbol"] for item in finding(saved)["selected_controls"]]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "inconsistent", "nonfinite"])
@pytest.mark.parametrize("symbol", ["KRX:000001", "KRX:000002"])
def test_missing_invalid_or_duplicate_feature_is_unknown_keeps_original_peers(mutation, symbol):
    saved, chunks = fixture()
    raw = chunks[DAY, symbol]["rows"]
    if mutation == "missing":
        raw.pop(10)
    elif mutation == "duplicate":
        raw.extend([deepcopy(raw[10]), deepcopy(raw[10])])
    elif mutation == "inconsistent":
        raw[10].update(high="99")
    else:
        raw[10].update(close="NaN")
    rebind_vintage(saved, chunks, symbol=symbol)
    row = finding(cg.build_calibration(saved, chunks))
    assert row["selection_state"] == "unknown" and row["label_cohort_state"] == "not_selected"
    assert row["original_pair_status"] == "complete" and len(row["selected_controls"]) == 19
    assert ("event_feature_unknown" if symbol.endswith("000001") else "original_selected_control_feature_unknown") in row["selection_unknown_reasons"]


def test_late_availability_is_unknown_and_preopening_window_does_not_wrap():
    saved, chunks = fixture()
    event = finding(saved)["event"]
    event["decision_at"] = DAY + "T09:36:00+09:00"
    row = finding(cg.build_calibration(saved, chunks))
    assert row["selection_state"] == "unknown"
    assert row["selection_unknown_reasons"] == ["feature_unavailable_at_original_decision"]
    event["signal_bar_end"] = DAY + "T09:25:00+09:00"
    event["decision_at"] = DAY + "T09:27:00+09:00"
    row = finding(cg.build_calibration(saved, chunks))
    assert row["selection_state"] == "unknown"
    assert row["feature"]["event"]["missing_minute_buckets"] == 5


def test_future_raw_and_future_label_values_never_change_selection_and_no_detectors(monkeypatch):
    saved, chunks = fixture()
    before = finding(cg.build_calibration(saved, chunks))
    for raw in chunks[DAY, "KRX:000001"]["rows"][35:]:
        raw.update(open="900", high="1000", low="800", close="950")
    rebind_vintage(saved, chunks)
    for response in finding(saved)["responses"].values():
        response["event_price_change"] = {"numerator": "-99", "denominator": "100"}
    def forbidden(*args, **kwargs):
        pytest.fail("calibration must not detect events, reconstruct future labels or compare outcomes")
    for module, name in ((cg.cc, "aggregate_session"), (cg.cc, "calibrate_session"),
            (cg.cc, "dispersion"), (cg.cd, "build_comparison"), (cg.cd, "_distribution"), (ce, "_label")):
        monkeypatch.setattr(module, name, forbidden)
    after = finding(cg.build_calibration(saved, chunks))
    assert after["feature"] == before["feature"] and after["selection_state"] == before["selection_state"]


def test_original_unknown_pair_never_rescued_and_all_unknown_target_rows_retained():
    missing_future = minute_rows(symbol="KRX:000002")
    missing_future.pop(100)
    saved, chunks = fixture(overrides={(DAY, "KRX:000002"): missing_future})
    output = cg.build_calibration(saved, chunks)
    row = finding(output)
    assert row["selection_state"] == "pass" and row["original_pair_status"] == "unknown"
    assert row["label_cohort_state"] == "outside_original_complete_cohort"
    assert all(item["measured_labels"] == 0 for item in output["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"].values())
    empty, chunks = fixture(only_event=True, overrides={(DAY, "KRX:000001"): []})
    output = cg.build_calibration(empty, chunks)
    for variant in output["variants"].values():
        assert len(variant["days"]) == 20
        for family in cg.FAMILIES:
            assert variant["families"][family]["counts"]["unknown"] == 20
            assert variant["families"][family]["selection_counts"]["non_signal"] == 20
    assert output["requires_operator_choice"] is True


def test_selected_own_label_power_all_reference_dates_all_horizons_and_no_waiver():
    days = (DAY, "2025-09-05", "2025-09-08", "2025-09-09", "2025-09-10", "2025-09-11")
    saved, chunks = fixture(days=days, overrides={(day, "KRX:000001"): minute_rows(day) for day in days[1:-1]})
    finding(saved, day=days[-1])["responses"]["120"]["event_price_change"] = {"numerator": "-1", "denominator": "100"}
    summary = cg.build_calibration(saved, chunks)["variants"]["D+1"]["families"][BREAKOUT]
    records = [{"session": days[0], "label": Fraction(1, 100)}, {"session": days[-1], "label": Fraction(-1, 100)}]
    own = summary["by_horizon"]["120"]
    assert own["reference_sessions"] == 6 and own["known_label_sessions"] == 2 and own["measured_labels"] == 2
    assert all(own[key] == value for key, value in ce._statistics(records, list(days), lambda: None).items())
    assert own["design_se"] == max(own["naive_se"], own["session_cluster_se"], own["bootstrap_se"])
    assert own["mde_bps"] == (NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) * own["design_se"] * 10000
    assert own["mde_bps"] > 33 and own["requires_operator_choice"] is True
    assert own["underpower_exception_applied"] is False
    assert set(summary["by_horizon"]) == {"5", "15", "30", "60", "120"}
    assert all(item["measured_labels"] == 2 for item in summary["by_horizon"].values())
    assert cg._own_power([], list(days), lambda: None)["requires_operator_choice"] is True
    zero = cg._own_power([{"session": days[0], "label": Fraction(0)},
        {"session": days[-1], "label": Fraction(0)}], list(days), lambda: None)
    assert zero["mde_bps"] == 0 and zero["requires_operator_choice"] is False


@pytest.mark.parametrize("mutation", ["state", "cohort", "controls", "reference", "horizons", "vintage", "counts", "metadata", "order", "extra", "missing"])
def test_rejects_inconsistent_upstream_or_raw_population(mutation):
    saved, chunks = fixture()
    provided = chunks
    if mutation == "state":
        finding(saved)["status"] = "profit"
    elif mutation == "cohort":
        finding(saved)["original_pair_status"] = "unknown"
    elif mutation == "controls":
        finding(saved)["selected_controls"].append(finding(saved)["selected_controls"][0])
    elif mutation == "reference":
        saved["reference_sessions"].append("2025-09-05")
    elif mutation == "horizons":
        finding(saved)["responses"].pop("120")
    elif mutation == "vintage":
        saved["variants"]["D+1"]["days"][0]["data_vintage"] = "0" * 64
    elif mutation == "counts":
        saved["input_evidence"]["queried_rows"] -= 1
    elif mutation == "metadata":
        chunks[DAY, "KRX:000001"]["available_at"] = "2025-09-04T09:00:00+09:00"
    elif mutation == "order":
        provided = list(reversed(list(chunks.values())))
    elif mutation == "extra":
        provided = [*chunks.values(), next(iter(chunks.values()))]
    else:
        provided = list(chunks.values())[:-1]
    with pytest.raises((cg.EntryStrengthRefusal, cg.cd.ComparisonRefusal)):
        cg.build_calibration(saved, provided)


def specification():
    return {"id": cg.SPECIFICATION_ID, "mode": cg.MODE, "window": deepcopy(cg.WINDOW),
        "parameters": deepcopy(cg.PARAMETERS), "resources": deepcopy(cg.RESOURCES), **cg.REGISTERED_PATHS,
        "upstream_sha256": cg.UPSTREAM_SHA256, "input_compressed_sha256": cg.INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": cg.INPUT_STREAM_SHA256}


@pytest.mark.parametrize("mutation", ["feature", "selection", "bootstrap", "cpu", "waiver", "pin", "output"])
def test_exact_spec_rejects_every_scope_change(mutation):
    spec = specification()
    if mutation == "feature":
        spec["parameters"]["feature_minutes"] = 15
    elif mutation == "selection":
        spec["parameters"]["feature_selection"] = "greater_or_equal_zero"
    elif mutation == "bootstrap":
        spec["parameters"]["bootstrap_draws"] = 1999
    elif mutation == "cpu":
        spec["resources"]["cpu_seconds"] = 301
    elif mutation == "waiver":
        spec["underpower_exception"] = True
    elif mutation == "pin":
        spec["upstream_sha256"] = "0" * 64
    else:
        spec["output_directory"] += "-retry"
    with pytest.raises(cg.EntryStrengthRefusal, match="specification_scope_mismatch"):
        cg.validate_spec(spec)


def test_committed_spec_actual_pins_counts_and_no_scope_waiver():
    cg.validate_spec(specification())
    cg.validate_spec(json.loads(cg.SPECIFICATION_PATH.read_bytes()))
    assert cg.EXPECTED_REFERENCE_SESSIONS == 257
    assert cg.EXPECTED_INPUT_COUNTS == {"unique_target_days": 5158, "queried_rows": 1002211}
    assert cg.RESOURCES == ce.RESOURCES and cg.HORIZONS == (5, 15, 30, 60, 120)
    assert cg.FLAGS["underpower_exception_applied"] is False


class SyntheticBudget:
    def __init__(self):
        self.stages = []
        self.fail = False

    def checkpoint(self, stage):
        self.stages.append(stage)
        if self.fail:
            raise RuntimeError("synthetic_shared_budget_exhausted")

    def finish(self):
        pytest.fail("only the wrapper finishes the shared budget")


@pytest.fixture
def runner(tmp_path, monkeypatch):
    saved, chunks = fixture()
    archive, upstream, spec_path = [tmp_path / name for name in ("synthetic.jsonl.gz", "synthetic.json", "spec.json")]
    stream = b"".join(cg._canonical(chunk) + b"\n" for chunk in chunks.values())
    archive.write_bytes(gzip.compress(stream, mtime=0))
    monkeypatch.setattr(cg, "INPUT_COMPRESSED_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(cg, "INPUT_STREAM_SHA256", hashlib.sha256(stream).hexdigest())
    monkeypatch.setattr(ce, "INPUT_STREAM_SHA256", cg.INPUT_STREAM_SHA256)
    saved["input_evidence"].update(compressed_sha256=cg.INPUT_COMPRESSED_SHA256, stream_sha256=cg.INPUT_STREAM_SHA256)
    upstream.write_bytes(cg._canonical(saved))
    monkeypatch.setattr(cg, "UPSTREAM_SHA256", hashlib.sha256(upstream.read_bytes()).hexdigest())
    monkeypatch.setattr(cg, "EXPECTED_REFERENCE_SESSIONS", 1)
    monkeypatch.setattr(cg, "EXPECTED_INPUT_COUNTS", {key: saved["input_evidence"][key] for key in cg.EXPECTED_INPUT_COUNTS})
    monkeypatch.setattr(cg, "EXPECTED_COMPLETE_PAIRS", {key: {family:
        sum(row["families"][family]["original_pair_status"] == "complete" for row in saved["variants"][key]["days"])
        for family in cg.FAMILIES} for key in ("D+1", "D+2")})
    paths = {"upstream_path": str(upstream), "input_archive_path": str(archive),
        "output_directory": str(tmp_path / "output"), "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(cg, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(cg, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(cg._canonical(specification()))
    return {"specification_path": spec_path, "upstream_path": upstream, "input_archive_path": archive,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"]),
        "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_runner_durable_start_exclusive_private_output_streaming_and_open_shared_budget(runner, monkeypatch):
    real_open = Path.open
    def checked_open(path, *args, **kwargs):
        if path in {runner["upstream_path"], runner["input_archive_path"]}:
            assert statuses(runner)[0] == "started"
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", checked_open)
    result = cg.run_calibration(**runner)
    path = runner["output_directory"] / "report.json"
    report = json.loads(path.read_bytes())
    assert result["status"] == "completed" and statuses(runner) == ["started", "completed"]
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    assert path.read_bytes() == cg._canonical(report) + b"\n"
    assert len(report["source_sha256"]) == 12 and report["runtime"]["executable_sha256"]
    assert result["result"]["requires_operator_choice"] is True and len(runner["budget"].stages) > 100
    with pytest.raises(cg.EntryStrengthRefusal, match="existing_output"):
        cg.run_calibration(**runner)


@pytest.mark.parametrize("target", ["source", "runtime", "upstream_path", "input_archive_path", "report", "budget", "callback"])
def test_runner_preserves_failure_on_final_drift_or_exhaustion(runner, monkeypatch, target):
    real_sha, changed = cg._sha, False
    def sha(path):
        drift = ((target == "source" and path == Path(cg.__file__))
            or (target == "runtime" and path == Path(cg.sys.executable).resolve()))
        return "f" * 64 if changed and drift else real_sha(path)
    monkeypatch.setattr(cg, "_sha", sha)
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
    with pytest.raises((cg.EntryStrengthRefusal, RuntimeError)):
        cg.run_calibration(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]
    assert (runner["output_directory"] / "report.json").is_file()


@pytest.mark.parametrize("target", ["spec_alias", "unregistered", "symlink", "hardlink"])
def test_runner_rejects_path_aliases_before_evidence_read(runner, monkeypatch, target):
    if target == "spec_alias":
        runner["specification_path"] = runner["specification_path"].parent / "absent" / ".." / "spec.json"
    elif target == "unregistered":
        runner["upstream_path"] = runner["upstream_path"].with_name("other.json")
    else:
        alias = runner["upstream_path"].with_name("alias.json")
        if target == "symlink":
            alias.symlink_to(runner["upstream_path"])
            cg.REGISTERED_PATHS["upstream_path"] = str(alias)
            runner["upstream_path"] = alias
        else:
            alias.hardlink_to(runner["upstream_path"])
            cg.REGISTERED_PATHS["input_archive_path"] = str(alias)
            runner["input_archive_path"] = alias
        runner["specification_path"].write_bytes(cg._canonical(specification()))
    with pytest.raises(cg.EntryStrengthRefusal, match="unregistered|symlinked|aliased"):
        cg.run_calibration(**runner)
    assert not runner["runs_path"].exists() and not runner["output_directory"].exists()


def test_runner_wrong_pin_fails_before_build_and_records_failure(runner, monkeypatch):
    runner["upstream_path"].write_bytes(b"{}")
    monkeypatch.setattr(cg, "build_calibration", lambda *args, **kwargs: pytest.fail("invalid evidence must not build"))
    with pytest.raises(cg.EntryStrengthRefusal, match="hash_mismatch"):
        cg.run_calibration(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("population", ["reference", "pairs", "raw"])
def test_runner_exact_registered_population_cannot_drift(runner, monkeypatch, population):
    if population == "reference":
        monkeypatch.setattr(cg, "EXPECTED_REFERENCE_SESSIONS", 2)
    elif population == "pairs":
        monkeypatch.setattr(cg, "EXPECTED_COMPLETE_PAIRS", {key: {family: 99 for family in cg.FAMILIES} for key in ("D+1", "D+2")})
    else:
        monkeypatch.setattr(cg, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 21, "queried_rows": 7800})
    with pytest.raises(cg.EntryStrengthRefusal, match="population_changed"):
        cg.run_calibration(**runner)
    assert statuses(runner) == ["started", "failed"]


def test_build_and_bootstrap_budget_failures_propagate():
    saved, chunks = fixture()
    def exhausted():
        raise RuntimeError("synthetic_budget_exhausted")
    with pytest.raises(RuntimeError, match="synthetic_budget_exhausted"):
        cg.build_calibration(saved, chunks, checkpoint=exhausted)
    with pytest.raises(RuntimeError, match="synthetic_budget_exhausted"):
        cg._own_power([{"session": DAY, "label": Fraction(1)},
            {"session": "2025-09-05", "label": Fraction(0)}], [DAY, "2025-09-05"], exhausted)
