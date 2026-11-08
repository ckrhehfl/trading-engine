"""Bind EI forecast bridge metadata to an independently audited snapshot.

The bridge authenticates the reviewed registration and calls the predecessor
gate under the existing research locks before creating a marker or STARTED.
This module never opens snapshot or market payloads.
"""

import json

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_forecast_compute as compute
from research import intraday_context_forecast_predecessor as predecessor
from research import intraday_context_forecast_seal as seal
from research import intraday_context_forecast_unit as forecast
from research import intraday_context_snapshot_unit as snapshot
from research import intraday_ed_numerical as numerical
from research.intraday_context_numerical_receipt import _map
from research.intraday_context_support_unit import FULL, ORIGINAL_PIN, WINDOW


SCENARIOS = {"EI-FORECAST-S1": "S1", "EI-FORECAST-S2": "S2"}
_MODES = ("qualification", "actual")


def _need(condition, message):
    wire.require(condition, "EI forecast binding " + message)


def _same(value, expected, message):
    _need(wire.encode(value) == wire.encode(expected), message)


def original_pins(original_registration: bytes) -> tuple[bridge.InputPin, ...]:
    """Derive the CB/CC/CX pins from reviewed registration bytes, without I/O."""
    _need(type(original_registration) is bytes and 0 < len(original_registration) <= wire.MIB
          and wire.digest(original_registration) == ORIGINAL_PIN, "original registration pin differs")
    source = json.loads(original_registration)["actual_source_preparation"]
    _same(source["geometry"], FULL, "original geometry differs")
    _same(source["window"], [WINDOW["start"], WINDOW["end"]], "original window differs")
    roster = {row["name"]: row for row in source["local_received_sources"]}
    pins = []
    for name, maximum in wire.EI_SUPPORT_INPUT_BOUNDS:
        row = roster[name]
        _need(type(row["maximum"]) is int and row["maximum"] == maximum and wire.valid_sha(row["sha256"]),
              "original source pin differs")
        pins.append(bridge.InputPin(name, row["path"], row["sha256"], maximum))
    return tuple(pins)


def _bound_document(plan):
    _need(type(plan) is bridge.UnitPlan and plan.unit_id in SCENARIOS
          and plan.input_contract == compute.INPUT_CONTRACT and plan.local_coordinator is True
          and plan.local_execution_root == predecessor.NATIVE and plan.resource_profile == wire.EI_LOCAL_MODEL.id
          and plan.full_geometry == wire.encode(FULL) and plan.window == wire.encode(WINDOW)
          and plan.output_filename == compute.OUTPUT and plan.output_max_bytes == forecast.MAXIMUM_BYTES,
          "fixed plan, geometry or profile differs")
    scenario = SCENARIOS[plan.unit_id]
    _need(plan.candidate_bounds == wire.encode({scenario: {"original": compute.EVALUATION_COORDINATES}}),
          "fixed scenario denominators differ")
    raw = plan.forecast_predecessors
    _need(type(raw) is bytes and 0 < len(raw) <= 65536, "bounded handoff document required")
    document = bridge.canonical(raw)
    _need(set(document) == {"predecessor", *_MODES}, "handoff fields differ")
    pin = document["predecessor"]
    predecessor._validate_pins(pin)
    envelopes = {}
    for mode in _MODES:
        entry = document[mode]
        _need(type(entry) is dict and set(entry) == {"inputs", "summary"}
              and type(entry["inputs"]) is list and len(entry["inputs"]) == 2
              and all(type(row) is dict and set(row) == {"name", "original_path", "sha256", "maximum", "size"}
                      for row in entry["inputs"]), "handoff input metadata differs")
        inputs = tuple(bridge.InputFile(**row) for row in entry["inputs"])
        digest = compute.input_digest(inputs)
        cb, bundle = inputs
        invented = mode == "qualification"
        leaf = snapshot.UNIT_ID + ("-invented-" if invented else "-") + pin[mode + "_attempt"]
        native = plan.local_execution_root
        _need(cb.original_path == str(native / "cache" / leaf / "received" / "CB")
              and bundle.original_path == str(native / "output" / leaf / snapshot.OUTPUT)
              and (bundle.sha256, bundle.size) == (pin[mode]["output_sha256"], pin[mode]["output_bytes"]),
              "fixed snapshot mode paths or output pin differ")
        summary = entry["summary"]
        _need(type(summary) is dict and summary.get("universe_sha256") == cb.sha256
              and type(summary.get("members")) is dict and set(summary["members"]) == {"features", "labels"},
              "snapshot summary or CB pin differs")
        envelopes[mode] = bridge.InputEnvelope(inputs, digest, plan.full_geometry, compute.INPUT_CONTRACT)
    actual = envelopes["actual"]
    _need(plan.input_pins == tuple(bridge.InputPin(row.name, row.original_path, row.sha256, row.maximum)
                                  for row in actual.manifest)
          and plan.decoded_stream_sha256 == actual.stream_sha256, "plan differs from actual handoff pins")
    return scenario, document, envelopes


def _mode(invented_only: bool) -> str:
    _need(type(invented_only) is bool, "typed forecast mode required")
    return "qualification" if invented_only else "actual"


def forecast_envelope(plan: bridge.UnitPlan, invented_only: bool) -> bridge.InputEnvelope:
    """Return the exact two-file envelope for the selected mode, without I/O."""
    mode = _mode(invented_only)
    return _bound_document(plan)[2][mode]


def verify_forecast_handoff(plan: bridge.UnitPlan, *, reviewed_predecessor: dict,
                            original_registration: bytes, checkpoint) -> None:
    """Authenticate both modes before this unit's marker, under caller-held locks."""
    _, document, envelopes = _bound_document(plan)
    _same(document["predecessor"], reviewed_predecessor, "reviewed predecessor pin differs")
    evidence = predecessor.validate_snapshot_predecessor(reviewed_predecessor, plan.local_execution_root,
                                                         original_pins(original_registration), checkpoint)
    for mode in _MODES:
        audited = evidence[mode]
        _need(audited.invented_only is (mode == "qualification") and audited.inputs == envelopes[mode].manifest,
              "audited predecessor input manifest differs")
        _same(audited.summary, document[mode]["summary"], "audited predecessor summary differs")


def _validate_numerical(receipt):
    _need(type(receipt) is dict and set(receipt) == {"before", "after"}, "numerical before/after receipts required")
    before = receipt["before"]
    fixed = {"registered": numerical.ENVIRONMENT, "environment_sha256": wire.digest(wire.encode(numerical.ENVIRONMENT)),
             "manifest_sha256": numerical.MANIFEST_SHA256, "import_proof_sha256": numerical.IMPORT_PROOF_SHA256,
             "installed_file_count": numerical.INSTALLED_FILE_COUNT, "versions": numerical.VERSIONS,
             "thread_environment": numerical.THREAD_ENVIRONMENT, "bytecode_writes": False}
    _need(type(before) is dict and set(before) == set(fixed) | {"installed_file_map_sha256", "mapped_libraries",
                                                              "mapped_libraries_sha256", "runtime_sha256"},
          "numerical activation receipt fields differ")
    _same({key: before[key] for key in fixed}, fixed, "fixed numerical activation differs")
    mapped = _map(before["mapped_libraries"], absolute=True, maximum=numerical.INSTALLED_FILE_COUNT)
    _need(wire.digest(wire.encode(mapped)) == before["mapped_libraries_sha256"]
          and wire.valid_sha(before["installed_file_map_sha256"]) and wire.valid_sha(before["runtime_sha256"]),
          "numerical receipt digests differ")
    after = {key: before[key] for key in ("environment_sha256", "installed_file_count",
                                          "installed_file_map_sha256", "mapped_libraries_sha256")}
    _same(receipt["after"], {**after, "verified": True, "full_installed_roster_rehashed": True,
                            "mapped_binary_closure_unchanged": True}, "numerical after-verification differs")


def validate_forecast_result(result: dict, envelope: bridge.InputEnvelope, plan: bridge.UnitPlan, *,
                             invented_only: bool) -> None:
    """Check the EI proof after the bridge's common result checks.

    Numerical file evidence and score reconstruction remain the separate
    saved auditor's responsibility. This check opens neither native files nor
    the forecast artifact.
    """
    mode = _mode(invented_only)
    scenario, document, envelopes = _bound_document(plan)
    _need(envelope == envelopes[mode], "result does not match the admitted envelope")
    summary = document[mode]["summary"]
    manifest = [item.metadata() for item in envelope.manifest]
    fixed = {"schema": forecast.SCHEMA, "verified": True, "invented_only": invented_only,
             "full_geometry": True, "full_eof_verified": True, "all_original_coordinates_verified": True,
             "input_manifest": manifest, "input_manifest_sha256": wire.digest(wire.encode(manifest)),
             "snapshot_summary_sha256": wire.digest(wire.encode(summary)), "historical_availability_certified": False,
             "stored_volume_units_certified": False, "contains_economic_returns": False}
    proof = result.get("independent_verification")
    _need(type(proof) is dict and set(proof) == set(fixed) | {"forecast_summary", "numerical_environment"},
          "independent forecast proof fields differ")
    _same({key: proof[key] for key in fixed}, fixed, "independent forecast proof differs")
    _same(result.get("candidate_counts"), {scenario: {"original": compute.EVALUATION_COORDINATES}},
          "forecast candidate counts differ")

    measured = proof["forecast_summary"]
    fixed = {"schema": forecast.SCHEMA, "scenario": scenario, "adapter_mode": "fixed_native",
             "input_members": summary["members"], "folds": forecast.FOLD_COUNT,
             "evaluation_sessions": compute.EVALUATION_DATES, "original_evaluation_coordinates": compute.EVALUATION_COORDINATES,
             "fold_prediction_readback_verified": True, "report_reference_verified": True,
             "independent_saved_bundle_audit": False, "timing_scenarios_are_independent": False,
             "contains_economic_returns": False}
    _need(type(measured) is dict and set(measured) == set(fixed) | {"original_evaluation_questions", "learned_fit_count",
                                                                   "empirical_fit_count", "fold_seals", "io"},
          "forecast result summary fields differ")
    _same({key: measured[key] for key in fixed}, fixed, "forecast result summary differs")
    for key, maximum in (("learned_fit_count", forecast.MAX_LEARNED_FITS),
                         ("empirical_fit_count", forecast.MAX_EMPIRICAL_FITS)):
        _need(type(measured[key]) is int and 0 <= measured[key] <= maximum, "forecast fit budget differs")
    folds = measured["fold_seals"]
    _need(type(measured["original_evaluation_questions"]) is int and measured["original_evaluation_questions"] >= 0
          and type(measured["io"]) is dict and type(folds) is list and len(folds) == forecast.FOLD_COUNT
          and all(type(row) is dict and set(row) == {"schema", "sha256", "size", "records"}
                  and row["schema"] == seal.SCHEMA and wire.valid_sha(row["sha256"])
                  and type(row["size"]) is int and row["size"] > 0
                  and type(row["records"]) is int and 0 <= row["records"] <= seal.MAX_RECORDS for row in folds)
          and 0 < result["output_bytes"] - sum(row["size"] for row in folds) <= forecast.MAXIMUM_HEADER,
          "forecast fold seals, geometry or I/O summary differs")
    _validate_numerical(proof["numerical_environment"])
