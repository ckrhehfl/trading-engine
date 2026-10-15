"""Stream the existing BS cohort engine through individual Discovery trials.

The outer registered caller freezes source/runtime/specification and all actual
inputs, verifies independent receipts, prepares the immutable scope binding and
checks final source identity. This function opens only its exclusive outputs and
the canonical trial log. It supplies no input loader, CLI, OS resource policy or
source/evidence certificate. Each zero-budget cohort is still one logged look.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
import os
from pathlib import Path
import stat
import traceback

from research import activity_cohort_comparison as comparison
from research import activity_preflight as saved
from research import experiment_log
from research.activity_cohort_connection import CohortScopeBinding
from research.activity_cohort_selection import CONTROL_IDS
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_quote_extension import _same
from research.activity_timing_report import _overlap


SCHEMA = "activity-cohort-execution-v1"


@dataclass(frozen=True)
class _NavOnly:
    """Internal _summary input, never a replay/book or a serialized artifact."""

    navs: tuple[Decimal, ...]


@dataclass(frozen=True)
class _InventoryOnly:
    """Expose the existing summary's indexing contract over one Decimal tuple."""

    values: tuple[Decimal, ...]

    def __getitem__(self, index: int) -> dict:
        return {"marked_inventory_value": self.values[index]}


def _counts(keys: tuple) -> dict:
    return {arm: sum(key[0] == arm for key in keys) for arm in comparison.ARMS}


def _key_value(key: tuple) -> dict:
    return {"arm": key[0], "group_id": key[1], "formation_on": key[2].isoformat()}


def _compact(cohort: dict) -> dict:
    """Retain scalar statistics and NAV/inventory tuples, releasing full books."""
    result = {key: value for key, value in cohort.items()
        if key not in {"replay", "exposure", "metrics", "raw_metrics"}}
    result["metrics"] = {key: value for key, value in cohort["metrics"].items() if key != "path"}
    result["raw_metrics"] = {key: value for key, value in cohort["raw_metrics"].items() if key != "path"}
    result["exposure_statistics"] = {key: value for key, value in cohort["exposure"].items() if key != "path"}
    result["replay"] = _NavOnly(cohort["replay"].navs)
    result["exposure"] = {"path": _InventoryOnly(tuple(
        row["marked_inventory_value"] for row in cohort["exposure"]["path"]))}
    return result


def _cohort_statistics(row: dict) -> dict:
    """Only real engine statistics escape; internal summary adapters do not."""
    return _json_value({key: value for key, value in row.items() if key not in {"replay", "exposure"}})


def _summaries(rows: tuple[dict, ...], days: tuple[date, ...], declarations: tuple) -> dict:
    """Reuse the unchanged accounting attribution and descriptive comparisons."""
    aggregates, comparisons, selection_change = {}, {}, {}
    for arm in comparison.ARMS:
        current = tuple(row for row in rows if row["arm"] == arm)
        aggregates[arm] = {group: comparison._summary(tuple(row for row in current if row["group_id"] == group), days)
            for group in comparison.GROUPS}
        formations = sorted({row["formation_on"] for row in current})
        per_formation = {}
        for formation in formations:
            cohorts = {row["group_id"]: row for row in current if row["formation_on"] == formation}
            per_formation[formation.isoformat()] = comparison._deltas(
                cohorts["ACTIVITY"], {group: cohorts[group] for group in CONTROL_IDS})
        comparisons[arm] = {**comparison._deltas(aggregates[arm]["ACTIVITY"],
            {group: aggregates[arm][group] for group in CONTROL_IDS}), "per_formation": per_formation}
    for group in comparison.GROUPS:
        paired = {arm: tuple((row.formation_on, row.chosen_codes) for row in sorted(
            (row for row in declarations if row.arm == arm and row.group_id == group), key=lambda row: row.formation_on))
            for arm in comparison.ARMS}
        selection_change[group] = _overlap(paired["D1"], paired["D2"])
    return {"aggregates": aggregates, "comparisons": comparisons, "target_selection_change": selection_change,
        "selection_change_meaning": "pre-opening declared targets; not filled-entry change or a reconstructed signal pool"}


def _failure(error: BaseException, *, attempted: tuple, completed: tuple) -> dict:
    return {"schema": SCHEMA + ":failure-v1", "status": "failed", "error_type": type(error).__name__,
        "error_frames": [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)],
        "engine_callbacks_started": [_key_value(key) for key in attempted],
        "canonically_completed_cohorts": [_key_value(key) for key in completed],
        "completed_cohort_studies_by_arm": _counts(completed),
        "partial_package_preserved": True, "automatic_retries": 0}


def run_cohort_family(
    declarations: tuple, holding_inputs, events: tuple, *, parameters: dict,
    scope_binding: CohortScopeBinding, study_id: str, source_manifest_sha256: str,
    specification_sha256: str, runs_path: Path, output_dir: Path, raw_quote_view=None,
) -> dict:
    """Run every declared group/formation/arm once, retaining one full book at a time.

    output_dir must be a new child of an existing private 0700 directory; an
    outer caller may use its own manifest directory as that parent. Original
    input paths/byte pins, complete receipt validation and prepare_cohort_scope
    precede this boundary. Supplied values are immutable, and the canonical log
    has one writer. Existing log permissions are preserved. Actual input reads,
    source/runtime freezes and the final identity check remain outer obligations.
    """
    saved.require(type(parameters) is dict and saved.encoded(parameters) == saved.encoded(PARAMETERS),
        "unchanged canonical parameters required")
    parameters = saved.strict_json(saved.encoded(parameters))
    saved.require(type(scope_binding) is CohortScopeBinding
        and _same(scope_binding.declarations, declarations)
        and scope_binding.parameters_json == saved.encoded(parameters), "prepared immutable cohort scope binding required")
    for value in (source_manifest_sha256, specification_sha256, scope_binding.binding_sha256):
        saved._pin(value)
    saved.require(type(study_id) is str and bool(study_id) and study_id == study_id.strip(), "explicit frozen study id required")
    saved.require(type(events) is tuple, "immutable prepared events required")
    calendar = holding_inputs.calendar
    saved.require(type(calendar) is tuple and bool(calendar)
        and calendar[0] >= date.fromisoformat(saved.WINDOW["start"]), "spent discovery calendar required")
    keyed = comparison._declarations(declarations, calendar, parameters)
    keys = tuple(sorted(keyed))
    days = calendar[parameters["lookback"] + 1:calendar.index(date.fromisoformat(parameters["end"])) + 1]
    saved.require(Path(output_dir).is_absolute() and Path(runs_path).is_absolute(), "explicit absolute output/log paths required")
    output, runs = saved._safe_path(Path(output_dir)), saved._safe_path(Path(runs_path))
    saved.private_directory(output.parent)
    saved.require(not output.exists() and not runs.is_relative_to(output)
        and not output.is_relative_to(Path(__file__).resolve().parents[2]), "fresh private output outside checkout and log required")
    if runs.exists():
        info = runs.lstat()
        saved.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "canonical log must be single-link regular file")
    with localcontext() as context:
        context.prec = 28
        planned_cohorts = [{**_key_value(key), "decision_on": keyed[key].decision_on.isoformat(),
            "selection_at": keyed[key].selection_at.isoformat(), "chosen_codes": list(keyed[key].chosen_codes),
            "original_activity_targets": list(keyed[key].activity_targets), "target_ceiling": keyed[key].target_ceiling,
            "assigned_capital": str(Decimal(keyed[key].target_ceiling) / parameters["slots"]),
            "activity_targets_sha256": keyed[key].activity_targets_sha256} for key in keys]
    plan = {"schema": SCHEMA + ":plan-v1", "study_id": study_id,
        "source_manifest_sha256": source_manifest_sha256, "specification_sha256": specification_sha256,
        "conditional_scope_binding_sha256": scope_binding.binding_sha256,
        "expected_cohort_studies_by_arm": _counts(keys),
        "cohorts": planned_cohorts,
        "automatic_retries": 0}
    plan_raw = saved.encoded(plan)
    plan_sha = saved.digest(plan_raw)
    compact_rows, receipts, attempted, completed = [], [], [], []
    previous = os.umask(0o077)
    created = False
    try:
        output.mkdir(mode=0o700)
        created = True
        saved._sync_directory(output.parent)
        saved.write_exclusive(output / "cohort-plan.json", plan_raw)
        with localcontext() as context:
            context.prec = 28
            prepared, execute = comparison._prepare_cohort_replay(declarations, holding_inputs, events,
                parameters=parameters, raw_quote_view=raw_quote_view, scope_binding=scope_binding)
            for ordinal, key in enumerate(keys, 1):
                declaration = keyed[key]
                name = f"cohort-{ordinal:04d}-{key[0]}-{key[1]}-{key[2].isoformat()}"
                target = output / (name + ".json")
                compact = None

                def evaluate():
                    nonlocal compact
                    attempted.append(key)
                    try:
                        one = execute(prepared, (key,))
                        saved.require(len(one["cohorts"]) == 1 and one["cohorts"][0]["key"] == key,
                            "one exact declared cohort per trial required")
                        cohort = one["cohorts"][0]
                        saved.require(cohort["chosen_codes"] == declaration.chosen_codes
                            and cohort["target_ceiling"] == declaration.target_ceiling
                            and cohort["assigned_capital"] == Decimal(declaration.target_ceiling) / parameters["slots"],
                            "fixed cohort choices or assigned budget changed")
                        compact = _compact(cohort)
                        document = {"schema": SCHEMA + ":cohort-v1", "cohort_key": _key_value(key),
                            "source_manifest_sha256": source_manifest_sha256, "specification_sha256": specification_sha256,
                            "cohort_plan_sha256": plan_sha, "conditional_scope_binding_sha256": scope_binding.binding_sha256,
                            "engine_result": _json_value(one), "navs": _json_value(compact["replay"].navs)}
                        raw = saved.encoded(document)
                        saved.write_exclusive(target, raw)
                        return {"cohort_key": _key_value(key), "cohort_output": target.name,
                            "cohort_sha256": saved.digest(raw), "cohort_bytes": len(raw), "automatic_retries": 0,
                            "D1_completed_studies": int(key[0] == "D1"), "D2_completed_studies": int(key[0] == "D2")}
                    except BaseException as error:
                        saved.write_exclusive(output / (name + ".failure.json"),
                            saved.encoded(_failure(error, attempted=(key,), completed=())))
                        raise

                record = experiment_log.run_discovery_trial(study_id=study_id, specification_sha256=specification_sha256,
                    parameters={**parameters, "mode": "discovery_equal_assigned_capital_cohort",
                        "evaluation_unit": "one_group_formation_arm_cohort", "cohort_key": _key_value(key),
                        "chosen_codes": declaration.chosen_codes, "original_activity_targets": declaration.activity_targets,
                        "assigned_capital": str(Decimal(declaration.target_ceiling) / parameters["slots"]),
                        "source_manifest_sha256": source_manifest_sha256, "cohort_plan_sha256": plan_sha,
                        "conditional_scope_binding_sha256": scope_binding.binding_sha256,
                        "output_file": str(target), "automatic_retries": 0},
                    window=saved.WINDOW, runs_path=runs, evaluate=evaluate)
                completed.append(key)
                saved.require(compact is not None, "completed trial must retain its summary")
                compact_rows.append(compact)
                receipt_raw = saved.encoded(_json_value(record))
                receipt_name = name + ".receipt.json"
                saved.write_exclusive(output / receipt_name, receipt_raw)
                receipts.append({**record["result"], "run_id": record["run_id"], "receipt_file": receipt_name,
                    "receipt_sha256": saved.digest(receipt_raw), "statistics": _cohort_statistics(compact)})
            saved.require(tuple(completed) == keys, "every declared cohort requires one canonical completion")
            summaries = _summaries(tuple(compact_rows), days, declarations)
        report = _json_value({"schema": SCHEMA + ":report-v1", "study_id": study_id,
            "initial_nav_per_cohort": comparison.ONE, "parameters": parameters,
            "source_manifest_sha256": source_manifest_sha256, "specification_sha256": specification_sha256,
            "conditional_scope_binding_sha256": scope_binding.binding_sha256, "cohort_plan_sha256": plan_sha,
            "activity_snapshot_sha256": scope_binding.original.activity_snapshot_sha256,
            "quote_snapshot_sha256": scope_binding.quotes.quote_snapshot_sha256,
            "cohort_evaluation_counts": _counts(tuple(completed)), "cohort_receipts": receipts, **summaries,
            "aggregation": "ex-post sum(cohort_NAV-1)/sum(original_activity_target_count/20); not a deployable continuous strategy",
            "control_interpretation": "five fixed controls share market sessions; not independent market samples; no p-values",
            "future_counts_used_only_for_ex_post_attribution": True, "independent_output_verification": "pending",
            "comparison_power_prerequisite_met": False, "power_design_passed": False, "promotion_allowed": False})
        report_raw = saved.encoded(report)
        saved.write_exclusive(output / "report.json", report_raw)
        completion = {"schema": SCHEMA + ":completion-v1", "status": "logged_cohort_family_executed",
            "report_sha256": saved.digest(report_raw), "cohort_plan_sha256": plan_sha,
            "completed_cohort_studies_by_arm": _counts(tuple(completed)),
            "D1_completed_studies": _counts(tuple(completed))["D1"],
            "D2_completed_studies": _counts(tuple(completed))["D2"],
            "independent_output_verification": "pending", "automatic_retries": 0}
        completion_raw = saved.encoded(completion)
        saved.write_exclusive(output / "completion.json", completion_raw)
        return {**completion, "output_dir": str(output), "completion_sha256": saved.digest(completion_raw), "report": report}
    except BaseException as error:
        if created:
            saved.write_exclusive(output / "failure.json",
                saved.encoded(_failure(error, attempted=tuple(attempted), completed=tuple(completed))))
        raise
    finally:
        os.umask(previous)
