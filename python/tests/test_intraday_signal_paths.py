"""Synthetic-only fixed-mask path arithmetic, causality and durable IO."""

from copy import deepcopy
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from research import intraday_signal_paths as ci


DAY = "2025-09-04"
BREAKOUT, RECOVERY = ci.FAMILIES
SYMBOL = "KRX:000001"


def minute_rows(symbol=SYMBOL):
    opening = ci.cc.session_open(DAY)
    return [{"symbol": symbol, "interval": "1m", "open_time_ms": int((opening + i * ci.cc.MINUTE).timestamp()) * 1000,
        "open": "100", "high": "100", "low": "100", "close": "100", "fetched_at": "2026-10-20T00:00:00Z"}
        for i in range(390)]


def fixture(raw=None):
    symbols = [f"KRX:{i:06d}" for i in range(1, 21)]
    opening = ci.cc.session_open(DAY)
    event = {"signal_bar_end": (opening + 35 * ci.cc.MINUTE).isoformat(),
        "decision_at": (opening + 37 * ci.cc.MINUTE).isoformat()}
    chunks = {(DAY, symbol): {"symbol": symbol, "session": DAY, "observation_date": DAY,
        "available_at": None, "retrieved_at": "2026-10-20T01:00:00+00:00", "source": "collector klines",
        "is_final": None, "evidence_level": "assumed", "rows": deepcopy(raw if symbol == SYMBOL and raw is not None else minute_rows(symbol))}
        for symbol in symbols}
    upstream = {"study_id": ci.ch.SPECIFICATION_ID, "mode": ci.ch.MODE, "window": deepcopy(ci.WINDOW),
        "parameters": deepcopy(ci.ch.PARAMETERS), **ci.ch.FLAGS, "reference_sessions": [DAY],
        "qualified_cells_power_prerequisite_met": True, "upstream_calibration_requires_operator_choice": True,
        "excluded_horizons_minutes": {RECOVERY: [60, 120]}, "variants": {}}
    for key in ("D+1", "D+2"):
        variant = {"days": [], "families": {}}
        for index, symbol in enumerate(symbols):
            chunk = chunks[DAY, symbol]
            row = {"symbol": symbol, "session": DAY,
                "data_vintage": hashlib.sha256(ci._canonical(chunk) + b"\n").hexdigest(), "families": {}}
            for family in ci.FAMILIES:
                signal = index < 3
                status = "signal" if signal else "unknown" if index == 3 else "known_no_signal"
                state = "pass" if index in {0, 2} else "reject" if index == 1 else "non_signal"
                cohort = "selected_complete" if index == 0 else "not_selected" if index == 1 else "outside_original_complete_cohort" if index == 2 else "non_signal"
                finding = {"status": status, "event": deepcopy(event) if signal else None,
                    "original_pair_status": "unknown" if index == 2 else "complete" if signal else None,
                    "original_pair_unknown_reasons": ["event_label_unknown"] if index == 2 else [],
                    "original_unknown_control_symbols": [], "original_event_label_state":
                        {"status": "unknown" if index == 2 else "known", "missing_minute_buckets": 1 if index == 2 else 0,
                         "unknown_reason": "incomplete_120minute_path" if index == 2 else None} if signal else None,
                    "selected_controls": [{"symbol": peer} for peer in symbols if peer != symbol] if signal else [],
                    "excluded_controls": [], "selection_state": state, "selection_unknown_reasons": [],
                    "label_cohort_state": cohort, "responses": {}}
                if signal:
                    for horizon in ci.QUALIFIED_HORIZONS[family]:
                        change = (Fraction(chunk["rows"][40 + horizon - 1]["close"]) / Fraction(chunk["rows"][40]["open"]) - 1) if index == 0 else None
                        finding["responses"][str(horizon)] = {"status": "measured" if index == 0 else f"not_measured_{cohort}",
                            "event_price_change": change, "control_mean_price_change": Fraction() if index == 0 else None,
                            "excess_price_change": change}
                row["families"][family] = finding
            variant["days"].append(row)
        for family in ci.FAMILIES:
            variant["families"][family] = ci.cg._counts([row["families"][family] for row in variant["days"]])
        upstream["variants"][key] = variant
    return json.loads(ci._canonical(upstream)), chunks


def finding(report, *, family=BREAKOUT, index=0, variant="D+1"):
    return report["variants"][variant]["days"][index]["families"][family]


def diagnostic(report, **kwargs):
    return finding(report, **kwargs)["diagnostics"]["5"]


def rebind(upstream, chunks):
    for variant in upstream["variants"].values():
        for row in variant["days"]:
            row["data_vintage"] = hashlib.sha256(ci._canonical(chunks[DAY, row["symbol"]]) + b"\n").hexdigest()


def test_extrema_exact_ratios_earliest_ties_cost_equality_and_hindsight_flags():
    raw = minute_rows()
    raw[35]["high"] = "100.4"
    raw[40].update(high="100.3300", low="99.6700")
    raw[41].update(high="100.5", low="99.4")
    raw[42].update(high="100.5", low="99.4")
    raw[44].update(high="100.1", close="100.1")
    upstream, chunks = fixture(raw)
    output = ci.build_paths(upstream, chunks)
    item = diagnostic(output)
    opening = ci.cc.session_open(DAY)
    post = item["post_entry"]
    assert post["mfe"] == Fraction(1, 200) and post["mae"] == Fraction(-3, 500)
    assert post["terminal_price_change"] == Fraction(1, 1000) and post["giveback"] == Fraction(1, 250)
    assert post["maximum_high_minute"] == post["minimum_low_minute"] == opening + 41 * ci.cc.MINUTE
    assert post["first_cost_up_minute"] == post["first_cost_down_minute"] == opening + 41 * ci.cc.MINUTE
    assert post["cost_touch_order"] == "same_minute_order_unknown"
    assert post["mfe_exceeds_cost"] is True and post["exceeded_but_terminal_not_exceed"] is True
    assert item["timing"]["first_signal_anchor_cost_up_phase"] == "unavailable"
    assert item["timing"]["signal_to_entry_minutes"] == 5 and item["timing"]["decision_to_entry_minutes"] == 3
    assert item["signal_anchor_available_at"] == opening + 37 * ci.cc.MINUTE
    assert item["path_available_at"] == opening + 47 * ci.cc.MINUTE
    summary = output["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"]["5"]
    assert summary["mfe_exceeds_cost"] == {"known": 1, "unknown": 0, "count": 1, "fraction": 1}
    assert summary["post_entry"]["mfe"]["median"] == Fraction(1, 200)
    assert summary["first_touch_conditional_minutes_from_entry"]["up"]["mean"] == 1
    assert all(output[name] is False for name in ci.cc.FLAGS)
    assert output["diagnostic_only"] is True and output["hindsight_extrema"] is True
    assert output["earlier_entry_policy_tested"] is output["exit_policy_tested"] is False


@pytest.mark.parametrize("up,down,order", [(None, None, "neither"), (40, None, "up_only"),
    (None, 40, "down_only"), (40, 41, "up_before_down"), (41, 40, "down_before_up"), (40, 40, "same_minute_order_unknown")])
def test_touch_orders_never_infer_intraminute_sequence(up, down, order):
    raw = minute_rows()
    raw[40].update(high="100.33", low="99.67")
    if up is not None:
        raw[up]["high"] = "100.3300001"
    if down is not None:
        raw[down]["low"] = "99.6699999"
    upstream, chunks = fixture(raw)
    assert diagnostic(ci.build_paths(upstream, chunks))["post_entry"]["cost_touch_order"] == order


@pytest.mark.parametrize("phase,index", [("unavailable", 35), ("postdecision_wait", 37), ("post_entry", 40), ("none", None)])
def test_signal_anchor_timing_phases_and_different_anchors_are_descriptive(phase, index):
    raw = minute_rows()
    if index is not None:
        raw[index]["high"] = "100.34"
    upstream, chunks = fixture(raw)
    timing = diagnostic(ci.build_paths(upstream, chunks))["timing"]
    assert timing["first_signal_anchor_cost_up_phase"] == phase
    assert timing["pre_signal_anchor_exceeded_without_post_own_anchor_exceed"] == (phase in ci.PHASES)
    assert timing["entry_price_change_from_signal"] == 0


@pytest.mark.parametrize("missing", [35, 37])
def test_pre_unknown_keeps_all_events_and_independent_phase_known_counts(missing):
    upstream, chunks = fixture()
    raw = chunks[DAY, SYMBOL]["rows"]
    raw[40]["high"] = "101"
    raw.pop(missing)
    rebind(upstream, chunks)
    output = ci.build_paths(upstream, chunks)
    item = diagnostic(output)
    timing = item["timing"]
    assert item["post_entry"]["mfe_exceeds_cost"] is True
    assert timing["pre_entry_status"] == "unknown" and timing["pre_entry_missing_minute_buckets"] == 1
    assert timing["first_signal_anchor_cost_up_phase"] == "unknown_pre_entry"
    assert timing["first_signal_anchor_cost_up_minute"] is None
    assert timing["full_signal_anchor_mfe"] is timing["full_signal_anchor_mae"] is None
    assert timing["pre_signal_anchor_exceeded_without_post_own_anchor_exceed"] is None
    phase = "unavailable" if missing == 35 else "postdecision_wait"
    assert timing["phases"][phase]["signal_anchor_cost_exceeded"] is None
    assert timing["phases"][next(p for p in ci.PHASES if p != phase)]["status"] == "known"
    summary = output["variants"]["D+1"]["families"][BREAKOUT]["by_horizon"]["5"]
    assert summary["events"] == summary["pre_entry_unknown"] == 1 and summary["pre_entry_known"] == 0
    assert summary["pre_signal_anchor_exceeded_without_post_own_anchor_exceed"]["fraction"] is None


@pytest.mark.parametrize("mutation", ["missing_post", "invalid_post", "duplicate_post", "anchor", "late_decision", "terminal"])
def test_original_complete_contradictions_refuse_instead_of_dropping(mutation):
    upstream, chunks = fixture()
    raw = chunks[DAY, SYMBOL]["rows"]
    if mutation == "missing_post":
        raw.pop(41)
    elif mutation == "invalid_post":
        raw[41]["high"] = "NaN"
    elif mutation == "duplicate_post":
        raw.append(deepcopy(raw[41]))
    elif mutation == "anchor":
        raw[34]["close"] = "0"
    elif mutation == "late_decision":
        finding(upstream)["event"]["decision_at"] = (ci.cc.session_open(DAY) + 38 * ci.cc.MINUTE).isoformat()
    else:
        finding(upstream)["responses"]["5"]["event_price_change"] = Fraction(1, 10)
        finding(upstream)["responses"]["5"]["excess_price_change"] = Fraction(1, 10)
    rebind(upstream, chunks)
    with pytest.raises(ci.PathRefusal, match="post_path_invalid|anchor_invalid|boundary|terminal_label_mismatch"):
        ci.build_paths(upstream, chunks)


def test_original_projection_unknowns_peer_identities_and_metadata_are_preserved(monkeypatch):
    upstream, chunks = fixture()
    def forbidden(*args, **kwargs):
        pytest.fail("no detector, feature, peer or performance rerun")
    monkeypatch.setattr(ci.ch, "build_comparison", forbidden)
    monkeypatch.setattr(ci.cg, "build_calibration", forbidden)
    monkeypatch.setattr(ci.cc, "calibrate_session", forbidden)
    output = ci.build_paths(upstream, chunks)
    for key, variant in output["variants"].items():
        assert len(variant["days"]) == 20
        for original, row in zip(upstream["variants"][key]["days"], variant["days"], strict=True):
            assert [row[name] for name in ("symbol", "session", "data_vintage")] == [original[name] for name in ("symbol", "session", "data_vintage")]
            assert row["raw_chunk_metadata"] == {name: chunks[DAY, row["symbol"]][name] for name in row["raw_chunk_metadata"]}
            for family in ci.FAMILIES:
                assert {name: row["families"][family][name] for name in ci.ORIGINAL_FIELDS} == {name: original["families"][family][name] for name in ci.ORIGINAL_FIELDS}
                if original["families"][family]["label_cohort_state"] != "selected_complete":
                    assert row["families"][family]["diagnostics"] is None
        assert set(variant["families"][RECOVERY]["by_horizon"]) == {"5", "15", "30"}
    assert output["input_evidence"]["unique_target_days"] == 20
    assert output["input_evidence"]["queried_rows"] == 7800


def test_shared_day_cache_across_variants_and_families(monkeypatch):
    upstream, chunks = fixture()
    real, calls = ci._diagnostic, []
    def observed(*args):
        calls.append(args[-1])
        return real(*args)
    monkeypatch.setattr(ci, "_diagnostic", observed)
    ci.build_paths(upstream, chunks)
    assert calls == [5, 15, 30, 60, 120]


def specification():
    return {"id": ci.SPECIFICATION_ID, "mode": ci.MODE, "window": deepcopy(ci.WINDOW),
        "parameters": deepcopy(ci.PARAMETERS), "resources": deepcopy(ci.RESOURCES), **ci.REGISTERED_PATHS,
        "upstream_sha256": ci.UPSTREAM_SHA256, "input_compressed_sha256": ci.INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": ci.INPUT_STREAM_SHA256}


def test_committed_specification_exact_scope_pins_and_budget():
    ci.validate_spec(json.loads(ci.SPECIFICATION_PATH.read_bytes()))
    assert ci.EXPECTED_REFERENCE_SESSIONS == 257
    assert ci.EXPECTED_SELECTED_COMPLETE == {"D+1": {BREAKOUT: 787, RECOVERY: 377}, "D+2": {BREAKOUT: 785, RECOVERY: 376}}
    assert ci.EXPECTED_INPUT_COUNTS == {"unique_target_days": 5158, "queried_rows": 1002211}
    for key in ("parameters", "resources", "upstream_sha256", "input_stream_sha256", "output_directory"):
        wrong = specification()
        wrong[key] = "drift"
        with pytest.raises(ci.PathRefusal, match="scope_mismatch"):
            ci.validate_spec(wrong)


class SyntheticBudget:
    def __init__(self):
        self.stages, self.fail = [], False

    def checkpoint(self, stage):
        self.stages.append(stage)
        if self.fail:
            raise RuntimeError("synthetic_shared_budget_exhausted")

    def finish(self):
        pytest.fail("parent alone finishes shared budget")


@pytest.fixture
def runner(tmp_path, monkeypatch):
    upstream, chunks = fixture()
    upstream_path, archive, spec_path = [tmp_path / name for name in ("ch.json", "inputs.jsonl.gz", "spec.json")]
    upstream_path.write_bytes(ci._canonical(upstream))
    stream = b"".join(ci._canonical(chunk) + b"\n" for chunk in chunks.values())
    archive.write_bytes(gzip.compress(stream, mtime=0))
    monkeypatch.setattr(ci, "UPSTREAM_SHA256", ci._sha(upstream_path))
    monkeypatch.setattr(ci, "INPUT_COMPRESSED_SHA256", ci._sha(archive))
    monkeypatch.setattr(ci, "INPUT_STREAM_SHA256", hashlib.sha256(stream).hexdigest())
    monkeypatch.setattr(ci.horizons, "INPUT_STREAM_SHA256", ci.INPUT_STREAM_SHA256)
    monkeypatch.setattr(ci, "EXPECTED_REFERENCE_SESSIONS", 1)
    monkeypatch.setattr(ci, "EXPECTED_SELECTED_COMPLETE", {key: {family: 1 for family in ci.FAMILIES} for key in ("D+1", "D+2")})
    monkeypatch.setattr(ci, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 20, "queried_rows": 7800})
    paths = {"upstream_path": str(upstream_path), "input_archive_path": str(archive),
        "output_directory": str(tmp_path / "output"), "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(ci, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(ci, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(ci._canonical(specification()))
    return {"specification_path": spec_path, "upstream_path": upstream_path, "input_archive_path": archive,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"]), "budget": SyntheticBudget()}


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_durable_start_precedes_both_inputs_and_exclusive_private_report(runner, monkeypatch):
    real_open, reads = Path.open, []
    def observed(path, *args, **kwargs):
        if path in {runner["upstream_path"], runner["input_archive_path"]}:
            assert statuses(runner)[0] == "started"
            reads.append(path)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", observed)
    receipt = ci.run_paths(**runner)
    report_path = runner["output_directory"] / "report.json"
    report = json.loads(report_path.read_bytes())
    assert statuses(runner) == ["started", "completed"] and receipt["status"] == "completed"
    assert receipt["result"]["diagnostic_only"] is True and reads
    assert len(report["source_sha256"]) == 14
    assert report_path.read_bytes() == ci._canonical(report) + b"\n"
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert report_path.parent.stat().st_mode & 0o777 == 0o700
    runner["budget"].checkpoint("independent_verification_shares_same_budget")
    with pytest.raises(ci.PathRefusal, match="existing_output"):
        ci.run_paths(**runner)


@pytest.mark.parametrize("target", ["source", "runtime", "upstream_path", "input_archive_path", "report", "budget", "callback"])
def test_failed_receipt_on_final_source_runtime_input_report_or_budget_drift(runner, monkeypatch, target):
    real_sha, changed = ci._sha, False
    def sha(path):
        drift = (target == "source" and path == Path(ci.__file__)) or (target == "runtime" and path == Path(ci.sys.executable).resolve())
        return "f" * 64 if changed and drift else real_sha(path)
    monkeypatch.setattr(ci, "_sha", sha)
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
    with pytest.raises((ci.PathRefusal, RuntimeError)):
        ci.run_paths(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("which", ["upstream_path", "input_archive_path"])
def test_bad_pin_refuses_before_build(runner, monkeypatch, which):
    runner[which].write_bytes(b"{}")
    monkeypatch.setattr(ci, "build_paths", lambda *args, **kwargs: pytest.fail("bad evidence must not build"))
    with pytest.raises(ci.PathRefusal, match="hash_mismatch"):
        ci.run_paths(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("population", ["reference", "selected", "raw"])
def test_registered_population_cannot_drift(runner, monkeypatch, population):
    if population == "reference":
        monkeypatch.setattr(ci, "EXPECTED_REFERENCE_SESSIONS", 257)
    elif population == "selected":
        monkeypatch.setattr(ci, "EXPECTED_SELECTED_COMPLETE", {key: {family: 2 for family in ci.FAMILIES} for key in ("D+1", "D+2")})
    else:
        monkeypatch.setattr(ci, "EXPECTED_INPUT_COUNTS", {"unique_target_days": 21, "queried_rows": 7800})
    with pytest.raises(ci.PathRefusal, match="population_changed"):
        ci.run_paths(**runner)
    assert statuses(runner) == ["started", "failed"]


@pytest.mark.parametrize("mutation", ["unregistered", "symlink", "hardlink"])
def test_paths_block_redirects_and_aliases_before_saved_reads(runner, mutation):
    if mutation == "unregistered":
        runner["upstream_path"] = runner["upstream_path"].with_name("other.json")
    else:
        alias = runner["upstream_path"].with_name("alias.json")
        if mutation == "symlink":
            alias.symlink_to(runner["input_archive_path"])
        else:
            alias.hardlink_to(runner["input_archive_path"])
        ci.REGISTERED_PATHS["upstream_path"] = str(alias)
        runner["upstream_path"] = alias
        runner["specification_path"].write_bytes(ci._canonical(specification()))
    with pytest.raises(ci.PathRefusal, match="unregistered|symlinked|aliased"):
        ci.run_paths(**runner)
    assert not runner["runs_path"].exists()
