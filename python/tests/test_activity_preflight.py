"""Synthetic-only contracts for the logged saved-response coverage preflight."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from data import krx_formation_audit as formation
from data import krx_liquidity_audit as liquidity
from data import krx_openapi_probe as probe
from research import activity_preflight as preflight
from research import experiment_log
from test_krx_formation_audit import rows_for_day
from test_krx_liquidity_audit import synthetic_calendar


def spec_fixture() -> dict:
    return {
        "schema": preflight.SCHEMA, "mode": "discovery_input_coverage_preflight",
        "study_id": "synthetic-input-preflight", "promotion_allowed": False,
        "window": preflight.WINDOW, "reference_specification": preflight.REFERENCE,
        "parameters": {"lookback": 60, "holding_sessions": 126, "slots": 20, "seed": 20261005},
        "inputs": {
            "calendar_manifest": {"path": "/synthetic/calendar.json", "sha256": formation.CALENDAR_MANIFEST_SHA256},
            "bb_result": {"path": "/synthetic/bb/result.json", "sha256": preflight.BB_SHA256},
            "aq": {"root": "/synthetic/aq", "report_sha256": liquidity.FORMATION_REPORT_SHA256,
                   "started_sha256": liquidity.FORMATION_STARTED_SHA256,
                   "ledger_sha256": liquidity.FORMATION_LEDGER_SHA256},
            "ar_original": {"root": "/synthetic/ar", "report_sha256": liquidity.RECOVERY_REPORT_SHA256,
                            "started_sha256": liquidity.RECOVERY_STARTED_SHA256,
                            "ledger_sha256": liquidity.RECOVERY_LEDGER_SHA256},
            "ar_recovery": {"root": "/synthetic/recovery", "report_sha256": preflight.RECOVERY_REPORT_SHA256,
                            "started_sha256": preflight.RECOVERY_STARTED_SHA256,
                            "ledger_sha256": preflight.RECOVERY_LEDGER_SHA256,
                            "candidates_sha256": preflight.CANDIDATES_SHA256},
        },
    }


def trade(**changes) -> dict:
    row = rows_for_day()[probe.SERVICES[0]][0]
    row.update(TDD_OPNPRC="50000", TDD_HGPRC="50010", TDD_LWPRC="49990",
               ACC_TRDVAL="10000000000")
    row.update(changes)
    return row


def test_actual_zero_absence_frozen_and_zero_open_are_distinct():
    assert preflight.bar_state(trade(ACC_TRDVAL="0"), response_saved=True) == "observed_zero_turnover"
    assert preflight.bar_state(trade(TDD_OPNPRC="50000", TDD_HGPRC="50000", TDD_LWPRC="50000",
                                     ACC_TRDVAL="0"), response_saved=True) == "observed_frozen_zero"
    assert preflight.bar_state(trade(TDD_OPNPRC="0"), response_saved=True) == "observed_zero_open"
    assert preflight.bar_state(None, response_saved=True) == "missing_raw_row_cause_unknown"
    assert preflight.bar_state(None, response_saved=False) == "no_saved_response_day"


def test_coverage_preserves_union_omitted_days_and_numeric_failures():
    windows = [
        {"formation": "20190402", "code": "111111", "prior_count": 60,
         "required_observed_dates": ["20190329", "20190401", "20190402"]},
        {"formation": "20190403", "code": "111111", "prior_count": 59,
         "required_observed_dates": ["20190401", "20190402", "20190403"]},
    ]
    observed = {("111111", "20190401"): trade(ACC_TRDVAL="0"),
                ("111111", "20190402"): trade()}
    result = preflight.coverage_summary(windows, observed, {"20190401", "20190402", "20190403"}, {})
    assert result["required_unique_code_dates"] == 4  # Repeated windows do not multiply the denominator.
    assert result["raw_states"] == {"no_saved_response_day": 1, "observed_zero_turnover": 1,
                                    "observed_positive_turnover": 1, "missing_raw_row_cause_unknown": 1}
    assert result["provenance_unknown_code_dates"] == 2
    assert result["unresolved_potentially_influential_windows"] == 1
    assert result["windows_with_proven_numeric_failure"] == 1
    assert result["windows"][1]["proven_numeric_failures"] == ["BB_verified_observed_history_less_than_60"]
    assert "not-listed" not in json.dumps(result)


def test_exact_cap_and_median_boundaries_and_missing_is_not_zero():
    rows = rows_for_day()
    assert formation.audit_day(formation.DATES[0], rows)["cap_pass_rows"] == 2
    rows[probe.SERVICES[0]][0].update(TDD_CLSPRC="49999.99999999", MKTCAP="4999999999999")
    assert formation.audit_day(formation.DATES[0], rows)["cap_below_rows"] == 1
    days = [(datetime(2019, 1, 2) + timedelta(days=i)).strftime("%Y%m%d") for i in range(60)]
    target = {"formation": "20190402", "code": "111111", "reported_listing_date": "20000104",
              "source_common_label": True, "formation_tradability_proxy": True}
    history = {day: {"turnover": Decimal("10000000000"), "equal_ohlc_zero_turnover": False,
                     "zero_ohl": False} for day in days}
    result = liquidity.summarize_history(target, days, history, set(days))
    assert result["liquidity_pass"] is True and result["median_traded_value_krw"] == "10000000000"
    history[days[0]]["turnover"] = Decimal(0)
    zero = liquidity.summarize_history(target, days, history, set(days))
    assert zero["zero_turnover_count"] == 1 and zero["missing_count"] == 0 and zero["liquidity_pass"] is True
    del history[days[0]]
    missing = liquidity.summarize_history(target, days, history, set(days))
    assert missing["missing_count"] == 1 and missing["median_traded_value_krw"] is None
    assert missing["liquidity_pass"] is None and missing["uncollected_dates"] == []


def test_raw_formation_proxy_failure_does_not_eliminate_adjusted_quote_influence():
    key = ("20190402", "111111")
    candidates = [{"formation": key[0], "code": key[1], "liquidity_pass": True,
                   "formation_tradability_proxy": False}]
    failures = preflight.numeric_liquidity_failures(candidates)
    assert failures == {key: []}
    frozen = trade(TDD_HGPRC="50000", TDD_LWPRC="50000", ACC_TRDVAL="0")
    result = preflight.coverage_summary(
        [{"formation": key[0], "code": key[1], "prior_count": 60, "required_observed_dates": [key[0]]}],
        {(key[1], key[0]): frozen}, {key[0]}, failures, {key: False})
    assert result["raw_states"] == {"observed_frozen_zero": 1}
    assert result["unresolved_potentially_influential_windows"] == 1
    assert result["windows_with_proven_numeric_failure"] == 0
    assert result["windows"][0]["raw_formation_tradability_proxy"] is False
    assert result["windows"][0]["raw_to_adjusted_formation_state_verified"] is False


@pytest.mark.parametrize("raw", [b'{"OutBlock_1":[],"OutBlock_1":[]}',
                                 b'{"OutBlock_1":[{"ISU_CD":"111111","ISU_CD":"222222"}]}',
                                 b'{"OutBlock_1":[],"unexpected":1}', b'{"OutBlock_1":[NaN]}'])
def test_strict_offline_envelope_refuses_ambiguous_json(raw):
    with pytest.raises(ValueError):
        preflight.parse_envelope(raw)


def test_saved_raw_reader_refuses_duplicate_codes_and_preserves_hash_manifest():
    original = [trade()]
    raw = preflight.encoded({"OutBlock_1": original * 2})
    observation = formation._summarize(original, probe.SERVICES[0], formation.DATES[0])
    observation["response_sha256"] = preflight.digest(raw)
    inputs = preflight.PinnedInputs(lambda path: raw)
    with pytest.raises(ValueError, match="re-audit mismatch"):
        preflight._rows(inputs, Path("/synthetic"), 1,
                        {"service": probe.SERVICES[0], "bas_dd": formation.DATES[0]}, observation, "aq", aq=True)
    assert inputs.manifest[0]["sha256"] == preflight.digest(raw)


def test_hash_mismatch_refuses_before_json_parse():
    inputs = preflight.PinnedInputs(lambda path: b"not JSON")
    with pytest.raises(ValueError, match="hash mismatch"):
        inputs.json(Path("/synthetic/source"), "0" * 64, "synthetic")
    assert inputs.manifest == [{"role": "synthetic", "path": "/synthetic/source", "bytes": 8,
                                "sha256": preflight.digest(b"not JSON"), "expected_sha256": "0" * 64,
                                "read_status": "hash_mismatch"}]


def test_saved_summary_coordinate_and_hash_are_both_enforced():
    raw = preflight.encoded({"OutBlock_1": [trade()]})
    observation = formation._summarize([trade()], probe.SERVICES[0], formation.DATES[0])
    observation["response_sha256"] = preflight.digest(raw)
    inputs = preflight.PinnedInputs(lambda path: raw)
    rows = preflight._rows(inputs, Path("/synthetic"), 1,
                           {"service": probe.SERVICES[0], "bas_dd": formation.DATES[0]}, observation, "aq", aq=True)
    assert len(rows) == 1
    observation["rows"] += 1
    with pytest.raises(ValueError, match="re-audit mismatch"):
        preflight._rows(inputs, Path("/synthetic"), 1,
                        {"service": probe.SERVICES[0], "bas_dd": formation.DATES[0]}, observation, "aq", aq=True)


def test_source_pin_and_v1_parameter_changes_refused():
    spec = spec_fixture()
    reference = {"parameters": deepcopy(spec["parameters"])}
    preflight.validate_spec(spec, reference)
    spec["parameters"]["seed"] += 1
    with pytest.raises(ValueError, match="fixed preflight"):
        preflight.validate_spec(spec, reference)
    spec = spec_fixture()
    spec["inputs"]["bb_result"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="input pin"):
        preflight.validate_spec(spec, {"parameters": spec["parameters"]})


def test_repository_production_spec_matches_validator_without_actual_input_access():
    root = Path(__file__).resolve().parents[2]
    spec = preflight.strict_json((root / "configs/research/discovery/activity-timing-preflight-v1.json").read_bytes())
    reference = preflight.strict_json((root / preflight.REFERENCE).read_bytes())
    preflight.validate_spec(spec, reference)


def test_shared_seven_field_provenance_does_not_invent_retrieval_release_or_finality():
    calendar = [date(2020, 1, 3), date(2020, 1, 6), date(2020, 1, 7)]
    result = preflight.response_metadata("20200103", probe.SERVICES[0],
        {"response_sha256": "1" * 64, "at": "2026-10-07T00:00:00+00:00"}, calendar, 0, "aq:1")
    assert result["available_at"] == "2020-01-06T08:30:00+09:00"
    assert result["retrieved_at"] == "unknown" and result["source_public_available_at"] is None
    assert result["is_final"] is None and result["historical_version_unknown"] is True
    assert result["evidence_level"] == "assumed" and result["evidence_level_scope"] == "modeled_availability_only"
    assert result["data_vintage"] == "retrieved_snapshot_sha256:" + "1" * 64
    assert result["observation_receipt_recorded_at"] == "2026-10-07T00:00:00+00:00"
    assert {"observation_date", "available_at", "retrieved_at", "source", "is_final", "data_vintage", "evidence_level"} <= result.keys()


def test_calendar_uses_same_14_formations_and_two_actual_session_lags(monkeypatch):
    calendar = synthetic_calendar()
    monkeypatch.setattr(formation, "CALENDAR_SHA256", preflight.digest(probe.canonical(calendar)))
    manifest = {"calendar_dates": calendar, "formation_dates": list(formation.DATES)}
    _, windows, schedule = preflight.calendar_schedule(manifest, spec_fixture()["parameters"])
    assert len(windows) == len(schedule) == 14
    assert schedule[0]["formation"] == formation.DATES[0]
    for index, row in enumerate(schedule):
        formation_index = 60 + index * 126
        assert [arm["execution_on"].replace("-", "") for arm in row["arms"]] == calendar[formation_index + 1:formation_index + 3]
        assert [arm["due_on"].replace("-", "") for arm in row["arms"]] == calendar[formation_index + 127:formation_index + 129]
        assert all(arm["status"] == "input_coverage_only_not_executed" for arm in row["arms"])
    manifest["calendar_dates"] = calendar[:-1]
    with pytest.raises(ValueError, match="pinned calendar"):
        preflight.calendar_schedule(manifest, spec_fixture()["parameters"])


@pytest.fixture
def synthetic_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    spec = spec_fixture()
    files = {"python/research/activity_preflight.py": b"# synthetic runner\n",
             "python/deep/nested/a_source_outside_v1.py": b"# synthetic additional source\n",
             "python/pyproject.toml": b"[project]\nname='synthetic'\nversion='0.1'\n",
             "python/uv.lock": b"version = 1\n",
             preflight.REFERENCE: preflight.encoded({"parameters": spec["parameters"]}),
             "configs/research/discovery/synthetic-preflight.json": preflight.encoded(spec)}
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "core.hooksPath=/dev/null", "commit", "-qm", "synthetic")
    return root, root / "configs/research/discovery/synthetic-preflight.json", git


def test_freeze_manifest_includes_all_python_and_locked_dependencies(synthetic_repo):
    root, spec, _ = synthetic_repo
    manifest, raw, parsed = preflight.freeze_sources(root, spec)
    assert parsed["schema"] == preflight.SCHEMA and raw == spec.read_bytes()
    files = {item["path"]: item for item in manifest["files"]}
    assert "python/deep/nested/a_source_outside_v1.py" in files
    assert {"python/pyproject.toml", "python/uv.lock", preflight.REFERENCE} <= set(files)
    assert all(item["sha256"] == preflight.digest((root / name).read_bytes()) for name, item in files.items())


def test_unused_installed_package_drift_is_diagnostic_only(synthetic_repo, monkeypatch):
    root, spec, git = synthetic_repo
    (root / "python/uv.lock").write_text('version = 1\n[[package]]\nname = "unused-package"\nversion = "99.0"\n')
    git("add", "python/uv.lock")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "core.hooksPath=/dev/null", "commit", "-qm", "synthetic lock")
    monkeypatch.setattr(preflight.metadata, "version", lambda name: "0.1")
    manifest, _, _ = preflight.freeze_sources(root, spec)
    assert manifest["installed_locked_distributions"]["unused-package"] == {
        "installed_version": "0.1", "locked_version": "99.0", "matches_lock": False}


@pytest.mark.parametrize("kind", ["unstaged_python", "staged_python", "untracked_python", "lock", "project", "spec"])
def test_full_freeze_refuses_source_or_spec_changes(synthetic_repo, kind):
    root, spec, git = synthetic_repo
    if kind == "spec":
        path = spec
    elif kind == "lock":
        path = root / "python/uv.lock"
    elif kind == "project":
        path = root / "python/pyproject.toml"
    else:
        path = root / ("python/deep/new.py" if kind == "untracked_python" else "python/deep/nested/a_source_outside_v1.py")
    path.write_bytes(path.read_bytes() + b"\n" if path.exists() else b"# untracked\n")
    if kind == "staged_python":
        git("add", str(path.relative_to(root)))
    with pytest.raises(ValueError, match="must be clean"):
        preflight.freeze_sources(root, spec)


@pytest.mark.parametrize("mutation", ["symlink", "hardlink", "public_file", "public_parent"])
def test_private_reader_refuses_unsafe_inputs(tmp_path, mutation):
    source = tmp_path / "source.json"
    source.write_bytes(b"{}")
    source.chmod(0o600)
    tmp_path.chmod(0o700)
    if mutation == "symlink":
        alias = tmp_path / "alias.json"
        alias.symlink_to(source)
        source = alias
    elif mutation == "hardlink":
        os.link(source, tmp_path / "alias.json")
    elif mutation == "public_file":
        source.chmod(0o644)
    else:
        tmp_path.chmod(0o755)
    with pytest.raises(ValueError):
        preflight.read_private(source)


def test_trial_is_durable_before_first_actual_read_and_failed_package_survives(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    spec = spec_fixture()
    source_manifest = {"files": [{"path": "synthetic.py", "sha256": "1" * 64}]}
    monkeypatch.setattr(preflight, "freeze_sources", lambda *args: (source_manifest, preflight.encoded(spec), spec))
    runs = tmp_path / "trials.jsonl"
    output = tmp_path / "attempt"
    reads, syncs = [], []
    real_fsync = os.fsync

    def synced(fd):
        syncs.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", synced)

    def reader(path):
        reads.append(path)
        records = [json.loads(line) for line in runs.read_text().splitlines()]
        assert len(records) == 1 and records[0]["status"] == "started"
        assert syncs and (output / "source-manifest.json").exists()
        return b"synthetic actual input"

    def evaluate(spec, inputs):
        inputs.read(Path("/synthetic/input"), preflight.digest(b"synthetic actual input"), "synthetic")
        raise ValueError("synthetic late failure")

    monkeypatch.setattr(preflight, "evaluate_inputs", evaluate)
    with pytest.raises(ValueError, match="late failure"):
        preflight.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    assert reads == [Path("/synthetic/input")]
    records = list(experiment_log.read_records(runs))
    assert [row["status"] for row in records] == ["started", "failed"]
    assert records[0]["parameters"]["automatic_retries"] == 0
    assert records[0]["parameters"]["source_manifest"] == source_manifest
    failure = json.loads((output / "failure.json").read_bytes())
    assert failure["partial_package_preserved"] is True and len(failure["actual_inputs_attempted"]) == 1
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())
    assert stat.S_IMODE(runs.stat().st_mode) == 0o600
    # A second attempt cannot replace the previous evidence or read an input.
    with pytest.raises(FileExistsError):
        preflight.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    assert len(reads) == 1 and len(list(experiment_log.read_records(runs))) == 4


@pytest.mark.parametrize("change", ["unused_package", "source", "lock", "python_version", "python_executable", "spec_bytes"])
def test_terminal_identity_freeze_ignores_only_unused_package_diagnostics(tmp_path, monkeypatch, change):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    spec = spec_fixture()
    raw = preflight.encoded(spec)
    before = {
        "code_version": "1" * 40,
        "files": [{"path": "python/source.py", "sha256": "1" * 64},
                  {"path": "python/uv.lock", "sha256": "2" * 64}],
        "python_version": "synthetic Python 3.12", "python_executable": "/synthetic/python",
        "runtime_dependencies": "stdlib_and_manifested_local_Python_only",
        "installed_locked_distributions": {"unused-package": {
            "installed_version": "1", "locked_version": "1", "matches_lock": True}},
    }
    after = deepcopy(before)
    after_raw = raw
    if change == "unused_package":
        after["installed_locked_distributions"]["unused-package"].update(installed_version="2", matches_lock=False)
    elif change in {"source", "lock"}:
        after["files"][0 if change == "source" else 1]["sha256"] = "3" * 64
    elif change == "spec_bytes":
        after_raw += b"\n"
    else:
        after[change] += "-changed"
    snapshots = iter([(before, raw, spec), (after, after_raw, spec)])
    monkeypatch.setattr(preflight, "freeze_sources", lambda *args: next(snapshots))

    def evaluate(spec, inputs):
        return {"status": "synthetic_coverage_audited", "formation_totals": {}, "liquidity_totals": {},
                "coverage": {"windows": []}, "input_manifest": inputs.manifest}

    monkeypatch.setattr(preflight, "evaluate_inputs", evaluate)
    output, runs = tmp_path / "output", tmp_path / "trials.jsonl"
    if change == "unused_package":
        record = preflight.run_preflight(root, root / "spec.json", output, runs)
        assert [row["status"] for row in experiment_log.read_records(runs)] == ["started", "completed"]
        diagnostics = record["result"]["unused_installed_package_diagnostics"]
        assert diagnostics == {"before": before["installed_locked_distributions"],
                               "after": after["installed_locked_distributions"],
                               "changed": True, "execution_dependency": False}
        assert preflight.strict_json((output / "result.json").read_bytes())["unused_installed_package_diagnostics"] == diagnostics
        assert not (output / "failure.json").exists()
    else:
        with pytest.raises(ValueError, match="source changed"):
            preflight.run_preflight(root, root / "spec.json", output, runs)
        assert [row["status"] for row in experiment_log.read_records(runs)] == ["started", "failed"]
        assert (output / "failure.json").exists() and not (output / "result.json").exists()


def test_failed_durable_start_never_calls_actual_input_reader(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec = spec_fixture()
    monkeypatch.setattr(preflight, "freeze_sources", lambda *args: ({}, preflight.encoded(spec), spec))

    def no_log(*args, **kwargs):
        raise OSError("synthetic durable append failure")

    def no_read(path):
        pytest.fail("actual input must not be read without durable start")

    monkeypatch.setattr(experiment_log, "_append_record", no_log)
    with pytest.raises(OSError, match="durable append"):
        preflight.run_preflight(tmp_path, tmp_path / "spec.json", tmp_path.parent / "not-created",
                                tmp_path / "trials.jsonl", reader=no_read)


@pytest.mark.parametrize("target", ["report", "raw", "bb", "calendar", "source", "spec", "reference", "lock",
                                    "symlink_alias", "hardlink_alias", "inside_output"])
def test_trial_log_cannot_mutate_input_source_or_exclusive_output(tmp_path, monkeypatch, target):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    spec = spec_fixture()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for role, declaration in spec["inputs"].items():
        if "root" in declaration:
            declaration["root"] = str(evidence / role)
        else:
            declaration["path"] = str(evidence / role / "input.json")
    paths = {
        "report": Path(spec["inputs"]["aq"]["root"]) / "report.json",
        "raw": Path(spec["inputs"]["aq"]["root"]) / "response-01-stk_bydd_trd-20190402.json",
        "bb": Path(spec["inputs"]["bb_result"]["path"]),
        "calendar": Path(spec["inputs"]["calendar_manifest"]["path"]),
        "source": root / "python/deep/source.py", "spec": root / "preflight.json",
        "reference": root / preflight.REFERENCE, "lock": root / "python/uv.lock",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic preserved original")
    sources = {"files": [{"path": "python/deep/source.py", "sha256": "1" * 64}]}
    monkeypatch.setattr(preflight, "freeze_sources", lambda *args: (sources, preflight.encoded(spec), spec))
    output = tmp_path / "output"
    if target in {"symlink_alias", "hardlink_alias"}:
        runs = tmp_path / "aliased-trials.jsonl"
        if target == "symlink_alias":
            runs.symlink_to(paths["report"])
        else:
            os.link(paths["report"], runs)
    else:
        runs = output / "trials.jsonl" if target == "inside_output" else paths[target]

    def no_write(*args, **kwargs):
        pytest.fail("write boundary must fail before any logger write")

    monkeypatch.setattr(experiment_log, "run_discovery_trial", no_write)
    with pytest.raises(ValueError):
        preflight.run_preflight(root, paths["spec"], output, runs)
    assert all(path.read_bytes() == b"synthetic preserved original" for path in paths.values())
    assert not output.exists()


@pytest.mark.parametrize("overlap", ["inside_input", "input_ancestor"])
def test_output_cannot_overlap_declared_input_roots(tmp_path, overlap):
    spec = spec_fixture()
    spec["inputs"]["aq"]["root"] = str(tmp_path / "inputs" / "aq")
    output = Path(spec["inputs"]["aq"]["root"]) / "new-output" if overlap == "inside_input" else tmp_path / "inputs"
    with pytest.raises(ValueError, match="output overlaps"):
        preflight.validate_write_boundaries(tmp_path / "repo", tmp_path / "repo/spec.json", spec, {},
                                             output, tmp_path / "canonical-trials.jsonl")


def test_existing_canonical_log_permissions_are_retained_without_a_new_gate(tmp_path):
    runs = tmp_path / "canonical-trials.jsonl"
    runs.write_bytes(b"synthetic historical trial\n")
    runs.chmod(0o644)
    _, actual = preflight.validate_write_boundaries(tmp_path / "repo", tmp_path / "repo/spec.json",
        spec_fixture(), {}, tmp_path / "output", runs)
    assert actual == runs and stat.S_IMODE(runs.stat().st_mode) == 0o644


def test_original_transport_failure_and_recovery_log_coordinates():
    item = {"bas_dd": "20210309", "service": probe.SERVICES[0]}
    ledger = [{**item, "event": event, "number": 1, "http_status": 200 if event == "response" else None}
              for event in ("attempt", "response")]
    preflight._ledger(ledger, [item])
    recovery = [{**row, "logical_number": 550} for row in ledger]
    preflight._ledger(recovery, [item], logical_first=550, recovery=True)
    recovery[-1]["logical_number"] = 551
    with pytest.raises(ValueError, match="ledger request"):
        preflight._ledger(recovery, [item], logical_first=550, recovery=True)


@pytest.fixture
def full_saved_inputs(monkeypatch):
    """Real 56+549+1131 file geometry, with two synthetic rows per market day.

    Only population constants shrink; parsing, re-audits, receipts, row joins,
    exact median reconstruction and full content manifests execute unchanged.
    """
    spec = spec_fixture()
    store = {}

    def put(path, raw):
        store[str(path)] = raw
        return preflight.digest(raw)

    def put_json(path, obj):
        return put(path, preflight.encoded(obj))

    calendar = synthetic_calendar()
    monkeypatch.setattr(formation, "CALENDAR_SHA256", preflight.digest(probe.canonical(calendar)))
    windows = {day: calendar[calendar.index(day) - 60:calendar.index(day)] for day in formation.DATES}
    manifest = {"calendar_dates": calendar, "formation_dates": list(formation.DATES)}
    spec["inputs"]["calendar_manifest"]["sha256"] = put_json("/synthetic/calendar.json", manifest)
    bb_windows = [{"formation": day, "code": code, "prior_count": 60, "prelisting_search_dates": [],
                   "required_observed_dates": windows[day] + [day]}
                  for day in formation.DATES for code in ("111111", "222222")]
    denominators = {"target_windows": 28, "target_codes": 2, "retained_controls": 0,
                    "population_rows": 28, "short_windows": 0, "listing_bounded_unique_code_dates": 1708}
    monkeypatch.setattr(preflight, "BB_DENOMINATORS", denominators)
    spec["inputs"]["bb_result"]["sha256"] = put_json("/synthetic/bb/result.json",
        {"status": "passed", **denominators, "windows": bb_windows})
    monkeypatch.setattr(preflight, "AQ_TOTALS", {"trade_rows": 28, "cap_pass_rows": 28,
                                               "cap_below_rows": 0, "cap_pass_common_label_rows": 28})

    def rows_for(day):
        rows = rows_for_day(day)
        for service in probe.SERVICES[:2]:
            rows[service][0].update(TDD_OPNPRC="50000", TDD_HGPRC="50010", TDD_LWPRC="49990",
                                     ACC_TRDVAL="10000000000")
        return rows

    def receipts(role, report, started, ledger):
        root = Path(spec["inputs"][role]["root"])
        spec["inputs"][role]["report_sha256"] = put_json(root / "report.json", report)
        spec["inputs"][role]["started_sha256"] = put_json(root / "started.json", started)
        spec["inputs"][role]["ledger_sha256"] = put(root / "requests.jsonl",
            b"".join(preflight.encoded(row).replace(b"\n", b"") + b"\n" for row in ledger))

    aq_matrix = [{"service": service, "bas_dd": day} for day in formation.DATES for service in probe.SERVICES]
    aq_report = {"status": "formations_audited", "report_persisted": True,
                 "cap_pass_rows": 28, "cap_below_rows": 0, "cap_pass_common_label_rows": 28,
                 "observations": [], "days": [formation.audit_day(day, rows_for(day)) for day in formation.DATES]}
    aq_ledger = []
    for number, item in enumerate(aq_matrix, 1):
        rows = rows_for(item["bas_dd"])[item["service"]]
        raw = preflight.encoded({"OutBlock_1": rows})
        obs = formation._summarize(rows, item["service"], item["bas_dd"])
        obs["response_sha256"] = put(Path(spec["inputs"]["aq"]["root"]) /
            f"response-{number:02d}-{item['service']}-{item['bas_dd']}.json", raw)
        aq_report["observations"].append(obs)
        aq_ledger.extend({**item, "number": number, "event": event, "http_status": 200 if event == "response" else None}
                         for event in ("attempt", "response"))
    receipts("aq", aq_report, {"request_matrix": aq_matrix}, aq_ledger)
    days = sorted({day for window in windows.values() for day in window})
    matrix = [{"bas_dd": day, "service": service} for day in days for service in probe.SERVICES[:2]]
    original = {"status": "failed", "failure": "transport_failure", "requests_attempted": 550, "observations": []}
    recovery = {"status": "liquidity_audited", "combined_valid_responses": 1680,
                "candidates_persisted": True, "transport_retries_used": 0, "observations": []}
    ledgers = {"ar_original": [], "ar_recovery": []}
    for logical, item in enumerate(matrix, 1):
        role = "ar_original" if logical <= 549 else "ar_recovery"
        number = logical if logical <= 549 else logical - 549
        rows = rows_for(item["bas_dd"])[item["service"]]
        obs = formation.summarize_trading_day(rows, item["service"], item["bas_dd"])
        obs["response_sha256"] = put(Path(spec["inputs"][role]["root"]) /
            f"response-{logical:04d}-{item['service']}-{item['bas_dd']}.json", preflight.encoded({"OutBlock_1": rows}))
        if role == "ar_recovery":
            obs.update(number=number, logical_number=logical)
        (original if role == "ar_original" else recovery)["observations"].append(obs)
        ledgers[role].extend({**item, "number": number, "event": event, "http_status": 200 if event == "response" else None,
                             **({"logical_number": logical} if role == "ar_recovery" else {})}
                            for event in ("attempt", "response"))
    ledgers["ar_original"].extend({**matrix[549], "number": 550, "event": event, "http_status": None}
                                 for event in ("attempt", "response"))
    candidates = [liquidity.summarize_history({"formation": day, "code": code,
        "source_common_label": True, "reported_listing_date": "20000104", "formation_tradability_proxy": True},
        windows[day], {observed: liquidity._bar_metrics(trade()) for observed in windows[day]}, set(days))
        for day in formation.DATES for code in ("111111", "222222")]
    recovery["totals"] = recovery["common_label_totals"] = liquidity._counts(candidates)
    recovery["candidates_sha256"] = put_json("/synthetic/recovery/candidates.json", {"candidates": candidates})
    spec["inputs"]["ar_recovery"]["candidates_sha256"] = recovery["candidates_sha256"]
    receipts("ar_original", original, {"request_matrix": matrix}, ledgers["ar_original"])
    receipts("ar_recovery", recovery, {"request_matrix": matrix, "new_request_matrix": matrix[549:]}, ledgers["ar_recovery"])
    return spec, store


def test_full_saved_input_pipeline_reaudits_original_and_recovery_and_emits_manifest(full_saved_inputs):
    spec, store = full_saved_inputs
    inputs = preflight.PinnedInputs(lambda path: store[str(path)])
    result = preflight.evaluate_inputs(spec, inputs)
    assert result["status"] == "saved_input_coverage_audited"
    assert result["formation_totals"]["cap_pass_rows"] == result["liquidity_totals"]["liquidity_pass_rows"] == 28
    assert len(result["input_manifest"]) == 1748 and all(row["read_status"] == "hash_verified" for row in result["input_manifest"])
    assert result["coverage"]["required_unique_code_dates"] == 1708
    assert result["coverage"]["raw_states"] == {"observed_positive_turnover": 1708}
    assert result["coverage"]["unresolved_potentially_influential_windows"] == 28
    assert len(result["response_provenance"]) == len(result["row_provenance"]) == 1708
    references = {row["response_id"] for row in result["response_provenance"]}
    assert all(row["response_metadata_ref"] in references for row in result["row_provenance"])
    assert result["holding_lot_denominator"] is result["action_lot_denominator"] is None
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert result["returns_computed"] is result["historical_eligibility_certified"] is False


def test_full_pipeline_missing_saved_file_preserves_attempt_without_inventing_missing_cause(full_saved_inputs):
    spec, store = full_saved_inputs
    missing = next(path for path in store if "/ar/response-0001-" in path)
    del store[missing]
    inputs = preflight.PinnedInputs(lambda path: store[str(path)])
    with pytest.raises(KeyError):
        preflight.evaluate_inputs(spec, inputs)
    assert inputs.manifest[-1]["path"] == missing and inputs.manifest[-1]["read_status"] == "started"
    assert "sha256" not in inputs.manifest[-1]
