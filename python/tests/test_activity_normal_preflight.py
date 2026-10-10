from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research import activity_normal_preflight as module
from research import activity_preflight as saved
from research.activity_failure import activity_failure
from research.activity_screen import ScreenObservation
from research.activity_timing import AvailabilityMetadata, SessionLagPolicy


def population_fixture():
    rows = [{"formation": "20200301", "code": f"{i:06d}", "isin": f"ISIN{i}",
             "capitalization": "6000000000000" if i else "4999999999999",
             "cap_pass": bool(i), "negative_type": "preferred" if i == 1 else None}
            for i in range(5)]
    bh = [{"formation": "20200301", "code": f"{i:06d}",
           "proven_numeric_failures": ["liquidity_failure"] if i == 2 else [],
           "potentially_influential_unresolved": i != 2} for i in (2, 3, 4)]
    bi = [{"formation": "20200301", "code": f"{i:06d}", "proven_failure": i == 3,
           "reason": "activity_below_threshold" if i == 3 else "numeric_pass_unresolved"}
          for i in (3, 4)]
    potential = {("20200301", "000004")}
    counts = {key: 1 for key in module.COUNTS}
    return rows, bh, bi, potential, counts


def test_complete_partition_preserves_source_rows_and_all_failure_control_proofs():
    rows, bh, bi, potential, counts = population_fixture()
    result = module.partition_population(rows, bh, bi, potential, counts=counts)
    assert [r["disposition"] for r in result] == list(counts)
    assert len(result) == len(rows)
    assert result[0]["proof"]["capitalization"] == "4999999999999"
    assert result[1]["proof"]["literal_type"] == "preferred"
    assert result[2]["proof"] == bh[0]
    assert result[3]["proof"] == bi[0]


@pytest.mark.parametrize("corruption", ["duplicate_source", "narrowed_panel", "unknown_control",
    "extra_proof", "missing_proof", "failure_as_candidate", "cap_failure_as_control",
    "duplicate_bh", "duplicate_bi"])
def test_population_cannot_silently_drop_or_relabel_rows(corruption):
    rows, bh, bi, potential, counts = population_fixture()
    if corruption == "duplicate_source":
        rows.append(deepcopy(rows[0]))
    elif corruption == "narrowed_panel":
        rows.pop(0)
    elif corruption == "unknown_control":
        rows[4]["negative_type"] = "preferred"
    elif corruption == "extra_proof":
        bh.append({**bh[0], "code": "999999"})
    elif corruption == "missing_proof":
        bi.pop()
    elif corruption == "failure_as_candidate":
        potential.add(("20200301", "000003"))
    elif corruption == "cap_failure_as_control":
        rows[1]["cap_pass"] = False
    elif corruption == "duplicate_bh":
        bh.append(deepcopy(bh[0]))
    else:
        bi.append(deepcopy(bi[0]))
    with pytest.raises(ValueError):
        module.partition_population(rows, bh, bi, potential, counts=counts)


def proof_fixture():
    calendar = tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(65))
    policy = SessionLagPolicy(1)
    observations = {}
    for i, day in enumerate(calendar[:61]):
        amount = Decimal("100") if i < 60 else Decimal("299")
        meta = AvailabilityMetadata(day, policy.selection_at(calendar, i), "unknown",
            "synthetic immutable activity receipt", None, None, "assumed",
            availability_policy="synthetic D1", evidence_reference="synthetic")
        observations[day] = ScreenObservation("observed", amount, None, meta)
    formation = calendar[60]
    windows = [{"formation": formation.strftime("%Y%m%d"), "code": "111111",
                "required_dates": calendar[:61], "frozen_dates": (), "formation_state": "observed"}]
    value = activity_failure(calendar, formation, observations,
        bb_required_observed_dates=calendar[:61], bb_frozen_dates=(), threshold=Decimal("3"),
        lookback=60, bb_formation_state="observed").to_dict()
    bi = {"windows": [{"formation": windows[0]["formation"], "code": "111111", **value}]}
    loaded = SimpleNamespace(observations={"111111": observations})
    return calendar, windows, loaded, bi


def test_replayed_failure_matches_every_pinned_field():
    calendar, windows, loaded, bi = proof_fixture()
    assert module.replay_proofs(windows, loaded, bi, calendar, {"threshold": 3, "lookback": 60}) == bi["windows"]


def test_original_fixed_threshold_decimal_scale_survives_receipt_replay():
    """Equal numeric thresholds must preserve the original exact receipt strings."""
    calendar, windows, loaded, bi = proof_fixture()
    original = activity_failure(calendar, calendar[60], loaded.observations["111111"],
        bb_required_observed_dates=calendar[:61], bb_frozen_dates=(), threshold=Decimal("3.0")).to_dict()
    bi["windows"][0].update(original)
    assert bi["windows"][0]["required_turnover"] == "300.0"
    with pytest.raises(ValueError, match="restored BI proof differs"):
        module.replay_proofs(windows, loaded, bi, calendar, {"threshold": 3, "lookback": 60})
    assert module.replay_proofs(windows, loaded, bi, calendar,
                               {"threshold": 3.0, "lookback": 60}) == bi["windows"]


def test_population_metadata_references_resolve_exactly_without_losing_rows():
    shared = {"observation_date": "2020-03-01", "source": "synthetic service", "is_final": None}
    rows = [{"code": code, "capitalization_availability": shared,
             "classification_availability": {**shared, "source": "synthetic basic service"},
             "trade_section_availability": shared} for code in ("111111", "222222")]
    audit = module.population_audit(rows)
    assert len(audit["availability_metadata"]) == 2
    assert [r["code"] for r in audit["rows"]] == ["111111", "222222"]
    for original, compact in zip(rows, audit["rows"], strict=True):
        for field in ("capitalization_availability", "classification_availability", "trade_section_availability"):
            assert audit["availability_metadata"][compact[field]] == original[field]


@pytest.mark.parametrize("corruption", ["turnover", "state", "baseline", "normal_date", "source_time", "duplicate"])
def test_changed_snapshot_or_proof_cannot_reuse_numeric_exclusions(corruption):
    calendar, windows, loaded, bi = proof_fixture()
    if corruption in {"turnover", "state", "source_time"}:
        d = calendar[60]
        row = loaded.observations["111111"][d]
        if corruption == "turnover":
            row = ScreenObservation("observed", Decimal("300"), None, row.availability)
        elif corruption == "state":
            row = ScreenObservation("frozen", Decimal("0"), None, row.availability)
        else:
            from dataclasses import replace
            row = replace(row, availability=replace(row.availability,
                available_at=SessionLagPolicy(2).selection_at(calendar, 61)))
        loaded.observations["111111"][d] = row
    elif corruption == "baseline":
        bi["windows"][0]["baseline"] = "101"
    elif corruption == "normal_date":
        bi["windows"][0]["normal_dates"].pop()
    else:
        bi["windows"].append(deepcopy(bi["windows"][0]))
    with pytest.raises(ValueError):
        module.replay_proofs(windows, loaded, bi, calendar, {"threshold": 3, "lookback": 60})


def test_adjudication_requires_exact_window_population_and_unique_code():
    record = {"code": "111111", "decision": "accepted",
              "required_windows": [{"formation": "2020-03-01", "window_id": "20200301/111111"}]}
    ledger = {"records": [record]}
    assert module.adjudication_records(ledger, {("20200301", "111111")}) == {"111111": record}
    for changed in ({"records": [record, record]},
                    {"records": [{**record, "decision": "unknown"}]},
                    {"records": [{**record, "required_windows": record["required_windows"] * 2}]}):
        with pytest.raises(ValueError):
            module.adjudication_records(changed, {("20200301", "111111")})
    with pytest.raises(ValueError):
        module.adjudication_records(ledger, {("20200302", "111111")})


def test_fixed_specification_pins_source_snapshot_and_unchanged_predicate():
    """Reject scope, threshold value/scale and promotion changes to the fixed study."""
    root = Path(__file__).resolve().parents[2]
    spec = json.loads((root / "configs/research/discovery/activity-normal-input-assembly-v1.json").read_text())
    reference = json.loads((root / saved.REFERENCE).read_text())
    module.validate_spec(spec, reference)
    for corruption in ("snapshot", "threshold", "threshold_scale", "input_role", "mode", "promotion"):
        changed = deepcopy(spec)
        if corruption == "snapshot":
            changed["inputs"]["bi_result"]["sha256"] = "f" * 64
        elif corruption == "threshold":
            changed["parameters"]["threshold"] = 2.99
        elif corruption == "threshold_scale":
            changed["parameters"]["threshold"] = 3
        elif corruption == "input_role":
            changed["inputs"]["scan_database"] = {"path": "/never/read/db"}
        elif corruption == "mode":
            changed["mode"] = "performance"
        else:
            changed["promotion_allowed"] = True
        with pytest.raises(ValueError):
            module.validate_spec(changed, reference)


def runner_fixture(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    source = {"files": [{"path": "python/research/synthetic.py", "sha256": "a" * 64}],
              "code_version": "a" * 40}
    spec = {"study_id": module.SCHEMA, "parameters": {}, "window": saved.WINDOW,
            "inputs": {role: {"path": str(evidence / (role + ".json")), "sha256": "0" * 64}
                       for role in module.INPUTS}}
    monkeypatch.chdir(root)
    monkeypatch.setattr(saved, "freeze_sources", lambda *a, **kw: (source, b"{}", spec))
    return root, parent / "run", tmp_path / "runs.jsonl", spec


def test_started_is_durable_before_first_read_and_failure_is_preserved(tmp_path, monkeypatch):
    """A failed attempt retains its pre-read start, input manifest and safe frames."""
    root, output, runs, _ = runner_fixture(tmp_path, monkeypatch)
    attempted = []
    def reader(path):
        """Check durable start before the synthetic reader deliberately fails."""
        attempted.append(path)
        assert [json.loads(line)["status"] for line in runs.read_text().splitlines()] == ["started"]
        assert (output / "source-manifest.json").exists()
        return b"{}"
    with pytest.raises(ValueError, match="actual input hash mismatch"):
        module.run_preflight(root, root / "spec.json", output, runs, reader=reader)
    assert len(attempted) == 1
    assert [json.loads(line)["status"] for line in runs.read_text().splitlines()] == ["started", "failed"]
    failure = json.loads((output / "failure.json").read_text())
    assert failure["automatic_retries"] == 0
    assert failure["error_frames"][-1]["function"] == "require"
    assert all(set(frame) == {"file", "line", "function"} for frame in failure["error_frames"])
    assert failure["actual_inputs_attempted"] == json.loads((output / "input-manifest.json").read_text())
    assert not (output / "result.json").exists()


def test_immutable_input_cannot_be_used_as_log_or_output(tmp_path, monkeypatch):
    root, output, runs, spec = runner_fixture(tmp_path, monkeypatch)
    target = Path(spec["inputs"]["bi_result"]["path"])
    with pytest.raises(ValueError, match="immutable input"):
        module.run_preflight(root, root / "spec.json", output, target)
    assert not target.exists() and not output.exists()


def test_changed_source_after_assembly_cannot_publish_result(tmp_path, monkeypatch):
    root, output, runs, spec = runner_fixture(tmp_path, monkeypatch)
    sources = iter([{"code_version": "a" * 40}, {"code_version": "b" * 40}])
    monkeypatch.setattr(saved, "freeze_sources", lambda *a, **kw: (next(sources), b"{}", spec))
    monkeypatch.setattr(module, "evaluate_inputs", lambda *a: {"status": "normal_inputs_assembled"})
    with pytest.raises(ValueError, match="source/runtime changed"):
        module.run_preflight(root, root / "spec.json", output, runs)
    assert (output / "failure.json").exists() and not (output / "result.json").exists()


@pytest.mark.parametrize("conflicting_source", [None, "response", "observation", "row"])
def test_connected_evaluation_restores_proofs_and_runs_real_normal_screen(tmp_path, monkeypatch,
                                                                       conflicting_source):
    """Exercise cross-module contracts with a temporary synthetic DB/receipt.

    Only raw IO and large production count/calendar declarations are
    substituted; the real formation-row producer joins an independently
    specified AX-style accepted coordinate before restore/period/screen output.
    """
    import sqlite3
    from research.activity_failure_inputs import load_activity_failure_inputs
    from research import activity_normal_sources as source_reader
    from test_activity_normal_assembly import fixture as screen_fixture
    from test_krx_formation_audit import rows_for_day

    fixture = screen_fixture()
    calendar, code, formation = fixture["calendar"], fixture["code"], fixture["formation"]
    dates = calendar[:calendar.index(formation) + 1]
    key = formation.strftime("%Y%m%d"), code
    # Existing AX/BK contract: third AQ response, first raw basic row, both
    # one-based. Never copy this expected coordinate from the BL producer.
    fixture["accepted_record"]["required_windows"][0]["krx_metadata_provenance"] = {
        "code": code, "formation": key[0], "source_observation_position": 3,
        "source_response_sha256": "c" * 64, "source_row_position": 1,
        "source_service": "stk_isu_base_info",
    }
    raw_rows = rows_for_day(key[0])
    raw_rows["stk_bydd_trd"][0].update(ISU_CD=code)
    raw_rows["stk_isu_base_info"][0].update(
        ISU_SRT_CD=code, ISU_CD=fixture["isin"], LIST_DD="20100101")
    observations = {service: {"response_sha256": "c" * 64,
                              "at": "2026-10-09T00:00:00+00:00"}
                    for service in saved.probe.SERVICES}
    produced = source_reader._formation_rows(key[0], raw_rows, observations,
        {service: index for index, service in enumerate(saved.probe.SERVICES)}, calendar)
    source_row = next(row for row in produced if row["code"] == code)
    database = tmp_path / "synthetic.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE scan_panel(id,start,end);"
            "CREATE TABLE scan_progress(code,first_date,last_date,bars,frozen,status,fetched_at);"
            "CREATE TABLE scan_bars(code,bsop_date,open,high,low,close,turnover);"
        )
        connection.execute("INSERT INTO scan_panel VALUES(1,'20190102','20260918')")
        connection.execute("INSERT INTO scan_progress VALUES(?,?,?,?,?,?,?)",
            (code, "20190102", "20260918", len(dates), 2, "done", "2026-10-09T00:00:00+00:00"))
        for day in dates:
            connection.execute("INSERT INTO scan_bars VALUES(?,?,?,?,?,?,?)",
                (code, day.strftime("%Y%m%d"), "100", "100", "100", "100",
                 str(fixture["observations"][day].turnover)))
    loaded = load_activity_failure_inputs(database, calendar, {code: dates})
    frozen = tuple(d for d in dates[:-1] if loaded.observations[code][d].state == "frozen")
    value = activity_failure(calendar, formation, loaded.observations[code],
        bb_required_observed_dates=dates, bb_frozen_dates=frozen, threshold=Decimal("3.0")).to_dict()
    bh_row = {"formation": key[0], "code": code, "required_dates": len(dates),
              "proven_numeric_failures": [], "potentially_influential_unresolved": True}
    bb_row = {"formation": key[0], "code": code, "inventory_start": dates[0].strftime("%Y%m%d"),
              "required_observed_dates": [d.strftime("%Y%m%d") for d in dates],
              "prior_count": 60, "formation_state": "NONFROZEN"}
    values = {"calendar_manifest": {}, "bb_result": {},
        "bh_result": {"coverage": {"windows": [bh_row]}},
        "au_result": {"rows": [{"formation": key[0], "code": code,
            "formation_state": {"state": "NONFROZEN"}, "prior_nonfrozen_candidates": {
                "count": 60, "dates": [d.strftime("%Y%m%d") for d in dates[:-1] if d not in frozen],
                "skipped_frozen_dates": [d.strftime("%Y%m%d") for d in frozen],
                "missing_dates": [], "invalid_dates": []}}]},
        "bi_result": {"windows": [{"formation": key[0], "code": code, **value}],
                      "scan_snapshot": loaded.snapshot.to_dict()},
        "bk_adjudication": {"records": [fixture["accepted_record"]]}}
    store, declared = {}, {}
    for role, body in values.items():
        path = tmp_path / (role + ".json")
        store[path] = saved.encoded(body)
        declared[role] = {"path": str(path), "sha256": saved.digest(store[path])}
    monkeypatch.setattr(saved, "calendar_schedule", lambda *a:
                        ([d.strftime("%Y%m%d") for d in calendar], {}, []))
    monkeypatch.setattr(saved, "_bb_windows", lambda *a: ([bb_row], set()))
    monkeypatch.setattr(module.prior, "population_snapshot", lambda *a: {"prior_dispositions": [bh_row]})
    monkeypatch.setattr(module.prior, "plan_scope", lambda *a:
                        {"possible_windows": [{"formation": key[0], "code": code}]})
    original_partition = module.partition_population
    monkeypatch.setattr(module, "partition_population", lambda *a:
                        original_partition(*a, counts={"normal_window": 1}))
    provenance = deepcopy(source_row["krx_metadata_provenance"])
    if conflicting_source == "response":
        provenance["source_response_sha256"] = "f" * 64
    elif conflicting_source == "observation":
        provenance["source_observation_position"] -= 1
    elif conflicting_source == "row":
        provenance["source_row_position"] -= 1
    source = {"isin": fixture["isin"], "listing_date": fixture["listing_date"],
              "capitalization": fixture["capitalization"], "liquidity": fixture["liquidity"],
              "krx_metadata_provenance": provenance, "source_provenance": {"synthetic": True}}
    population = [{"formation": key[0], "code": code, "isin": fixture["isin"],
                   "capitalization": "5000000000000", "cap_pass": True, "negative_type": None}]
    def sources(*args):
        """Supply one synthetic source join with the real raw-scope output boundary."""
        saved.write_exclusive(args[-1] / "raw-source-read-scope.json", b"{}")
        return {"population": population, "potential_sources": {key: source},
                "formation_totals": {"trade_rows": 1}, "negative_type_controls": []}
    monkeypatch.setattr(module, "load_sources", sources)
    output = tmp_path / "output"
    output.mkdir()
    parameters = {**fixture["params"], "threshold": 3.0}
    if conflicting_source:
        with pytest.raises(ValueError, match="accepted issue source provenance mismatch"):
            module.evaluate_inputs({"inputs": declared, "parameters": parameters},
                                   saved.PinnedInputs(store.__getitem__), output)
        assert not (output / "normal-input-audit.json").exists()
    else:
        result = module.evaluate_inputs({"inputs": declared, "parameters": parameters},
                                       saved.PinnedInputs(store.__getitem__), output)
        assert result["summary"]["signal_windows"] == {"D1": 1, "D2": 1}
        assert result["summary"]["paired_signal_symmetric_difference"] == 0
        restored = json.loads((output / "restored-activity-audit.json").read_text())
        assert restored["restored_observations_by_code"][code][formation.isoformat()]["turnover"] == "30"
        normal = json.loads((output / "normal-input-audit.json").read_text())
        assert normal[0]["arms"]["D1"]["normal_dates"] == [d.isoformat() for d in calendar[:60]]
        assert result["returns_computed"] is False
