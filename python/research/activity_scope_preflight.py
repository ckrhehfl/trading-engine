"""Logged conservative original-issue price/action investigation scope.

Only three pinned saved summaries are parsed in place on the research host.
This planner does not extract/use their saved quote or turnover values, read
any new price/action source, select holdings, or certify whole-strategy scope.
Possible holdings can survive failed sales, so intervals extend to evaluation
end rather than their scheduled due dates. Successor closure remains open.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import os
from pathlib import Path
import re

from research import activity_preflight as saved
from research import experiment_log
from research.activity_timing import SessionLagPolicy


SCHEMA = "activity-price-action-scope-v1"
INPUTS = {
    "calendar_manifest": {
        "path": "/home/minjun4897/research-evidence/large-liquid-calendar-20261007-v1/manifest.json",
        "sha256": "ccdcee1681ad9fa16aef837e4aceaf4d2f40847974046a1714d1352c3df01882"},
    "bh_result": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bh-timing-preflight-20261009-recovery-v1/run/result.json",
        "sha256": "dc9234b69339a5c4cce9295d00436c0a1d198402936a798c8eac636811e70ef6"},
    "bi_result": {
        "path": "/home/minjun4897/research-evidence/large-liquid-bi-activity-failures-20261009-v1/run/result.json",
        "sha256": "b011e43ff4c34c26e8f6e66b216b202b1f1b06c340abc9e4d1340b6393729dc3"},
}
REGISTERED_COUNTS = {
    "formation_totals": saved.AQ_TOTALS,
    "bb_population": {"population_rows": 944, "target_windows": 917,
                      "target_codes": 136, "retained_controls": 27},
    "prior_windows": 917, "bh_failures": 41, "bi_windows": 876,
    "bi_failures": 840, "potential_windows": 36, "potential_codes": 31,
}
BH_FAILURE_REASONS = {"exact_preceding_60_session_median_below_threshold",
                      "BB_verified_observed_history_less_than_60"}
BI_FAILURE_REASONS = {"formation_frozen", "zero_baseline", "activity_below_threshold"}


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(spec.get("schema") == SCHEMA and spec.get("study_id") == SCHEMA
                  and spec.get("mode") == "discovery_conservative_price_action_scope"
                  and spec.get("promotion_allowed") is False
                  and spec.get("reference_specification") == saved.REFERENCE
                  and spec.get("parameters") == reference["parameters"]
                  and spec.get("window") == saved.WINDOW
                  and spec.get("inputs") == INPUTS, "fixed BJ scope specification mismatch")


def _keyed(rows: list[dict], formations: set[str], label: str) -> dict:
    saved.require(isinstance(rows, list), f"invalid {label} windows")
    result = {}
    for row in rows:
        saved.require(isinstance(row, dict) and row.get("formation") in formations
                      and isinstance(row.get("code"), str)
                      and re.fullmatch(r"[0-9A-Z]{6}", row["code"]) is not None,
                      f"invalid {label} window identity")
        key = row["formation"], row["code"]
        saved.require(key not in result, f"duplicate {label} formation/code identity")
        result[key] = row
    return result


def _integer_counts(actual: dict, expected: dict, label: str) -> None:
    saved.require(isinstance(actual, dict) and actual == expected
                  and all(type(value) is int for value in actual.values()),
                  f"{label} count mismatch")


def population_snapshot(bh: dict, schedule: list[dict], *, counts: dict = REGISTERED_COUNTS) -> dict:
    """Retain only BH population metadata before loading the larger BI input."""
    saved.require(bh.get("schema") == saved.SCHEMA
                  and bh.get("status") == "saved_input_coverage_audited"
                  and bh.get("returns_computed") is False
                  and bh.get("holding_selection_executed") is False
                  and bh.get("paired_schedule") == schedule, "BH receipt/schedule mismatch")
    _integer_counts(bh["formation_totals"], counts["formation_totals"], "full source")
    _integer_counts(bh["bb_population"], counts["bb_population"], "BB population")
    totals, population = bh["formation_totals"], bh["bb_population"]
    saved.require(totals["trade_rows"] == totals["cap_below_rows"] + totals["cap_pass_rows"]
                  and totals["cap_pass_rows"] == population["population_rows"]
                  == population["retained_controls"] + counts["prior_windows"]
                  and population["target_windows"] == counts["prior_windows"],
                  "full source partition mismatch")
    coverage = bh["coverage"]
    prior = _keyed(coverage["windows"], {row["formation"] for row in schedule}, "BH")
    failures, unresolved = [], []
    for key, row in sorted(prior.items()):
        reasons = row.get("proven_numeric_failures")
        saved.require(isinstance(reasons, list) and reasons == sorted(set(reasons))
                      and all(isinstance(reason, str) and reason in BH_FAILURE_REASONS for reason in reasons)
                      and type(row.get("potentially_influential_unresolved")) is bool
                      and row["potentially_influential_unresolved"] == (not reasons),
                      "BH reason/disposition mismatch")
        (unresolved if not reasons else failures).append(key)
    saved.require(len(prior) == counts["prior_windows"]
                  and len({code for _, code in prior}) == population["target_codes"]
                  and len(failures) == counts["bh_failures"]
                  and len(unresolved) == counts["bi_windows"], "BH disposition partition mismatch")
    _integer_counts({key: coverage[key] for key in (
        "unresolved_potentially_influential_windows", "windows_with_proven_numeric_failure")},
        {"unresolved_potentially_influential_windows": len(unresolved),
         "windows_with_proven_numeric_failure": len(failures)}, "BH disposition")
    return {"formation_totals": totals, "bb_population": population,
            "prior_dispositions": coverage["windows"]}


def _paired_schedule(calendar: list[str], params: dict) -> tuple[list[date], int, list[dict]]:
    saved.require(isinstance(calendar, list) and bool(calendar)
                  and all(isinstance(value, str) and re.fullmatch(r"[0-9]{8}", value) for value in calendar)
                  and calendar == sorted(set(calendar)), "invalid complete index calendar")
    days = [date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}") for value in calendar]
    end_on = date.fromisoformat(params["end"])
    saved.require(end_on in days, "evaluation end missing from calendar")
    end = days.index(end_on)
    policies = [SessionLagPolicy(lag, paired=True) for lag in (1, 2)]
    indices = policies[0].formation_indices(days, lookback=params["lookback"],
        holding_sessions=params["holding_sessions"], end_index=end)
    saved.require(indices == policies[1].formation_indices(days, lookback=params["lookback"],
        holding_sessions=params["holding_sessions"], end_index=end), "paired formation geometry differs")
    schedule = [{"formation": calendar[i], "arms": [
        {"session_lag": policy.session_lag, "execution_on": policy.execution_on(days, i).isoformat(),
         "selection_at": policy.selection_at(days, i).isoformat(),
         "due_on": days[i + policy.session_lag + params["holding_sessions"]].isoformat(),
         "status": "input_coverage_only_not_executed"} for policy in policies]} for i in indices]
    return days, end, schedule


def plan_scope(population: dict, bi: dict, calendar: list[str], schedule: list[dict], params: dict,
               *, counts: dict = REGISTERED_COUNTS) -> dict:
    """Pure metadata planner; explicit small geometry/counts support synthetic tests.

    Production calls use the registered calendar_schedule and REGISTERED_COUNTS.
    No baseline/turnover fields, selector, lot book or event tuple are consumed.
    """
    days, end, recomputed = _paired_schedule(calendar, params)
    saved.require(schedule == recomputed and bi.get("paired_schedule") == recomputed,
                  "BI/recomputed paired schedule mismatch")
    saved.require(bi.get("schema") == "activity-failure-preflight-v1"
                  and bi.get("status") == "numeric_failures_audited"
                  and bi.get("returns_computed") is False
                  and bi.get("holding_selection_executed") is False
                  and bi.get("historical_eligibility_certified") is False
                  and bi.get("promotion_allowed") is False
                  and bi.get("prior_dispositions") == population["prior_dispositions"],
                  "BI receipt/prior dispositions mismatch")
    by_formation = {row["formation"]: row["arms"] for row in schedule}
    prior = _keyed(population["prior_dispositions"], set(by_formation), "prior")
    rows = _keyed(bi["windows"], set(by_formation), "BI")
    expected = {key for key, row in prior.items() if row["potentially_influential_unresolved"]}
    saved.require(set(rows) == expected and len(rows) == counts["bi_windows"], "BI key partition mismatch")
    failures, potential = [], []
    for key, row in sorted(rows.items()):
        proven, reason, unresolved = row.get("proven_failure"), row.get("reason"), row.get("unresolved")
        saved.require(type(proven) is bool
                      and row.get("historical_eligibility_certified") is False
                      and row.get("selection_at") == {f"D{arm['session_lag']}": arm["selection_at"]
                                                     for arm in by_formation[key[0]]},
                      "BI boolean/cutoff metadata mismatch")
        saved.require((proven and reason in BI_FAILURE_REASONS and unresolved == [])
                      or (not proven and reason == "numeric_pass_unresolved"
                          and unresolved == ["operating_period_and_eligibility_uncertified"]),
                      "unknown or inconsistent BI reason")
        (failures if proven else potential).append({"formation": key[0], "code": key[1], "reason": reason})
    summary = {"input_windows": len(rows), "proven_activity_failure_windows": len(failures),
               "remaining_potential_windows": len(potential),
               "remaining_distinct_codes": len({row["code"] for row in potential}),
               "unresolved_input_windows": 0,
               "failure_reasons": dict(sorted(Counter(row["reason"] for row in failures).items()))}
    saved.require(bi.get("summary") == summary
                  and all(type(value) is int for key, value in bi["summary"].items() if key != "failure_reasons")
                  and all(type(value) is int for value in bi["summary"]["failure_reasons"].values())
                  and len(failures) == counts["bi_failures"]
                  and len(potential) == counts["potential_windows"]
                  and summary["remaining_distinct_codes"] == counts["potential_codes"],
                  "BI fixed count/summary mismatch")
    requested: dict[str, set[str]] = {}
    windows = []
    positions = {day: index for index, day in enumerate(days)}
    for row in potential:
        arms = []
        for arm in by_formation[row["formation"]]:
            entry = date.fromisoformat(arm["execution_on"])
            interval = [day.isoformat() for day in days[positions[entry]:end + 1]]
            saved.require(bool(interval) and interval[-1] == params["end"], "empty possible holding interval")
            requested.setdefault(row["code"], set()).update(interval)
            arms.append({"session_lag": arm["session_lag"], "possible_entry_on": arm["execution_on"],
                         "selection_at": arm["selection_at"], "scheduled_due_on": arm["due_on"],
                         "investigation_start": interval[0], "investigation_end": interval[-1],
                         "investigation_sessions": len(interval)})
        windows.append({"formation": row["formation"], "code": row["code"], "arms": arms})
    union = {code: sorted(values) for code, values in sorted(requested.items())}
    return {"schema": SCHEMA, "status": "conservative_original_issue_scope_planned",
            "scope_kind": "upper_bound_for_BI_potential_subset_only",
            "interval_policy": "every index session from possible entry through fixed evaluation end inclusive",
            "evaluation_end": params["end"], "calendar_dates": calendar, "paired_schedule": schedule,
            "formation_totals": population["formation_totals"], "bb_population": population["bb_population"],
            "prior_dispositions_reference": {"role": "bh_result", "sha256": INPUTS["bh_result"]["sha256"],
                                             "count": len(prior)},
            "retained_controls_reference": {"role": "bh_result", "sha256": INPUTS["bh_result"]["sha256"],
                "field": "bb_population.retained_controls", "count": population["bb_population"]["retained_controls"]},
            "bh_failure_windows": [{"formation": key[0], "code": key[1],
                "proven_numeric_failures": row["proven_numeric_failures"]}
                for key, row in sorted(prior.items()) if not row["potentially_influential_unresolved"]],
            "bi_failure_windows": failures, "possible_windows": windows, "requested_code_dates": union,
            "summary": {"potential_windows": len(windows), "potential_codes": len(union),
                "arm_windows": {"D1": len(windows), "D2": len(windows)},
                "required_unique_code_dates": sum(map(len, union.values())),
                "prior_windows": len(prior), "bh_failure_windows": counts["bh_failures"],
                "bi_failure_windows": len(failures), "unresolved_input_windows": 0},
            "successor_closure_complete": False, "no_event_coverage_reviewed": False,
            "baseline_formation_price_basis_certified": False, "settlement_coverage_certified": False,
            "remaining_obligations": ["successor issue closure and consequential action evidence",
                "reviewed no-event coverage before any empty event tuple",
                "baseline and formation price-basis coverage", "settlement dates and cash/slot obligations",
                "continuous operating periods and logged actual selector/holding integration"],
            "historical_eligibility_certified": False, "holding_selection_executed": False,
            "holding_lot_denominator": None, "action_lot_denominator": None,
            "returns_computed": False, "D1_completed_studies": 0, "D2_completed_studies": 0,
            "database_access": False, "new_source_requests": 0, "promotion_allowed": False}


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs) -> dict:
    declared = spec["inputs"]
    manifest = inputs.json(Path(declared["calendar_manifest"]["path"]),
                           declared["calendar_manifest"]["sha256"], "calendar_manifest")
    calendar, _, schedule = saved.calendar_schedule(manifest, spec["parameters"])
    del manifest
    bh = inputs.json(Path(declared["bh_result"]["path"]), declared["bh_result"]["sha256"], "bh_result")
    population = population_snapshot(bh, schedule)
    del bh  # Large raw row provenance is not retained while loading BI.
    bi = inputs.json(Path(declared["bi_result"]["path"]), declared["bi_result"]["sha256"], "bi_result")
    result = plan_scope(population, bi, calendar, schedule, spec["parameters"])
    del bi
    result["input_manifest"] = inputs.manifest
    return result


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path,
                  *, reader=saved.read_private) -> dict:
    """One durable logged attempt with exclusive private outputs and no retry."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root, "run from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    inputs = saved.PinnedInputs(reader)

    def evaluate():
        created = False
        try:
            saved.private_directory(output.parent)
            output.mkdir(mode=0o700)
            created = True
            saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = evaluate_inputs(spec, inputs)
            after, after_raw, _ = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items()
                                      if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)),
                          specification_sha256=saved.digest(raw))
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
            return {"status": result["status"], "output_dir": str(output), "summary": result["summary"],
                    "result_sha256": saved.digest(saved.encoded(result)),
                    "source_manifest_sha256": result["source_manifest_sha256"],
                    "input_manifest_sha256": saved.digest(saved.encoded(inputs.manifest)),
                    "holding_lot_denominator": None, "action_lot_denominator": None,
                    "D1_completed_studies": 0, "D2_completed_studies": 0}
        except BaseException as exc:
            if created:
                if not (output / "input-manifest.json").exists():
                    saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed",
                    "error_type": type(exc).__name__, "actual_inputs_attempted": inputs.manifest,
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)),
                    "specification_sha256": saved.digest(raw),
                    "partial_package_preserved": True, "automatic_retries": 0}))
            raise

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"],
            specification_sha256=saved.digest(raw), window=spec["window"], runs_path=runs,
            parameters={**spec["parameters"], "source_manifest": sources, "declared_inputs": spec["inputs"],
                        "output_dir": str(output), "automatic_retries": 0}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(saved.encoded({"status": "failed", "error_type": type(exc).__name__, "automatic_retries": 0}).decode())
        return 1
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
