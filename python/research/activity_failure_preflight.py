"""Logged one-way activity failures; numeric survivors remain uncertified.

Only the registered spent KIS code/date union is read. No holding quotes,
classification acquisitions, returns, exchange calls or automatic retry.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from decimal import Decimal
import os
from pathlib import Path

from research import activity_preflight as saved
from research import experiment_log
from research.activity_failure import activity_failure
from research.activity_failure_inputs import load_activity_failure_inputs


SCHEMA = "activity-failure-preflight-v1"
PINS = {
    "calendar_manifest": "ccdcee1681ad9fa16aef837e4aceaf4d2f40847974046a1714d1352c3df01882",
    "bb_result": "2ac0b2c81d19921d19cfea95a850574d9bef8dc7b5d1ebc900aa1f039ea0d9b4",
    "bh_result": "dc9234b69339a5c4cce9295d00436c0a1d198402936a798c8eac636811e70ef6",
    "au_result": "eb95349f4a5555a45363b98ae907c6a7791e12297a3fee17fa7729a492ce1fa4",
}
DATABASE = "/home/minjun4897/trading-engine/python/data/var/krx_scan.sqlite3"
ALLOWED_COLUMNS = ["bsop_date", "open", "high", "low", "close", "turnover"]
TARGET_COUNTS = (876, 133)
BH_DISPOSITION_COUNTS = (876, 41)


def day(value: str) -> date:
    return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(spec.get("schema") == SCHEMA
                  and spec.get("mode") == "discovery_numeric_failure_preflight"
                  and spec.get("study_id") == "activity-numeric-failure-preflight-v1"
                  and spec.get("promotion_allowed") is False
                  and spec.get("reference_specification") == saved.REFERENCE
                  and spec.get("parameters") == reference["parameters"]
                  and spec.get("window") == saved.WINDOW, "fixed BI specification mismatch")
    inputs = spec["inputs"]
    saved.require(set(inputs) == {*PINS, "scan_database"}, "unexpected BI input roles")
    for role, pin in PINS.items():
        row = inputs[role]
        saved.require(set(row) == {"path", "sha256"} and row["sha256"] == pin
                      and isinstance(row["path"], str) and Path(row["path"]).is_absolute(),
                      "BI pinned input mismatch")
    saved.require(inputs["scan_database"] == {
        "path": DATABASE, "snapshot_policy": "readonly_single_transaction_exact_requested_code_dates",
        "whole_database_hash": None, "allowed_columns": ALLOWED_COLUMNS}, "BI database scope mismatch")


def plan_windows(bb_windows: list[dict], coverage: list[dict], au_rows: list[dict],
                 calendar: list[str]) -> tuple[list[dict], dict[str, tuple[date, ...]]]:
    """One-to-one pinned populations and complete intervals before DB values."""
    def keyed(rows):
        result = {(row["formation"], row["code"]): row for row in rows}
        saved.require(len(result) == len(rows), "duplicate formation/code identity")
        return result

    bb, bh, au = keyed(bb_windows), keyed(coverage), keyed(au_rows)
    saved.require(set(bb) == set(bh) and set(bb) <= set(au), "BI population mismatch")
    positions = {value: index for index, value in enumerate(calendar)}
    requested: dict[str, set[date]] = {}
    windows = []
    for key, row in sorted(bb.items()):
        prior = bh[key]
        saved.require(type(prior["potentially_influential_unresolved"]) is bool
                      and prior["potentially_influential_unresolved"] == (not prior["proven_numeric_failures"])
                      and prior["required_dates"] == len(row["required_observed_dates"]),
                      "BH failure/population disposition mismatch")
        if not prior["potentially_influential_unresolved"]:
            continue
        dates = row["required_observed_dates"]
        expected = calendar[positions[row["inventory_start"]]:positions[key[0]] + 1]
        saved.require(dates == expected and len(dates) >= 61 and row["prior_count"] == 60,
                      "BB must cover every required trading session")
        normal = au[key]["prior_nonfrozen_candidates"]
        frozen = normal["skipped_frozen_dates"]
        saved.require(normal["count"] == len(normal["dates"]) == row["prior_count"]
                      and not normal["missing_dates"] and not normal["invalid_dates"]
                      and normal["dates"] == sorted(set(normal["dates"]))
                      and frozen == sorted(set(frozen))
                      and not set(normal["dates"]) & set(frozen)
                      and sorted(normal["dates"] + frozen + [key[0]]) == dates
                      and au[key]["formation_state"]["state"] == row["formation_state"],
                      "AU/BB state-date partition mismatch")
        state = {"NONFROZEN": "observed", "LOCKED_POSITIVE": "observed", "FROZEN_ZERO": "frozen"}.get(
            row["formation_state"])
        saved.require(state is not None, "unresolved pinned formation state")
        converted = tuple(day(value) for value in dates)
        requested.setdefault(key[1], set()).update(converted)
        windows.append({"formation": key[0], "code": key[1], "required_dates": converted,
                        "frozen_dates": tuple(day(value) for value in frozen), "formation_state": state})
    return windows, {code: tuple(sorted(dates)) for code, dates in sorted(requested.items())}


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path,
                    *, database_reader=load_activity_failure_inputs) -> dict:
    values = {role: inputs.json(Path(spec["inputs"][role]["path"]),
                               spec["inputs"][role]["sha256"], role) for role in PINS}
    calendar, _, schedule = saved.calendar_schedule(values["calendar_manifest"], spec["parameters"])
    bb, _ = saved._bb_windows(values["bb_result"], calendar)
    bh = values["bh_result"]
    saved.require(bh.get("status") == "saved_input_coverage_audited"
                  and bh.get("returns_computed") is False
                  and (bh["coverage"]["unresolved_potentially_influential_windows"],
                       bh["coverage"]["windows_with_proven_numeric_failure"]) == BH_DISPOSITION_COUNTS,
                  "unexpected BH preflight receipt")
    windows, requested = plan_windows(bb, bh["coverage"]["windows"], values["au_result"]["rows"], calendar)
    saved.require((len(windows), len(requested)) == TARGET_COUNTS, "fixed BI target scope mismatch")
    previous_dispositions = bh["coverage"]["windows"]
    del values, bh  # Saved raw provenance inventories are no longer execution inputs.
    scope = {"database": spec["inputs"]["scan_database"], "window_count": len(windows),
             "calendar_dates": calendar,
             "planned_windows": [{"formation": row["formation"], "code": row["code"],
                 "required_dates": [d.isoformat() for d in row["required_dates"]],
                 "frozen_dates": [d.isoformat() for d in row["frozen_dates"]],
                 "formation_state": row["formation_state"]} for row in windows],
             "requested_code_dates": {code: [d.isoformat() for d in dates] for code, dates in requested.items()},
             "requested_unique_code_dates": sum(map(len, requested.values())),
             "input_manifest": inputs.manifest, "new_source_requests": 0, "returns_computed": False}
    saved.write_exclusive(output / "read-scope.json", saved.encoded(scope))
    # The scope is durable before the first SQLite metadata/value read.
    database_attempt = {"role": "scan_database", "path": spec["inputs"]["scan_database"]["path"],
                        "read_status": "started", "read_scope_sha256": saved.digest(saved.encoded(scope)),
                        "whole_database_hash": None}
    inputs.manifest.append(database_attempt)
    calendar_dates = tuple(day(value) for value in calendar)
    loaded = database_reader(Path(spec["inputs"]["scan_database"]["path"]), calendar_dates, requested)
    database_attempt.update(read_status="snapshot_captured", consumed_rows_sha256=loaded.snapshot.dataset_sha256)
    results = []
    for row in windows:
        result = activity_failure(calendar_dates, day(row["formation"]),
            loaded.observations[row["code"]], bb_required_observed_dates=row["required_dates"],
            bb_frozen_dates=row["frozen_dates"], bb_formation_state=row["formation_state"],
            threshold=Decimal(str(spec["parameters"]["threshold"])), lookback=spec["parameters"]["lookback"])
        results.append({"formation": row["formation"], "code": row["code"], **result.to_dict()})
    proven = [row for row in results if row["proven_failure"]]
    remaining = [row for row in results if not row["proven_failure"]]
    return {"schema": SCHEMA, "status": "numeric_failures_audited", "windows": results,
            "summary": {"input_windows": len(results), "proven_activity_failure_windows": len(proven),
                "remaining_potential_windows": len(remaining),
                "remaining_distinct_codes": len({row["code"] for row in remaining}),
                "unresolved_input_windows": sum(row["reason"] == "unresolved_input" for row in results),
                "failure_reasons": dict(sorted(Counter(row["reason"] for row in proven).items()))},
            "prior_dispositions": previous_dispositions, "scan_snapshot": loaded.snapshot.to_dict(),
            "paired_schedule": schedule, "input_manifest": inputs.manifest,
            "historical_eligibility_certified": False, "normal_baseline_certified": False,
            "historical_publication_certified": False, "holding_lot_denominator": None,
            "action_lot_denominator": None, "returns_computed": False, "holding_selection_executed": False,
            "D1_completed_studies": 0, "D2_completed_studies": 0, "promotion_allowed": False,
            "new_source_requests": 0,
            "failure_reuse_condition": "same consumed KIS turnover/state snapshot and unchanged AN predicate"}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path,
                  *, reader=saved.read_private) -> dict:
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
            result = evaluate_inputs(spec, inputs, output)
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
                    "D1_completed_studies": 0, "D2_completed_studies": 0}
        except BaseException as exc:
            if created:
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed",
                    "error_type": type(exc).__name__, "actual_inputs_attempted": inputs.manifest,
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
