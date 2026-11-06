"""Closed no-fit confirmation-delay follow-up over the audited EG snapshot."""

from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_runtime as runtime
from research import intraday_highvol_fit_unit as fit_unit
from research import intraday_highvol_snapshot_unit as snapshot
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_highvol_evaluation_inputs import EvaluationInputs, _key, _order
from research.intraday_highvol_fit_run import _seal
from research.intraday_highvol_onset import evaluate_onset, prepare_onset, prospective_keys
from research.intraday_highvol_snapshot_io import open_snapshot
from research.intraday_highvol_unit import SCIENCE, _json


UNIT = "EG-ONSET-EXPANSION-1"
SPEC = "configs/research/discovery/intraday-highvol-onset-v1.json"
INPUT_CONTRACT = "eg-highvol-onset-expansion-v1"
OUTPUT = "eg-onset-expansion.json"
OUTPUT_LIMIT = 16 * wire.MIB
REPORT_SCHEMA = "EG-onset-expansion-comparison-v1"


def onset_contract(specification: bytes, science: bytes, *, actual: bool) -> None:
    spec, design = map(_json, (specification, science))
    bounds, resources = design["campaign_bounds"], design["resource_approval"]
    wire.require(type(actual) is bool and spec["id"] == "intraday-highvol-onset-v1"
                 and spec["campaign"] == design["campaign"] == "EG" and design["id"] == "intraday-highvol-v1"
                 and spec["unit_id"] == UNIT and spec["input_contract"] == INPUT_CONTRACT
                 and spec["resource_profile"] == resources["profile"] == runtime.PROFILE
                 and spec["execution_host"] == "local_wsl_only"
                 and type(spec["actual_runner_ready"]) is bool
                 and spec["actual_execution_authorized_by_this_file"] is spec["actual_runner_ready"]
                 and spec["automatic_actual_retry"] is resources["automatic_actual_retry"] is False
                 and spec["new_fits"] == 0 and spec["conditional_units_for_this_registration"] == 1
                 and UNIT not in bounds["primary_units"] and snapshot.UNIT in bounds["primary_units"]
                 and bounds["conditional_followup_units_max"] == 4
                 and "confirmation consumed remaining movement" in bounds["conditional_reasons"]
                 and spec["boundaries"] == {"gcp": False, "new_acquisition": False, "holdout_or_forward": False,
                                             "order_or_promotion": False}, "EG onset registration scope differs")
    if actual:
        wire.require(spec["actual_runner_ready"] is True, "EG onset actual source not registered ready")
    wire.require(spec["selection"] == {"first_observed_high_only": True, "initial_bar_count": 3,
                    "bar_minutes": 5, "require_positive_signed_move": True,
                    "require_third_close_above_first_two_highs": True, "use_later_scenario_or_outcome": False}
                 and spec["comparison"] == {"timings": ["S1", "S2"], "evaluation_sessions": 157,
                    "costs_bps": [33, 43], "entry_minutes_after_high_decision": 1, "holding_minutes": 30,
                    "all_high_same_initial_support": True, "known_decline_contribution_is_zero": True,
                    "missing_return_is_zero": False}
                 and spec["candidate"] == {"net43_positive": True, "net43_day_cluster_interval_lower_positive": True,
                    "minimum_contributing_days": 10, "both_overlapping_timings": True, "weekly_target": 15},
                 "EG onset clock, comparison or claim differs")


def _read_snapshot_inputs(directory, receipt, checkpoint):
    """Bind event order from features alone; never prepare a fit or parse an outcome."""
    keys = []
    with open_snapshot(directory, "features", _seal(receipt["seals"]["features"]), checkpoint) as reader:
        universe = reader.header["universe"]
        wire.require(reader.header["binding"] == receipt["binding"], "EG onset sealed feature header differs")
        calendar = fixed_calendar(universe["sessions"])
        days = set(calendar.source_reference_sessions)
        for frame in reader.records():
            key = _key(frame["key"])
            wire.require(key[0] in days and (not keys or _order(keys[-1]) < _order(key)),
                         "EG onset sealed event order differs")
            keys.append(key)
    return EvaluationInputs(directory, receipt, calendar, universe, tuple(keys), {}, checkpoint)


def _arm_summary(arm):
    weeks = arm["weeks"].values()
    return {"known": arm["net_pp"]["observations"], "mean_gross_pp": arm["gross_pp"]["mean"],
            "mean_net_pp": arm["net_pp"]["mean"], "selected": arm["decision_counts"].get("selected", 0),
            "modeled_entries": sum(week["modeled_entries"] for week in weeks),
            "weeks_ge15": sum(week["modeled_entries"] >= 15 for week in weeks)}


def _summary(report, key_count, predecessor):
    return {"schema": "EG-onset-expansion-summary-v1", "evaluation_sessions": len(report["reference_sessions"]),
            "prospective_outcomes": key_count, "snapshot_dependency": predecessor,
            "candidate_net43_both_timings": report["candidate_net43_both_timings"],
            "timings": {timing: {"observed_clusters": value["observed_clusters"],
                "support_counts": value["support_counts"], "candidate": value["candidate"],
                "costs": {cost: {arm: _arm_summary(group[arm]) for arm in ("early_a", "all_high")}
                          for cost, group in value["costs"].items()}}
                for timing, value in report["timings"].items()}}


def _require_invented_outcome(summary):
    wire.require(any(timing["costs"]["33"]["early_a"]["known"] > 0 for timing in summary["timings"].values()),
                 "EG onset qualification must exercise a known expansion outcome")


def closed_onset_unit(run: runtime.RunContract, specification: bytes, science: bytes, *,
                      snapshot_attempt: str, snapshot_audit_sha256: str, snapshot_merge_commit: str) -> runtime.ClosedUnit:
    directory = fit_unit._snapshot_scope(run.invented_only, snapshot_attempt, snapshot_audit_sha256, snapshot_merge_commit)
    predecessor = {"unit_id": snapshot.UNIT, "attempt": snapshot_attempt,
                   "audit_sha256": snapshot_audit_sha256, "merge_commit": snapshot_merge_commit}
    prepared = False
    output = None

    def preflight():
        nonlocal prepared
        pins = dict(run.source_hashes)
        wire.require(run.unit_id == UNIT and run.binding["input_contract"] == INPUT_CONTRACT
                     and pins.get(SPEC) == wire.digest(specification) and pins.get(SCIENCE) == wire.digest(science),
                     "EG onset registered source differs")
        onset_contract(specification, science, actual=not run.invented_only)
        prepared = True

    def open_inputs(checkpoint):
        prior = fit_unit._predecessor(directory, run.invented_only, snapshot_attempt, snapshot_audit_sha256,
                                     snapshot_merge_commit, checkpoint)
        return _read_snapshot_inputs(directory, prior, checkpoint)

    def compute(target: Path, binding: dict, checkpoint):
        nonlocal output
        wire.require(prepared and output is None and target == run.directory and binding == run.binding,
                     "EG onset compute binding differs")
        fit_unit._require_started(target, binding)
        inputs = open_inputs(checkpoint)
        plan = prepare_onset(inputs.source_dates, inputs.features())
        keys = prospective_keys(plan)
        with inputs.open_labels(keys) as labels_at:
            report = evaluate_onset(plan, labels_at=labels_at)
        sessions = fixed_calendar(inputs.source_dates).evaluation_reference_sessions
        wire.require(report["schema"] == REPORT_SCHEMA and report["reference_sessions"] == list(sessions),
                     "EG onset report date scope differs")
        summary = _summary(report, len(keys), predecessor)
        if run.invented_only:
            _require_invented_outcome(summary)
        body = {"schema": "EG-onset-expansion-artifact-v1", "binding": binding, "snapshot_dependency": predecessor,
                "prospective_outcomes": {"keys": len(keys), "sha256": wire.digest(wire.encode(keys))}, "report": report}
        raw = wire.encode(body)
        wire.require(len(raw) <= OUTPUT_LIMIT, "EG onset persisted report exceeds bound")
        wire.exclusive_bytes(target / OUTPUT, raw)
        artifact = runtime.Artifact(OUTPUT, wire.digest(raw), len(raw), OUTPUT_LIMIT)
        output = runtime.UnitOutput((artifact,), wire.encode(runtime.FULL), wire.encode(summary))
        return output

    def independent_verify(target: Path, supplied: runtime.UnitOutput, binding: dict, checkpoint):
        from research.intraday_highvol_onset_reference import verify_onset_expansion

        wire.require(output is not None and supplied == output and target == run.directory and binding == run.binding,
                     "EG onset independent handoff differs")
        fit_unit._require_started(target, binding)
        raw = runtime._read(target / OUTPUT, OUTPUT_LIMIT)
        pin, = output.artifacts
        wire.require((wire.digest(raw), len(raw)) == (pin.sha256, pin.size), "EG onset stored report differs")
        body = runtime._json(raw)
        wire.require(body["schema"] == "EG-onset-expansion-artifact-v1" and body["binding"] == binding
                     and body["snapshot_dependency"] == predecessor, "EG onset stored scope differs")
        proof = verify_onset_expansion(open_inputs(checkpoint), body["report"], checkpoint)
        wire.require(proof["complete"] is True and proof["prospective_outcomes"] == body["prospective_outcomes"]
                     and output.summary == wire.encode(_summary(body["report"], proof["prospective_outcomes"]["keys"], predecessor)),
                     "EG onset independent reconstruction differs")
        return {"schema": "EG-onset-expansion-independent-v1", "complete": True, "report_sha256": pin.sha256,
                "refit_count": 0, "reconstruction": proof}

    return runtime.ClosedUnit(preflight, compute, independent_verify)
