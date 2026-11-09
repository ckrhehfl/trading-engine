"""Verify that the economic worker supplies the actual-admission proof fields."""

from contextlib import nullcontext
from types import SimpleNamespace

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_economic_compute as economic
from research.intraday_context_economic_fold import DecisionSeal
from research.intraday_scenario_snapshot_unit import BundleSeal


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
