"""EI end-to-end invented original archives, never actual input acquisition."""

from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_support_unit as unit


ROOT = Path(__file__).resolve().parents[2]


def prepared(tmp_path):
    generated = tmp_path / "generated"
    generated.mkdir()
    envelope = unit.make_invented(generated, 1, lambda _: None)
    work = tmp_path / "work"
    received = work / "received"
    received.mkdir(parents=True)
    work.chmod(0o700)
    for item in envelope.manifest:
        shutil.copyfile(generated / item.name, received / item.name)
    return work, envelope


def test_registration_pins_closed_original_roster():
    raw = (ROOT / "configs/research/discovery/intraday-context-support-v1.json").read_bytes()
    original = (ROOT / unit.ORIGINAL_SPEC).read_bytes()
    spec, pins, decoded = unit.contract(raw, original)
    assert spec["unit_id"] == "EI-SUPPORT-1"
    assert [pin.name for pin in pins] == ["CB", "CC", "CX"]
    assert wire.valid_sha(decoded)
    with pytest.raises(ValueError, match="original registration"):
        unit.contract(raw, original + b" ")


def test_invented_full_pipeline_checks_raw_coordinates_and_chart_cases(tmp_path):
    work, envelope = prepared(tmp_path)
    result = unit.compute(work, envelope, SimpleNamespace(local_worktree=str(ROOT)),
                          invented_only=True, checkpoint=lambda _: None)
    proof = bridge.canonical(result.independent_verification)
    assert proof["verified"] and proof["all_original_coordinates_verified"]
    assert proof["invented_only"] and not proof["full_geometry"]
    assert proof["support_totals"]["union"]["coordinates"] == 21
    assert proof["support_totals"]["S1"]["coordinates"] == 20
    assert proof["support_totals"]["S2"]["coordinates"] == 20
    assert set(proof["raw_byte_audit"]["archives"]) == {"CC", "CX"}
    classes = proof["support_totals"]["union"]["class_frames"]
    assert all(classes[kind] > 0 for kind in ("A", "B", "other", "unclassified", "unknown"))
    rows = [json.loads(line) for line in (work / unit.OUTPUT).read_bytes().splitlines()]
    assert rows[-1]["cases"] == 10
    assert not rows[-1]["summary"]["contains_outcomes_or_returns"]
    assert (work / unit.OUTPUT).stat().st_mode & 0o222 == 0


def test_received_bytes_must_still_match_before_science(tmp_path):
    work, envelope = prepared(tmp_path)
    with (work / "received" / "CX").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="received source differs"):
        unit.compute(work, envelope, SimpleNamespace(local_worktree=str(ROOT)),
                     invented_only=True, checkpoint=lambda _: None)


def test_wrong_input_contract_cannot_open_science(tmp_path):
    work, envelope = prepared(tmp_path)
    with pytest.raises(ValueError, match="input contract differs"):
        unit.compute(work, replace(envelope, input_contract=wire.EE_INPUT_CONTRACT),
                     SimpleNamespace(local_worktree=str(ROOT)), invented_only=True, checkpoint=lambda _: None)


def test_producer_decoder_corruption_is_not_shared_by_reference(tmp_path, monkeypatch):
    from research import intraday_ed_sources as sources
    work, envelope = prepared(tmp_path)
    decode = sources._session_observations

    def corrupt(*args, **kwargs):
        session, quality = decode(*args, **kwargs)
        minutes = {at: replace(row, shares=row.shares + Decimal(1))
                   if row.volume_state == "observed" else row for at, row in session.minutes.items()}
        return replace(session, minutes=minutes), quality

    monkeypatch.setattr(sources, "_session_observations", corrupt)
    with pytest.raises(ValueError):
        unit.compute(work, envelope, SimpleNamespace(local_worktree=str(ROOT)),
                     invented_only=True, checkpoint=lambda _: None)
