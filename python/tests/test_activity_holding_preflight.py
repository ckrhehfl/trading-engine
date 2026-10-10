"""Holding diagnostic acceptance checks use fabricated BL bytes and SQLite only."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import stat

import pytest

from research import activity_holding_preflight as module
from research import activity_preflight as saved
from research import activity_price_parity as prices
from test_activity_failure_inputs import database
from test_activity_partition_selection import package


def fixture(tmp_path, monkeypatch, *, cash=False, mutate=None):
    """Build a small producer-shaped package; no actual evidence is loaded."""
    root, private, evidence = tmp_path / "repo", tmp_path / "private", tmp_path / "evidence"
    root.mkdir()
    private.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    result, population, normal, calendar, params = package()
    calendar = (*calendar, calendar[-1] + timedelta(days=1), calendar[-1] + timedelta(days=2))
    params.update(threshold=3.0, slippage_bps_per_side=5.0)
    if cash:
        # Turn the sole possible signal into an ordinary baseline failure.
        for arm in normal[0]["arms"].values():
            arm["candidate"]["operating_baseline"] = "fail"
            arm["selected"] = False
            arm["exclusion_reasons"] = ["operating_baseline_fail"]
        for arm in result["arms"].values():
            arm["signals"] = []
            arm["signal_windows"] = 0
        result["summary"].update(signal_windows={"D1": 0, "D2": 0}, paired_signal_union=0)
    bi = {"path": "/synthetic/upstream-bi.json", "sha256": "1" * 64}
    bl_spec = {"schema": "activity-normal-input-assembly-v1", "study_id": "activity-normal-input-assembly-v1",
        "mode": "discovery_normal_input_assembly", "promotion_allowed": False,
        "reference_specification": saved.REFERENCE, "window": saved.WINDOW, "parameters": params,
        "inputs": {"bi_result": bi}}
    manifest = [{"role": "bi_result", **bi, "expected_sha256": bi["sha256"], "bytes": 12, "read_status": "hash_verified"}]
    source_spec = "configs/research/discovery/activity-normal-input-assembly-v1.json"
    source = {"files": [{"path": source_spec, "bytes": 0, "sha256": "2" * 64}],
        "code_version": "a" * 40, "runtime_dependencies": "stdlib_and_manifested_local_Python_only"}
    activity = "3" * 64
    result.update(input_manifest=manifest, activity_input={"source_sha256": bi["sha256"],
        "upstream_dataset_sha256": activity, "typed_OHLC_fingerprint_recomputed": False})
    scope = {"schema": bl_spec["schema"], "calendar_dates": [day.isoformat() for day in calendar],
        "restored_activity_source": bi, "upstream_dataset_sha256": activity,
        "typed_OHLC_fingerprint_recomputed": False, "database_access": False, "returns_computed": False,
        "requested_code_dates": {"000001": [calendar[60].isoformat()]}}
    restored = {"upstream_result_sha256": bi["sha256"], "upstream_dataset_sha256": activity,
        "typed_OHLC_fingerprint_recomputed": False,
        "restored_observations_by_code": {"000001": {calendar[60].isoformat(): {"state": "observed"}}}}
    data = dict(zip(module.BL_FILES, (result, bl_spec, source, manifest, scope,
        {"aq_raw_responses": [], "ar_raw_responses": []}, population, normal, restored)))
    bj = {"schema": "activity-price-action-scope-v1", "status": "conservative_original_issue_scope_planned",
        "scope_kind": "upper_bound_for_BI_potential_subset_only",
        "interval_policy": "every index session from possible entry through fixed evaluation end inclusive",
        "evaluation_end": params["end"], "calendar_dates": [day.strftime("%Y%m%d") for day in calendar],
        "paired_schedule": result["paired_schedule"], "requested_code_dates": {
            code: [day.isoformat() for day in calendar[:-2]] for code in ("000001", "000006")},
        "returns_computed": False, "holding_selection_executed": False,
        "promotion_allowed": False, "database_access": False}
    if mutate:
        mutate(data, bj)
    source["files"][0].update(sha256=saved.digest(saved.encoded(bl_spec)), bytes=len(saved.encoded(bl_spec)))
    result["source_manifest_sha256"] = saved.digest(saved.encoded(source))
    result["specification_sha256"] = saved.digest(saved.encoded(bl_spec))
    result["audit_sha256"] = {name: saved.digest(saved.encoded(data[name])) for name in module.BL_FILES[4:]}
    payloads = {name: saved.encoded(value) for name, value in data.items()}
    hashes = {name: saved.digest(raw) for name, raw in payloads.items()}
    receipt = {"verified": True, "scope": module.RECEIPT_SCOPE, "summary": result["summary"],
        "file_sha256": hashes, "package_sha256": saved.digest(module._canonical([[name, hashes[name]] for name in module.BL_FILES])),
        **{name: False for name in module.RECEIPT_FALSE}}
    store, declared = {}, {}
    for role, name in module.BL_ROLES.items():
        path = evidence / name
        store[path] = payloads[name]
        declared[role] = {"path": str(path), "sha256": hashes[name]}
    for role, value in (("bl_verification", receipt), ("bj_scope", bj)):
        path = evidence / (role + ".json")
        store[path] = saved.encoded(value)
        declared[role] = {"path": str(path), "sha256": saved.digest(store[path])}
    db_dir = tmp_path / "database"
    db_dir.mkdir()
    db = database(db_dir)
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM scan_bars")
        connection.execute("DELETE FROM scan_progress")
        days = calendar[61:130]
        connection.executemany("INSERT INTO scan_bars VALUES(?,?,?,?,?,?,?,?)", [
            ("000001", day.strftime("%Y%m%d"), "100", "101", "99", "100", "100", "10") for day in days])
        connection.execute("INSERT INTO scan_progress VALUES(?,?,?,?,?,?,?)", ("000001", days[0].strftime("%Y%m%d"),
            days[-1].strftime("%Y%m%d"), len(days), 0, "done", "2026-10-09T00:00:00+00:00"))
        connection.execute("UPDATE scan_bars SET open='100',high='100',low='100',close='100',turnover='0' WHERE bsop_date=?", (days[0].strftime("%Y%m%d"),))
        connection.execute("UPDATE scan_bars SET high='100',low='100' WHERE bsop_date=?", (days[1].strftime("%Y%m%d"),))
        connection.execute("DELETE FROM scan_bars WHERE bsop_date=?", (days[2].strftime("%Y%m%d"),))
        connection.execute("UPDATE scan_bars SET close=NULL WHERE bsop_date=?", (days[3].strftime("%Y%m%d"),))
    declared["scan_database"] = {"path": str(db), "mode": "ro", "columns": list(prices.READ_COLUMNS)}
    spec = {"schema": module.SCHEMA, "study_id": module.SCHEMA, "mode": "discovery_holding_price_diagnostic",
        "promotion_allowed": False, "window": saved.WINDOW, "reference_specification": saved.REFERENCE,
        "parameters": params, "inputs": declared}
    frozen = {"files": [{"path": "python/synthetic.py", "sha256": "a" * 64}], "code_version": "a" * 40}
    monkeypatch.chdir(root)
    monkeypatch.setattr(saved, "freeze_sources", lambda *a, **kw: (frozen, saved.encoded(spec), spec))
    return root, private / "run", tmp_path / "runs.jsonl", spec, store, frozen, calendar


def statuses(runs):
    """Read status order from the temporary synthetic trial ledger."""
    return [json.loads(line)["status"] for line in runs.read_text().splitlines()]


def test_committed_bl_reference_slippage_literals_match_without_weakening_bm_spec(tmp_path, monkeypatch):
    """Exercise the exact committed 5/5.0 mismatch using source JSON only."""
    root = Path(__file__).resolve().parents[2]
    directory = root / "configs/research/discovery"
    upstream = saved.strict_json((directory / "activity-normal-input-assembly-v1.json").read_bytes())["parameters"]
    reference = saved.strict_json((directory / "activity-calibration-v1.json").read_bytes())
    assert type(upstream["slippage_bps_per_side"]) is int
    assert type(reference["parameters"]["slippage_bps_per_side"]) is float
    assert saved.encoded(upstream) != saved.encoded(reference["parameters"])
    before = deepcopy((upstream, reference))
    assert module._bl_parameters_match(upstream, reference["parameters"])
    assert (upstream, reference) == before
    _, _, _, spec, _, _, _ = fixture(tmp_path, monkeypatch, cash=True)
    spec["parameters"] = deepcopy(reference["parameters"])
    module.validate_spec(spec, reference)
    spec["parameters"]["slippage_bps_per_side"] = 5
    with pytest.raises(ValueError, match="fixed holding diagnostic specification mismatch"):
        module.validate_spec(spec, reference)


@pytest.mark.parametrize("left,right", [(5, 5.0), (5.0, 5), (5, 5), (5.0, 5.0)])
def test_only_equivalent_numeric_slippage_representation_is_accepted(left, right):
    """Accept equivalent finite JSON numbers without changing their inputs."""
    upstream = {"slippage_bps_per_side": left, "threshold": 3.0, "lookback": 60}
    expected = {**upstream, "slippage_bps_per_side": right}
    before = deepcopy((upstream, expected))
    assert module._bl_parameters_match(upstream, expected)
    assert (upstream, expected) == before


@pytest.mark.parametrize("side", ["upstream", "expected"])
@pytest.mark.parametrize("value", [6, 5.01, "5", True, False, None, float("nan"), float("inf"), -float("inf"), Decimal("5")])
def test_slippage_boundary_rejects_changed_cost_or_non_json_finite_numbers(side, value):
    """Neither input may smuggle a bool/string/nonfinite or changed cost."""
    upstream = {"slippage_bps_per_side": 5, "lookback": 60}
    expected = {"slippage_bps_per_side": 5.0, "lookback": 60}
    (upstream if side == "upstream" else expected)["slippage_bps_per_side"] = value
    assert module._bl_parameters_match(upstream, expected) is False


@pytest.mark.parametrize("field,value", [
    ("lookback", 60.0), ("holding_sessions", 126.0), ("slots", 20.0), ("seed", 20261005.0),
    ("bootstrap_repetitions", 2000.0), ("bootstrap_blocks", [63.0, 126, 252]), ("threshold", 3),
    ("commission_bps_per_side", 1.78), ("commission_bps_per_side", "1.77"),
    ("sizing_round_trip_bps", 43.55), ("end", "2026-09-17"), ("extra_parameter", 0),
])
def test_all_other_parameter_values_names_and_numeric_types_stay_exact(field, value):
    """The BL input exception never normalizes another cost/count/predicate."""
    reference = Path(__file__).resolve().parents[2] / saved.REFERENCE
    expected = saved.strict_json(reference.read_bytes())["parameters"]
    upstream = {**expected, "slippage_bps_per_side": 5, field: value}
    assert module._bl_parameters_match(upstream, expected) is False
    assert module._bl_parameters_match(expected, upstream) is False


@pytest.mark.parametrize("missing", ["slippage_bps_per_side", "lookback"])
def test_parameter_key_omissions_are_not_normalized(missing):
    """An equivalence exception cannot erase a required parameter name."""
    expected = {"slippage_bps_per_side": 5.0, "lookback": 60}
    upstream = {key: value for key, value in expected.items() if key != missing}
    assert module._bl_parameters_match(upstream, expected) is False
    assert module._bl_parameters_match(expected, upstream) is False


def test_repinned_synthetic_bl_integer_slippage_reaches_diagnostic_without_rewriting_inputs(tmp_path, monkeypatch):
    """Use the genuine package boundary with distinct BL int/BM float pins."""
    def mutate(data, bj):
        """Keep upstream literal identity distinct before registering fixture pins."""
        data["specification.json"]["parameters"] = {
            **data["specification.json"]["parameters"], "slippage_bps_per_side": 5}
    root, output, runs, spec, store, _, _ = fixture(tmp_path, monkeypatch, cash=True, mutate=mutate)
    before = deepcopy(store)
    assert type(spec["parameters"]["slippage_bps_per_side"]) is float
    monkeypatch.setattr(prices, "load_scan", lambda *a: pytest.fail("cash-only diagnostic opened quotes"))
    module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    assert statuses(runs) == ["started", "completed"] and store == before
    assert saved.strict_json((output / "result.json").read_bytes())["status"] == "holding_price_inputs_audited"


@pytest.mark.parametrize("corruption", [None, "missing_receipt", "wrong_raw_role", "extra_input", "duplicate_raw"])
def test_full_receipt_and_raw_roles_match_exact_internal_manifest(corruption):
    """Exercise real BL role shapes independently of the small connected fixture."""
    spec = {"inputs": {
        "bi_result": {"path": "/evidence/bi/result.json", "sha256": "1" * 64},
        "aq": {"root": "/evidence/aq", "report_sha256": "2" * 64,
               "started_sha256": "3" * 64, "ledger_sha256": "4" * 64},
        "ar_original": {"root": "/evidence/ar-original", "report_sha256": "5" * 64,
               "started_sha256": "6" * 64, "ledger_sha256": "7" * 64},
        "ar_recovery": {"root": "/evidence/ar-recovery", "report_sha256": "8" * 64,
               "started_sha256": "9" * 64, "ledger_sha256": "a" * 64,
               "candidates_sha256": "b" * 64}}}
    identities = [
        ("bi_result", "/evidence/bi/result.json", "1"),
        ("aq:report", "/evidence/aq/report.json", "2"),
        ("aq:started", "/evidence/aq/started.json", "3"),
        ("aq:ledger", "/evidence/aq/requests.jsonl", "4"),
        ("ar_original:report", "/evidence/ar-original/report.json", "5"),
        ("ar_original:started", "/evidence/ar-original/started.json", "6"),
        ("ar_original:ledger", "/evidence/ar-original/requests.jsonl", "7"),
        ("ar_recovery:report", "/evidence/ar-recovery/report.json", "8"),
        ("ar_recovery:started", "/evidence/ar-recovery/started.json", "9"),
        ("ar_recovery:ledger", "/evidence/ar-recovery/requests.jsonl", "a"),
        ("aq:raw", "/evidence/aq/response-01-stk_bydd_trd-20200301.json", "c"),
        ("ar_original:raw", "/evidence/ar-original/response-0001-stk_bydd_trd-20200101.json", "d"),
        ("ar_recovery:raw", "/evidence/ar-recovery/response-0550-ksq_bydd_trd-20200102.json", "e"),
    ]
    manifest = [{"role": role, "path": path, "sha256": pin * 64, "expected_sha256": pin * 64,
                 "bytes": 10, "read_status": "hash_verified"} for role, path, pin in identities]
    rawscope = {
        "aq_raw_responses": [{"role": "aq", "path": identities[10][1], "sha256": "c" * 64}],
        "ar_raw_responses": [{"role": "ar_original", "path": identities[11][1], "sha256": "d" * 64},
                             {"role": "ar_recovery", "path": identities[12][1], "sha256": "e" * 64}]}
    if corruption == "missing_receipt": manifest.pop(7)
    elif corruption == "wrong_raw_role": manifest[-1]["role"] = "ar_original:raw"
    elif corruption == "extra_input": manifest.append({**manifest[0], "role": "unexpected"})
    elif corruption == "duplicate_raw": rawscope["aq_raw_responses"].append(deepcopy(rawscope["aq_raw_responses"][0]))
    if corruption is None:
        module._manifest_agreement(spec, manifest, rawscope)
    else:
        with pytest.raises(ValueError):
            module._manifest_agreement(spec, manifest, rawscope)


def test_durable_start_precedes_every_input_and_one_exact_read_only_scan(tmp_path, monkeypatch):
    """Require durable registration and exact scope before one read-only synthetic scan."""
    root, output, runs, spec, store, _, calendar = fixture(tmp_path, monkeypatch)
    original, trace, connections = sqlite3.connect, [], []
    before = Path(spec["inputs"]["scan_database"]["path"]).read_bytes()
    def reader(path):
        """Assert durable start and source receipts precede each synthetic input read."""
        assert statuses(runs) == ["started"]
        assert (output / "specification.json").exists() and (output / "source-manifest.json").exists()
        return store[path]
    def connect(*args, **kwargs):
        """Trace the single read-only connection after the exact scope is persisted."""
        assert args[0].endswith("?mode=ro") and kwargs["uri"] is True
        assert statuses(runs) == ["started"] and not (output / "result.json").exists()
        scope = json.loads((output / "read-scope.json").read_text())
        assert scope["requested_code_dates"] == {"000001": [day.isoformat() for day in calendar[61:130]]}
        assert len(scope["input_manifest_before_database"]) == 11
        connection = original(*args, **kwargs)
        connection.set_trace_callback(trace.append)
        connections.append(connection)
        return connection
    monkeypatch.setattr(prices.sqlite3, "connect", connect)
    record = module.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    assert len(connections) == trace.count("BEGIN") == trace.count("ROLLBACK") == 1
    queries = [value for value in trace if "FROM scan_bars" in value]
    assert len(queries) == 1 and "WHERE code='000001' AND bsop_date IN (" in queries[0]
    assert Path(spec["inputs"]["scan_database"]["path"]).read_bytes() == before
    result = json.loads((output / "result.json").read_text())
    audit = json.loads((output / "holding-input-audit.json").read_text())
    assert result["status"] == record["result"]["status"] == "holding_price_inputs_audited"
    assert result["summary"]["state_counts"] == {"absent_requested": 1, "frozen": 1, "locked": 1, "observed": 65, "unresolved_observed": 1}
    assert result["summary"]["requested_code_dates"] == 69 and result["summary"]["unrequested_code_dates"] == 63
    assert audit["rows"][2]["typed_values"] is None and audit["rows"][3]["typed_values"][4] == ["null", None]
    assert result["quote_snapshot_sha256"] == saved.digest(saved.encoded(audit["fingerprint"]))
    assert result["activity_snapshot_sha256"] == "3" * 64 != result["quote_snapshot_sha256"]
    assert result["holding_input_audit_sha256"] == saved.digest((output / "holding-input-audit.json").read_bytes())
    assert result["read_scope_sha256"] == saved.digest((output / "read-scope.json").read_bytes())
    assert statuses(runs) == ["started", "completed"]
    assert all(result[name] is False for name in ("returns_computed", "actions_applied", "price_basis_certified", "event_coverage_certified", "promotion_allowed"))
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


def test_cash_only_reads_no_database_and_has_no_quote_fingerprint(tmp_path, monkeypatch):
    """Publish empty cash-only diagnostics without opening a database or inventing a pin."""
    root, output, runs, _, store, _, _ = fixture(tmp_path, monkeypatch, cash=True)
    monkeypatch.setattr(prices, "load_scan", lambda *a: pytest.fail("cash-only scope opened quotes"))
    module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    result = json.loads((output / "result.json").read_text())
    audit = json.loads((output / "holding-input-audit.json").read_text())
    assert result["quote_snapshot_sha256"] is audit["fingerprint"] is audit["snapshot_received_at"] is None
    assert result["database_access"] is False and result["summary"]["requested_code_dates"] == 0
    assert len(result["input_manifest"]) == 11


def test_default_reader_raises_only_population_cap(tmp_path, monkeypatch):
    """Route only the population audit through the larger bounded reader."""
    root, output, runs, spec, store, _, _ = fixture(tmp_path, monkeypatch, cash=True)
    population, ordinary = [], []
    def population_reader(path):
        """Record population reads while returning fabricated pinned bytes."""
        population.append(path)
        return store[path]
    def ordinary_reader(path):
        """Record ordinary reads while returning fabricated pinned bytes."""
        ordinary.append(path)
        return store[path]
    monkeypatch.setattr(module, "_read_population", population_reader)
    monkeypatch.setattr(saved, "read_private", ordinary_reader)
    module.run_preflight(root, root / "spec.json", output, runs)
    assert population == [Path(spec["inputs"]["bl_population_audit"]["path"])]
    assert len(ordinary) == 10 and not set(population) & set(ordinary)


@pytest.mark.parametrize("corruption", [None, "mode", "oversize"])
def test_population_reader_is_private_and_bounded(tmp_path, corruption):
    """Reject public permissions or oversized temporary population files."""
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "population.json"
    path.write_bytes(b"{}")
    path.chmod(0o600)
    if corruption == "mode": path.chmod(0o644)
    elif corruption == "oversize":
        with path.open("r+b") as handle:
            handle.truncate(128 * 1024**2 + 1)
    if corruption is None:
        assert module._read_population(path) == b"{}"
    else:
        with pytest.raises(ValueError, match="bounded"):
            module._read_population(path)


@pytest.mark.parametrize("corruption", ["pin", "receipt_pin", "receipt_file", "receipt_package", "receipt_verified", "receipt_boundary"])
def test_corrupt_pins_or_independent_receipt_fail_before_scan(tmp_path, monkeypatch, corruption):
    """Refuse invalid pins or independent receipts before any quote scan."""
    root, output, runs, spec, store, _, _ = fixture(tmp_path, monkeypatch)
    role = "bl_verification" if corruption != "pin" else "bl_result"
    item = spec["inputs"][role]
    path = Path(item["path"])
    if corruption in {"pin", "receipt_pin"}:
        item["sha256"] = "0" * 64
    else:
        value = saved.strict_json(store[path])
        if corruption == "receipt_file": value["file_sha256"]["normal-input-audit.json"] = "0" * 64
        elif corruption == "receipt_package": value["package_sha256"] = "0" * 64
        elif corruption == "receipt_verified": value["verified"] = False
        else: value["original_source_truth_verified"] = True
        store[path] = saved.encoded(value)
        item["sha256"] = saved.digest(store[path])
    monkeypatch.setattr(prices, "load_scan", lambda *a: pytest.fail("invalid package opened quotes"))
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    assert statuses(runs) == ["started", "failed"] and not (output / "result.json").exists()
    failure = saved.strict_json((output / "failure.json").read_bytes())
    assert failure["partial_package_preserved"] is True and failure["automatic_retries"] == 0
    assert all(set(frame) == {"file", "line", "function"} for frame in failure["error_frames"])


@pytest.mark.parametrize("corruption", ["manifest", "source_spec", "restored_pin", "restored_dates", "BL_params",
    "BJ_schema", "BJ_calendar", "BJ_end", "BJ_bound", "late_metadata", "signal", "audit_hash"])
def test_repinned_internal_disagreement_cannot_reach_database(tmp_path, monkeypatch, corruption):
    """Reject internal package disagreement even when outer byte pins are refreshed."""
    def mutate(data, bj):
        """Introduce one synthetic package disagreement before repinning the fixture."""
        if corruption == "manifest": data["input-manifest.json"][0]["expected_sha256"] = "0" * 64
        elif corruption == "source_spec": data["source-manifest.json"]["files"][0]["path"] = "other.json"
        elif corruption == "restored_pin": data["restored-activity-audit.json"]["upstream_dataset_sha256"] = "0" * 64
        elif corruption == "restored_dates": data["restored-activity-audit.json"]["restored_observations_by_code"]["000001"] = {}
        elif corruption == "BL_params": data["specification.json"]["parameters"] = {"lookback": 61}
        elif corruption == "BJ_schema": bj["schema"] = "other"
        elif corruption == "BJ_calendar": bj["calendar_dates"].pop()
        elif corruption == "BJ_end": bj["evaluation_end"] = "2020-01-01"
        elif corruption == "BJ_bound": bj["requested_code_dates"]["000001"] = bj["requested_code_dates"]["000001"][:90]
        elif corruption == "late_metadata": data["population-audit.json"]["availability_metadata"]["m0"]["available_at"] = "2099-01-01T08:30:00+09:00"
        elif corruption == "signal": data["result.json"]["arms"]["D2"]["signals"] = []
    root, output, runs, spec, store, _, _ = fixture(tmp_path, monkeypatch, mutate=mutate)
    if corruption == "audit_hash":
        path = Path(spec["inputs"]["bl_result"]["path"])
        result = saved.strict_json(store[path])
        result["audit_sha256"]["normal-input-audit.json"] = "0" * 64
        store[path] = saved.encoded(result)
        spec["inputs"]["bl_result"]["sha256"] = saved.digest(store[path])
    monkeypatch.setattr(prices, "load_scan", lambda *a: pytest.fail("inconsistent package opened quotes"))
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    assert statuses(runs) == ["started", "failed"] and not (output / "read-scope.json").exists()


@pytest.mark.parametrize("corruption", ["parameters", "threshold_type", "study", "window", "reference", "roles", "pin", "columns", "mode", "relative", "package_path"])
def test_source_only_specification_validator_freezes_shapes_and_parameters(tmp_path, monkeypatch, corruption):
    """Reject altered specification roles, shapes, bounds or fixed parameters."""
    _, _, _, spec, _, _, _ = fixture(tmp_path, monkeypatch)
    reference = {"parameters": deepcopy(spec["parameters"])}
    module.validate_spec(spec, reference)
    if corruption == "parameters": spec["parameters"]["lookback"] += 1
    elif corruption == "threshold_type": spec["parameters"]["threshold"] = 3
    elif corruption == "study": spec["study_id"] += "-retry"
    elif corruption == "window": spec["window"] = {**saved.WINDOW, "start": "2018-01-01"}
    elif corruption == "reference": spec["reference_specification"] = "other"
    elif corruption == "roles": spec["inputs"].pop("bl_read_scope")
    elif corruption == "pin": spec["inputs"]["bj_scope"]["sha256"] = "unknown"
    elif corruption == "columns": spec["inputs"]["scan_database"]["columns"].pop()
    elif corruption == "mode": spec["inputs"]["scan_database"]["mode"] = "rw"
    elif corruption == "relative": spec["inputs"]["bj_scope"]["path"] = "relative.json"
    else: spec["inputs"]["bl_result"]["path"] += "-other"
    with pytest.raises(ValueError):
        module.validate_spec(spec, reference)


def test_source_drift_preserves_scope_audit_and_value_free_failure(tmp_path, monkeypatch):
    """Preserve partial diagnostics and value-free failure frames after source drift."""
    root, output, runs, spec, store, frozen, _ = fixture(tmp_path, monkeypatch)
    sources = iter([frozen, {**frozen, "code_version": "b" * 40}])
    monkeypatch.setattr(saved, "freeze_sources", lambda *a, **kw: (next(sources), saved.encoded(spec), spec))
    with pytest.raises(ValueError, match="source/runtime changed"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    assert (output / "read-scope.json").exists() and (output / "holding-input-audit.json").exists()
    assert (output / "failure.json").exists() and not (output / "result.json").exists()
    assert statuses(runs) == ["started", "failed"]


def test_existing_output_is_preserved_without_reads(tmp_path, monkeypatch):
    """Refuse an existing output directory without consuming synthetic inputs."""
    root, output, runs, _, _, _, _ = fixture(tmp_path, monkeypatch)
    output.mkdir(mode=0o700)
    sentinel = output / "sentinel"
    sentinel.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        module.run_preflight(root, root / "spec.json", output, runs, reader=lambda path: pytest.fail("unexpected read"))
    assert sentinel.read_bytes() == b"original" and list(output.iterdir()) == [sentinel]


@pytest.mark.parametrize("alias", ["input_log", "source_log", "input_output", "checkout_output", "output_log"])
def test_write_boundaries_fail_before_start_or_input_reads(tmp_path, monkeypatch, alias):
    """Reject unsafe output boundaries before trial registration or input reads."""
    root, output, runs, spec, _, _, _ = fixture(tmp_path, monkeypatch)
    if alias == "input_log": runs = Path(spec["inputs"]["bl_result"]["path"])
    elif alias == "source_log": runs = root / "python/synthetic.py"
    elif alias == "input_output": output = Path(spec["inputs"]["bj_scope"]["path"]).parent / "run"
    elif alias == "checkout_output": output = root / "run"
    else: runs = output / "runs.jsonl"
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs, reader=lambda path: pytest.fail("unexpected read"))
    assert not output.exists() and not runs.exists()
