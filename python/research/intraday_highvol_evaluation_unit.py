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
from research.intraday_highvol_policy import (
    ARMS, BRANCHES, COST_BPS, RULE, WEEKLY_TARGET, EntryForecast, compare_entry_policies, entry_decision,
)
from research.intraday_highvol_saved_management import HoldingForecast, compare_saved_management
from research.intraday_highvol_snapshot import HOLD_STEPS, PRIMARY_HORIZON, TIMINGS, _fraction
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
CONTROL_FIELD = "invented_management_control"
CONTROL_SCHEMA = "EG-invented-management-control-v1"
CONTROL_REGISTRATION = {"arm": "TCN_CHART", "entry_score_pp": 0.8, "hold_scores_pp": [-0.4, -0.4],
                        "holding_steps": [5, 10], "costs_bps": [33, 43],
                        "requires": ["half_reduction", "early_close", "known_fixed_and_managed_paths"]}


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
                 and spec["management"]["decision_updates_minutes_after_entry"] == list(HOLD_STEPS)
                 and spec["management"]["invented_control"] == CONTROL_REGISTRATION,
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
                                     "first_hour_observed_clusters": common["first_hour_observed_clusters"],
                                     "first_hour_confirmed_onsets": common["first_hour_confirmed_onsets"],
                                     "first_hour_left_censored_clusters": common["first_hour_left_censored_clusters"],
                                     "costs": {
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


def _require_policy_qualification(summary: dict) -> None:
    wire.require(any(arm["net_pp"] is not None for group in summary["groups"].values()
                     for timing in group.values() for costs in timing["costs"].values() for arm in costs.values()),
                 "EG policy qualification must exercise a known outcome")


class _InventedManagementView:
    def __init__(self, inputs, frame, timing):
        key, branch = tuple(frame["key"]), frame["attempt"]["scenario"]
        arm = CONTROL_REGISTRATION["arm"]
        self.inputs, self.frame = inputs, frame
        self.source_dates, self.evaluation_keys = inputs.source_dates, (key,)
        self.identity = {"schema": CONTROL_SCHEMA, "key": list(key), "branch": branch, "timing": timing, "arm": arm}
        self._entry = {(branch, timing, arm, (*key, 0)): EntryForecast(CONTROL_REGISTRATION["entry_score_pp"])}
        self._hold = {(branch, timing, arm, (*key, step)): HoldingForecast(score) for step, score in
                      zip(CONTROL_REGISTRATION["holding_steps"], CONTROL_REGISTRATION["hold_scores_pp"], strict=True)}

    def features(self):
        yield self.frame

    def entry_forecasts(self):
        return self._entry

    def holding_forecasts(self):
        return self._hold

    def open_labels(self, keys):
        wire.require(keys == self.evaluation_keys, "EG invented management outcome key differs")
        return self.inputs.open_labels(keys)


def _invented_management_view(inputs):
    eligible = set(inputs.evaluation_keys)
    selected = None
    for frame in inputs.features():
        entry = frame["entry"]
        if (selected is not None or tuple(frame["key"]) not in eligible or entry is None
                or not entry["primary_clock_supported"] or entry["chart"]["status"] != "available"):
            continue
        timing = next((value for value in frame["timings"] if entry["peers"][value]["status"] == "available"), None)
        if timing is None:
            continue
        stop = _fraction(frame["attempt"]["confirmation"]["stop"])
        updates = {row["step_minutes"]: row for row in frame["holding"]}
        if all((row := updates.get(step)) is not None and row["chart"]["status"] == "available"
               and row["last_close"] is not None and _fraction(row["last_close"]) > stop
               for step in CONTROL_REGISTRATION["holding_steps"]):
            selected = _InventedManagementView(inputs, frame, timing)
    wire.require(selected is not None, "EG invented management qualification has no prospective control event")
    return selected


def _control_witness(identity, report):
    for cost in COST_BPS:
        row = report["groups"][identity["branch"]][identity["timing"]][str(cost)][identity["arm"]]
        wire.require(row["known_matched"] == 1 and row["unresolved_matched"] == 0
                     and row["entry_status_counts"] == {"modeled_entry": 1}
                     and row["update_reasons"] == {"first_nonpositive_hold_gain": 1, "consecutive_nonpositive_hold_gain": 1}
                     and row["managed_leg_statuses"] == {"modeled": 2}
                     and all(row[name]["status_counts"] == {"closed": 1} for name in ("fixed", "managed")),
                     "EG invented management qualification must exercise half reduction and early close")
    return {**identity, "known_matched_costs": list(COST_BPS), "half_reduction": True, "early_close": True}


def _encoded_summary(unit_id, report, key_count, control=None):
    summary = _policy_summary(report) if unit_id == POLICY else _management_summary(report)
    if control is not None:
        summary[CONTROL_FIELD] = control
    return wire.encode({**summary, "evaluation_sessions": len(report["reference_sessions"]), "refit_count": 0,
                        "prospective_outcomes": key_count})


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
    needs_control = run.invented_only and run.unit_id == MANAGEMENT
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
        control = None
        if needs_control:
            view = _invented_management_view(open_inputs(checkpoint))
            with view.open_labels(view.evaluation_keys) as labels_at:
                control_report = compare_saved_management(view.source_dates, view.features(), labels_at=labels_at,
                    entry_forecasts=view.entry_forecasts(), holding_forecasts=view.holding_forecasts())
            control = _control_witness(view.identity, control_report)
            body[CONTROL_FIELD] = {**view.identity, "report": control_report}
        elif run.invented_only:
            _require_policy_qualification(_policy_summary(report))
        raw = wire.encode(body)
        encoded_summary = _encoded_summary(run.unit_id, report, len(keys), control)
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
                     and body["predecessors"] == predecessors and (CONTROL_FIELD in body) is needs_control,
                     "EG stored evaluation artifact binding differs")
        proof = verify_evaluation(run.unit_id, open_inputs(checkpoint), body["report"], checkpoint)
        control = None
        if needs_control:
            saved = body[CONTROL_FIELD]
            view = _invented_management_view(open_inputs(checkpoint))
            wire.require(set(saved) == set(view.identity) | {"report"}
                         and all(saved[key] == value for key, value in view.identity.items()),
                         "EG invented management independent identity differs")
            verified = verify_evaluation(MANAGEMENT, view, saved["report"], checkpoint)
            wire.require(verified.get("complete") is True and verified["checked_matched_paths"] == len(COST_BPS)
                         and verified["prospective_outcomes"] == {"keys": 1,
                             "sha256": wire.digest(wire.encode(view.evaluation_keys))},
                         "EG invented management independent control reconstruction differs")
            control = _control_witness(view.identity, saved["report"])
        wire.require(type(proof) is dict and proof.get("complete") is True
                     and proof.get("prospective_outcomes") == body["prospective_outcomes"]
                     and output.summary == _encoded_summary(run.unit_id, body["report"], proof["prospective_outcomes"]["keys"], control)
                     and len(wire.encode(proof)) <= PROOF_LIMIT,
                     "EG evaluation independent reconstruction incomplete")
        result = {"complete": True, "schema": "EG-evaluation-independent-v1", "report_sha256": pin.sha256,
                  "refit_count": 0, "reconstruction": proof}
        if control is not None:
            result[CONTROL_FIELD] = control
        return result

    return runtime.ClosedUnit(preflight, compute, independent_verify)
