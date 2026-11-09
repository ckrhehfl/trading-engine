"""Verify that the economic worker supplies the actual-admission proof fields."""

from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_economic_binding as binding
from research import intraday_context_economic_compute as economic
from research.intraday_context_economic_fold import DecisionSeal
from research.intraday_scenario_snapshot_unit import BundleSeal


def test_s1_recovery_changes_only_admission_identity_and_retains_original_closed_gate():
    root = Path(__file__).resolve().parents[2]
    unit = bridge.EI_ECONOMIC_S1_RECOVERY_UNIT_ID
    registered = bridge._EH_BINDINGS[unit]
    old_binding = bridge._EH_BINDINGS["EI-ECONOMIC-S1"]
    original = json.loads((root / old_binding.specification).read_text(encoding="utf-8"))
    recovery = json.loads((root / registered.specification).read_text(encoding="utf-8"))
    identity = {"id", "unit_id", "actual_runner_ready", "private_source_pins", "source_roster_sha256"}
    assert {k: v for k, v in recovery.items() if k not in identity} == {
        k: v for k, v in original.items() if k not in identity
    }
    assert original["actual_runner_ready"] is False
    assert recovery["actual_runner_ready"] is True
    assert recovery["unit_id"] == unit
    assert registered.scenario == binding.SCENARIOS[unit] == "S1"
    assert registered.private_dependencies == old_binding.private_dependencies
    handoff = bridge.EI_ECONOMIC_HANDOFF.format("s1")
    assert recovery["private_source_pins"][handoff] == original["private_source_pins"][handoff]
    assert {registered.runtime, registered.auditor} <= recovery["private_source_pins"].keys()
    assert {old_binding.runtime, old_binding.auditor}.isdisjoint(recovery["private_source_pins"])


def test_s1_recovery_origin_rejects_changed_evidence_or_original_publication(tmp_path, monkeypatch):
    name = "launches/EI-ECONOMIC-S1/actual1/launch.json"
    launch = tmp_path / name
    launch.parent.mkdir(parents=True)
    launch.write_bytes(b"invented preserved launch")
    monkeypatch.setattr(binding, "_S1_FAILED_ATTEMPT", {name: wire.digest(launch.read_bytes())})
    binding.verify_s1_recovery_origin(tmp_path)
    publication = tmp_path / "output/EI-ECONOMIC-S1-actual1/publication.json"
    publication.parent.mkdir(parents=True)
    publication.write_bytes(b"unexpected publication")
    with pytest.raises(ValueError, match="already has a publication"):
        binding.verify_s1_recovery_origin(tmp_path)
    launch.write_bytes(b"changed launch")
    with pytest.raises(ValueError, match="failure evidence differs"):
        binding.verify_s1_recovery_origin(tmp_path)


def test_whole_invented_worker_proof_attests_the_common_qualification_fields(monkeypatch, tmp_path):
    inputs = tuple(bridge.InputFile(name, f"/invented/{name}", str(index + 1) * 64, maximum, 1)
                   for index, (name, maximum) in enumerate(economic.INPUT_BOUNDS))
    envelope = bridge.InputEnvelope(inputs, economic.input_digest(inputs), wire.encode(economic.FULL), economic.INPUT_CONTRACT)
    files = {item.name: (item.sha256, item.size) for item in inputs}
    monkeypatch.setattr(wire, "file_digest", lambda path, *_: files[path.name])
    monkeypatch.setattr(bridge, "bounded_read", lambda *_: b"invented")
    monkeypatch.setattr(economic.sources, "load_universe", lambda *_, **__: object())
    monkeypatch.setattr(economic.sources.ByteChunkIndex, "build", lambda *_: {"archives": {}, "sha256": "a" * 64})
    monkeypatch.setattr(economic.sources, "audit_index", lambda *_, **__: nullcontext(
        SimpleNamespace(assert_unchanged=lambda: None)))
    outcome = SimpleNamespace(report={"entry": {"original_evaluation_coordinates": 3140}},
        folds=tuple(SimpleNamespace(decision_seal=DecisionSeal("b" * 64, 10, 628)) for _ in range(5)))
    monkeypatch.setattr(economic, "compare_economic_artifacts", lambda *_, **__: outcome)
    monkeypatch.setattr(economic, "write_economic_ledger", lambda *_, **__: BundleSeal("c" * 64, 10))

    receipt = economic.compute_received_economics(tmp_path, envelope, expected_inputs=inputs,
        original_decoded_sha256={"CC": "d" * 64, "CX": "e" * 64}, snapshot_summary={}, scenario="S2", cost_bps=33,
        execution_policy_sha256="f" * 64, invented_only=True, include_reentry=True, checkpoint=lambda _: None)
    proof = receipt.payload()["independent_verification"]
    for name in ("verified", "invented_only", "full_eof_verified", "all_original_coordinates_verified", "full_geometry"):
        assert proof[name] is True
