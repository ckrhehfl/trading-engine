"""Synthetic-only CD checks: causal controls, strict pairs and durable IO."""

from copy import deepcopy
from datetime import timedelta
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from research import intraday_pattern_calibration as cc
from research import intraday_pattern_comparison as cd


DAY = "2025-09-04"
BREAKOUT, RECOVERY = cd.FAMILIES


def minute_rows(day=DAY, symbol="KRX:000001", *, breakout=False, recovery=False, terminal="101"):
    opening = cc.session_open(day)
    raw = [{"symbol": symbol, "interval": "1m",
        "open_time_ms": int((opening + index * cc.MINUTE).timestamp()) * 1000,
        "open": "100", "high": "100", "low": "100", "close": "100",
        "fetched_at": "2026-10-20T00:00:00Z"} for index in range(390)]
    if breakout:
        raw[34].update(high="102", close="102")
        raw[159].update(high=max("100", terminal), low=min("100", terminal), close=terminal)
    if recovery:
        raw[34].update(low="97", close="97")
        raw[39].update(low="99", close="99")
    return raw


def fixture(*, days=(DAY,), overrides=None, variant_symbols=None, only_event=False):
    overrides = overrides or {}
    symbols = [f"KRX:{index:06d}" for index in range(1, 21)]
    variant_symbols = variant_symbols or {key: symbols for key in ("D+1", "D+2")}
    union = sorted(set().union(*map(set, variant_symbols.values())))
    chunks, findings, vintages = {}, {}, {}
    for day in days:
        for symbol in union:
            raw = deepcopy(overrides.get((day, symbol), [] if only_event else minute_rows(day, symbol)))
            chunk = {"symbol": symbol, "session": day, "observation_date": day,
                "available_at": None, "retrieved_at": "2026-10-20T01:00:00+00:00",
                "source": "collector klines", "is_final": None, "evidence_level": "assumed", "rows": raw}
            chunks[day, symbol] = chunk
            vintages[day, symbol] = hashlib.sha256(cd._canonical(chunk) + b"\n").hexdigest()
            findings[day, symbol] = cc.calibrate_session(cc.aggregate_session(raw, cc.session_open(day)))
    report = {"study_id": cc.SPECIFICATION_ID, "parameters": deepcopy(cc.PARAMETERS),
        **cc.FLAGS, "variants": {}}
    for key, names in variant_symbols.items():
        report["variants"][key] = {"days": [], "families": {}}
        for day in days:
            membership = {"session": day, "observation_date": day[:8] + "01",
                "available_at": day + "T08:00:00+09:00", "selected_at": day + "T08:30:00+09:00"}
            for symbol in names:
                report["variants"][key]["days"].append({"symbol": symbol, "session": day,
                    "data_vintage": vintages[day, symbol], "membership_source": membership,
                    "membership_observation_date": membership["observation_date"],
                    "membership_available_at": membership["available_at"],
                    "families": deepcopy(findings[day, symbol])})
        for family in cd.FAMILIES:
            records = [{"session": day, "label": findings[day, symbol][family]["label"]["price_change"]}
                for day in days for symbol in names if findings[day, symbol][family]["status"] == "signal"]
            report["variants"][key]["families"][family] = {"dispersion": cc.dispersion(records, list(days))}
    report["input_evidence"] = {"unique_target_days": len(chunks),
        "queried_rows": sum(len(chunk["rows"]) for chunk in chunks.values()),
        "compressed_sha256": cd.INPUT_COMPRESSED_SHA256, "stream_sha256": cd.INPUT_STREAM_SHA256}
    # Exercise the wire representation, including exact ratio objects and ISO datetimes.
    return json.loads(cd._canonical(report)), chunks


def finding(report, symbol="KRX:000001", family=BREAKOUT, variant="D+1", day=DAY):
    return next(row["families"][family] for row in report["variants"][variant]["days"]
        if row["symbol"] == symbol and row["session"] == day)


def test_complete_pair_equal_all_other_members_exact_cost_and_concentration():
    report, chunks = fixture(overrides={(DAY, "KRX:000001"): minute_rows(breakout=True, terminal="103")})
    output = cd.build_comparison(report, chunks)
    item = finding(output)["comparison"]
    assert item["pair_status"] == "complete"
    assert len(item["selected_controls"]) == 19 and item["excluded_controls"] == []
    assert item["event_label"]["price_change"] == Fraction(3, 100)
    assert item["control_mean_price_change"] == 0 and item["excess_price_change"] == Fraction(3, 100)
    assert item["event_label"]["entry_label_start"] == cc.session_open(DAY) + 40 * cc.MINUTE
    summary = output["variants"]["D+1"]["families"][BREAKOUT]
    assert summary["counts"] == {"signal": 1, "known_no_signal": 19, "unknown": 0}
    assert summary["pair_counts"] == {"complete": 1, "unknown": 0}
    assert summary["paired_complete_conditional"]["event"]["minus_33bp_assumption"]["mean"] == pytest.approx(.0267)
    assert summary["paired_complete_conditional"]["control"]["minus_33bp_assumption"]["mean"] == pytest.approx(-.0033)
    assert summary["by_month"]["2025-09"]["pair_counts"]["complete"] == 1
    assert summary["by_symbol"]["KRX:000001"]["pair_counts"]["complete"] == 1
    assert summary["by_symbol"]["KRX:000002"]["paired_complete_conditional"]["event"]["gross"]["mean"] is None
    assert output["exploratory_only"] is True and output["actual_portfolio_pnl"] is False
    assert all(output[key] is False for key in cc.FLAGS)
    sizing = summary["upstream_calibration_sizing"]
    assert sizing["known_labels"] == 1
    assert sizing["underpower_exception_applied"] is False
    recovery_sizing = output["variants"]["D+1"]["families"][RECOVERY]["upstream_calibration_sizing"]
    assert recovery_sizing["underpower_exception_applied"] is True
    assert "mde_bps" in recovery_sizing and recovery_sizing["cost_floor_bps_assumed"] == "33"


@pytest.mark.parametrize("gap_index,selected", [(6, False), (34, False), (35, True), (60, True), (200, True)])
def test_control_selection_only_uses_contiguous_prefix_at_event_decision(gap_index, selected):
    raw = minute_rows(symbol="KRX:000002")
    raw.pop(gap_index)
    report, chunks = fixture(overrides={(DAY, "KRX:000001"): minute_rows(breakout=True), (DAY, "KRX:000002"): raw})
    item = finding(cd.build_comparison(report, chunks))["comparison"]
    assert ("KRX:000002" in [row["symbol"] for row in item["selected_controls"]]) is selected
    if gap_index == 60:
        assert item["pair_status"] == "unknown" and item["control_mean_price_change"] is None
        assert item["unknown_control_symbols"] == ["KRX:000002"]
        assert item["pair_unknown_reasons"] == ["selected_control_label_unknown"]
    elif gap_index == 35 or gap_index == 200 or not selected:
        assert item["pair_status"] == "complete"


def test_missing_one_selected_control_never_changes_pool_or_becomes_zero():
    complete, chunks = fixture(overrides={(DAY, "KRX:000001"): minute_rows(breakout=True)})
    before = finding(cd.build_comparison(complete, chunks))["comparison"]
    broken = minute_rows(symbol="KRX:000002")
    broken.pop(80)
    report, chunks = fixture(overrides={(DAY, "KRX:000001"): minute_rows(breakout=True), (DAY, "KRX:000002"): broken})
    output = cd.build_comparison(report, iter(chunks.values()))
    after = finding(output)["comparison"]
    assert [item["symbol"] for item in before["selected_controls"]] == [item["symbol"] for item in after["selected_controls"]]
    assert after["excess_price_change"] is None
    assert after["selected_controls"][0]["label"]["price_change"] is None
    assert after["selected_controls"][0]["label"]["missing_minute_buckets"] == 1
    summary = output["variants"]["D+1"]["families"][BREAKOUT]
    assert summary["paired_complete_conditional"]["events"] == 0
    assert summary["control_counts"]["selected_label_unknown"] == 1


def test_control_that_also_signals_is_included_and_label_uses_event_boundary():
    other = minute_rows(symbol="KRX:000002", breakout=True, terminal="104")
    other[34].update(high="100", close="100")
    other[64].update(high="102", close="102")
    report, chunks = fixture(overrides={(DAY, "KRX:000001"): minute_rows(breakout=True), (DAY, "KRX:000002"): other})
    output = cd.build_comparison(report, chunks)
    item = finding(output)["comparison"]
    control = next(row for row in item["selected_controls"] if row["symbol"] == "KRX:000002")
    assert finding(output, "KRX:000002")["status"] == "signal"
    assert control["label"]["entry_label_start"] == item["event_label"]["entry_label_start"]
    assert control["label"]["price_change"] == Fraction(4, 100)
    assert item["control_mean_price_change"] == Fraction(4, 1900)


def test_event_future_gap_preserves_signal_but_not_paired_measurement():
    raw = minute_rows(breakout=True)
    raw.pop(60)
    report, chunks = fixture(overrides={(DAY, "KRX:000001"): raw})
    item = finding(cd.build_comparison(report, chunks))
    assert item["status"] == "signal"
    assert item["comparison"]["pair_unknown_reasons"] == ["event_label_unknown"]
    assert item["comparison"]["event_label"]["status"] == "unknown"


def test_no_controls_and_whole_target_grid_remain_unknown():
    report, chunks = fixture(overrides={(DAY, "KRX:000001"): minute_rows(breakout=True)}, only_event=True)
    output = cd.build_comparison(report, chunks)
    assert len(output["variants"]["D+1"]["days"]) == 20
    item = finding(output)["comparison"]
    assert item["pair_unknown_reasons"] == ["no_selected_controls"]
    assert len(item["excluded_controls"]) == 19
    summary = output["variants"]["D+1"]["families"][BREAKOUT]
    assert summary["counts"] == {"signal": 1, "known_no_signal": 0, "unknown": 19}
    assert summary["control_counts"]["events_without_selected_controls"] == 1


def test_membership_variants_use_their_own_twenty_names_without_holding_changes():
    symbols = [f"KRX:{index:06d}" for index in range(1, 22)]
    report, chunks = fixture(variant_symbols={"D+1": symbols[:20], "D+2": symbols[:19] + symbols[20:]},
        overrides={(DAY, "KRX:000001"): minute_rows(breakout=True)})
    output = cd.build_comparison(report, chunks)
    first, second = (finding(output, variant=key)["comparison"] for key in ("D+1", "D+2"))
    assert "KRX:000020" in [row["symbol"] for row in first["selected_controls"]]
    assert "KRX:000021" in [row["symbol"] for row in second["selected_controls"]]
    assert first["event_label"] == second["event_label"]
    assert output["input_evidence"]["unique_target_days"] == 21


def test_both_frozen_families_recovery_uses_identical_120minute_label():
    report, chunks = fixture(overrides={(DAY, "KRX:000001"): minute_rows(recovery=True)})
    item = finding(cd.build_comparison(report, chunks), family=RECOVERY)
    assert item["status"] == "signal" and item["comparison"]["pair_status"] == "complete"
    label = item["comparison"]["event_label"]
    assert label["entry_label_start"] == cc.session_open(DAY) + 45 * cc.MINUTE
    assert label["end_label"] - label["entry_label_start"] == timedelta(minutes=120)


def test_exact_distribution_statistics_and_cost_positive_fraction():
    result = cd._distribution([Fraction(1, 1000), Fraction(1, 100), Fraction(-1, 100)])
    assert result["gross"]["mean"] == pytest.approx(1 / 3000)
    assert result["gross"]["median"] == Fraction(1, 1000)
    assert result["gross"]["positive_fraction"] == Fraction(2, 3)
    assert result["minus_33bp_assumption"]["positive_fraction"] == Fraction(1, 3)


def test_uncertainty_includes_zero_event_reference_dates_and_is_repeatable():
    days = [f"2025-09-{index:02d}" for index in range(4, 14)]
    pairs = [{"session": days[index], "comparison": {"excess_price_change": Fraction(value, 100)}}
        for index, value in ((0, 1), (0, 3), (1, -1), (1, 1))]
    output = cd._uncertainty(pairs, days)
    assert output["reference_sessions"] == 10 and output["known_pair_sessions"] == 2
    assert output["session_cluster_se"] == pytest.approx(math_cluster := (10 / 9 * (.02 ** 2 + (-.02) ** 2)) ** .5 / 4)
    assert math_cluster > 0
    assert output["design_se"] == max(output[key] for key in ("naive_se", "session_cluster_se", "bootstrap_se"))
    assert output["corrected_to_naive_se_ratio"] == output["design_se"] / output["naive_se"]
    assert output["bootstrap_zero_pair_draws"] > 0
    assert output == cd._uncertainty(pairs, days)


def test_one_known_date_never_supplies_cluster_or_bootstrap_replication():
    pairs = [{"session": DAY, "comparison": {"excess_price_change": Fraction(value, 100)}} for value in (1, -1)]
    output = cd._uncertainty(pairs, [DAY, "2025-09-05"])
    assert output["naive_se"] > 0
    assert all(output[key] is None for key in ("session_cluster_se", "bootstrap_se", "design_se", "corrected_to_naive_se_ratio"))


def test_circular_five_session_bootstrap_preserves_whole_dates():
    days = [f"2025-09-{index:02d}" for index in range(4, 9)]
    pairs = [{"session": day, "comparison": {"excess_price_change": Fraction(index, 100)}} for index, day in enumerate(days)]
    assert cd._uncertainty(pairs, days)["bootstrap_se"] == pytest.approx(0, abs=1e-16)


@pytest.mark.parametrize("change", ["remove_target", "duplicate_target", "remove_day", "certify", "late_membership", "wrong_finding", "wrong_vintage", "wrong_count"])
def test_rejects_upstream_population_provenance_and_replay_drift(change):
    report, chunks = fixture(days=(DAY, "2025-09-05"))
    rows = report["variants"]["D+1"]["days"]
    if change == "remove_target":
        rows.pop()
    elif change == "duplicate_target":
        rows[1] = deepcopy(rows[0])
    elif change == "remove_day":
        del rows[20:]
    elif change == "certify":
        report["performance_certified"] = True
    elif change == "late_membership":
        rows[0]["membership_source"]["selected_at"] = DAY + "T09:30:00+09:00"
    elif change == "wrong_finding":
        rows[0]["families"][BREAKOUT]["status"] = "unknown"
    elif change == "wrong_vintage":
        rows[0]["data_vintage"] = "a" * 64
    else:
        report["input_evidence"]["queried_rows"] += 1
    with pytest.raises(cd.ComparisonRefusal):
        cd.build_comparison(report, chunks)


@pytest.mark.parametrize("change", ["missing", "extra", "out_of_order", "wrong_symbol", "extra_rows"])
def test_rejects_archive_population_order_identity_or_budget_drift(change):
    report, chunks = fixture()
    values = list(chunks.values())
    if change == "missing":
        values.pop()
    elif change == "extra":
        values.append(values[-1])
    elif change == "out_of_order":
        values[0], values[1] = values[1], values[0]
    elif change == "wrong_symbol":
        values[0]["rows"][0]["symbol"] = "KRX:999999"
    else:
        values[0]["rows"] += values[0]["rows"]
    with pytest.raises(cd.ComparisonRefusal):
        cd.build_comparison(report, iter(values))


def specification():
    return {"id": cd.SPECIFICATION_ID, "mode": cd.MODE, "window": deepcopy(cd.WINDOW),
        "parameters": deepcopy(cd.PARAMETERS), "resources": deepcopy(cd.RESOURCES), **cd.REGISTERED_PATHS,
        "upstream_sha256": cd.UPSTREAM_SHA256, "input_compressed_sha256": cd.INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": cd.INPUT_STREAM_SHA256}


def test_committed_registration_matches_exact_implementation_contract():
    cd.validate_spec(json.loads(cd.SPECIFICATION_PATH.read_bytes()))


def test_aggregate_statistical_boundary_avoids_unbounded_fraction_denominators():
    # Pairwise coprime prices would make a pooled exact Fraction too large to
    # serialize under Python's default integer-string digit limit.
    primes = []
    candidate = 100_003
    while len(primes) < 1100:
        if all(candidate % divisor for divisor in range(2, int(candidate ** .5) + 1)):
            primes.append(candidate)
        candidate += 2
    values = [Fraction(1, prime) for prime in primes]
    result = cd._distribution(values)
    assert isinstance(result["gross"]["mean"], float)
    assert len(cd._canonical(result)) < 1000
    assert isinstance(result["gross"]["median"], Fraction)


@pytest.mark.parametrize("change", [lambda spec: spec.update(extra=True),
    lambda spec: spec["parameters"].update(bootstrap_seed=1),
    lambda spec: spec.update(upstream_sha256="a" * 64),
    lambda spec: spec["resources"].update(automatic_retries=1),
    lambda spec: spec["window"].update(end="2026-10-20")])
def test_specification_is_fully_frozen(change):
    spec = specification()
    cd.validate_spec(spec)
    change(spec)
    with pytest.raises(cd.ComparisonRefusal, match="scope_mismatch"):
        cd.validate_spec(spec)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    report, chunks = fixture(overrides={(DAY, "KRX:000001"): minute_rows(breakout=True)})
    archive_path, upstream_path, spec_path = (tmp_path / name for name in ("inputs.jsonl.gz", "upstream.json", "spec.json"))
    stream = b"".join(cd._canonical(chunk) + b"\n" for chunk in chunks.values())
    archive_path.write_bytes(gzip.compress(stream, mtime=0))
    monkeypatch.setattr(cd, "INPUT_COMPRESSED_SHA256", hashlib.sha256(archive_path.read_bytes()).hexdigest())
    monkeypatch.setattr(cd, "INPUT_STREAM_SHA256", hashlib.sha256(stream).hexdigest())
    report["input_evidence"].update(compressed_sha256=cd.INPUT_COMPRESSED_SHA256, stream_sha256=cd.INPUT_STREAM_SHA256)
    upstream_path.write_bytes(cd._canonical(report))
    monkeypatch.setattr(cd, "UPSTREAM_SHA256", hashlib.sha256(upstream_path.read_bytes()).hexdigest())
    monkeypatch.setattr(cd, "EXPECTED_REFERENCE_SESSIONS", 1)
    paths = {"upstream_path": str(upstream_path), "input_archive_path": str(archive_path),
        "output_directory": str(tmp_path / "output"), "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    monkeypatch.setattr(cd, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(cd, "SPECIFICATION_PATH", spec_path)
    spec_path.write_bytes(cd._canonical(specification()))
    args = {"specification_path": spec_path, "upstream_path": upstream_path, "input_archive_path": archive_path,
        "output_directory": Path(paths["output_directory"]), "runs_path": Path(paths["canonical_runs_path"])}
    return args


def statuses(args):
    return [json.loads(line)["status"] for line in args["runs_path"].read_text().splitlines()]


def test_runner_started_is_durable_before_any_saved_evidence_read_and_outputs_are_exclusive(runner, monkeypatch):
    original_open = Path.open
    protected = {runner["upstream_path"], runner["input_archive_path"]}
    def checked_open(path, *args, **kwargs):
        if path in protected:
            assert statuses(runner)[0] == "started"
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", checked_open)
    result = cd.run_comparison(**runner)
    assert result["status"] == "completed" and statuses(runner) == ["started", "completed"]
    report_path = runner["output_directory"] / "report.json"
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert runner["output_directory"].stat().st_mode & 0o777 == 0o700
    report = json.loads(report_path.read_bytes())
    assert report["input_evidence"]["queried_rows"] == 7800
    assert result["result"]["output_sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert report["source_sha256"] and report["runtime"]["executable_sha256"]
    with pytest.raises(cd.ComparisonRefusal, match="existing_output"):
        cd.run_comparison(**runner)


@pytest.mark.parametrize("target", ["upstream_path", "input_archive_path", "report", "source", "callback"])
def test_runner_completion_rejects_changed_inputs_sources_or_output(runner, monkeypatch, target):
    real_sha = cd._sha
    changed = False
    def sha(path):
        if changed and target == "source" and path == Path(cd.__file__):
            return "b" * 64
        return real_sha(path)
    monkeypatch.setattr(cd, "_sha", sha)
    def mutate():
        nonlocal changed
        changed = True
        if target == "callback":
            raise RuntimeError("synthetic_callback_failure")
        if target != "source":
            path = runner["output_directory"] / "report.json" if target == "report" else runner[target]
            with path.open("ab") as stream:
                stream.write(b" ")
    with pytest.raises((cd.ComparisonRefusal, RuntimeError)):
        cd.run_comparison(**runner, before_completion=mutate)
    assert statuses(runner) == ["started", "failed"]
    assert (runner["output_directory"] / "report.json").is_file()


def test_runner_input_hash_failure_records_failed_before_replay(runner, monkeypatch):
    runner["upstream_path"].write_bytes(b"{}")
    monkeypatch.setattr(cd, "build_comparison", lambda *args: pytest.fail("must not replay invalid input"))
    with pytest.raises(cd.ComparisonRefusal, match="hash_mismatch"):
        cd.run_comparison(**runner)
    assert statuses(runner) == ["started", "failed"]


def test_archive_stream_hash_and_framing_are_enforced(tmp_path, monkeypatch):
    path = tmp_path / "archive.gz"
    stream = b'{"synthetic":true}\n'
    path.write_bytes(gzip.compress(stream))
    monkeypatch.setattr(cd, "INPUT_STREAM_SHA256", hashlib.sha256(stream).hexdigest())
    assert list(cd._archive_chunks(path)) == [{"synthetic": True}]
    monkeypatch.setattr(cd, "INPUT_STREAM_SHA256", "a" * 64)
    with pytest.raises(cd.ComparisonRefusal, match="stream_hash"):
        list(cd._archive_chunks(path))
    path.write_bytes(gzip.compress(stream.rstrip()))
    with pytest.raises(cd.ComparisonRefusal, match="framing"):
        list(cd._archive_chunks(path))
