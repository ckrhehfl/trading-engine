"""Invented-only EI forecast binding tests before bridge admission."""

from dataclasses import replace
import os
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_forecast_binding as binding
from research import intraday_context_forecast_compute as compute
from research import intraday_context_forecast_predecessor as predecessor
from research import intraday_context_forecast_seal as seal
from research import intraday_context_forecast_unit as forecast
from research import intraday_ed_numerical as numerical
from test_intraday_campaign_wsl_bridge import plan_at
from test_intraday_context_forecast_predecessor import _invent


@pytest.fixture
def forecast_case(tmp_path, monkeypatch):
    pin, original, paths = _invent(tmp_path, monkeypatch)
    evidence = predecessor.validate_snapshot_predecessor(pin, tmp_path, original, lambda _: None)
    document = {"predecessor": pin, **{mode: {"inputs": [item.metadata() for item in value.inputs],
                "summary": value.summary} for mode, value in evidence.items()}}
    actual = evidence["actual"].inputs
    plan = replace(plan_at(tmp_path, tmp_path), unit_id="EI-FORECAST-S1", output_filename=compute.OUTPUT,
        output_max_bytes=forecast.MAXIMUM_BYTES, input_contract=compute.INPUT_CONTRACT, local_coordinator=True,
        local_execution_root=tmp_path, resource_profile=wire.EI_LOCAL_MODEL.id, full_geometry=wire.encode(compute.FULL),
        window=wire.encode(binding.WINDOW), candidate_bounds=wire.encode({"S1": {"original": compute.EVALUATION_COORDINATES}}),
        input_pins=tuple(bridge.InputPin(item.name, item.original_path, item.sha256, item.maximum) for item in actual),
        decoded_stream_sha256=compute.input_digest(actual), forecast_predecessors=wire.encode(document))
    metadata = wire.encode({"actual_source_preparation": {"geometry": compute.FULL,
        "window": [binding.WINDOW["start"], binding.WINDOW["end"]],
        "local_received_sources": [{"name": row.name, "path": row.original_path,
                                    "sha256": row.sha256, "maximum": row.maximum} for row in original]}})
    monkeypatch.setattr(binding, "ORIGINAL_PIN", wire.digest(metadata))
    return plan, document, evidence, paths, metadata


def test_both_envelopes_and_audited_gate_bind_receipts_without_reading_bodies(forecast_case, monkeypatch):
    plan, document, evidence, paths, metadata = forecast_case
    protected = {path for name, path in paths.items() if name.endswith((".cb", ".bundle"))}
    for owner, name in ((Path, "open"), (os, "open")):
        original = getattr(owner, name)

        def observed(path, *args, original=original, **kwargs):
            assert isinstance(path, int) or Path(path) not in protected
            return original(path, *args, **kwargs)

        monkeypatch.setattr(owner, name, observed)
    stages = []
    for invented, mode in ((True, "qualification"), (False, "actual")):
        envelope = binding.forecast_envelope(plan, invented)
        assert envelope.manifest == evidence[mode].inputs
        assert envelope.stream_sha256 == compute.input_digest(evidence[mode].inputs)
    binding.verify_forecast_handoff(plan, reviewed_predecessor=document["predecessor"],
                                    original_registration=metadata, checkpoint=stages.append)
    assert stages[-1] == "EI_snapshot_predecessor_complete"


def test_handoff_refuses_wrong_mode_path_and_original_registration(forecast_case):
    plan, document, _, _, metadata = forecast_case
    with pytest.raises(ValueError, match="registration pin"):
        binding.verify_forecast_handoff(plan, reviewed_predecessor=document["predecessor"],
                                        original_registration=metadata + b" ", checkpoint=lambda _: None)
    document["qualification"]["inputs"][0] = document["actual"]["inputs"][0]
    wrong = replace(plan, forecast_predecessors=wire.encode(document))
    with pytest.raises(ValueError, match="mode paths"):
        binding.forecast_envelope(wrong, True)


def test_gate_compares_the_unselected_mode_with_its_audited_summary(forecast_case):
    plan, document, _, _, metadata = forecast_case
    document["qualification"]["summary"]["calendar_sha256"] = "0" * 64
    wrong = replace(plan, forecast_predecessors=wire.encode(document))
    with pytest.raises(ValueError, match="audited predecessor summary"):
        binding.verify_forecast_handoff(wrong, reviewed_predecessor=document["predecessor"],
                                        original_registration=metadata, checkpoint=lambda _: None)


def _result(envelope, summary):
    manifest = [item.metadata() for item in envelope.manifest]
    mapped = {"/invented/lib.so": "a" * 64}
    digests = {"environment_sha256": wire.digest(wire.encode(numerical.ENVIRONMENT)),
               "installed_file_count": numerical.INSTALLED_FILE_COUNT, "installed_file_map_sha256": "b" * 64,
               "mapped_libraries_sha256": wire.digest(wire.encode(mapped))}
    before = {**digests, "registered": numerical.ENVIRONMENT, "manifest_sha256": numerical.MANIFEST_SHA256,
              "import_proof_sha256": numerical.IMPORT_PROOF_SHA256, "mapped_libraries": mapped,
              "versions": numerical.VERSIONS, "thread_environment": numerical.THREAD_ENVIRONMENT,
              "bytecode_writes": False, "runtime_sha256": "c" * 64}
    after = {**digests, "verified": True, "full_installed_roster_rehashed": True, "mapped_binary_closure_unchanged": True}
    proof = {"schema": forecast.SCHEMA, "verified": True, "invented_only": False,
             "full_geometry": True, "full_eof_verified": True, "all_original_coordinates_verified": True,
             "input_manifest": manifest, "input_manifest_sha256": wire.digest(wire.encode(manifest)),
             "snapshot_summary_sha256": wire.digest(wire.encode(summary)), "historical_availability_certified": False,
             "stored_volume_units_certified": False, "contains_economic_returns": False,
             "numerical_environment": {"before": before, "after": after},
             "forecast_summary": {"schema": forecast.SCHEMA, "scenario": "S1", "adapter_mode": "fixed_native",
                 "input_members": summary["members"], "folds": 5, "evaluation_sessions": 157,
                 "original_evaluation_coordinates": 3140, "original_evaluation_questions": 0,
                 "fold_prediction_readback_verified": True, "report_reference_verified": True,
                 "independent_saved_bundle_audit": False, "timing_scenarios_are_independent": False,
                 "contains_economic_returns": False, "learned_fit_count": 0, "empirical_fit_count": 0, "io": {},
                 "fold_seals": [{"schema": seal.SCHEMA, "sha256": str(i) * 64, "size": 10, "records": 5}
                                for i in range(5)]}}
    return {"independent_verification": proof, "candidate_counts": {"S1": {"original": 3140}}, "output_bytes": 80}


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda row: row["independent_verification"].update(coordinator_prior_snapshot={}), "proof fields"),
    (lambda row: row["independent_verification"].update(snapshot_summary_sha256="0" * 64), "proof differs"),
    (lambda row: row["independent_verification"]["forecast_summary"].update(scenario="S2"), "summary differs"),
    (lambda row: row["independent_verification"]["forecast_summary"].update(learned_fit_count=True), "fit budget"),
    (lambda row: row["independent_verification"]["numerical_environment"]["after"].update(verified=1), "after-verification"),
    (lambda row: row.update(output_bytes=50), "fold seals"),
])
def test_result_proof_rejects_changed_bindings(forecast_case, mutation, message):
    plan, document, _, _, _ = forecast_case
    envelope = binding.forecast_envelope(plan, False)
    result = _result(envelope, document["actual"]["summary"])
    binding.validate_forecast_result(result, envelope, plan, invented_only=False)
    mutation(result)
    with pytest.raises(ValueError, match=message):
        binding.validate_forecast_result(result, envelope, plan, invented_only=False)
