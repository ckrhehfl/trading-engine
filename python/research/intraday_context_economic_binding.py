"""Metadata-only admission and output checks for the fixed EI economic policies."""

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_economic_compute import EVALUATION_COORDINATES, INPUT_CONTRACT, OUTPUT, input_digest
from research.intraday_context_economic_predecessor import validate_economic_predecessor
from research.intraday_context_economic_unit import ECONOMIC_SCHEMA, MAXIMUM_LEDGER_BYTES
from research.intraday_context_support_unit import FULL, WINDOW


SCENARIOS = {"EI-ECONOMIC-S1": "S1", "EI-ECONOMIC-S2": "S2"}
_MODES = ("qualification", "actual")
COST_BPS = 33
INCLUDE_REENTRY = True
POLICY = {"initial_forecaster": "context_sequence", "risk_budget_won": 100_000, "cost_bps": COST_BPS,
          "entry": "same_fixed_reservation_rule_vs_positive_training_payoff_value",
          "management": "fixed_vs_chart_vs_chart_context_on_same_selected_fills",
          "reentry": "separate_first_fresh_breakout_with_original_expiry", "event_level_only": True}
POLICY_SHA256 = wire.digest(wire.encode(POLICY))


def _same(actual, expected, reason):
    wire.require(wire.encode(actual) == wire.encode(expected), "EI economic binding " + reason)


def economic_document(plan):
    wire.require(type(plan) is bridge.UnitPlan and plan.unit_id in SCENARIOS and plan.input_contract == INPUT_CONTRACT
                 and plan.local_coordinator is True and plan.local_execution_root == bridge.EI_SUPPORT_EXECUTION_ROOT
                 and plan.resource_profile == wire.EI_LOCAL_MODEL.id and plan.full_geometry == wire.encode(FULL)
                 and plan.window == wire.encode(WINDOW) and plan.output_filename == OUTPUT
                 and plan.output_max_bytes == MAXIMUM_LEDGER_BYTES,
                 "EI economic fixed plan differs")
    scenario = SCENARIOS[plan.unit_id]
    wire.require(plan.candidate_bounds == wire.encode({scenario: {"original": EVALUATION_COORDINATES}}),
                 "EI economic original denominator differs")
    raw = plan.forecast_predecessors
    wire.require(type(raw) is bytes and 0 < len(raw) <= 65536, "bounded EI economic handoff required")
    document = bridge.canonical(raw)
    wire.require(set(document) == {"predecessor", *_MODES} and document["predecessor"]["scenario"] == scenario,
                 "EI economic handoff mode or scenario differs")
    envelopes = {}
    for mode in _MODES:
        row = document[mode]
        wire.require(type(row) is dict and set(row) == {"inputs", "snapshot_summary", "decoded"}
                     and type(row["inputs"]) is list and type(row["decoded"]) is dict
                     and set(row["decoded"]) == {"CC", "CX"} and all(wire.valid_sha(x) for x in row["decoded"].values()),
                     "EI economic sealed input row differs")
        inputs = tuple(bridge.InputFile(**value) for value in row["inputs"])
        envelopes[mode] = bridge.InputEnvelope(inputs, input_digest(inputs), plan.full_geometry, INPUT_CONTRACT)
    actual = envelopes["actual"]
    wire.require(plan.input_pins == tuple(bridge.InputPin(row.name, row.original_path, row.sha256, row.maximum)
                                         for row in actual.manifest)
                 and plan.decoded_stream_sha256 == actual.stream_sha256,
                 "EI economic original inputs differ from fixed plan")
    return document, envelopes


def economic_envelope(plan, invented_only):
    wire.require(type(invented_only) is bool, "typed EI economic mode required")
    return economic_document(plan)[1]["qualification" if invented_only else "actual"]


def verify_economic_handoff(plan, *, reviewed_predecessor, original_registration, checkpoint):
    document, envelopes = economic_document(plan)
    _same(document["predecessor"], reviewed_predecessor, "reviewed predecessor differs")
    evidence = validate_economic_predecessor(reviewed_predecessor, plan.local_execution_root, original_registration,
                                             checkpoint)
    for mode in _MODES:
        audited = evidence[mode]
        wire.require(audited.files == envelopes[mode].manifest, "EI economic audited inputs differ")
        _same(document[mode]["snapshot_summary"], audited.snapshot_summary, "audited snapshot summary differs")
        _same(document[mode]["decoded"], audited.decoded, "audited raw stream differs")


def validate_economic_result(result, envelope, plan, *, invented_only):
    wire.require(envelope == economic_envelope(plan, invented_only), "EI economic result admitted envelope differs")
    scenario = SCENARIOS[plan.unit_id]
    manifest = [item.metadata() for item in envelope.manifest]
    expected = {"schema": ECONOMIC_SCHEMA, "verified": True, "full_eof_verified": True,
        "invented_only": invented_only, "input_manifest": manifest, "input_manifest_sha256": wire.digest(wire.encode(manifest)),
        "scenario": scenario, "cost_bps": COST_BPS, "include_reentry": INCLUDE_REENTRY, "folds": 5,
        "original_evaluation_coordinates": EVALUATION_COORDINATES, "initial_model": POLICY["initial_forecaster"],
        "event_level": True, "account_return": False, "execution_policy_sha256": POLICY_SHA256,
        "saved_ledger_audit_complete": False}
    proof = result["independent_verification"]
    wire.require(type(proof) is dict and set(proof) == set(expected) | {"entry_decision_seals", "training_source_sha256"}
                 and wire.valid_sha(proof["training_source_sha256"]), "EI economic proof shape differs")
    _same({key: proof[key] for key in expected}, expected, "output proof differs")
    _same(result["candidate_counts"], {scenario: {"original": EVALUATION_COORDINATES}}, "output denominator differs")
    decisions = proof["entry_decision_seals"]
    wire.require(type(decisions) is list and len(decisions) == 5
                 and all(type(row) is dict and set(row) == {"sha256", "size", "coordinates"}
                         and wire.valid_sha(row["sha256"]) and type(row["size"]) is int
                         and 0 < row["size"] <= 128 * wire.MIB and type(row["coordinates"]) is int
                         and 0 < row["coordinates"] <= EVALUATION_COORDINATES for row in decisions)
                 and sum(row["coordinates"] for row in decisions) == EVALUATION_COORDINATES,
                 "EI economic sealed decision ledger bounds differ")
