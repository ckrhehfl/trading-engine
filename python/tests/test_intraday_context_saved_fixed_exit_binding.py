"""Invented admission and worker proof checks for the saved S2 comparison."""

from dataclasses import replace

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_saved_fixed_exit_binding as binding
from test_intraday_campaign_wsl_bridge import plan_at


@pytest.fixture
def admission(tmp_path):
    native = bridge.EI_SUPPORT_EXECUTION_ROOT
    summaries = {"qualification": {"invented": True}, "actual": {"invented": False}}
    document = {"predecessor": {"schema": binding.PREDECESSOR_SCHEMA,
                "attempts": {key: {"snapshot_summary": row} for key, row in summaries.items()}}}
    for mode, letter in (("qualification", "a"), ("actual", "b")):
        inputs = [{"name": name, "original_path": f"/invented/{mode}/{name}", "sha256": letter * 64,
                   "size": 12, "maximum": maximum} for name, maximum in binding.BOUNDS]
        document[mode] = {"inputs": inputs, "decision_seals": [
            {"sha256": row["sha256"], "size": row["size"], "coordinates": count}
            for row, count in zip(inputs[3:-1], binding._FOLD_COUNTS, strict=True)]}
    actual = tuple(bridge.InputFile(**row) for row in document["actual"]["inputs"])
    plan = replace(plan_at(tmp_path, tmp_path), unit_id=binding.UNIT, input_contract=binding.CONTRACT,
        local_coordinator=True, local_execution_root=native, resource_profile=wire.EI_LOCAL_MODEL.id,
        full_geometry=wire.encode(binding.GEOMETRY), window=wire.encode(binding.WINDOW), output_filename=binding.OUTPUT,
        output_max_bytes=binding.MAXIMUM, candidate_bounds=wire.encode(binding.COUNTS), actual_runner_ready=True,
        forecast_predecessors=wire.encode(document), decoded_stream_sha256=binding.saved_fixed_exit_input_digest(actual),
        input_pins=tuple(bridge.InputPin(row.name, row.original_path, row.sha256, row.maximum) for row in actual))
    return plan, document


def test_handoff_uses_only_the_selected_audited_mode(admission, monkeypatch):
    plan, document = admission
    calls = []

    def inspect(pin, native, *, invented_only, checkpoint):
        assert pin == document["predecessor"] and native == plan.local_execution_root
        calls.append(invented_only)
        mode = "qualification" if invented_only else "actual"
        return (tuple(bridge.InputFile(**row) for row in document[mode]["inputs"]),
                pin["attempts"][mode]["snapshot_summary"])

    monkeypatch.setattr(binding, "validate_saved_fixed_exit_predecessor", inspect)
    binding.verify_saved_fixed_exit_handoff(plan, invented_only=True, checkpoint=lambda _: None)
    assert calls == [True]
    binding.verify_saved_fixed_exit_handoff(plan, invented_only=False, checkpoint=lambda _: None)
    assert calls == [True, False]


def test_rehashed_handoff_with_wrong_fold_seal_cannot_be_used(admission):
    plan, document = admission
    document["qualification"]["decision_seals"][0]["coordinates"] -= 1
    with pytest.raises(ValueError, match="predecessor decision seals differ"):
        binding.saved_fixed_exit_envelope(replace(plan, forecast_predecessors=wire.encode(document)), True)


def _result(envelope, summary):
    seal = {"sha256": "d" * 64, "coordinates": 3140, "size": 100}
    return {"output_sha256": "e" * 64, "output_bytes": 200, "geometry": binding.GEOMETRY,
            "verification_complete": True, "candidate_counts": binding.COUNTS, "independent_verification": {
                "schema": binding.OUTPUT_SCHEMA, "verified": True, "full_eof_verified": True, "full_geometry": True,
                "all_original_coordinates_verified": True, "invented_only": True, "scenario": "S2", "cost_bps": 33,
                "replacement": "context_lightgbm", "input_manifest_sha256": envelope.stream_sha256, "account_return": False,
                "snapshot_summary_sha256": wire.digest(wire.encode(summary)), "decision_seal": seal,
                "causal_replay": {"verified": True, "coordinates": 3140, "questions": 1794, "decision_sha256": seal["sha256"]}}}


@pytest.mark.parametrize("corrupt", (None, "mode", "input", "causal_seal", "geometry"))
def test_result_proves_same_mode_geometry_input_and_independent_decision_seal(admission, corrupt):
    plan, document = admission
    envelope = binding.saved_fixed_exit_envelope(plan, True)
    summary = document["predecessor"]["attempts"]["qualification"]["snapshot_summary"]
    result = _result(envelope, summary)
    proof = result["independent_verification"]
    if corrupt is None:
        binding.validate_saved_fixed_exit_result(result, envelope, plan, invented_only=True)
        return
    if corrupt == "mode":
        proof["invented_only"] = False
    elif corrupt == "input":
        proof["input_manifest_sha256"] = "f" * 64
    elif corrupt == "causal_seal":
        proof["causal_replay"]["decision_sha256"] = "f" * 64
    else:
        result["geometry"] = {**binding.GEOMETRY, "evaluation_coordinates": 3139}
    with pytest.raises(ValueError):
        binding.validate_saved_fixed_exit_result(result, envelope, plan, invented_only=True)
