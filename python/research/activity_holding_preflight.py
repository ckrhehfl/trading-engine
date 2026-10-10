"""Logged bounded holding-price diagnostics from independently verified BL outputs.

The actual committed specification is registered separately after BL succeeds.
This runner applies no actions and computes no returns or performance studies.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import stat
import traceback

from research import activity_preflight as saved
from research import activity_price_parity as prices
from research import experiment_log
from research.activity_holding_inputs import adapt_holding_scan, holding_requirements
from research.activity_partition_selection import selections_from_bl


SCHEMA = "activity-holding-price-preflight-v1"
BL_FILES = (
    "result.json", "specification.json", "source-manifest.json", "input-manifest.json",
    "read-scope.json", "raw-source-read-scope.json", "population-audit.json",
    "normal-input-audit.json", "restored-activity-audit.json",
)
BL_ROLES = {"bl_" + name.removesuffix(".json").replace("-", "_"): name for name in BL_FILES}
RECEIPT_SCOPE = "new_output_arithmetic_partition_and_provenance_links_only"
RECEIPT_FALSE = (
    "original_source_truth_verified", "typed_OHLC_fingerprint_recomputed",
    "historical_publication_certified", "classification_truth_certified",
    "original_raw_source_consumption_verified", "database_access", "api_access", "returns_computed",
)


def validate_spec(spec: dict, reference: dict) -> None:
    """Freeze the study/parameters and declaration shapes; actual pins come later."""
    saved.require(isinstance(spec, dict) and spec.get("schema") == SCHEMA
        and spec.get("study_id") == SCHEMA and spec.get("mode") == "discovery_holding_price_diagnostic"
        and spec.get("promotion_allowed") is False and spec.get("window") == saved.WINDOW
        and spec.get("reference_specification") == saved.REFERENCE
        and saved.encoded(spec.get("parameters")) == saved.encoded(reference["parameters"]),
        "fixed holding diagnostic specification mismatch")
    declared = spec.get("inputs")
    saved.require(type(declared) is dict and set(declared) == {
        *BL_ROLES, "bl_verification", "bj_scope", "scan_database"}, "exact holding diagnostic input roles required")
    paths = []
    for role, declaration in declared.items():
        saved.require(type(declaration) is dict, "input declaration must be a record")
        fields = {"path", "mode", "columns"} if role == "scan_database" else {"path", "sha256"}
        saved.require(set(declaration) == fields and type(declaration["path"]) is str
                      and Path(declaration["path"]).is_absolute(), "exact absolute input declaration required")
        paths.append(declaration["path"])
        if role == "scan_database":
            saved.require(declaration["mode"] == "ro" and declaration["columns"] == list(prices.READ_COLUMNS),
                          "exact bounded read-only scan declaration required")
        else:
            saved._pin(declaration["sha256"])
    saved.require(len(paths) == len(set(paths)), "input paths must be distinct")
    saved.require(len({str(Path(declared[role]["path"]).parent) for role in BL_ROLES}) == 1
                  and all(Path(declared[role]["path"]).name == name for role, name in BL_ROLES.items()),
                  "nine BL files must identify one exact output package")


def _read_population(path: Path) -> bytes:
    """Bound only BL's compact 35k-row population above the shared 64 MiB cap.

    The producer deduplicates availability metadata but retains every row and
    its proofs/provenance. Other roles retain saved.read_private's 64 MiB cap.
    """
    path = saved._safe_path(path)
    saved.private_directory(path.parent)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as handle:
        info = os.fstat(handle.fileno())
        ceiling = 128 * 1024**2
        saved.require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
                      and info.st_nlink == 1 and info.st_size <= ceiling,
                      "population input must be bounded single-link regular mode 0600")
        raw = handle.read(ceiling + 1)
        saved.require(len(raw) <= ceiling, "population input exceeds role-specific ceiling")
        return raw


def _canonical(value: object) -> bytes:
    """The independent verifier's package hash uses compact JSON, not encoded."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _manifest_agreement(spec: dict, manifest: list, rawscope: dict) -> None:
    """Join BL's internal consumed-input manifest without opening those sources."""
    expected = {}
    for role, declaration in spec["inputs"].items():
        if "path" in declaration:
            expected[role, declaration["path"]] = declaration["sha256"]
        else:
            for part, name in (("report", "report.json"), ("started", "started.json"), ("ledger", "requests.jsonl")):
                expected[role + ":" + part, declaration["root"] + "/" + name] = declaration[part + "_sha256"]
    for field in ("aq_raw_responses", "ar_raw_responses"):
        for row in rawscope[field]:
            identity = row["role"] + ":raw", row["path"]
            saved.require(identity not in expected, "duplicate BL raw-source manifest identity")
            expected[identity] = row["sha256"]
    actual = {}
    saved.require(type(manifest) is list, "BL input manifest must be records")
    for row in manifest:
        identity = row["role"], row["path"]
        saved._pin(row["sha256"])
        saved.require(identity not in actual and row.get("read_status") == "hash_verified"
                      and row.get("expected_sha256") == row["sha256"]
                      and type(row.get("bytes")) is int and row["bytes"] >= 0,
                      "duplicate or unverified BL internal input")
        actual[identity] = row["sha256"]
    saved.require(actual == expected, "BL internal input/specification/raw-source agreement mismatch")


def validate_package(data: dict, hashes: dict, receipt: dict, bj: dict, params: dict) -> tuple[tuple[date, ...], str]:
    """Validate byte-linked package/receipt boundaries before selection or quotes."""
    result, spec, sources, manifest = (data[name] for name in BL_FILES[:4])
    scope, rawscope, _, _, restored = (data[name] for name in BL_FILES[4:])
    saved.require(type(receipt) is dict and receipt.get("verified") is True
                  and receipt.get("scope") == RECEIPT_SCOPE and receipt.get("file_sha256") == hashes
                  and receipt.get("package_sha256") == saved.digest(_canonical([[name, hashes[name]] for name in BL_FILES]))
                  and all(receipt.get(name) is False for name in RECEIPT_FALSE)
                  and receipt.get("summary") == result.get("summary"), "independent BL verification receipt mismatch")
    saved.require(result.get("schema") == spec.get("schema") == scope.get("schema") == "activity-normal-input-assembly-v1"
                  and result.get("status") == "normal_inputs_assembled"
                  and spec.get("study_id") == "activity-normal-input-assembly-v1"
                  and spec.get("mode") == "discovery_normal_input_assembly"
                  and spec.get("window") == saved.WINDOW and spec.get("reference_specification") == saved.REFERENCE
                  and saved.encoded(spec.get("parameters")) == saved.encoded(params)
                  and spec.get("promotion_allowed") is False, "BL specification/status mismatch")
    saved.require(result.get("specification_sha256") == hashes["specification.json"]
                  and result.get("source_manifest_sha256") == hashes["source-manifest.json"]
                  and result.get("audit_sha256") == {name: hashes[name] for name in BL_FILES[4:]}
                  and result.get("input_manifest") == manifest, "BL package lineage mismatch")
    source_rows = sources.get("files")
    saved.require(type(source_rows) is list and sources.get("runtime_dependencies") == "stdlib_and_manifested_local_Python_only",
                  "BL source/runtime manifest mismatch")
    source_pins = {}
    for row in source_rows:
        path = row["path"]
        saved._pin(row["sha256"])
        saved.require(type(path) is str and path not in source_pins and not Path(path).is_absolute()
                      and ".." not in Path(path).parts and type(row.get("bytes")) is int and row["bytes"] >= 0,
                      "invalid BL source manifest identity")
        source_pins[path] = row["sha256"]
    saved.require(source_pins.get("configs/research/discovery/activity-normal-input-assembly-v1.json")
                  == hashes["specification.json"], "BL source specification link mismatch")
    _manifest_agreement(spec, manifest, rawscope)
    saved.require(scope.get("restored_activity_source") == spec["inputs"]["bi_result"]
                  and restored.get("upstream_result_sha256") == result["activity_input"]["source_sha256"]
                  == spec["inputs"]["bi_result"]["sha256"]
                  and restored.get("upstream_dataset_sha256") == scope.get("upstream_dataset_sha256")
                  == result["activity_input"]["upstream_dataset_sha256"], "BL restored activity lineage mismatch")
    activity_sha = restored["upstream_dataset_sha256"]
    saved._pin(activity_sha)
    saved.require(all(obj.get("typed_OHLC_fingerprint_recomputed") is False for obj in (scope, restored, result["activity_input"]))
                  and scope.get("database_access") is False and scope.get("returns_computed") is False,
                  "BL restored activity boundary mismatch")
    raw_calendar = scope.get("calendar_dates")
    saved.require(type(raw_calendar) is list and all(type(value) is str for value in raw_calendar), "BL restored calendar required")
    calendar = tuple(date.fromisoformat(value) for value in raw_calendar)
    saved.require([value.isoformat() for value in calendar] == raw_calendar, "canonical restored calendar required")
    requested = scope.get("requested_code_dates")
    observations = restored.get("restored_observations_by_code")
    saved.require(type(requested) is dict and type(observations) is dict and set(requested) == set(observations)
                  and all(type(observations[code]) is dict and days == sorted(set(days))
                          and set(days) == set(observations[code]) for code, days in requested.items()),
                  "BL restored activity request/observation agreement mismatch")
    saved.require(bj.get("schema") == "activity-price-action-scope-v1"
                  and bj.get("status") == "conservative_original_issue_scope_planned"
                  and bj.get("scope_kind") == "upper_bound_for_BI_potential_subset_only"
                  and bj.get("interval_policy") == "every index session from possible entry through fixed evaluation end inclusive"
                  and bj.get("evaluation_end") == params["end"]
                  and bj.get("calendar_dates") == [value.strftime("%Y%m%d") for value in calendar]
                  and bj.get("paired_schedule") == result.get("paired_schedule")
                  and all(bj.get(name) is False for name in ("returns_computed", "holding_selection_executed", "promotion_allowed", "database_access"))
                  and type(bj.get("requested_code_dates")) is dict, "BJ conservative scope/schema mismatch")
    return calendar, activity_sha


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    """Read pinned output evidence, persist exact union, then make one bounded scan."""
    declared, data, hashes = spec["inputs"], {}, {}
    for role, name in BL_ROLES.items():
        item = declared[role]
        data[name] = inputs.json(Path(item["path"]), item["sha256"], role)
        hashes[name] = item["sha256"]
    receipt = inputs.json(Path(declared["bl_verification"]["path"]), declared["bl_verification"]["sha256"], "bl_verification")
    bj = inputs.json(Path(declared["bj_scope"]["path"]), declared["bj_scope"]["sha256"], "bj_scope")
    calendar, activity_sha = validate_package(data, hashes, receipt, bj, spec["parameters"])
    arms = {arm: selections_from_bl(data["result.json"], data["population-audit.json"],
            data["normal-input-audit.json"], calendar, spec["parameters"], arm) for arm in ("D1", "D2")}
    requested = {code: list(days) for code, days in holding_requirements(
        arms["D1"], arms["D2"], calendar, spec["parameters"], bj["requested_code_dates"]).items()}
    read_scope = {"schema": SCHEMA + ":read-scope-v1", "calendar_dates": [day.strftime("%Y%m%d") for day in calendar],
        "requested_code_dates": requested, "BL_package_sha256": receipt["package_sha256"],
        "BL_file_sha256": hashes, "BL_verification_sha256": declared["bl_verification"]["sha256"],
        "BJ_scope_sha256": declared["bj_scope"]["sha256"], "activity_snapshot_sha256": activity_sha,
        "scan_database": declared["scan_database"], "input_manifest_before_database": inputs.manifest,
        "new_source_requests": 0, "automatic_retries": 0, "returns_computed": False}
    scope_raw = saved.encoded(read_scope)
    saved.write_exclusive(output / "read-scope.json", scope_raw)
    scope_sha = saved.digest(scope_raw)
    scan = prices.load_scan(Path(declared["scan_database"]["path"]),
        {"calendar_dates": read_scope["calendar_dates"], "intersection_code_dates": requested}, inputs, scope_sha) if requested else None
    quote_sha = None if scan is None else scan["typed_snapshot_sha256"]
    adapted = adapt_holding_scan(scan, calendar, requested, quote_snapshot_sha256=quote_sha,
        read_scope_sha256=scope_sha, activity_snapshot_sha256=activity_sha)
    counts = Counter()
    rows = []
    panel, positions = adapted.panel, {day: index for index, day in enumerate(calendar)}
    for observation in adapted.observations:
        series, index = panel[observation.code], positions[observation.observation_date]
        state = ("absent_requested" if observation.typed_values is None else "unresolved_observed" if observation.issues
                 else "frozen" if series.frozen[index] else "locked" if series.locked[index] else "observed")
        counts[state] += 1
        rows.append({"code": observation.code, "observation_date": observation.observation_date.isoformat(),
            "state": state, "issues": list(observation.issues), "typed_values": observation.typed_values,
            "provenance": saved.strict_json(observation.provenance_json)})
    summary = {"requested_codes": len(requested), "requested_code_dates": len(adapted.requested_coordinates),
        "absent_requested_code_dates": len(adapted.absent_coordinates),
        "unrequested_code_dates": len(adapted.unrequested_coordinates), "state_counts": dict(sorted(counts.items())),
        "signal_windows": {arm: sum(len(row.eligible_codes) for row in values) for arm, values in arms.items()}}
    audit = {"schema": SCHEMA + ":holding-input-audit-v1", "summary": summary,
        "read_scope_sha256": scope_sha, "activity_snapshot_sha256": activity_sha, "quote_snapshot_sha256": quote_sha,
        "snapshot_received_at": adapted.snapshot_received_at, "fingerprint": None if scan is None else scan["fingerprint"],
        "scan_metadata": None if scan is None else {key: value for key, value in scan.items() if key not in {"rows", "fingerprint"}},
        "requested_code_dates": requested, "absent_coordinates": [[code, day.isoformat()] for code, day in adapted.absent_coordinates],
        "unrequested_coordinates": [[code, day.isoformat()] for code, day in adapted.unrequested_coordinates], "rows": rows}
    audit_raw = saved.encoded(audit)
    saved.write_exclusive(output / "holding-input-audit.json", audit_raw)
    return {"schema": SCHEMA, "status": "holding_price_inputs_audited", "summary": summary,
        "read_scope_sha256": scope_sha, "holding_input_audit_sha256": saved.digest(audit_raw),
        "activity_snapshot_sha256": activity_sha, "quote_snapshot_sha256": quote_sha,
        "input_manifest": inputs.manifest, "database_access": bool(requested), "new_source_requests": 0,
        "automatic_retries": 0, "returns_computed": False, "actions_applied": False,
        "holding_selection_executed": False, "holding_lot_denominator": None, "action_lot_denominator": None,
        "event_coverage_certified": False, "no_event_coverage_certified": False, "price_basis_certified": False,
        "historical_vintage_certified": False, "historical_eligibility_certified": False,
        "D1_completed_studies": 0, "D2_completed_studies": 0, "promotion_allowed": False}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    """One durable logged attempt; retain private partial packages on any failure."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root, "run from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    population_path = Path(spec["inputs"]["bl_population_audit"]["path"])
    def bounded_reader(path):
        return _read_population(path) if path == population_path else saved.read_private(path)
    inputs = saved.PinnedInputs(bounded_reader if reader is None else reader)
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
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)), specification_sha256=saved.digest(raw))
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
            return {"status": result["status"], "output_dir": str(output), "summary": result["summary"],
                "result_sha256": saved.digest(saved.encoded(result)), "read_scope_sha256": result["read_scope_sha256"],
                "holding_input_audit_sha256": result["holding_input_audit_sha256"],
                "input_manifest_sha256": saved.digest(saved.encoded(inputs.manifest)),
                "source_manifest_sha256": result["source_manifest_sha256"], "D1_completed_studies": 0, "D2_completed_studies": 0}
        except BaseException as exc:
            if created:
                if not (output / "input-manifest.json").exists():
                    saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed", "error_type": type(exc).__name__,
                    "actual_inputs_attempted": inputs.manifest, "error_frames": [{"file": Path(frame.filename).name,
                        "line": frame.lineno, "function": frame.name} for frame in traceback.extract_tb(exc.__traceback__)],
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)), "specification_sha256": saved.digest(raw),
                    "partial_package_preserved": True, "automatic_retries": 0}))
            raise
    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw),
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources,
                "declared_inputs": spec["inputs"], "output_dir": str(output), "automatic_retries": 0}, evaluate=evaluate)
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
