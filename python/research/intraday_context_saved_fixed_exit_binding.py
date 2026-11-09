"""Fixed local admission and compact receipt checks for the EI saved comparison."""

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_campaign_ml_labels import FOLD_SLICES
from research.intraday_context_saved_fixed_exit_predecessor import SCHEMA as PREDECESSOR_SCHEMA
from research.intraday_context_saved_fixed_exit_predecessor import validate_saved_fixed_exit_predecessor
from research.intraday_context_saved_fixed_exit_unit import (
    GEOMETRY, OUTPUT, OUTPUT_SCHEMA, SCENARIO, saved_fixed_exit_input_digest,
)
from research.intraday_context_support_unit import WINDOW


UNIT = "EI-SAVED-FIXED-EXIT-S2"
CONTRACT = wire.EI_SAVED_FIXED_EXIT_INPUT_CONTRACT
BOUNDS = wire.EI_SAVED_FIXED_EXIT_INPUT_BOUNDS
MAXIMUM = 128 * wire.MIB
QUALIFICATION = {"attempt": "qualification-v1", "full_geometry_required": True,
                 "independent_saved_receipt_audit_required": True}
POLICY = {"original_forecaster": "context_sequence", "replacement_forecaster": "context_lightgbm",
          "cost_bps": 33, "entry": "same_fixed_reservation_and_positive_training_payoff_value",
          "exit": "same_request_audited_fixed_exit", "event_level_only": True, "account_return": False}
POLICY_SHA256 = wire.digest(wire.encode(POLICY))
COUNTS = {SCENARIO: {"original": GEOMETRY["evaluation_coordinates"]}}
_MODES = ("qualification", "actual")
_FOLD_COUNTS = tuple(20 * (end - start) for _, _, start, end in FOLD_SLICES)


def _same(left, right, message):
    wire.require(wire.encode(left) == wire.encode(right), "EI saved fixed-exit binding " + message)


def _mode(invented_only):
    wire.require(type(invented_only) is bool, "EI saved fixed-exit mode required")
    return "qualification" if invented_only else "actual"


def _seal_matches(seal, item, coordinates):
    return (type(seal) is dict and set(seal) == {"sha256", "size", "coordinates"}
            and wire.valid_sha(seal["sha256"]) and type(seal["size"]) is int and 0 < seal["size"] <= MAXIMUM
            and type(seal["coordinates"]) is int and seal["coordinates"] == coordinates
            and (item is None or (seal["sha256"], seal["size"]) == (item.sha256, item.size)))


def saved_fixed_exit_document(plan):
    """Validate the registered manifests without inspecting any payload paths."""
    wire.require(type(plan) is bridge.UnitPlan and plan.unit_id == UNIT and plan.input_contract == CONTRACT
                 and plan.local_coordinator is True and plan.local_execution_root == bridge.EI_SUPPORT_EXECUTION_ROOT
                 and plan.resource_profile == wire.EI_LOCAL_MODEL.id and plan.full_geometry == wire.encode(GEOMETRY)
                 and plan.window == wire.encode(WINDOW) and plan.output_filename == OUTPUT
                 and plan.output_max_bytes == MAXIMUM and plan.candidate_bounds == wire.encode(COUNTS),
                 "EI saved fixed-exit fixed plan differs")
    raw = plan.forecast_predecessors
    wire.require(type(raw) is bytes and 0 < len(raw) <= 65536, "EI saved fixed-exit bounded handoff required")
    document = bridge.canonical(raw)
    wire.require(set(document) == {"predecessor", *_MODES} and type(document["predecessor"]) is dict
                 and document["predecessor"].get("schema") == PREDECESSOR_SCHEMA
                 and type(document["predecessor"].get("attempts")) is dict
                 and set(document["predecessor"]["attempts"]) == set(_MODES), "EI saved fixed-exit handoff shape differs")
    envelopes = {}
    for mode in _MODES:
        row = document[mode]
        wire.require(type(row) is dict and set(row) == {"inputs", "decision_seals"}, "EI saved fixed-exit handoff fields differ")
        wire.validate_registered_input_manifest(row["inputs"], input_contract=CONTRACT)
        files = tuple(bridge.InputFile(**item) for item in row["inputs"])
        seals = row["decision_seals"]
        wire.require(type(seals) is list and len(seals) == len(_FOLD_COUNTS)
                     and all(_seal_matches(seal, item, count)
                             for seal, item, count in zip(seals, files[3:-1], _FOLD_COUNTS, strict=True)),
                     "EI saved fixed-exit predecessor decision seals differ")
        envelopes[mode] = bridge.InputEnvelope(files, saved_fixed_exit_input_digest(files), plan.full_geometry, CONTRACT)
    actual = envelopes["actual"]
    wire.require(actual.manifest != envelopes["qualification"].manifest
                 and plan.input_pins == tuple(bridge.InputPin(item.name, item.original_path, item.sha256, item.maximum)
                                              for item in actual.manifest)
                 and plan.decoded_stream_sha256 == actual.stream_sha256, "EI saved fixed-exit actual plan differs")
    return document, envelopes


def saved_fixed_exit_envelope(plan, invented_only):
    return saved_fixed_exit_document(plan)[1][_mode(invented_only)]


def verify_saved_fixed_exit_registration(specification, plan, handoff_raw, *, invented_only):
    _mode(invented_only)
    wire.require(handoff_raw == plan.forecast_predecessors and type(plan.actual_runner_ready) is bool
                 and (invented_only or plan.actual_runner_ready), "EI saved fixed-exit registered mode or handoff differs")
    document, _ = saved_fixed_exit_document(plan)
    fixed = {"unit_id": UNIT, "scenario": SCENARIO, "source_roster_sha256": plan.source_roster_sha256,
             "input_roster": [list(row) for row in BOUNDS], "geometry": GEOMETRY,
             "predecessor_sha256": wire.digest(wire.encode(document["predecessor"])), "qualification": QUALIFICATION,
             "execution_policy": POLICY, "execution_policy_sha256": POLICY_SHA256,
             "output": {"filename": OUTPUT, "schema": OUTPUT_SCHEMA, "maximum_bytes": MAXIMUM},
             "live_or_paper_order_capable": False}
    _same({key: specification.get(key) for key in fixed}, fixed, "reviewed registration differs")


def verify_saved_fixed_exit_handoff(plan, *, invented_only, checkpoint):
    mode = _mode(invented_only)
    document, envelopes = saved_fixed_exit_document(plan)
    files, snapshot_summary = validate_saved_fixed_exit_predecessor(document["predecessor"], plan.local_execution_root,
        invented_only=invented_only, checkpoint=checkpoint)
    wire.require(files == envelopes[mode].manifest, "EI saved fixed-exit audited artifacts differ from the registered handoff")
    _same(snapshot_summary, document["predecessor"]["attempts"][mode]["snapshot_summary"], "audited snapshot summary differs")


def validate_saved_fixed_exit_proof(result, envelope, snapshot_summary, *, invented_only):
    _mode(invented_only)
    _same(result["candidate_counts"], COUNTS, "result population differs")
    _same(result["geometry"], GEOMETRY, "result geometry differs")
    wire.require(result["verification_complete"] is True and wire.valid_sha(result["output_sha256"])
                 and type(result["output_bytes"]) is int and 0 < result["output_bytes"] <= MAXIMUM,
                 "EI saved fixed-exit output receipt differs")
    expected = {"schema": OUTPUT_SCHEMA, "verified": True, "full_eof_verified": True, "full_geometry": True,
                "all_original_coordinates_verified": True, "invented_only": invented_only, "scenario": SCENARIO,
                "cost_bps": 33, "replacement": "context_lightgbm", "input_manifest_sha256": envelope.stream_sha256,
                "snapshot_summary_sha256": wire.digest(wire.encode(snapshot_summary)), "account_return": False}
    proof = result["independent_verification"]
    wire.require(type(proof) is dict and set(proof) == set(expected) | {"decision_seal", "causal_replay"},
                 "EI saved fixed-exit result proof fields differ")
    _same({key: proof[key] for key in expected}, expected, "output proof differs")
    seal, causal = proof["decision_seal"], proof["causal_replay"]
    coordinates = GEOMETRY["evaluation_coordinates"]
    wire.require(_seal_matches(seal, None, coordinates) and type(causal) is dict
                 and set(causal) == {"verified", "coordinates", "questions", "decision_sha256"}
                 and type(causal["questions"]) is int and causal["questions"] > 0,
                 "EI saved fixed-exit causal replay or decision seal differs")
    _same({key: value for key, value in causal.items() if key != "questions"},
          {"verified": True, "coordinates": coordinates, "decision_sha256": seal["sha256"]}, "causal proof differs")


def validate_saved_fixed_exit_result(result, envelope, plan, *, invented_only):
    mode = _mode(invented_only)
    document, envelopes = saved_fixed_exit_document(plan)
    wire.require(envelope == envelopes[mode], "EI saved fixed-exit result admitted envelope differs")
    validate_saved_fixed_exit_proof(result, envelope, document["predecessor"]["attempts"][mode]["snapshot_summary"],
                                    invented_only=invented_only)
