"""Small invented EI snapshot worker and sealed-bundle boundary checks."""

from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_snapshot_unit as unit
from research import intraday_ed_sources as sources


ROOT = Path(__file__).resolve().parents[2]
PLAN = SimpleNamespace(local_worktree=ROOT)


def checkpoint(_):
    pass


def specification():
    return {"unit_id": unit.UNIT_ID, "input_contract": wire.EI_SUPPORT_INPUT_CONTRACT,
            "source_registration": {"path": unit.ORIGINAL_SPEC, "sha256": unit.ORIGINAL_PIN},
            "geometry": dict(unit.FULL), "input_roster": [list(row) for row in wire.EI_SUPPORT_INPUT_BOUNDS],
            "snapshot_contract": deepcopy(unit.SNAPSHOT_CONTRACT), "output": dict(unit.OUTPUT_CONTRACT),
            "resource_profile": wire.EI_LOCAL_MODEL.id, "execution_host": "local_wsl_only", "actual_attempt": "actual1",
            "automatic_actual_retries": 0, "actual_runner_ready": False, "live_or_paper_order_capable": False}


def prepare(path):
    path.mkdir(mode=0o700)
    received = path / "received"
    received.mkdir(mode=0o700)
    return unit.make_invented(received, 1, checkpoint)


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    work = tmp_path_factory.mktemp("ei-snapshot-unit") / "work"
    envelope = prepare(work)
    events = []
    receipt = unit.compute(work, envelope, PLAN, invented_only=True, checkpoint=events.append)
    universe = sources.load_universe((work / "received" / "CB").read_bytes(), envelope.manifest[0].sha256,
                                     expected_sessions=1)
    return work, envelope, receipt, universe, events


def sealed(path, raw):
    path.write_bytes(raw)
    path.chmod(0o400)


def test_contract_binds_original_sources_science_and_closed_readiness():
    raw = (ROOT / unit.ORIGINAL_SPEC).read_bytes()
    spec, pins, decoded = unit.contract(wire.encode(specification()), raw)
    assert spec["actual_runner_ready"] is False
    assert [row.name for row in pins] == ["CB", "CC", "CX"]
    assert wire.valid_sha(decoded)
    assert unit.MAX_OUTPUT == 256 * wire.MIB and unit.MAX_HEADER == 4096
    ready = specification()
    ready["actual_runner_ready"] = True
    assert unit.contract(wire.encode(ready), raw)[0]["actual_runner_ready"] is True
    with pytest.raises(ValueError, match="original registration"):
        unit.contract(wire.encode(specification()), raw + b" ")


@pytest.mark.parametrize("field,value", [
    ("unit_id", "EH-SNAPSHOT-1"), ("input_contract", wire.EH_SUPPORT_INPUT_CONTRACT),
    ("geometry", {}), ("input_roster", [["CB", wire.MIB]]), ("snapshot_contract", {}),
    ("output", {}), ("resource_profile", "ec-core600-v1"), ("execution_host", "remote"),
    ("actual_attempt", "actual2"), ("automatic_actual_retries", True), ("actual_runner_ready", 1),
    ("live_or_paper_order_capable", True),
])
def test_contract_refuses_changed_science_scope_and_resource_fields(field, value):
    spec = specification()
    spec[field] = value
    with pytest.raises(ValueError, match="registration changed"):
        unit.contract(wire.encode(spec), (ROOT / unit.ORIGINAL_SPEC).read_bytes())


def test_worker_keeps_every_original_coordinate_and_seals_features_before_labels(completed):
    work, envelope, receipt, universe, events = completed
    proof = bridge.canonical(receipt.independent_verification)
    assert proof["schema"] == unit.SCHEMA and proof["verified"] and proof["invented_only"]
    assert proof["full_eof_verified"] and proof["all_original_coordinates_verified"] and not proof["full_geometry"]
    assert not proof["historical_availability_certified"] and not proof["stored_volume_units_certified"]
    assert not proof["contains_model_fits_or_returns"]
    assert bridge.canonical(receipt.candidate_counts) == {"S1": {"original": 20}, "S2": {"original": 20}}
    sealed_index = events.index("ei_snapshot_features_sealed")
    assert events[:sealed_index].count("ei_snapshot_feature_coordinate") == 21
    assert "ei_snapshot_label_coordinate" not in events[:sealed_index]
    assert "ei_snapshot_feature_coordinate" not in events[sealed_index:]
    summary = proof["snapshot_summary"]
    assert (summary["original_dates"], summary["original_coordinates"], summary["original_memberships"]) == (1, 21, 40)
    assert summary["reference_session_calls"] == 21
    assert summary["universe_sha256"] == universe.raw_sha256
    assert summary["calendar_sha256"] == wire.digest(wire.encode(universe.sessions))
    assert summary["counts"]["union"]["questions"] > 0
    assert summary["counts"]["union"]["frame_states"]["unknown"] > 0
    assert proof["support_totals"] == summary["counts"]
    assert proof["summary_sha256"] == wire.digest(wire.encode(summary))
    assert set(proof["raw_byte_audit"]["archives"]) == {"CC", "CX"}
    assert proof["input_manifest"] == envelope.payload()["inputs"]
    assert proof["input_manifest_sha256"] == wire.digest(wire.encode(proof["input_manifest"]))
    assert wire.file_digest(work / unit.OUTPUT, unit.MAX_OUTPUT, checkpoint) == (receipt.output_sha256, receipt.output_bytes)
    assert (work / unit.OUTPUT).stat().st_mode & 0o777 == 0o400
    assert not list(work.glob("ei-snapshot-members-*"))


def test_bundle_roundtrip_exact_members_and_exclusive_destinations(completed, tmp_path):
    work, _, receipt, _, _ = completed
    proof = bridge.canonical(receipt.independent_verification)
    expected = proof["snapshot_summary"]["members"]
    features, labels = unit.verify_bundle(work / unit.OUTPUT, checkpoint)
    assert expected == {"features": asdict(features), "labels": asdict(labels)}
    assert features.records == labels.records == 21
    scratch = tmp_path / "unpacked"
    scratch.mkdir(mode=0o700)
    feature_path, feature_seal, label_path, label_seal = unit.open_bundle(work / unit.OUTPUT, scratch, checkpoint)
    assert (feature_seal, label_seal) == (features, labels)
    for path, seal in ((feature_path, feature_seal), (label_path, label_seal)):
        assert wire.file_digest(path, unit.snapshot.MAX_BYTES, checkpoint) == (seal.sha256, seal.size)
        assert path.stat().st_mode & 0o777 == 0o400
    with pytest.raises(ValueError, match="fresh private directory"):
        unit.open_bundle(work / unit.OUTPUT, scratch, checkpoint)


@pytest.mark.parametrize("change,message", [
    ("truncated", "member length"), ("trailing", "member length"), ("hash", "member hash"),
    ("noncanonical", "noncanonical"), ("oversized_header", "header exceeded"),
    ("schema", "header differs"), ("feature_binding", "member header"), ("count", "feature header"),
])
def test_bundle_corruption_is_refused(completed, tmp_path, change, message):
    raw = (completed[0] / unit.OUTPUT).read_bytes()
    if change == "truncated":
        raw = raw[:-1]
    elif change == "trailing":
        raw += b"\n"
    elif change == "hash":
        first, body = raw.split(b"\n", 1)
        body = body.replace(b'"ready"', b'"xeady"', 1)
        raw = first + b"\n" + body
    elif change == "noncanonical":
        raw = b" " + raw
    elif change == "oversized_header":
        raw = b" " * (unit.MAX_HEADER + 1) + raw
    else:
        first, body = raw.split(b"\n", 1)
        header = json.loads(first)
        if change == "schema":
            header["schema"] = "EH-SNAPSHOT-1-forecast-snapshot-v1"
        elif change == "feature_binding":
            header["feature_header"]["roster_sha256"] = "0" * 64
        else:
            header["labels"]["records"] -= 1
        raw = wire.encode(header) + body
    path = tmp_path / "corrupt"
    sealed(path, raw)
    with pytest.raises(ValueError, match=message):
        unit.verify_bundle(path, checkpoint)


def test_total_bound_includes_header_before_any_output_creation(completed, tmp_path, monkeypatch):
    work, _, receipt, universe, _ = completed
    scratch = tmp_path / "members"
    scratch.mkdir(mode=0o700)
    feature_path, feature_seal, label_path, label_seal = unit.open_bundle(work / unit.OUTPUT, scratch, checkpoint)
    monkeypatch.setattr(unit, "MAX_OUTPUT", receipt.output_bytes - 1)
    output = tmp_path / "too-small"
    with pytest.raises(ValueError, match="size exceeded"):
        unit._pack_bundle(output, feature_path, feature_seal, label_path, label_seal, checkpoint,
                          expected_targets=universe.targets)
    assert not output.exists()


def test_saved_audit_recounts_exact_original_denominators_without_reexecuting_science(completed, tmp_path, monkeypatch):
    work, envelope, receipt, universe, _ = completed
    proof = bridge.canonical(receipt.independent_verification)

    def forbidden(*args, **kwargs):
        pytest.fail("saved audit reran science or original price decoder")

    monkeypatch.setattr(unit.reference, "verify_snapshot", forbidden)
    monkeypatch.setattr(unit.snapshot, "project_coordinate", forbidden)
    monkeypatch.setattr(sources, "session_inputs", forbidden)
    scratch = tmp_path / "audit"
    scratch.mkdir(mode=0o700)
    audit = unit.audit_saved_bundle(work / unit.OUTPUT, scratch, expected_sha256=receipt.output_sha256,
        expected_bytes=receipt.output_bytes, universe=universe, expected_geometry=bridge.canonical(envelope.geometry),
        checkpoint=checkpoint)
    assert audit["full_eof_verified"] and audit["science_reexecuted"] is False
    assert audit["candidate_counts"] == bridge.canonical(receipt.candidate_counts)
    for key in ("counts", "counts_sha256", "members", "universe_sha256", "calendar_sha256", "original_dates",
                "original_coordinates", "original_memberships"):
        assert audit[key] == proof["snapshot_summary"][key]


@pytest.mark.parametrize("change", ["pin", "population", "roster"])
def test_saved_audit_needs_external_pin_and_original_roster(completed, tmp_path, change):
    work, envelope, receipt, universe, _ = completed
    geometry = bridge.canonical(envelope.geometry)
    digest = receipt.output_sha256
    if change == "pin":
        digest = "0" * 64
    elif change == "population":
        geometry = {**geometry, "targets_per_scenario": 19}
    else:
        universe = replace(universe, targets=tuple(replace(row, stored_row_count=999) for row in universe.targets))
    scratch = tmp_path / "audit"
    scratch.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        unit.audit_saved_bundle(work / unit.OUTPUT, scratch, expected_sha256=digest, expected_bytes=receipt.output_bytes,
            universe=universe, expected_geometry=geometry, checkpoint=checkpoint)


def test_wrong_input_contract_and_closed_actual_readiness_refuse_before_source_access(tmp_path):
    envelope = prepare(tmp_path / "work")
    with pytest.raises(ValueError, match="input contract differs"):
        unit.compute(tmp_path / "absent", replace(envelope, input_contract=wire.EH_SUPPORT_INPUT_CONTRACT), PLAN,
                     invented_only=True, checkpoint=checkpoint)
    plan = SimpleNamespace(unit_id=unit.UNIT_ID, actual_runner_ready=False, resource_profile=wire.ORDINARY300.id)
    with pytest.raises(ValueError, match="actual readiness closed"):
        unit.compute(tmp_path / "absent", envelope, plan, invented_only=False, checkpoint=checkpoint)


def test_changed_received_volume_bytes_refuse_before_projection(tmp_path, monkeypatch):
    work = tmp_path / "work"
    envelope = prepare(work)
    with (work / "received" / "CX").open("ab") as stream:
        stream.write(b"changed")
    monkeypatch.setattr(unit.snapshot, "project_coordinate", lambda *args: pytest.fail("projection before authentication"))
    with pytest.raises(ValueError, match="received source differs"):
        unit.compute(work, envelope, PLAN, invented_only=True, checkpoint=checkpoint)


def test_producer_volume_decoder_corruption_is_caught_by_independent_original_reconstruction(tmp_path, monkeypatch):
    work = tmp_path / "work"
    envelope = prepare(work)
    original = sources._session_observations

    def corrupt(*args, **kwargs):
        value, quality = original(*args, **kwargs)
        rows = {at: replace(row, shares=row.shares + Decimal(1)) if row.volume_state == "observed" else row
                for at, row in value.minutes.items()}
        return replace(value, minutes=rows), quality

    monkeypatch.setattr(sources, "_session_observations", corrupt)
    with pytest.raises(ValueError, match="original_feature_or_frame_differs"):
        unit.compute(work, envelope, PLAN, invented_only=True, checkpoint=checkpoint)
    assert not (work / unit.OUTPUT).exists()
