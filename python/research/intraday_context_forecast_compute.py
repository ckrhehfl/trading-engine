"""Worker-side EI model handoff from an authenticated snapshot and CB roster.

This is source-only integration, not an execution authorization. The caller
must authenticate the predecessor receipts, fix these inputs in the registered
transport, and enter the budgeted worker after STARTED. No raw CC/CX archive,
predecessor discovery, package installation or source fallback occurs here.
"""

from dataclasses import asdict
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_forecast_unit as forecasts
from research import intraday_context_snapshot_unit as snapshots
from research import intraday_ed_numerical as numerical
from research import intraday_ed_sources as sources
from research.intraday_context_support_unit import FULL


INPUT_CONTRACT = "ei-forecast-audited-snapshot-and-cb-v1"
INPUT_BOUNDS = (("CB", 16 * wire.MIB), ("EI-SNAPSHOT", snapshots.MAX_OUTPUT))
OUTPUT = "ei-context-forecast.bundle"
EVALUATION_DATES = 157
EVALUATION_COORDINATES = 3140


def _need(condition, reason):
    wire.require(condition, "EI model compute " + reason)


def input_digest(inputs: tuple[bridge.InputFile, ...]) -> str:
    """Identity of the two received files, not a claimed decoded market stream."""
    _need(type(inputs) is tuple and len(inputs) == 2, "two fixed input files required")
    rows = []
    for item, (name, maximum) in zip(inputs, INPUT_BOUNDS, strict=True):
        _need(type(item) is bridge.InputFile and item.name == name
              and type(item.maximum) is int and item.maximum == maximum
              and type(item.size) is int and 0 < item.size <= maximum
              and type(item.original_path) is str and Path(item.original_path).is_absolute()
              and wire.valid_sha(item.sha256), "bounded input metadata differs")
        rows.append({"name": name, "sha256": item.sha256, "size": item.size})
    return wire.digest(wire.encode(rows))


def compute_received(directory: Path, envelope: bridge.InputEnvelope, *, scenario: str,
                     expected_inputs: tuple[bridge.InputFile, ...], expected_members: dict,
                     expected_summary: dict, environment: dict, invented_only: bool,
                     checkpoint) -> bridge.OutputReceipt:
    """Authenticate inputs before numerical import, then seal fixed forecasts.

    ``expected_*`` must come from the separately authenticated original snapshot
    publication, not from a result header supplied by this worker. The public
    transport has no EI forecast admission yet; this function cannot launch one.
    It has no test-adapter parameter, including for invented qualifications.
    """
    _need(isinstance(directory, Path), "worker directory required")
    _need(type(invented_only) is bool and scenario in ("S1", "S2") and callable(checkpoint),
          "typed mode and fixed scenario required")
    registered = numerical.validate_environment(environment)
    stream_pin = input_digest(expected_inputs)
    _need(type(envelope) is bridge.InputEnvelope and envelope.manifest == expected_inputs
          and envelope.input_contract == INPUT_CONTRACT and envelope.stream_sha256 == stream_pin
          and envelope.geometry == wire.encode(FULL), "received envelope differs")
    _need(type(expected_summary) is dict and type(expected_members) is dict
          and set(expected_members) == {"features", "labels"}
          and expected_summary.get("members") == expected_members,
          "audited snapshot summary and members required")

    paths = {item.name: directory / "received" / item.name for item in expected_inputs}
    for item in expected_inputs:
        _need(wire.file_digest(paths[item.name], item.maximum, checkpoint) == (item.sha256, item.size),
              "received bytes differ from predecessor")
    cb = expected_inputs[0]
    universe = sources.load_universe(bridge.bounded_read(paths["CB"], cb.maximum), cb.sha256,
                                    expected_sessions=FULL["sessions"])
    calendar, groups = forecasts._original(universe, expected_members, expected_summary)
    _need(len(groups) == FULL["union_coordinates"]
          and len(universe.targets) == 2 * FULL["targets_per_scenario"]
          and len(calendar.evaluation_reference_sessions) == EVALUATION_DATES,
          "original full population differs")
    feature_seal, label_seal = snapshots.verify_bundle(paths["EI-SNAPSHOT"], checkpoint)
    actual_members = {"features": asdict(feature_seal), "labels": asdict(label_seal)}
    _need(wire.encode(actual_members) == wire.encode(expected_members),
          "received snapshot seals differ from predecessor")
    # Frozen release-qualified inputs are checked before loading any ML package.
    checkpoint("ei_model_inputs_authenticated")
    before = numerical.activate(registered, checkpoint)
    result = forecasts.compare_snapshot(paths["EI-SNAPSHOT"], directory / OUTPUT, scenario=scenario,
        expected_members=expected_members, expected_summary=expected_summary,
        universe=universe, checkpoint=checkpoint)
    after = numerical.verify_after(before, checkpoint)
    summary = result.summary
    fixed = {"schema": forecasts.SCHEMA, "scenario": scenario, "adapter_mode": "fixed_native",
        "input_members": expected_members, "folds": forecasts.FOLD_COUNT,
        "evaluation_sessions": EVALUATION_DATES,
        "original_evaluation_coordinates": EVALUATION_COORDINATES,
        "fold_prediction_readback_verified": True, "report_reference_verified": True,
        "independent_saved_bundle_audit": False, "timing_scenarios_are_independent": False,
        "contains_economic_returns": False}
    _need(all(wire.encode(summary.get(key)) == wire.encode(value) for key, value in fixed.items()),
          "full forecast result differs")
    for name, maximum in (("learned_fit_count", forecasts.MAX_LEARNED_FITS),
                          ("empirical_fit_count", forecasts.MAX_EMPIRICAL_FITS)):
        _need(type(summary.get(name)) is int and 0 <= summary[name] <= maximum,
              "fixed fit budget differs")
    _need(wire.file_digest(directory / OUTPUT, forecasts.MAXIMUM_BYTES, checkpoint)
          == (result.seal.sha256, result.seal.size), "sealed output bytes changed")
    # Preserve the original files through the fit and verification as well.
    for item in expected_inputs:
        _need(wire.file_digest(paths[item.name], item.maximum, checkpoint) == (item.sha256, item.size),
              "received bytes changed during computation")
    manifest = [item.metadata() for item in expected_inputs]
    proof = {"schema": forecasts.SCHEMA, "verified": True, "invented_only": invented_only,
        "full_geometry": True, "full_eof_verified": True, "all_original_coordinates_verified": True,
        "input_manifest": manifest, "input_manifest_sha256": wire.digest(wire.encode(manifest)),
        "forecast_summary": summary, "snapshot_summary_sha256": wire.digest(wire.encode(expected_summary)),
        "numerical_environment": {"before": before, "after": after},
        "historical_availability_certified": False, "stored_volume_units_certified": False,
        "contains_economic_returns": False}
    return bridge.OutputReceipt(result.seal.sha256, result.seal.size, envelope.geometry,
        wire.encode(proof), wire.encode({scenario: {"original": EVALUATION_COORDINATES}}))
