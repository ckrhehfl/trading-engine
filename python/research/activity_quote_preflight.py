"""Logged bounded spin-off quote diagnostics from verified original packages.

Separately registered price-free plans scope reads without certifying event,
unit, custody delivery, historical vintage or actual performance claims.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import os
from pathlib import Path
import re
import time
import traceback

from research import activity_exit_preflight as child
from research import activity_holding_preflight as holding
from research import activity_preflight as saved
from research import activity_price_parity as prices
from research import experiment_log
from research.activity_holding_inputs import adapt_holding_scan
from research.activity_holding_restore import restore_holding_inputs
from research.activity_paired_replay import BM_ROLES, PACKAGE_ROLES, prepare_inputs
from research.activity_quote_extension import extend_holding_quotes

SCHEMA = "activity-quote-extension-preflight-v1"
PLAN_SCHEMA = "activity-price-free-event-coordinate-plan-v1"
FALSE = ("api_access", "returns_computed", "books_computed", "actions_applied", "promotion_allowed",
    "source_truth_certified", "event_coverage_certified", "no_event_coverage_certified",
    "price_basis_certified", "historical_vintage_certified", "historical_eligibility_certified",
    "unit_bridge_certified", "custody_delivery_certified", "actual_exits_certified",
    "atomic_source_vintage_certified", "actual_study_completed")

def validate_spec(spec: dict, reference: dict) -> None:
    """Retain original eighteen-package contracts plus one plan and ro database."""
    saved.require(type(spec) is dict and spec.get("schema") == spec.get("study_id") == SCHEMA
        and spec.get("mode") == "discovery_quote_extension_diagnostic", "fixed quote diagnostic required")
    declared = spec.get("inputs")
    saved.require(type(declared) is dict and set(declared) == {*PACKAGE_ROLES, "coordinate_plan", "scan_database"},
                  "exact original package, coordinate plan and database roles required")
    child.validate_spec({**spec, "schema": child.SCHEMA, "study_id": child.SCHEMA,
        "mode": "discovery_conditional_exit_scope", "inputs": {role: declared[role] for role in PACKAGE_ROLES}}, reference)
    plan, scan = declared["coordinate_plan"], declared["scan_database"]
    saved.require(type(plan) is dict and set(plan) == {"path", "sha256"}, "exact coordinate plan pin required")
    saved._pin(plan["sha256"])
    saved.require(type(scan) is dict and set(scan) == {"path", "mode", "columns"}
                  and scan["mode"] == "ro" and scan["columns"] == list(prices.READ_COLUMNS), "exact ro typed scan required")
    paths = [row["path"] for row in declared.values()]
    saved.require(all(type(path) is str and Path(path).is_absolute() for path in paths)
                  and len(paths) == len(set(paths)), "distinct absolute input paths required")

def extension_coordinates(plan: dict, prepared) -> dict[str, list[str]]:
    """Union successor coverage/anchors minus every original request. Effective
    dates may be non-sessions; anchors require spent sessions with a following
    calendar day. Preserve disjoint parent requirements without price repair."""
    calendar = prepared.calendar
    saved.require(type(plan) is dict and set(plan) == {"schema", "original_quote_snapshot_sha256",
        "original_read_scope_sha256", "events"}
        and plan["schema"] == PLAN_SCHEMA and plan["original_quote_snapshot_sha256"] == prepared.quote_snapshot_sha256
        and plan["original_read_scope_sha256"] == prepared.read_scope_sha256,
        "coordinate plan original/calendar identity mismatch")
    saved.require(calendar and tuple(sorted(set(calendar))) == calendar
                  and all(type(day) is date for day in calendar), "ordered unique calendar required")
    original = set(prepared.holding_inputs.requested_coordinates)
    required = {(code, date.fromisoformat(day)) for code, days in prepared.required_code_dates.items() for day in days}
    saved.require(original == required and bool(original), "exact nonempty original requirement union required")
    events = plan["events"]
    saved.require(type(events) is list and bool(events), "nonempty price-free event plan required")
    additions, ids, parents, successors = set(), set(), set(), set()
    start, end = date.fromisoformat(saved.WINDOW["start"]), date.fromisoformat(saved.WINDOW["end"])
    for row in events:
        saved.require(type(row) is dict and set(row) == {"event_id", "kind", "code", "successor_code",
            "old_basis_on", "effective_on", "retained_available_on", "new_available_on"}
            and row["kind"] == "compulsory_spin_off", "only exact price-free compulsory spin-offs supported")
        event_id, code, successor = row["event_id"], row["code"], row["successor_code"]
        saved.require(type(event_id) is str and bool(event_id.strip()) and event_id not in ids
            and all(type(value) is str and re.fullmatch(r"[0-9A-Z]{6}", value) for value in (code, successor))
            and code != successor and code not in parents and successor not in successors,
            "duplicate event or invalid issue code")
        days = []
        for name in ("old_basis_on", "effective_on", "retained_available_on", "new_available_on"):
            value = row[name]
            saved.require(type(value) is str, "event dates must be canonical ISO text")
            day = date.fromisoformat(value)
            saved.require(day.isoformat() == value and start <= day <= end, "event coordinate outside spent window")
            days.append(day)
        old, effect, retained, new = days
        saved.require(old < effect <= min(retained, new), "event anchor chronology mismatch")
        saved.require(all(day in calendar[:-1] for day in (old, retained, new)), "basis/availability must be bounded sessions")
        parent_days = {day for original_code, day in required if original_code == code}
        saved.require(parent_days and min(parent_days) <= effect <= max(parent_days), "event outside original parent scope")
        additions.update((successor, day) for day in parent_days if day >= effect and day >= new)
        additions.update(((code, old), (code, retained), (successor, new)))
        ids.add(event_id)
        parents.add(code)
        successors.add(successor)
    saved.require(not parents & successors, "chained events require a separately reviewed producer")
    additions.difference_update(original)  # Includes original absent-requested cells.
    saved.require(additions and not additions & original, "nonempty disjoint extension required")
    saved.require(all(start <= day <= end and day in calendar[:-1] for _, day in additions), "extension outside spent calendar")
    return {code: [day.isoformat() for candidate, day in sorted(additions) if candidate == code]
            for code in sorted({code for code, _ in additions})}

def _audit(scan: dict, adapted, requested: dict, scope_sha: str) -> dict:
    """Use the original diagnostic audit shape so the existing restore seam applies."""
    counts, rows, panel = Counter(), [], adapted.panel
    positions = {day: index for index, day in enumerate(adapted.calendar)}
    for observation in adapted.observations:
        series, index = panel[observation.code], positions[observation.observation_date]
        state = ("absent_requested" if observation.typed_values is None else "unresolved_observed" if observation.issues
                 else "frozen" if series.frozen[index] else "locked" if series.locked[index] else "observed")
        counts[state] += 1
        rows.append(dict(code=observation.code, observation_date=observation.observation_date.isoformat(), state=state,
            issues=list(observation.issues), typed_values=observation.typed_values,
            provenance=saved.strict_json(observation.provenance_json)))
    return dict(schema=holding.SCHEMA + ":holding-input-audit-v1", read_scope_sha256=scope_sha,
        activity_snapshot_sha256=adapted.activity_snapshot_sha256, quote_snapshot_sha256=adapted.quote_snapshot_sha256,
        snapshot_received_at=adapted.snapshot_received_at, fingerprint=scan["fingerprint"],
        scan_metadata={key: value for key, value in scan.items() if key not in {"rows", "fingerprint"}},
        requested_code_dates=requested, absent_coordinates=[[code, day.isoformat()] for code, day in adapted.absent_coordinates],
        unrequested_coordinates=[[code, day.isoformat()] for code, day in adapted.unrequested_coordinates], rows=rows,
        summary=dict(requested_codes=len(requested), requested_code_dates=len(adapted.requested_coordinates),
            absent_requested_code_dates=len(adapted.absent_coordinates), unrequested_code_dates=len(adapted.unrequested_coordinates),
            state_counts=dict(sorted(counts.items()))))

def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path) -> dict:
    """Restore the original evidence, persist the new scope, then perform one scan."""
    declared, raw, pins = spec["inputs"], {}, {}
    for role in PACKAGE_ROLES:
        row = declared[role]
        pins[role] = row["sha256"]
        raw[role] = inputs.read(Path(row["path"]), pins[role], role)
    prepared = prepare_inputs({"files": {name: raw[role] for role, name in holding.BL_ROLES.items()},
        "verification": raw["bl_verification"], "bj_scope": raw["bj_scope"]},
        {"files": {name: raw[role] for role, name in BM_ROLES.items()}, "verification": raw["bm_verification"]},
        parameters=spec["parameters"], expected_pins=pins)
    row = declared["coordinate_plan"]
    plan = inputs.json(Path(row["path"]), row["sha256"], "coordinate_plan")
    requested = extension_coordinates(plan, prepared)
    scope = dict(schema=holding.SCHEMA + ":read-scope-v1", calendar_dates=[day.strftime("%Y%m%d") for day in prepared.calendar],
        requested_code_dates=requested, activity_snapshot_sha256=prepared.activity_snapshot_sha256,
        original_quote_snapshot_sha256=prepared.quote_snapshot_sha256, original_read_scope_sha256=prepared.read_scope_sha256,
        original_input_sha256=pins, coordinate_plan_sha256=row["sha256"], scan_database=declared["scan_database"],
        input_manifest_before_database=list(inputs.manifest), new_source_requests=0, automatic_retries=0, returns_computed=False)
    scope_raw = saved.encoded(scope)
    saved.write_exclusive(output / "read-scope.json", scope_raw)
    scope_sha = saved.digest(scope_raw)
    scan = prices.load_scan(Path(declared["scan_database"]["path"]),
        {"calendar_dates": scope["calendar_dates"], "intersection_code_dates": requested}, inputs, scope_sha)
    component = adapt_holding_scan(scan, prepared.calendar, requested, quote_snapshot_sha256=scan["typed_snapshot_sha256"],
        read_scope_sha256=scope_sha, activity_snapshot_sha256=prepared.activity_snapshot_sha256)
    audit = _audit(scan, component, requested, scope_sha)
    restored = restore_holding_inputs(saved.strict_json(saved.encoded(audit)), scope,
        quote_snapshot_sha256=component.quote_snapshot_sha256, activity_snapshot_sha256=prepared.activity_snapshot_sha256,
        read_scope_sha256=scope_sha)
    composite = extend_holding_quotes(prepared.holding_inputs, restored)
    envelope = dict(schema="activity-quote-extension-envelope-v1", original_quote_snapshot_sha256=prepared.quote_snapshot_sha256,
                    read_scope=scope, holding_input_audit=audit)
    payloads = {"holding-input-audit.json": saved.encoded(audit), "quote-extension.json": saved.encoded(envelope),
                "composite-lineage.json": composite.fingerprint_json}
    for name, payload in payloads.items():
        saved.write_exclusive(output / name, payload)
    return dict(schema=SCHEMA, status="quote_extension_inputs_audited", summary=audit["summary"],
        original_quote_snapshot_sha256=prepared.quote_snapshot_sha256, original_read_scope_sha256=prepared.read_scope_sha256,
        original_input_sha256=pins, coordinate_plan_sha256=row["sha256"], read_scope_sha256=scope_sha,
        activity_snapshot_sha256=prepared.activity_snapshot_sha256, extension_quote_snapshot_sha256=component.quote_snapshot_sha256,
        composite_quote_snapshot_sha256=composite.quote_snapshot_sha256, input_manifest=inputs.manifest,
        output_sha256={name: saved.digest(payload) for name, payload in payloads.items()}, database_access=True,
        original_database_reread=False, original_inputs_preserved=True, new_source_requests=0, automatic_retries=0,
        D1_completed_studies=0, D2_completed_studies=0, **{name: False for name in FALSE})

def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None) -> dict:
    """One durable logged attempt over committed frozen source and specification."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root, "run from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    source_path = Path(__file__).resolve().relative_to(root).as_posix()
    saved.require(any(row["path"] == source_path and row["sha256"] == saved.digest(Path(__file__).read_bytes())
        for row in sources["files"]), "producer must be promoted to committed frozen source before actual invocation")
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    population = Path(spec["inputs"]["bl_population_audit"]["path"])
    inputs = saved.PinnedInputs((lambda path: holding._read_population(path) if path == population else saved.read_private(path))
                                if reader is None else reader)

    def evaluate():
        created, started = False, time.monotonic()
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
            resources = saved.encoded(child._resources(started))
            saved.write_exclusive(output / "resource-manifest.json", resources)
            result.update(source_manifest_sha256=saved.digest(saved.encoded(sources)), specification_sha256=saved.digest(raw),
                          resource_manifest_sha256=saved.digest(resources))
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(inputs.manifest))
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            return dict(status=result["status"], output_dir=str(output), summary=result["summary"],
                result_sha256=saved.digest(saved.encoded(result)), output_sha256=result["output_sha256"],
                D1_completed_studies=0, D2_completed_studies=0)
        except BaseException as exc:
            if created:
                for name, value in (("resource-manifest.json", child._resources(started)), ("input-manifest.json", inputs.manifest)):
                    if not (output / name).exists():
                        saved.write_exclusive(output / name, saved.encoded(value))
                saved.write_exclusive(output / "failure.json", saved.encoded(dict(status="failed", error_type=type(exc).__name__,
                    actual_inputs_attempted=inputs.manifest, error_frames=[dict(file=Path(frame.filename).name,
                        line=frame.lineno, function=frame.name) for frame in traceback.extract_tb(exc.__traceback__)],
                    source_manifest_sha256=saved.digest(saved.encoded(sources)), specification_sha256=saved.digest(raw),
                    partial_package_preserved=True, automatic_retries=0)))
            raise

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw),
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources,
                "declared_inputs": spec["inputs"], "output_dir": str(output), "automatic_retries": 0}, evaluate=evaluate)
    finally:
        os.umask(previous)

def main(argv=None) -> int:
    """Print only the completion receipt or a sanitized failure type."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("spec", "output-dir", "runs-path"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(saved.encoded(dict(status="failed", error_type=type(exc).__name__, automatic_retries=0)).decode())
        return 1
    print(saved.encoded(record["result"]).decode())
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
