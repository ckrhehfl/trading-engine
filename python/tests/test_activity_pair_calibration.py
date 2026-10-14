"""Synthetic saved-output calibration; no actual data, DB, network or GCP."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext
import json
from pathlib import Path
import socket
import sqlite3
import stat

import pytest

from research import activity_pair_calibration as module
from research import activity_preflight as saved
from research.activity_book import ActivityBook
from research.activity_replay import SyntheticReplay
from research.activity_timing_report import paired_timing_report


MODULE_PATH = Path(module.__file__)


def fixture(*, amplitudes=("0.000001", "0.000002"), zero=False):
    days, current = [], date.fromisoformat(saved.WINDOW["end"])
    while len(days) < 504:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    days.reverse()
    replays = []
    for text in amplitudes:
        amplitude = Decimal(text)
        with localcontext() as context:
            context.prec = 28
            values = [Decimal(1) + amplitude * (index % 7 - 3) for index in range(len(days))]
        if zero:
            values[20:] = [Decimal(0)] * (len(days) - 20)
        books = tuple(ActivityBook(nav, (), day) for nav, day in zip(values, days))
        replays.append(SyntheticReplay(books, (), (("entries", 0), ("unfilled_entries", 0)), 0))
    report = paired_timing_report(*replays, (), (), initial_nav=Decimal(1))
    upstream = {"synthetic_source": {"path": "/synthetic/evidence/source.json", "sha256": "a" * 64}}
    spec = dict(schema="activity-paired-preflight-v1", study_id="activity-paired-preflight-v1",
        mode="discovery_descriptive_timing_sensitivity", promotion_allowed=False,
        reference_specification=saved.REFERENCE, window=deepcopy(saved.WINDOW),
        parameters=deepcopy(module.PARAMETERS), inputs=upstream)
    result = dict(schema="activity-paired-preflight-v1", status="descriptive_timing_pair_executed",
        books_computed=True, returns_computed=True, logged_paired_execution_completed=True,
        independent_output_verification="pending", D1_completed_studies=1, D2_completed_studies=1,
        input_sha256={key: row["sha256"] for key, row in upstream.items()}, input_manifest=[],
        database_access=False, api_access=False, original_source_bodies_read=False,
        new_source_requests=0, automatic_retries=0, **dict.fromkeys(module.FALSE_FLAGS, False),
        **dict.fromkeys(module.LINK_FILES, "b" * 64), **dict.fromkeys(module.SNAPSHOTS, "c" * 64))
    verification = dict(schema="bq-raw-paired-independent-verification-v1", verified=True,
        scope="new_seven_outputs_links_and_five_saved_book_headline_metrics_only",
        published_D1_completed_studies=1, published_D2_completed_studies=1,
        D1_completed_studies=0, D2_completed_studies=0, automatic_retries=0,
        output_files_read=7, publication_files_read=2, original_input_files_read=0,
        input_sha256=deepcopy(result["input_sha256"]), **dict.fromkeys(module.SNAPSHOTS, "c" * 64),
        **dict.fromkeys((*module.FALSE_FLAGS, *module.VERIFY_FALSE), False),
        **dict.fromkeys(module.VERIFY_TRUE, True),
        metrics={"arms": {key: {name: row[name] for name in module.HEADLINES} for key, row in report["arms"].items()},
            **{name: {key: value for key, value in report[name].items() if key != "formations"}
               for name in ("signal_overlap", "target_overlap")}})
    return dict(zip(module.ROLES, (report, result, spec, verification)))


def linked(values):
    """Re-pin deliberate synthetic mutations so semantic failures are exercised."""
    report, result, spec, verification = (values[role] for role in module.ROLES)
    result["report_sha256"] = saved.digest(saved.encoded(report))
    result["specification_sha256"] = saved.digest(saved.encoded(spec))
    verification["file_sha256"] = {name: result[key] for key, name in module.LINK_FILES.items()}
    verification["file_sha256"]["result.json"] = saved.digest(saved.encoded(result))
    verification["specification_sha256"] = result["specification_sha256"]
    raw = {role: saved.encoded(values[role]) for role in module.ROLES}
    return raw, {role: saved.digest(body) for role, body in raw.items()}


def calibrate(values):
    raw, pins = linked(deepcopy(values))
    return module.calibrate_pair(raw, parameters=deepcopy(module.PARAMETERS),
        window=deepcopy(saved.WINDOW), expected_pins=pins)


def statuses(path):
    return [json.loads(line)["status"] for line in path.read_text().splitlines()]


def case(tmp_path, monkeypatch):
    root, evidence, private = (tmp_path / name for name in ("repo", "evidence", "private"))
    root.mkdir()
    evidence.mkdir(mode=0o700)
    private.mkdir(mode=0o700)
    raw, pins = linked(fixture())
    declared = {role: dict(path=str(evidence / (role + ".json")), sha256=pins[role]) for role in module.ROLES}
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode=module.MODE,
        promotion_allowed=False, reference_specification=saved.REFERENCE,
        parameters=deepcopy(module.PARAMETERS), window=deepcopy(saved.WINDOW), inputs=declared)
    store = {Path(declared[role]["path"]): body for role, body in raw.items()}
    sources = {"files": [{"path": module.SOURCE_PATH, "sha256": "d" * 64}], "code_version": "e" * 40}
    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_spec
        module.validate_spec(spec, {"parameters": module.PARAMETERS})
        return sources, saved.encoded(spec), spec
    monkeypatch.chdir(root)
    monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    monkeypatch.setattr(saved, "freeze_sources", freeze)
    for owner, name in ((sqlite3, "connect"), (socket, "create_connection")):
        monkeypatch.setattr(owner, name, lambda *args, **kwargs: pytest.fail("unexpected actual IO"))
    return root, private / "run", tmp_path / "runs.jsonl", spec, store, sources


def test_calibration_reuses_existing_session_engine_without_mean_ranking_or_replay(monkeypatch):
    values = fixture()
    before = deepcopy(values)
    engine, calls = module.calibration, []
    def capture(book, params):
        calls.append(deepcopy((book, params)))
        return engine(book, params)
    monkeypatch.setattr(module, "calibration", capture)
    result = calibrate(values)
    assert values == before and len(calls) == 2
    assert result["comparison_power_prerequisite_met"] is result["power_design_passed"] is True
    for arm, (book, params) in zip(("D1", "D2"), calls):
        assert params == module.PARAMETERS and len(book["returns"]) == 504
        assert result["arms"][arm] == engine(book, params)
        assert set(result["arms"][arm]["block_se"]) == {"63", "126", "252"}
        assert result["arms"][arm]["cost_floor_daily_bps"] == 43.54 / 126
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert all(result[key] is False for key in ("books_replayed", "full_economic_accounting_verified",
        "source_truth_certified", "historical_vintage_certified", "promotion_allowed"))
    serialized = saved.encoded(result)
    assert all(term not in serialized for term in (b'"total_return"', b'"maximum_drawdown"',
        b'"mean"', b'"p_value"', b'"ranking"', b'"winner"', b'"path"'))


@pytest.mark.parametrize("amplitudes", [("0.000001", "0.2"), ("0.2", "0.000001"), ("0.2", "0.2")])
def test_either_underpowered_arm_stops_comparison_prerequisite_without_edge_verdict(amplitudes):
    result = calibrate(fixture(amplitudes=amplitudes))
    assert result["comparison_power_prerequisite_met"] is result["power_design_passed"] is False
    assert result["stop_reason"] == "event_arm_cost_floor_not_resolvable"
    assert sum(row["cost_floor_resolvable"] for row in result["arms"].values()) == sum(Decimal(a) < Decimal("0.1") for a in amplitudes)


@pytest.mark.parametrize("role", module.ROLES)
def test_every_pin_is_checked_before_content(role):
    raw, pins = linked(fixture())
    raw[role] += b" "
    with pytest.raises(ValueError, match="pin mismatch"):
        module.calibrate_pair(raw, parameters=module.PARAMETERS, window=saved.WINDOW, expected_pins=pins)


@pytest.mark.parametrize("mutation", ["result_report_link", "verification_result_link", "verification_spec_link", "upstream_link", "snapshot_link",
    "unverified", "published_count_bool", "published_count_zero", "full_accounting", "source_truth", "promotion", "replayed"])
def test_saved_verification_and_completion_boundaries_are_not_upgraded(mutation):
    values = fixture()
    raw, pins = linked(values)
    result, verification = values["paired_result"], values["paired_verification"]
    if mutation == "result_report_link": result["report_sha256"] = "f" * 64
    elif mutation == "verification_result_link": verification["file_sha256"]["result.json"] = "f" * 64
    elif mutation == "verification_spec_link": verification["file_sha256"]["specification.json"] = "f" * 64
    elif mutation == "upstream_link": verification["input_sha256"]["synthetic_source"] = "f" * 64
    elif mutation == "snapshot_link": verification["quote_snapshot_sha256"] = "f" * 64
    elif mutation == "unverified": verification["verified"] = False
    elif mutation == "published_count_bool": verification["published_D1_completed_studies"] = True
    elif mutation == "published_count_zero": verification["published_D2_completed_studies"] = 0
    elif mutation == "full_accounting": verification["full_economic_accounting_verified"] = True
    elif mutation == "source_truth": verification["source_truth_certified"] = True
    elif mutation == "promotion": result["promotion_allowed"] = True
    else: verification["books_replayed"] = True
    for role in module.ROLES:
        raw[role] = saved.encoded(values[role])
        pins[role] = saved.digest(raw[role])
    with pytest.raises(ValueError):
        module.calibrate_pair(raw, parameters=module.PARAMETERS, window=saved.WINDOW, expected_pins=pins)


@pytest.mark.parametrize("mutation", ["return", "ratio", "nav_numeric", "nav_noncanonical", "nav_nonfinite", "negative_nav",
    "total", "mdd", "sd", "verified_sd", "count", "duplicate", "unordered", "late", "noncanonical_date", "different_dates", "short_tail", "zero_recovery", "unknown_field"])
def test_path_metrics_dates_and_decimal_text_must_reconcile(mutation):
    values = fixture(zero=mutation == "zero_recovery")
    arm = values["paired_report"]["arms"]["D1"]
    point = arm["path"][3]
    if mutation == "return": point["session_return"] = "0"
    elif mutation == "ratio": point["nav_ratio"] = "2"
    elif mutation == "nav_numeric": point["nav"] = 1
    elif mutation == "nav_noncanonical": point["nav"] = "+1"
    elif mutation == "nav_nonfinite": point["nav"] = "NaN"
    elif mutation == "negative_nav": point["nav"] = "-1"
    elif mutation == "total": arm["total_return"] = "0.1"
    elif mutation == "mdd": arm["maximum_drawdown"] = "0.5"
    elif mutation == "sd": arm["session_return_sd"] *= 2
    elif mutation == "verified_sd": values["paired_verification"]["metrics"]["arms"]["D1"]["session_return_sd"] *= 2
    elif mutation == "count": arm["session_return_count"] -= 1
    elif mutation == "duplicate": point["date"] = arm["path"][2]["date"]
    elif mutation == "unordered": point["date"] = arm["path"][0]["date"]
    elif mutation == "late": arm["path"][-1]["date"] = "2026-09-21"
    elif mutation == "noncanonical_date": point["date"] = point["date"].replace("-", "")
    elif mutation == "different_dates": point["date"] = (date.fromisoformat(point["date"]) + timedelta(days=1)).isoformat()
    elif mutation == "short_tail": arm["path"].pop()
    elif mutation == "zero_recovery": arm["path"][21]["nav"] = "1"
    else: point["adjusted_return"] = "0"
    with pytest.raises(ValueError): calibrate(values)


def test_absorbing_zero_is_preserved_and_all_cash_dispersion_is_refused():
    result = calibrate(fixture(zero=True))
    assert result["arms"]["D1"]["sessions"] == 504 and result["comparison_power_prerequisite_met"] is False
    with pytest.raises(ValueError, match="nonzero event-arm dispersion"):
        calibrate(fixture(amplitudes=("0", "0")))


@pytest.mark.parametrize("name,value", [("slippage_bps_per_side", 5), ("threshold", 3), ("seed", True),
    ("bootstrap_blocks", [63, 126]), ("bootstrap_repetitions", 200), ("alpha", 0.1), ("power", 0.9), ("sizing_round_trip_bps", 42.0)])
def test_parameter_json_representation_and_registered_power_are_fixed(tmp_path, monkeypatch, name, value):
    root, output, runs, spec, store, _ = case(tmp_path, monkeypatch)
    spec["parameters"][name] = value
    with pytest.raises(ValueError): module.validate_spec(spec, {"parameters": module.PARAMETERS})
    assert not output.exists() and not runs.exists()
    values = fixture()
    values["paired_specification"]["parameters"][name] = value
    with pytest.raises(ValueError): calibrate(values)


def test_complete_logged_runner_reads_only_four_pins_after_start_and_rechecks_source(tmp_path, monkeypatch):
    root, output, runs, spec, store, _ = case(tmp_path, monkeypatch)
    freeze, reads = saved.freeze_sources, []
    def checked_freeze(*args, **kwargs):
        if reads:
            assert statuses(runs) == ["started"] and (output / "calibration.json").exists()
            assert not (output / "result.json").exists()
        return freeze(*args, **kwargs)
    monkeypatch.setattr(saved, "freeze_sources", checked_freeze)
    def reader(path):
        assert statuses(runs) == ["started"] and (output / "source-manifest.json").exists()
        reads.append(path)
        return store[path]
    record = module.run_calibration(root, root / "spec.json", output, runs, reader=reader)
    assert statuses(runs) == ["started", "completed"] and len(reads) == len(set(reads)) == 4
    result = saved.strict_json((output / "result.json").read_bytes())
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert result["comparison_power_prerequisite_met"] and record["result"]["comparison_power_prerequisite_met"]
    assert result["input_manifest"] == saved.strict_json((output / "input-manifest.json").read_bytes())
    for field, name in (("calibration_sha256", "calibration.json"), ("source_manifest_sha256", "source-manifest.json"),
        ("specification_sha256", "specification.json"), ("input_manifest_sha256", "input-manifest.json"),
        ("resource_manifest_sha256", "resource-manifest.json")):
        assert result[field] == record["result"][field] == saved.digest((output / name).read_bytes())
    start = saved.strict_json(runs.read_bytes().splitlines()[0])
    assert start["promotion_trial_increment"] == 0 and start["parameters"]["books_replayed"] is False
    assert start["parameters"]["D1_additional_completed_studies"] == start["parameters"]["D2_additional_completed_studies"] == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


@pytest.mark.parametrize("failure", ["read", "pin", "source_drift", "source_recheck_error"])
def test_failed_runner_retains_manifest_partial_output_and_zero_additional_counts(tmp_path, monkeypatch, failure):
    root, output, runs, spec, store, sources = case(tmp_path, monkeypatch)
    reader = store.__getitem__
    if failure == "read":
        def reader(path): raise OSError("private body must never appear in receipt")
    elif failure == "pin": store[next(iter(store))] += b" "
    else:
        calls = []
        def freeze(*args, **kwargs):
            calls.append(1)
            if len(calls) > 1 and failure == "source_recheck_error":
                raise OSError("private source failure must never appear in receipt")
            return (sources if len(calls) == 1 else {**sources, "code_version": "f" * 40}), saved.encoded(spec), spec
        monkeypatch.setattr(saved, "freeze_sources", freeze)
    with pytest.raises((ValueError, OSError)):
        module.run_calibration(root, root / "spec.json", output, runs, reader=reader)
    assert statuses(runs) == ["started", "failed"] and not (output / "result.json").exists()
    raw = (output / "failure.json").read_bytes()
    failed = saved.strict_json(raw)
    assert b"private body" not in raw and b"private source failure" not in raw
    assert failed["D1_completed_studies"] == failed["D2_completed_studies"] == failed["automatic_retries"] == 0
    assert failed["comparison_power_prerequisite_met"] is False and failed["partial_package_preserved"]
    assert failed["actual_inputs_attempted"] == saved.strict_json((output / "input-manifest.json").read_bytes())
    assert (output / "resource-manifest.json").exists()
    if failure.startswith("source_"): assert (output / "calibration.json").exists()


def test_source_location_and_write_aliases_fail_before_input_reads(tmp_path, monkeypatch):
    root, output, runs, spec, store, _ = case(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "__file__", str(MODULE_PATH))
    with pytest.raises(ValueError, match="committed source location"):
        module.run_calibration(root, root / "spec.json", output, runs, reader=lambda path: pytest.fail("input read"))
    assert not output.exists() and not runs.exists()
    monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    alias = Path(spec["inputs"]["paired_report"]["path"])
    with pytest.raises(ValueError, match="immutable input"):
        module.run_calibration(root, root / "spec.json", output, alias, reader=lambda path: pytest.fail("input read"))
    assert not alias.exists()
    output.mkdir(mode=0o700)
    sentinel = output / "sentinel"
    sentinel.write_bytes(b"preserved")
    with pytest.raises(FileExistsError):
        module.run_calibration(root, root / "spec.json", output, runs, reader=lambda path: pytest.fail("input read"))
    assert sentinel.read_bytes() == b"preserved" and statuses(runs) == ["started", "failed"]
