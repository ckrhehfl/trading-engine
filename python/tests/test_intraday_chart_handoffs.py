from dataclasses import replace

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_chart_handoffs as handoff


def _published(root, unit, invented, *, snapshot=None):
    auditor = bridge.EF_RECEIPT_AUDITOR if snapshot is None else bridge.EF_COMPARISON_AUDITOR
    sources = {auditor: "a" * 64}
    out = root / "output" / handoff.unit_leaf(unit, invented)
    launches = handoff.launch_dir(root, unit, invented)
    out.mkdir(parents=True)
    launches.mkdir(parents=True)
    verification = {"verified": True}
    seal = ({"fingerprint": "b" * 64, "features_sha256": "c" * 64, "labels_sha256": "d" * 64}
            if snapshot is None else {"artifact_sha256": "e" * 64, "input_sha256": "f" * 64})
    receipt = {"seal": seal, "independent_verification": verification}
    if snapshot is not None:
        receipt["binding"] = {"snapshot_receipt_sha256": snapshot.receipt_sha256,
                              "snapshot_seal": snapshot.receipt["seal"]}
    raw = wire.encode(receipt)
    receipt_pin = wire.digest(raw)
    (out / handoff.receipt_name(unit)).write_bytes(raw)
    pub = wire.encode({"unit_id": unit, "status": "completed", "invented_only": invented,
                       "full_bridge": True, "local_pipe_test": False, "execution_host": bridge.LOCAL_HOST,
                       "local_execution_root": str(root), "source_hashes": sources, "output_sha256": receipt_pin,
                       "output_bytes": len(raw), "verification_complete": True, "independent_verification": verification})
    (out / "publication.json").write_bytes(pub)
    (launches / "original-receipt-audit.json").write_bytes(wire.encode({
        "status": "PASS", "invented_only": invented, "science_reexecuted": False,
        "publication_sha256": wire.digest(pub), "receipt_sha256": receipt_pin, "seal": seal,
        "auditor_source_sha256": sources[auditor], "source_manifest_sha256": wire.digest(wire.encode(sources)),
    }))
    return handoff.audited_artifact(root, unit, invented)


def test_fit_descriptor_keeps_lineage_and_never_needs_bodies(tmp_path):
    invented = _published(tmp_path, bridge.EF_UNIT_ID, True)
    actual = _published(tmp_path, bridge.EF_UNIT_ID, False)
    unit = next(iter(bridge.EF_FIT_UNITS))
    raw = handoff.make_handoff(tmp_path, unit, True)
    assert handoff.resolve_handoff(raw, tmp_path, unit, True) == (invented,)
    assert not invented.cache.exists() and not actual.cache.exists()
    with pytest.raises(ValueError, match="mixes invented and actual"):
        handoff.resolve_handoff(raw, tmp_path, unit, False)
    (tmp_path / "canonical").mkdir()
    (tmp_path / "canonical/ef-unresolved.json").touch()
    with pytest.raises(ValueError, match="unresolved EF campaign"):
        handoff.make_handoff(tmp_path, unit, False)


def test_report_rejects_a_fit_bound_to_a_different_snapshot(tmp_path):
    snapshot = _published(tmp_path, bridge.EF_UNIT_ID, True)
    for index, unit in enumerate(bridge.EF_FIT_UNITS):
        _published(tmp_path, unit, True, snapshot=replace(snapshot, receipt_sha256="1" * 64) if index == 3 else snapshot)
    with pytest.raises(ValueError, match="different snapshot"):
        handoff.make_handoff(tmp_path, bridge.EF_REPORT_UNIT, True)


def test_descriptor_refuses_changed_original_receipt(tmp_path):
    _published(tmp_path, bridge.EF_UNIT_ID, True)
    unit = next(iter(bridge.EF_FIT_UNITS))
    raw = handoff.make_handoff(tmp_path, unit, True)
    output = tmp_path / "output" / handoff.unit_leaf(bridge.EF_UNIT_ID, True)
    (output / "publication.json").write_bytes(wire.encode({"status": "other"}))
    with pytest.raises(ValueError, match="upstream audit, scope or receipt differs"):
        handoff.resolve_handoff(raw, tmp_path, unit, True)
