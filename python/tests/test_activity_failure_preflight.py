from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from research import activity_failure_preflight as module
from research import activity_preflight as saved


def fixture():
    dates = [(date(2020, 1, 1) + timedelta(days=i)).strftime("%Y%m%d") for i in range(63)]
    bb = {"formation": dates[-1], "code": "111111", "inventory_start": dates[0],
          "required_observed_dates": dates, "prior_count": 60, "formation_state": "NONFROZEN"}
    bh = {"formation": dates[-1], "code": "111111", "required_dates": 63,
          "proven_numeric_failures": [], "potentially_influential_unresolved": True}
    frozen = dates[10:12]
    normal = [d for d in dates[:-1] if d not in frozen]
    au = {"formation": dates[-1], "code": "111111", "formation_state": {"state": "NONFROZEN"},
          "prior_nonfrozen_candidates": {"count": 60, "dates": normal,
              "skipped_frozen_dates": frozen, "missing_dates": [], "invalid_dates": []}}
    return bb, bh, au, dates


def test_plan_includes_every_frozen_session_and_formation():
    bb, bh, au, dates = fixture()
    windows, scope = module.plan_windows([bb], [bh], [au], dates)
    assert len(scope["111111"]) == 63
    assert windows[0]["frozen_dates"] == tuple(module.day(d) for d in dates[10:12])
    assert scope["111111"][-1] == module.day(bb["formation"])


def test_removed_intervening_date_rejected_even_when_au_partition_also_removed():
    bb, bh, au, dates = fixture()
    bb["required_observed_dates"] = [d for d in dates if d != dates[10]]
    bh["required_dates"] -= 1
    au["prior_nonfrozen_candidates"]["skipped_frozen_dates"].pop(0)
    with pytest.raises(ValueError, match="every required trading session"):
        module.plan_windows([bb], [bh], [au], dates)


@pytest.mark.parametrize("mutate", [
    lambda bb, bh, au: bh.update(proven_numeric_failures=["liquidity_failure"]),
    lambda bb, bh, au: au["prior_nonfrozen_candidates"]["missing_dates"].append(bb["inventory_start"]),
    lambda bb, bh, au: au["formation_state"].update(state="FROZEN_ZERO"),
])
def test_inconsistent_previous_disposition_or_states_refused(mutate):
    bb, bh, au, dates = fixture()
    mutate(bb, bh, au)
    with pytest.raises(ValueError):
        module.plan_windows([bb], [bh], [au], dates)


def test_proven_previous_failures_are_preserved_without_database_scope():
    bb, bh, au, dates = fixture()
    bh.update(proven_numeric_failures=["verified_short"], potentially_influential_unresolved=False)
    assert module.plan_windows([bb], [bh], [au], dates) == ([], {})


def test_fixed_spec_refuses_unregistered_columns_and_parameter_change():
    root = Path(__file__).resolve().parents[2]
    spec = json.loads((root / "configs/research/discovery/activity-failure-preflight-v1.json").read_text())
    reference = json.loads((root / saved.REFERENCE).read_text())
    module.validate_spec(spec, reference)
    changed = deepcopy(spec)
    changed["inputs"]["scan_database"]["allowed_columns"].append("volume")
    with pytest.raises(ValueError):
        module.validate_spec(changed, reference)
    changed = deepcopy(spec)
    changed["parameters"]["threshold"] = 2.9
    with pytest.raises(ValueError):
        module.validate_spec(changed, reference)


def test_actual_input_pin_failure_records_started_failed_before_database(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    output = parent / "run"
    evidence = tmp_path / "input"
    evidence.mkdir()
    source = {"files": [], "code_version": "a" * 40}
    spec = {"study_id": "activity-numeric-failure-preflight-v1", "parameters": {},
            "window": saved.WINDOW, "inputs": {"calendar_manifest": {
                "path": str(evidence / "calendar.json"), "sha256": "0" * 64}}}
    monkeypatch.chdir(root)
    monkeypatch.setattr(saved, "freeze_sources", lambda *args, **kwargs: (source, b"{}", spec))
    calls = []

    def bad_reader(path):
        calls.append(path)
        log = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
        assert len(log) == 1 and log[0]["status"] == "started"
        return b"{}"

    with pytest.raises(ValueError, match="actual input hash mismatch"):
        module.run_preflight(root, root / "spec.json", output, tmp_path / "runs.jsonl", reader=bad_reader)
    log = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert [row["status"] for row in log] == ["started", "failed"]
    assert len(calls) == 1
    failure = json.loads((output / "failure.json").read_text())
    assert failure["actual_inputs_attempted"][0]["read_status"] == "hash_mismatch"
    assert not (output / "read-scope.json").exists()


@pytest.mark.parametrize("missing", [False, True])
def test_saved_population_to_scoped_sqlite_and_exact_predicate(tmp_path, monkeypatch, missing):
    bb, bh, au, dates = fixture()
    calendar = dates + [(module.day(dates[-1]) + timedelta(days=i)).strftime("%Y%m%d") for i in (1, 2)]
    bb_rows, bh_rows, au_rows = [], [], []
    for code in ("111111", "222222"):
        for original, rows in ((bb, bb_rows), (bh, bh_rows), (au, au_rows)):
            value = deepcopy(original)
            value["code"] = code
            rows.append(value)
    store, declarations = {}, {}
    objects = {"calendar_manifest": {}, "bb_result": {"windows": bb_rows},
        "bh_result": {"status": "saved_input_coverage_audited", "returns_computed": False,
            "coverage": {"windows": bh_rows, "unresolved_potentially_influential_windows": 2,
                         "windows_with_proven_numeric_failure": 0}}, "au_result": {"rows": au_rows}}
    for role, value in objects.items():
        path = tmp_path / (role + ".json")
        raw = saved.encoded(value)
        store[path] = raw
        declarations[role] = {"path": str(path), "sha256": saved.digest(raw)}
    database = tmp_path / "synthetic.sqlite3"
    with sqlite3.connect(database) as con:
        con.executescript("CREATE TABLE scan_panel(id INTEGER,start TEXT,end TEXT);"
            "CREATE TABLE scan_progress(code TEXT,first_date TEXT,last_date TEXT,bars INTEGER,"
            "frozen INTEGER,status TEXT,fetched_at TEXT);"
            "CREATE TABLE scan_bars(code TEXT,bsop_date TEXT,open TEXT,high TEXT,low TEXT,close TEXT,turnover TEXT);")
        con.execute("INSERT INTO scan_panel VALUES (1,'20190102','20260918')")
        for code in ("111111", "222222"):
            con.execute("INSERT INTO scan_progress VALUES (?,?,?,?,?,?,?)", (
                code, dates[0], dates[-1], len(dates), 2, "done", "2026-10-09T00:00:00+00:00"))
            for value in dates:
                if missing and code == "111111" and value == dates[20]:
                    continue
                frozen = value in dates[10:12]
                amount = "0" if frozen else "10" if value != dates[-1] else "20" if code == "111111" else "40"
                con.execute("INSERT INTO scan_bars VALUES (?,?,?,?,?,?,?)", (
                    code, value, "10", "10" if frozen else "11", "10", "10", amount))
    declarations["scan_database"] = {"path": str(database)}
    monkeypatch.setattr(module, "TARGET_COUNTS", (2, 2))
    monkeypatch.setattr(module, "BH_DISPOSITION_COUNTS", (2, 0))
    monkeypatch.setattr(saved, "calendar_schedule", lambda *args: (calendar, {}, [{"test": "paired"}]))
    monkeypatch.setattr(saved, "_bb_windows", lambda value, cal: (value["windows"], set()))
    output = tmp_path / "output"
    output.mkdir()
    result = module.evaluate_inputs({"inputs": declarations, "parameters": {"lookback": 60, "threshold": 3.0}},
        saved.PinnedInputs(lambda path: store[path]), output)
    assert result["summary"]["proven_activity_failure_windows"] == (0 if missing else 1)
    assert result["summary"]["remaining_potential_windows"] == (2 if missing else 1)
    assert result["summary"]["unresolved_input_windows"] == int(missing)
    assert result["windows"][1]["baseline"] == "10"
    assert result["windows"][1]["required_turnover"] == "30.0"
    assert result["input_manifest"][-1]["read_status"] == "snapshot_captured"
    assert (output / "read-scope.json").exists()
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
