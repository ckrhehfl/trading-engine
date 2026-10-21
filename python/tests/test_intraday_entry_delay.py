"""Synthetic-only fixed-exit label sizing and durable registration checks."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from research import intraday_entry_delay as ck
from test_intraday_signal_paths import fixture as path_fixture, minute_rows, finding, DAY, SYMBOL


CONTINUATION, RECOVERY = ck.FAMILIES


def fixture(raw=None, *, sessions=2):
    """Extend the hand-written CI matrix with separately retained CH sizing."""
    first, original = path_fixture(raw)
    upstream, chunks = deepcopy(first), {}
    days = [(date.fromisoformat(DAY) + timedelta(days=i)).isoformat() for i in range(sessions)]
    upstream["reference_sessions"] = days
    for variant in upstream["variants"].values():
        variant["days"] = []
    for day in days:
        offset = ck.cc.session_open(day) - ck.cc.session_open(DAY)
        for (_, symbol), saved in original.items():
            chunk = deepcopy(saved)
            chunk.update(session=day, observation_date=day)
            for row in chunk["rows"]:
                row["open_time_ms"] += int(offset.total_seconds()) * 1000
            chunks[day, symbol] = chunk
        for key, variant in upstream["variants"].items():
            for saved in first["variants"][key]["days"]:
                row = deepcopy(saved)
                row["session"] = day
                row["data_vintage"] = hashlib.sha256(ck._canonical(chunks[day, row["symbol"]]) + b"\n").hexdigest()
                for family in ck.FAMILIES:
                    event = row["families"][family]["event"]
                    if event is not None:
                        for name in event:
                            event[name] = (ck.horizons._instant(event[name]) + offset).isoformat()
                variant["days"].append(row)
    for variant in upstream["variants"].values():
        for family in ck.FAMILIES:
            counts = ck.cg._counts([row["families"][family] for row in variant["days"]])
            own = ck.cg._own_power([{"session": day, "label": Fraction()} for day in days], days, lambda: None)
            variant["families"][family] = {**counts, "by_horizon": {
                str(h): {"own_label_dispersion": deepcopy(own)} for h in ck.QUALIFIED_HORIZONS[family]}}
    return json.loads(ck._canonical(upstream)), chunks


def label(report, **kwargs):
    return finding(report, **kwargs)["labels"]["5"]


def cell(report, family=CONTINUATION, variant="D+1", horizon="5"):
    return report["variants"][variant]["families"][family]["by_horizon"][horizon]


def rebind_all(upstream, chunks):
    for variant in upstream["variants"].values():
        for row in variant["days"]:
            row["data_vintage"] = hashlib.sha256(ck._canonical(chunks[row["session"], row["symbol"]]) + b"\n").hexdigest()


def test_fixed_exit_clock_changes_wait_and_holding_without_moving_original_exit():
    raw = minute_rows()
    raw[38].update(open="99", high="100", low="99")
    raw[44].update(high="102", close="102")
    upstream, chunks = fixture(raw)
    output = ck.build_calibration(upstream, chunks)
    pair = label(output)
    opening = ck.cc.session_open(DAY)
    assert pair["decision_at"] == opening + 37 * ck.cc.MINUTE
    assert pair["current_entry_at"] == opening + 40 * ck.cc.MINUTE
    assert pair["earlier_entry_at"] == opening + 38 * ck.cc.MINUTE
    assert pair["exit_at"] == opening + 45 * ck.cc.MINUTE
    assert pair["label_available_at"] == opening + 47 * ck.cc.MINUTE
    assert (pair["original_holding_minutes"], pair["earlier_holding_minutes"]) == (5, 7)
    assert pair["current"]["price_change"] == Fraction(1, 50)
    assert pair["earlier"]["price_change"] == Fraction(1, 33)
    assert pair["current"]["terminal_close"] == pair["earlier"]["terminal_close"] == Decimal("102")
    for h, item in finding(output)["labels"].items():
        assert item["exit_at"] == item["current_entry_at"] + int(h) * ck.cc.MINUTE
        assert item["earlier_holding_minutes"] == int(h) + 2


def test_decimal_precision_survives_price_parsing_and_exact_ratio_boundary():
    raw = minute_rows()
    early = "100.00000000000000000000000000000000000001"
    close = "100.00000000000000000000000000000000000003"
    raw[38].update(open=early, high=early)
    raw[44].update(close=close, high=close)
    upstream, chunks = fixture(raw)
    with localcontext() as context:
        context.prec = 6
        pair = label(ck.build_calibration(upstream, chunks))
    assert pair["earlier"]["price_change"] == Fraction(close) / Fraction(early) - 1
    assert pair["earlier"]["price_change"] != 0
    assert pair["current"]["price_change"] == Fraction(close) / 100 - 1


@pytest.mark.parametrize("minute,mutation", [(38, "missing"), (39, "missing"), (38, "invalid"), (39, "duplicate")])
def test_new_pre_entry_unknown_keeps_selected_denominator_and_current_known(minute, mutation):
    upstream, chunks = fixture()
    raw = chunks[DAY, SYMBOL]["rows"]
    if mutation == "missing":
        raw.pop(minute)
    elif mutation == "invalid":
        raw[minute]["open"] = "0"
    else:
        raw.append(deepcopy(raw[minute]))
    rebind_all(upstream, chunks)
    output = ck.build_calibration(upstream, chunks)
    pair, summary = label(output), cell(output)
    assert pair["current"]["status"] == "known" and pair["current"]["price_change"] == 0
    assert pair["earlier"]["status"] == "unknown" and pair["earlier"]["price_change"] is None
    assert pair["earlier"]["missing_minute_buckets"] == 1
    assert summary["selected_complete"] == 2
    assert summary["earlier_known_labels"] == summary["earlier_unknown_labels"] == 1
    assert summary["earlier_own_power"]["measured_labels"] == 1
    assert summary["requires_operator_choice"] is output["requires_operator_choice"] is True
    assert len(output["variants"]["D+1"]["days"]) == 40


@pytest.mark.parametrize("mutation", ["missing", "invalid", "duplicate", "return", "decision", "wait"])
def test_old_path_or_exact_ch_label_contradiction_refuses(mutation):
    upstream, chunks = fixture()
    raw = chunks[DAY, SYMBOL]["rows"]
    if mutation == "missing":
        raw.pop(41)
    elif mutation == "invalid":
        raw[41]["high"] = "NaN"
    elif mutation == "duplicate":
        raw.append(deepcopy(raw[41]))
    elif mutation == "return":
        for name in ("event_price_change", "excess_price_change"):
            finding(upstream)["responses"]["5"][name] = Fraction(1, 10)
    else:
        event = finding(upstream)["event"]
        shift = 1 if mutation == "wait" else 0
        event["signal_bar_end"] = (ck.cc.session_open(DAY) + (35 + shift) * ck.cc.MINUTE).isoformat()
        event["decision_at"] = (ck.cc.session_open(DAY) + 38 * ck.cc.MINUTE).isoformat()
    rebind_all(upstream, chunks)
    with pytest.raises(ck.EntryDelayRefusal, match="current_path_invalid|label_mismatch|entry_boundary"):
        ck.build_calibration(upstream, chunks)


def test_original_matrix_unknowns_peers_and_raw_metadata_remain_without_comparison(monkeypatch):
    upstream, chunks = fixture()
    def forbidden(*args, **kwargs):
        pytest.fail("calibration must not rerun selection or future-mean comparisons")
    monkeypatch.setattr(ck.ch, "build_comparison", forbidden)
    monkeypatch.setattr(ck.cg, "build_calibration", forbidden)
    monkeypatch.setattr(ck.cd, "_distribution", forbidden)
    monkeypatch.setattr(ck.cc, "calibrate_session", forbidden)
    output = ck.build_calibration(upstream, chunks)
    for key, variant in output["variants"].items():
        for original, row in zip(upstream["variants"][key]["days"], variant["days"], strict=True):
            assert {name: row[name] for name in ("symbol", "session", "data_vintage")} == {name: original[name] for name in ("symbol", "session", "data_vintage")}
            assert row["raw_chunk_metadata"] == {name: chunks[row["session"], row["symbol"]][name] for name in row["raw_chunk_metadata"]}
            assert set(row["raw_chunk_metadata"]) == {"observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level"}
            for family in ck.FAMILIES:
                assert {name: row["families"][family][name] for name in ck.ORIGINAL_FIELDS} == {name: original["families"][family][name] for name in ck.ORIGINAL_FIELDS}
                if original["families"][family]["label_cohort_state"] != "selected_complete":
                    assert row["families"][family]["labels"] is None
        assert set(variant["families"][RECOVERY]["by_horizon"]) == {"5", "15", "30"}
    assert all(output[name] is False for name in ck.cc.FLAGS)
    assert output["calibration_only"] is True and output["comparison_performed"] is False
    assert output["earlier_entry_policy_compared"] is False
    # No upstream CH mean/cost/control output is copied into this report.
    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)
    assert not {"mean", "excess_mean", "control_mean_price_change", "p_value", "after_cost"} & set(keys(output))


def test_own_power_retains_current_and_uses_all_reference_days_before_family_choice(monkeypatch):
    upstream, chunks = fixture(sessions=7)
    # One existing selected event is not selected on this date; keep the date.
    for variant in upstream["variants"].values():
        row = next(row for row in variant["days"] if row["session"] == "2025-09-10" and row["symbol"] == SYMBOL)
        for family in ck.FAMILIES:
            saved = row["families"][family]
            saved.update(selection_state="reject", label_cohort_state="not_selected")
            for response in saved["responses"].values():
                response.update(status="not_measured_not_selected", event_price_change=None, control_mean_price_change=None, excess_price_change=None)
        for family in ck.FAMILIES:
            variant["families"][family].update(ck.cg._counts([r["families"][family] for r in variant["days"]]))
            for summary in variant["families"][family]["by_horizon"].values():
                summary["own_label_dispersion"]["measured_labels"] = 6
                summary["own_label_dispersion"]["known_label_sessions"] = 6
    actual, calls = ck.cg._own_power, []
    def observed(records, reference, checkpoint):
        calls.append((len(records), list(reference)))
        return actual(records, reference, checkpoint)
    monkeypatch.setattr(ck.cg, "_own_power", observed)
    output = ck.build_calibration(upstream, chunks)
    assert len(calls) == 16 and all(n == 6 and days == upstream["reference_sessions"] for n, days in calls)
    assert cell(output)["current_own_power"] == upstream["variants"]["D+1"]["families"][CONTINUATION]["by_horizon"]["5"]["own_label_dispersion"]
    assert cell(output)["earlier_own_power"]["reference_sessions"] == 7
    assert cell(output)["earlier_own_power"]["known_label_sessions"] == 6
    assert output["requires_operator_choice"] is False


@pytest.mark.parametrize("name,value", [("mde_bps", None), ("mde_bps", 34), ("mde_bps", float("nan")),
    ("design_se", 1), ("requires_operator_choice", True), ("underpower_exception_applied", True), ("measured_labels", 1)])
def test_invalid_old_power_is_retained_and_requires_choice(name, value):
    upstream, chunks = fixture()
    original = upstream["variants"]["D+1"]["families"][CONTINUATION]["by_horizon"]["5"]["own_label_dispersion"]
    original[name] = value
    output = ck.build_calibration(upstream, chunks)
    assert cell(output)["current_own_power"] is original
    assert cell(output)["current_power_prerequisite_met"] is False
    assert cell(output)["requires_operator_choice"] is output["requires_operator_choice"] is True


@pytest.mark.parametrize("name,value", [("known_label_sessions", 3), ("event_sd", 1)])
def test_impossible_old_session_count_or_sd_se_relation_cannot_silently_pass(name, value):
    upstream, chunks = fixture(sessions=3)
    if name == "known_label_sessions":
        # Three reference dates cannot imply three known sessions from two labels.
        own = upstream["variants"]["D+1"]["families"][CONTINUATION]["by_horizon"]["5"]["own_label_dispersion"]
        own["measured_labels"] = 2
        assert ck._current_power_valid(own, 2, upstream["reference_sessions"]) is False
    else:
        own = upstream["variants"]["D+1"]["families"][CONTINUATION]["by_horizon"]["5"]["own_label_dispersion"]
        own[name] = value
        output = ck.build_calibration(upstream, chunks)
        assert cell(output)["current_power_prerequisite_met"] is False
        assert cell(output)["requires_operator_choice"] is True


def test_new_earlier_power_failure_does_not_inherit_current_pass_or_exception():
    upstream, chunks = fixture()
    for i, day in enumerate(upstream["reference_sessions"]):
        raw = chunks[day, SYMBOL]["rows"]
        raw[38].update(open="50" if i else "150", low="50", high="150")
    rebind_all(upstream, chunks)
    output = ck.build_calibration(upstream, chunks)
    assert cell(output)["current_power_prerequisite_met"] is True
    assert cell(output)["earlier_own_power"]["mde_bps"] > 33
    assert cell(output)["requires_operator_choice"] is output["requires_operator_choice"] is True
    assert cell(output)["earlier_own_power"]["underpower_exception_applied"] is False


def specification():
    return {"id": ck.SPECIFICATION_ID, "mode": ck.MODE, "window": deepcopy(ck.WINDOW),
        "parameters": deepcopy(ck.PARAMETERS), "resources": deepcopy(ck.RESOURCES), **ck.REGISTERED_PATHS,
        "upstream_sha256": ck.UPSTREAM_SHA256, "input_compressed_sha256": ck.INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": ck.INPUT_STREAM_SHA256}


def test_committed_registration_fixes_inputs_clock_scope_and_original_budget():
    ck.validate_spec(json.loads(ck.SPECIFICATION_PATH.read_bytes()))
    assert ck.EXPECTED_REFERENCE_SESSIONS == 257
    assert ck.EXPECTED_SELECTED_COMPLETE == {"D+1": {CONTINUATION: 787, RECOVERY: 377}, "D+2": {CONTINUATION: 785, RECOVERY: 376}}
    assert ck.EXPECTED_INPUT_COUNTS == {"unique_target_days": 5158, "queried_rows": 1002211}
    assert ck.RESOURCES == ck.ci.RESOURCES
    for name in ("parameters", "resources", "upstream_sha256", "input_stream_sha256", "output_directory"):
        wrong = specification()
        wrong[name] = "drift"
        with pytest.raises(ck.EntryDelayRefusal, match="scope_mismatch"):
            ck.validate_spec(wrong)


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
    upstream_path.write_bytes(ck._canonical(upstream))
    stream = b"".join(ck._canonical(chunk) + b"\n" for chunk in chunks.values())
    archive.write_bytes(gzip.compress(stream, mtime=0))
    monkeypatch.setattr(ck, "UPSTREAM_SHA256", ck._sha(upstream_path))
    monkeypatch.setattr(ck, "INPUT_COMPRESSED_SHA256", ck._sha(archive))
    monkeypatch.setattr(ck, "INPUT_STREAM_SHA256", hashlib.sha256(stream).hexdigest())
    monkeypatch.setattr(ck.horizons, "INPUT_STREAM_SHA256", ck.INPUT_STREAM_SHA256)
    monkeypatch.setattr(ck, "EXPECTED_REFERENCE_SESSIONS", 2)
    monkeypatch.setattr(ck, "EXPECTED_SELECTED_COMPLETE", {key: {family: 2 for family in ck.FAMILIES} for key in ("D+1", "D+2")})
    monkeypatch.setattr(ck, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 40, "queried_rows": 15600})
    paths = {"upstream_path": str(upstream_path), "input_archive_path": str(archive),
        "output_directory": str(tmp_path / "output"), "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(ck, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(ck, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(ck._canonical(specification()))
    return {"specification_path": spec_path, "upstream_path": upstream_path, "input_archive_path": archive,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"]), "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_durable_start_before_both_inputs_private_output_and_single_budget(runner, monkeypatch):
    real_open, reads = Path.open, []
    def observed(path, *args, **kwargs):
        if path in {runner["upstream_path"], runner["input_archive_path"]}:
            assert statuses(runner)[0] == "started"
            reads.append(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    receipt = ck.run_calibration(**runner)
    report_path = runner["output_directory"] / "report.json"
    report = json.loads(report_path.read_bytes())
    assert statuses(runner) == ["started", "completed"] and receipt["status"] == "completed" and reads
    assert len(report["source_sha256"]) == 15
    assert report["source_sha256"][str(runner["specification_path"])] == ck._sha(runner["specification_path"])
    assert str(Path(ck.ci.__file__)) in report["source_sha256"]
    assert report_path.read_bytes() == ck._canonical(report) + b"\n"
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert report_path.parent.stat().st_mode & 0o777 == 0o700
    assert receipt["result"]["calibration_only"] is True
    assert receipt["result"]["comparison_performed"] is False
    runner["budget"].checkpoint("independent_verification_same_budget")
    with pytest.raises(ck.EntryDelayRefusal, match="existing_output"):
        ck.run_calibration(**runner)


@pytest.mark.parametrize("target", ["source", "runtime", "upstream_path", "input_archive_path", "report", "budget", "callback"])
def test_final_drift_or_shared_budget_exhaustion_retains_failed_receipt(runner, monkeypatch, target):
    real_sha, changed = ck._sha, False
    def sha(path):
        drift = (target == "source" and path == Path(ck.__file__)) or (target == "runtime" and path == Path(ck.sys.executable).resolve())
        return "f" * 64 if changed and drift else real_sha(path)
    monkeypatch.setattr(ck, "_sha", sha)
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
    with pytest.raises((ck.EntryDelayRefusal, RuntimeError)):
        ck.run_calibration(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["upstream_path", "input_archive_path"])
def test_input_pins_fail_before_calculation(runner, monkeypatch, target):
    runner[target].write_bytes(b"{}")
    monkeypatch.setattr(ck, "build_calibration", lambda *args, **kwargs: pytest.fail("bad input pin must refuse before build"))
    with pytest.raises(ck.EntryDelayRefusal, match="hash_mismatch"):
        ck.run_calibration(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("target", ["reference", "selected", "raw"])
def test_registered_population_is_enforced(runner, monkeypatch, target):
    if target == "reference":
        monkeypatch.setattr(ck, "EXPECTED_REFERENCE_SESSIONS", 257)
    elif target == "selected":
        monkeypatch.setattr(ck, "EXPECTED_SELECTED_COMPLETE", {key: {family: 3 for family in ck.FAMILIES} for key in ("D+1", "D+2")})
    else:
        monkeypatch.setattr(ck, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 41, "queried_rows": 15600})
    with pytest.raises(ck.EntryDelayRefusal, match="population_changed"):
        ck.run_calibration(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("mutation", ["copied_spec", "unregistered", "symlink", "hardlink"])
def test_registration_and_alias_refusal_precede_input_access(runner, monkeypatch, mutation):
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
        ck.REGISTERED_PATHS["upstream_path"] = str(alias)
        runner["upstream_path"] = alias
        runner["specification_path"].write_bytes(ck._canonical(specification()))
    real_open = Path.open
    def observed(path, *args, **kwargs):
        if path in {runner["upstream_path"], runner["input_archive_path"]}:
            pytest.fail("path refusal must precede either saved input read")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    with pytest.raises(ck.EntryDelayRefusal, match="unregistered|symlinked|aliased"):
        ck.run_calibration(**runner)
    assert not runner["runs_path"].exists()
