"""Only the audited snapshot may cross the local EH model bridge."""

from dataclasses import asdict, replace

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_scenario_forecast_handoff import SnapshotPredecessor
from test_intraday_campaign_wsl_bridge import eh_support_plan_at


def model_plan(tmp_path, scenario):
    base = eh_support_plan_at(tmp_path, snapshot=True)
    name = f"EH-MODEL-{scenario}"
    binding = bridge._EH_BINDINGS[name]
    names = tuple(sorted((set(base.source_names) - {base.specification_path}) | {
        binding.specification, binding.runtime, binding.auditor, *binding.private_dependencies}))
    invented = SnapshotPredecessor(True, "1" * 64, "2" * 64, "a" * 64, 100, "3" * 40, "4" * 40, "5" * 64, "6" * 64)
    actual = replace(invented, invented_only=False, output_sha256="b" * 64, output_bytes=101)
    original = base.local_execution_root / "output" / "EH-SNAPSHOT-1-actual1" / bridge.EH_SNAPSHOT_OUTPUT_FILENAME
    plan = replace(base, unit_id=name, specification_path=binding.specification, source_names=names,
                   source_roster_sha256=wire.digest(wire.encode(list(names))), private_entrypoint=binding.runtime,
                   input_contract=binding.input_contract, output_filename=binding.output, output_max_bytes=binding.output_max,
                   candidate_bounds=wire.encode({scenario: {"original": 3140}}), decoded_stream_sha256=actual.output_sha256,
                   forecast_predecessors=wire.encode({"invented": asdict(invented), "actual": asdict(actual)}),
                   input_pins=(bridge.InputPin("EH-SNAPSHOT", str(original), actual.output_sha256,
                                               bridge.EH_SNAPSHOT_OUTPUT_MAX_BYTES),))
    invented_path = (base.local_execution_root / "output" / "EH-SNAPSHOT-1-invented-qualification-v1"
                     / bridge.EH_SNAPSHOT_OUTPUT_FILENAME)
    envelope = bridge.InputEnvelope((bridge.InputFile("EH-SNAPSHOT", str(invented_path), invented.output_sha256,
        bridge.EH_SNAPSHOT_OUTPUT_MAX_BYTES, invented.output_bytes),), invented.output_sha256, plan.full_geometry,
        binding.input_contract)
    return plan, envelope


@pytest.mark.parametrize("scenario", ("S1", "S2"))
def test_model_plan_keeps_one_pinned_audited_native_input(tmp_path, scenario):
    plan, invented = model_plan(tmp_path, scenario)
    bridge.validate_plan(plan)
    assert bridge.profile_for_plan(plan) is wire.ORDINARY300
    bridge._invented_envelope(invented, plan, 257)
    actual = replace(invented, stream_sha256=plan.decoded_stream_sha256,
                     manifest=(bridge.InputFile(**plan.input_pins[0].metadata(101)),))
    bridge._actual_envelope(actual, plan)
    with pytest.raises(ValueError, match="size differs"):
        bridge._actual_envelope(replace(actual, manifest=(replace(actual.manifest[0], size=100),)), plan)
    with pytest.raises(ValueError, match="invented forecast predecessor differs"):
        bridge._invented_envelope(actual, plan, 257)
    with pytest.raises(ValueError, match="ordinary300 pairing"):
        bridge.profile_for_plan(replace(plan, input_contract=wire.EH_SUPPORT_INPUT_CONTRACT))
    with pytest.raises(ValueError, match="actual input|predecessor identity"):
        bridge.validate_plan(replace(plan, input_pins=(replace(plan.input_pins[0], original_path="/native/wrong"),)))


def test_model_verification_preserves_the_registered_population(tmp_path):
    plan, envelope = model_plan(tmp_path, "S2")
    manifest = envelope.payload()["inputs"]
    summary = {"schema": bridge.EH_FORECAST_OUTPUT_SCHEMA, "scenario": "S2", "folds": 5, "evaluation_sessions": 157,
               "original_evaluation_coordinates": 3140, "full_forecast_and_score_verification": True}
    proof = {"schema": bridge.EH_FORECAST_OUTPUT_SCHEMA, "verified": True, "invented_only": True, "full_geometry": True,
             "full_eof_verified": True, "all_original_coordinates_verified": True, "input_manifest": manifest,
             "input_manifest_sha256": wire.digest(wire.encode(manifest)), "numpy_manifest_sha256": "a" * 64,
             "forecast_summary": summary}
    result = bridge.OutputReceipt("9" * 64, 10, plan.full_geometry, wire.encode(proof), plan.candidate_bounds).payload()
    bridge.validate_result(result, envelope, plan, invented_only=True)
    for change in ({"scenario": "S1"}, {"evaluation_sessions": 156}, {"full_forecast_and_score_verification": False}):
        result["independent_verification"] = {**proof, "forecast_summary": {**summary, **change}}
        with pytest.raises(ValueError, match="forecast full prediction"):
            bridge.validate_result(result, envelope, plan, invented_only=True)
