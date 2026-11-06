"""Bind one registered EG comparison to the audited fit and scenario evidence."""

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_runtime as runtime
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_highvol_evaluation_inputs import (
    EvaluationInputs, PredecessorPin, _pin, open_evaluation_inputs,
)
from research.intraday_highvol_fit_unit import FIT_IDS, _require_started
from research.intraday_highvol_policy import ARMS, BRANCHES, COST_BPS, RULE, WEEKLY_TARGET, compare_entry_policies, entry_decision
from research.intraday_highvol_saved_management import compare_saved_management
from research.intraday_highvol_snapshot import HOLD_STEPS, PRIMARY_HORIZON, TIMINGS
from research.intraday_highvol_snapshot_unit import UNIT as SNAPSHOT_UNIT
from research.intraday_highvol_unit import SCIENCE, _json


SPEC = "configs/research/discovery/intraday-highvol-evaluation-v1.json"
INPUT_CONTRACT = "eg-highvol-evaluation-v1"
POLICY = "EG-POLICY-COMPARISON-1"
MANAGEMENT = "EG-MANAGEMENT-1"
OUTPUT_NAMES = {POLICY: "eg-policy-comparison.json", MANAGEMENT: "eg-management-comparison.json"}
REPORT_SCHEMAS = {POLICY: "EG-entry-policy-comparison-v1", MANAGEMENT: "EG-saved-management-v1"}
OUTPUT_LIMIT = 16 * wire.MIB
SUMMARY_LIMIT = 64 * 1024
PROOF_LIMIT = wire.MIB


def evaluation_contract(specification: bytes, science: bytes, unit_id: str, *, actual: bool) -> None:
    """Check the registration without touching a predecessor or result file."""
    spec, design = map(_json, (specification, science))
    resources, bounds = design["resource_approval"], design["campaign_bounds"]
    wire.require(type(actual) is bool and unit_id in OUTPUT_NAMES and spec["units"] == list(OUTPUT_NAMES)
                 and spec["id"] == "intraday-highvol-evaluation-v1" and design["id"] == "intraday-highvol-v1"
                 and spec["campaign"] == design["campaign"] == "EG" and spec["execution_host"] == "local_wsl_only"
                 and spec["resource_profile"] == resources["profile"] == runtime.PROFILE
                 and set(OUTPUT_NAMES) | {SNAPSHOT_UNIT, *FIT_IDS} <= set(bounds["primary_units"])
                 and spec["automatic_actual_retry"] is resources["automatic_actual_retry"] is False
                 and spec["boundaries"] == {"gcp": False, "new_acquisition": False, "holdout_or_forward": False,
                                             "order_or_promotion": False}
                 and all(value is False for value in spec["boundaries"].values())
                 and type(spec["actual_runner_ready"]) is bool
                 and spec["actual_execution_authorized_by_this_file"] is spec["actual_runner_ready"],
                 "EG evaluation registration scope differs")
    if actual:
        wire.require(spec["actual_runner_ready"] is True, "EG evaluation actual source not registered ready")
    wire.require(spec["costs_bps"] == list(COST_BPS)
                 and spec["policy"]["fixed_selection_stress"] == {"selection_bps": 33, "outcome_bps": 43}
                 and spec["policy"]["extra_chart_only_diagnostic"] is True
                 and spec["management"]["fixed_exit_minutes"] == PRIMARY_HORIZON
                 and spec["management"]["decision_updates_minutes_after_entry"] == list(HOLD_STEPS),
                 "EG evaluation timing or cost contract differs")


def _selected_management_keys(inputs: EvaluationInputs, forecasts) -> tuple:
    selected = set()
    for identity, forecast in forecasts.items():
        if inputs.forecasts[identity].comparison_scope == "common" and any(
            entry_decision(forecast, cost) == "selected" for cost in COST_BPS
        ):
            selected.add(identity[-1][:3])
    wire.require(selected <= set(inputs.evaluation_keys), "EG management prospective scope differs")
    return tuple(key for key in inputs.evaluation_keys if key in selected)


def _evaluate(unit_id: str, inputs: EvaluationInputs, checkpoint: Callable[[str], None]) -> tuple[dict, tuple]:
    """Prepare a fixed key roster and delay opening outcomes until features reach EOF."""
    forecasts = inputs.entry_forecasts()
    keys = inputs.evaluation_keys if unit_id == POLICY else _selected_management_keys(inputs, forecasts)
    features = inputs.features()
    consumed = iter(keys)
    with ExitStack() as stack:
        reader = None

        def labels_at(key):
            nonlocal reader
            wire.require(key == next(consumed, None), "EG evaluation prospective outcome request differs")
            if reader is None:
                reader = stack.enter_context(inputs.open_labels(keys))
            return reader(key)

        checkpoint("eg_evaluation_prospective_roster_frozen")
        if unit_id == POLICY:
            report = compare_entry_policies(inputs.source_dates, features, labels_at=labels_at, forecasts=forecasts,
                                            include_chart_only=True)
        else:
            report = compare_saved_management(inputs.source_dates, features, labels_at=labels_at,
                                              entry_forecasts=forecasts, holding_forecasts=inputs.holding_forecasts())
        wire.require(next(consumed, None) is None, "EG evaluation did not request every prospective outcome")
        if reader is None:
            stack.enter_context(inputs.open_labels(keys))
    checkpoint("eg_evaluation_sealed_labels_complete")
    return report, keys


def _policy_arm(value: dict) -> dict:
    net = value["net_pp"]
    return {"decision_counts": value["decision_counts"], "outcome_counts": value["outcome_counts"],
            "gross_pp": value["gross_pp"]["mean"], "net_pp": net["mean"],
            "net_interval_pp": net["normal95_exploratory"], "contributing_sessions": net["contributing_sessions"],
            "weeks_at_least_target_selected": sum(row["selected"] >= WEEKLY_TARGET for row in value["weeks"].values()),
            "weeks_at_least_target_nonoverlap": sum(row["same_symbol_nonoverlapping_selected"] >= WEEKLY_TARGET
                                                     for row in value["weeks"].values()),
            "weeks_at_least_target_modeled": sum(row["modeled_entries"] >= WEEKLY_TARGET
                                                 for row in value["weeks"].values()),
            "weeks_total": len(value["weeks"])}


def _policy_summary(report: dict) -> dict:
    groups = {}
    for branch in BRANCHES:
        groups[branch] = {}
        for timing in TIMINGS:
            group = report["groups"][branch][timing]
            common = group["common"]
            groups[branch][timing] = {"support_states": group["support_states"], "opportunities": common["opportunities"],
                                     "first_hour_onsets": common["first_hour_onsets"], "costs": {
                str(cost): {arm: _policy_arm(common["costs"][str(cost)]["arms"][arm]) for arm in (RULE, *ARMS)}
                for cost in COST_BPS}}
    candidates = [{"scenario": row["branch"], "arm": row["arm"]}
                  for row in report["candidate_screen"] if row["screen_passes"]]
    return {"groups": groups, "exploratory_candidates": candidates,
            "immediate_observed_clusters": {timing: report["immediate"][timing]["observed_clusters"] for timing in TIMINGS}}


def _management_summary(report: dict) -> dict:
    groups = {}
    for branch in BRANCHES:
        groups[branch] = {}
        for timing in TIMINGS:
            groups[branch][timing] = {}
            for cost in map(str, COST_BPS):
                groups[branch][timing][cost] = {}
                for arm in ARMS:
                    value = report["groups"][branch][timing][cost][arm]
                    delta = value["paired_managed_minus_fixed_net_pp"]
                    groups[branch][timing][cost][arm] = {"entry_status_counts": value["entry_status_counts"],
                        "known_matched": value["known_matched"], "unresolved_matched": value["unresolved_matched"],
                        "paired_delta_net_pp": delta["mean"], "paired_delta_interval_pp": delta["normal95_exploratory"],
                        "paired_contributing_sessions": delta["contributing_sessions"]}
    return {"groups": groups}


def _require_qualification_cases(unit_id: str, summary: dict) -> None:
    for group in summary["groups"].values():
        for timing in group.values():
            if unit_id == POLICY:
                if any(arm["net_pp"] is not None for costs in timing["costs"].values() for arm in costs.values()):
                    return
            elif any(arm["known_matched"] > 0 for costs in timing.values() for arm in costs.values()):
                return
    raise ValueError("EG evaluation qualification must exercise a known outcome")


def closed_evaluation_unit(run: runtime.RunContract, specification: bytes, science: bytes, *,
                           snapshot_pin: PredecessorPin, fit_pins: tuple[PredecessorPin, ...]) -> runtime.ClosedUnit:
    """Provide the closed lifecycle without decoding any data before durable STARTED."""
    wire.require(run.unit_id in OUTPUT_NAMES and type(run.invented_only) is bool and type(fit_pins) is tuple
                 and len(fit_pins) == len(FIT_IDS) and all(type(pin) is PredecessorPin for pin in fit_pins)
                 and {pin.unit_id for pin in fit_pins} == set(FIT_IDS), "EG evaluation fit predecessor roster differs")
    _pin(snapshot_pin, SNAPSHOT_UNIT, run.invented_only)
    by_id = {pin.unit_id: pin for pin in fit_pins}
    fit_pins = tuple(by_id[unit_id] for unit_id in FIT_IDS)
    for pin in fit_pins:
        _pin(pin, pin.unit_id, run.invented_only)
    predecessors = {"snapshot": asdict(snapshot_pin), "fits": [asdict(pin) for pin in fit_pins]}
    filename = OUTPUT_NAMES[run.unit_id]
    prepared = False
    produced = None

    def preflight():
        nonlocal prepared
        pins = dict(run.source_hashes)
        wire.require(pins.get(SPEC) == wire.digest(specification) and pins.get(SCIENCE) == wire.digest(science)
                     and run.binding["input_contract"] == INPUT_CONTRACT and run.binding["geometry"] == runtime.FULL,
                     "EG evaluation registration pins differ")
        evaluation_contract(specification, science, run.unit_id, actual=not run.invented_only)
        prepared = True

    def open_inputs(checkpoint):
        return open_evaluation_inputs(snapshot_pin, fit_pins, invented_only=run.invented_only, checkpoint=checkpoint)

    def compute(directory: Path, binding: dict, checkpoint):
        nonlocal produced
        wire.require(prepared and produced is None and directory == run.directory and binding == run.binding,
                     "EG evaluation compute binding or preflight differs")
        _require_started(directory, binding)
        inputs = open_inputs(checkpoint)
        report, keys = _evaluate(run.unit_id, inputs, checkpoint)
        sessions = fixed_calendar(inputs.source_dates).evaluation_reference_sessions
        wire.require(len(inputs.source_dates) == runtime.FULL["sessions"]
                     and report["schema"] == REPORT_SCHEMAS[run.unit_id]
                     and report["reference_sessions"] == list(sessions), "EG evaluation report scope differs")
        key_digest = wire.digest(wire.encode(keys))
        body = {"schema": "EG-evaluation-artifact-v1", "binding": binding, "predecessors": predecessors,
                "prospective_outcomes": {"keys": len(keys), "sha256": key_digest}, "report": report}
        raw = wire.encode(body)
        summary = _policy_summary(report) if run.unit_id == POLICY else _management_summary(report)
        if run.invented_only:
            _require_qualification_cases(run.unit_id, summary)
        encoded_summary = wire.encode({**summary, "evaluation_sessions": len(sessions), "refit_count": 0,
                                       "prospective_outcomes": len(keys)})
        wire.require(len(raw) <= OUTPUT_LIMIT and len(encoded_summary) <= SUMMARY_LIMIT,
                     "EG evaluation report or summary exceeds its bound")
        wire.exclusive_bytes(directory / filename, raw)
        pin = runtime.Artifact(filename, wire.digest(raw), len(raw), OUTPUT_LIMIT)
        produced = runtime.UnitOutput((pin,), wire.encode(runtime.FULL), encoded_summary)
        return produced

    def independent_verify(directory: Path, output: runtime.UnitOutput, binding: dict, checkpoint):
        from research.intraday_highvol_evaluation_reference import verify_evaluation

        wire.require(produced is not None and output == produced and directory == run.directory and binding == run.binding,
                     "EG evaluation verification binding or handoff differs")
        _require_started(directory, binding)
        raw = runtime._read(directory / filename, OUTPUT_LIMIT)
        pin = output.artifacts[0]
        wire.require((wire.digest(raw), len(raw)) == (pin.sha256, pin.size), "EG stored evaluation artifact differs")
        body = runtime._json(raw)
        wire.require(body["schema"] == "EG-evaluation-artifact-v1" and body["binding"] == binding
                     and body["predecessors"] == predecessors, "EG stored evaluation artifact binding differs")
        proof = verify_evaluation(run.unit_id, open_inputs(checkpoint), body["report"], checkpoint)
        wire.require(type(proof) is dict and proof.get("complete") is True
                     and proof.get("prospective_outcomes") == body["prospective_outcomes"]
                     and len(wire.encode(proof)) <= PROOF_LIMIT,
                     "EG evaluation independent reconstruction incomplete")
        return {"complete": True, "schema": "EG-evaluation-independent-v1", "report_sha256": pin.sha256,
                "refit_count": 0, "reconstruction": proof}

    return runtime.ClosedUnit(preflight, compute, independent_verify)
