"""Closed EG registrations and invented-only lifecycle composition."""
from pathlib import Path
import json

import pytest

from research import intraday_highvol_unit as unit
from research import intraday_campaign_wsl_transport as wire


ROOT = Path(__file__).resolve().parents[2]


def registrations():
    return tuple((ROOT / name).read_bytes() for name in (unit.SPEC, unit.SCIENCE, unit.ORIGINAL))


def binding():
    return {"unit_id": unit.UNIT, "invented_only": True, "input_contract": unit.INPUT_CONTRACT,
            "source_sha256": "a" * 64, "runtime_sha256": "b" * 64,
            "registration_sha256": wire.digest(registrations()[0])}


def noop(_):
    return None


def test_registered_coverage_is_only_local_cpu_and_old_source_scope_is_preserved():
    spec, source = unit.contract(*registrations(), actual=False)
    assert spec["uses_cuda"] is False
    assert source["window"] == unit.WINDOW and source["geometry"] == unit.FULL
    assert spec["actual_attempt"] == "actual1"


@pytest.mark.parametrize("field,value", [
    ("execution_host", "gcp"), ("unit_id", "EF-SNAPSHOT-1"), ("uses_cuda", True),
    ("automatic_actual_retry", True), ("actual_attempt", "actual2"),
    ("resource_profile", "ef-local-sequence-cpu1800-as4096-v1"),
])
def test_scope_mutations_are_refused_before_market_access(field, value):
    spec, science, original = registrations()
    changed = json.loads(spec)
    changed[field] = value
    with pytest.raises(ValueError, match="closed registration differs"):
        unit.contract(wire.encode(changed), science, original, actual=False)


def test_unready_actual_source_and_changed_science_pin_are_refused():
    spec, science, original = registrations()
    changed = json.loads(spec)
    changed["actual_runner_ready"] = False
    with pytest.raises(ValueError, match="not registered ready"):
        unit.contract(wire.encode(changed), science, original, actual=True)
    with pytest.raises(ValueError, match="closed registration differs"):
        unit.contract(spec, science + b"\n", original, actual=False)


def test_malformed_registration_objects_fail_closed():
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b' ' * (wire.MIB + 1)):
        with pytest.raises(ValueError):
            unit._json(raw)


def test_computation_binding_rejects_actual_invented_mix_before_source_access(tmp_path):
    with pytest.raises(ValueError, match="binding differs"):
        unit.compute_coverage(tmp_path, {}, binding(), noop)
    changed = {**binding(), "source_sha256": "not-a-pin"}
    with pytest.raises(ValueError, match="binding pins required"):
        unit.compute_coverage(tmp_path, {}, changed, noop, invented={})


def test_invented_originals_produce_verified_lossless_exclusive_artifacts(tmp_path):
    from research.intraday_highvol_invented import make_invented
    original = tmp_path / "original"
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    invented = make_invented(original, 21, noop)
    receipt = unit.compute_coverage(output, {}, binding(), noop, invented=invented)
    saved = unit.verify_persisted(output, receipt, noop)
    assert receipt["geometry"]["sessions"] == 21
    assert receipt["report"]["union_records"] == invented["geometry"]["union_coordinates"]
    assert receipt["independent_verification"]["complete"] is True
    assert saved["independent_reconstruction_complete"] is True
    assert [row["name"] for row in saved["artifacts"]] == ["eg-highvol-coverage.jsonl.gz", unit.OUTPUT]
    # Persisted bytes are evidence, never silently recomputed after corruption.
    artifact = output / saved["artifacts"][0]["name"]
    with artifact.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="artifact changed"):
        unit.verify_persisted(output, receipt, noop)
    with pytest.raises((FileExistsError, ValueError)):
        unit.compute_coverage(output, {}, binding(), noop, invented=invented)
