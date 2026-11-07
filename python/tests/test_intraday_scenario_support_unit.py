"""Invented-only worker integration through the saved support artifact."""

from pathlib import Path

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_scenario_support_unit as unit


ROOT = Path(__file__).resolve().parents[2]


def test_support_unit_binds_exact_price_archive_and_universe_registrations():
    spec = (ROOT / bridge.EH_SUPPORT_SPECIFICATION_PATH).read_bytes()
    original = (ROOT / unit.ORIGINAL_SPEC).read_bytes()
    registration, pins, decoded = unit.contract(spec, original)
    assert registration["geometry"] == unit.FULL
    assert [pin.name for pin in pins] == ["CB", "CC"] and wire.valid_sha(decoded)
    with pytest.raises(ValueError, match="original registration changed"):
        unit.contract(spec, original + b"\n")


def test_invented_support_worker_builds_only_price_index_and_verifies_saved_rows(tmp_path):
    received = tmp_path / "received"
    received.mkdir(mode=0o700)
    envelope = unit.make_invented(received, 2, lambda _: None)
    output = unit.compute(tmp_path, envelope, None, invented_only=True, checkpoint=lambda _: None).payload()
    proof = output["independent_verification"]
    assert proof["verified"] and proof["full_eof_verified"] and proof["invented_only"]
    assert proof["raw_byte_audit"]["raw_keys"] == 42
    assert set(proof["raw_byte_audit"]["archives"]) == {"CC"}
    assert proof["support_totals"]["union"]["coordinates"] == 42
    assert proof["support_totals"]["union"]["prior_status_counts"] == [
        ["complete", 1], ["incomplete", 20], ["outside_calendar", 21]]
    assert not proof["historical_availability_certified"] and not proof["full_geometry"]
    assert output["candidate_counts"] == {clock: {"original": 40} for clock in ("S1", "S2")}
    assert (tmp_path / unit.OUTPUT).stat().st_mode & 0o777 == 0o400
    assert wire.file_digest(tmp_path / unit.OUTPUT, unit.MAX_OUTPUT, lambda _: None) == (
        output["output_sha256"], output["output_bytes"])


def test_changed_received_archive_is_refused_before_index_creation(tmp_path):
    received = tmp_path / "received"
    received.mkdir(mode=0o700)
    envelope = unit.make_invented(received, 1, lambda _: None)
    with (received / "CC").open("ab") as stream:
        stream.write(b"invented corruption")
    with pytest.raises(ValueError, match="received source changed"):
        unit.compute(tmp_path, envelope, None, invented_only=True, checkpoint=lambda _: None)
    assert not (tmp_path / "eh-price-byte-chunks.sqlite").exists()
