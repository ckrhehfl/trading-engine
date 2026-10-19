"""Audit saved eligibility metadata before reading prices for one trend signal.

This command does not rank stocks, read quotes, execute books or infer that an
unresolved historical candidate is excluded. Reuse the existing source freezer,
private-file IO and durable Discovery logger instead of another SSH framework.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import os
from pathlib import Path
import resource
import signal
import time

from research import activity_preflight as saved
from research import experiment_log


SCHEMA = "price-trend-readiness-v1"
SOURCE = "python/research/price_trend_readiness.py"
FRONTIER = {
    "path": "/home/minjun4897/research-evidence/large-liquid-bs-cohort-normal-recovery-20261016-v1/run/frontier.json",
    "sha256": "10ae03788a728b153b157be5b68b470079f64fa68f15879a35c3ce44e1e9566a",
}


def validate_spec(spec: dict, reference: dict) -> None:
    saved.require(spec == {
        "schema": SCHEMA, "study_id": SCHEMA, "mode": "discovery_saved_metadata_only",
        "window": saved.WINDOW, "parameters": reference["parameters"],
        "reference_specification": saved.REFERENCE, "inputs": {"frontier": FRONTIER},
        "promotion_allowed": False,
    }, "fixed trend metadata specification mismatch")


def summarize_frontier(frontier: dict) -> dict:
    """Count whole-population proof gaps; a certified hash prefix is not a pool."""
    saved.require(frontier.get("schema") == "activity-cohort-scope-v1"
        and frontier.get("complete_target_prefix") is True,
        "completed original control frontier required")
    formations, population_counts, populations, windows = {}, {}, {}, {}
    for row in frontier["formations"]:
        formation = date.fromisoformat(row["formation"])
        saved.require(formation not in formations, "duplicate formation")
        arms = row["ACTIVITY"]
        k = arms["D1"]["target_ceiling"]
        saved.require(type(k) is int and 0 <= k <= 20
            and arms["D2"]["target_ceiling"] == k, "paired target ceiling mismatch")
        count = arms["D1"]["source_population_count"]
        saved.require(type(count) is int and count >= 0
            and arms["D2"]["source_population_count"] == count,
            "paired declared population count mismatch")
        formations[formation] = k
        population_counts[formation] = count
    for row in frontier["population_partition"]:
        key = date.fromisoformat(row["formation"]), row["code"]
        saved.require(key not in populations and key[0] in formations
            and row["normal_input_disposition"] in {"eligible", "excluded", "unresolved"},
            "complete unique explicit population disposition required")
        populations[key] = row
    for row in frontier["needed_normal_windows"]:
        key = date.fromisoformat(row["formation"]), row["code"]
        saved.require(key not in windows and key in populations
            and row["isin"] == populations[key]["isin"]
            and row["normal_input_disposition"] == populations[key]["normal_input_disposition"],
            "saved normal-window identity mismatch")
        normal = tuple(date.fromisoformat(day) for day in row["normal_dates"])
        saved.require(len(normal) == len(set(normal)) and tuple(sorted(normal)) == normal
            and all(day < key[0] for day in normal), "invalid preceding normal dates")
        windows[key] = normal
    rows = []
    for formation, k in sorted(formations.items()):
        population = {key: row for key, row in populations.items() if key[0] == formation}
        saved.require(len(population) == population_counts[formation], "declared population is incomplete")
        states = Counter(row["normal_input_disposition"] for row in population.values())
        eligible = {key for key, row in population.items() if row["normal_input_disposition"] == "eligible"}
        baselines = sum(key in windows and len(windows[key]) == 60 for key in eligible)
        rows.append({"formation": formation.isoformat(), "target_ceiling": k,
            "source_rows": len(population),
            "eligible": states["eligible"], "excluded": states["excluded"],
            "unresolved": states["unresolved"], "eligible_with_saved_60_normal_dates": baselines,
            "eligible_without_saved_60_normal_dates": len(eligible) - baselines,
            "selection_required": bool(k),
            "whole_pool_metadata_ready": not k or (not states["unresolved"] and baselines == len(eligible))})
    saved.require(bool(rows), "nonempty formation schedule required")
    active = [row for row in rows if row["selection_required"]]
    return {"schema": SCHEMA, "status": "saved_metadata_audited", "formations": rows,
        "summary": {"formations": len(rows), "nonzero_budget_formations": len(active),
            "source_rows": len(populations),
            "unresolved_nonzero_budget_candidate_windows": sum(row["unresolved"] for row in active),
            "eligible_baseline_gaps_nonzero_budget": sum(row["eligible_without_saved_60_normal_dates"] for row in active),
            "whole_pool_metadata_ready": all(row["whole_pool_metadata_ready"] for row in active)},
        "interpretation": "Gaps concern retained proof metadata, not an assertion that database prices are missing.",
        "independent_output_verification": "pending", "historical_eligibility_certified": False,
        "prices_read": False, "ranks_computed": False, "books_replayed": False,
        "returns_computed": False, "power_design_passed": False, "promotion_allowed": False,
        "D1_completed_studies": 0, "D2_completed_studies": 0}


def run(root: Path, spec_path: Path, output_dir: Path, runs_path: Path) -> dict:
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE,
        "run committed trend metadata source from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive output already exists")
    attempts = []
    def evaluate():
        started = time.monotonic()
        saved.private_directory(output.parent)
        output.mkdir(mode=0o700)
        saved._sync_directory(output.parent)
        saved.write_exclusive(output / "specification.json", raw)
        saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
        try:
            # Persist the intent before opening the only actual saved input.
            attempts.append({"role": "frontier", **FRONTIER, "read_status": "started"})
            saved.write_exclusive(output / "input-intent.json", saved.encoded(attempts))
            body = saved.read_private(Path(FRONTIER["path"]))
            saved.write_exclusive(output / "frontier.raw.json", body)
            attempts[0].update(bytes=len(body), actual_sha256=saved.digest(body))
            saved.require(saved.digest(body) == FRONTIER["sha256"], "frontier input hash mismatch")
            attempts[0]["read_status"] = "hash_verified"
            result = summarize_frontier(saved.strict_json(body))
            after, after_raw, _ = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(sources) == identity(after) and raw == after_raw, "source/runtime changed")
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)),
                specification_sha256=saved.digest(raw), input_sha256=FRONTIER["sha256"])
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            return result
        except BaseException as exc:
            saved.write_exclusive(output / "failure.json", saved.encoded({"error_type": type(exc).__name__,
                "partial_package_preserved": True, "automatic_retries": 0}))
            raise
        finally:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(attempts))
            saved.write_exclusive(output / "resources.json", saved.encoded({
                "elapsed_seconds": time.monotonic() - started,
                "user_cpu_seconds": usage.ru_utime, "system_cpu_seconds": usage.ru_stime,
                "peak_rss_kib": usage.ru_maxrss}))
    old = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=SCHEMA, specification_sha256=saved.digest(raw),
            parameters={"mode": spec["mode"], "source_manifest": sources,
                "declared_inputs": spec["inputs"], "output_dir": str(output), "automatic_retries": 0},
            window=spec["window"], runs_path=runs, evaluate=evaluate)
    finally:
        os.umask(old)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    def timeout(signum, frame):
        raise TimeoutError("trend metadata wall limit exceeded")
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(300)
    try:
        result = run(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
        print(saved.encoded(result["result"]["summary"]).decode())
    finally:
        signal.alarm(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
