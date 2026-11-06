from pathlib import Path

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_snapshot_unit as unit
from research.intraday_highvol_coverage import CoverageSeal
from research.intraday_highvol_invented import make_invented
from research.intraday_highvol_snapshot_io import open_snapshot, write_snapshot


ROOT = Path(__file__).resolve().parents[2]


def noop(_):
    return None


def registration():
    return tuple((ROOT / name).read_bytes() for name in (unit.SPEC, unit.SCIENCE, unit.ORIGINAL))


def binding():
    return {"unit_id": unit.UNIT, "input_contract": unit.INPUT_CONTRACT, "invented_only": True,
            "source_sha256": "a" * 64, "runtime_sha256": "b" * 64,
            "registration_sha256": wire.digest(registration()[0])}


def test_unready_and_changed_registration_refuse_actual_source():
    spec, science, original = registration()
    parsed, _ = unit.contract(spec, science, original, actual=False)
    parsed["actual_runner_ready"] = False
    with pytest.raises(ValueError, match="not registered ready"):
        unit.contract(wire.encode(parsed), science, original, actual=True)
    parsed["coverage_release"] = "0" * 40
    with pytest.raises(ValueError, match="registration differs"):
        unit.contract(wire.encode(parsed), science, original, actual=False)


def test_empty_event_streams_keep_all_weeks_and_no_outcomes_in_feature_footer(tmp_path):
    scope = {"sessions": ["2025-01-06", "2025-01-13"], "target_count": 2, "raw_sha256": "a" * 64}
    seals, report = write_snapshot(tmp_path, iter(()), binding(), scope, noop)
    assert report["union_events"] == 0
    assert report["timings"]["S1"]["weekly"] == [{"week": "2025-W02"}, {"week": "2025-W03"}]
    with open_snapshot(tmp_path, "features", seals["features"], noop) as reader:
        assert list(reader.records()) == []
        assert reader.footer["report"] == {"union_events": 0}
    with open_snapshot(tmp_path, "labels", seals["labels"], noop) as reader:
        assert list(reader.records()) == []
        assert reader.footer["report"] == report


def test_invented_snapshot_connects_both_scenarios_and_binds_persisted_artifacts(tmp_path):
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    bundle = make_invented(tmp_path / "original", 21, noop, scenario_fixture=True)
    receipt = unit.compute_snapshot(output, {}, binding(), noop, invented=bundle)
    result = unit.verify_persisted(output, receipt, noop)
    assert [row["name"] for row in result["artifacts"]] == [
        "eg-scenario-features.jsonl.gz", "eg-scenario-labels.jsonl.gz", unit.OUTPUT,
    ]
    assert receipt["independent_verification"]["complete"] is True
    with open_snapshot(output, "features", CoverageSeal(**receipt["seals"]["features"]), noop) as reader:
        features = tuple(reader.records())
    assert {item["attempt"]["scenario"] for item in features if item["attempt"]["state"] == "confirmed"} == {"A", "B"}
    assert any(item["entry"] and item["entry"]["primary_clock_supported"] for item in features)
    with (output / "eg-scenario-labels.jsonl.gz").open("ab") as changed:
        changed.write(b"corruption")
    with pytest.raises(ValueError, match="persisted artifact differs"):
        unit.verify_persisted(output, receipt, noop)
