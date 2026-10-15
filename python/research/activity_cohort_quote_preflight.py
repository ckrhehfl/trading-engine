"""Logged BS successor/basis quotes from a verified full holding package.

The separately registered price-free coordinate plan supplies explicit event
date roles. This diagnostic preserves original requests, including absences;
it establishes no action coverage, unit bridge, historical vintage or returns.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import re
import resource
import signal
import time
import traceback

from research import activity_cohort_preflight as cohort
from research import activity_cohort_holding_preflight as holding
from research import activity_preflight as saved
from research import activity_price_parity as prices
from research import activity_quote_preflight as quotes
from research import experiment_log
from research.activity_exit_bounds import validate_exit_bounds
from research.activity_holding_inputs import adapt_holding_scan
from research.activity_holding_restore import restore_holding_inputs
from research.activity_pair_calibration import PARAMETERS
from research.activity_quote_extension import extend_holding_quotes


SCHEMA = "activity-cohort-quote-preflight-v1"
PLAN_SCHEMA = "activity-cohort-price-free-event-coordinate-plan-v1"
MODE = "discovery_cohort_quote_extension_diagnostic"
SOURCE_PATH = "python/research/activity_cohort_quote_preflight.py"
SAVED_ROLES = (*cohort.HOLDING_ROLES, "holding_verification", "coordinate_plan")
FALSE_FLAGS = tuple(sorted(set((*quotes.FALSE, *holding.FALSE_FLAGS,
    "actual_input_reuse_readiness_established", "compulsory_successor_closure_complete",
    "resource_enforcement_independently_verified", *cohort.frontier.POWER_FLAGS))))


def validate_spec(spec: dict, reference: dict) -> None:
    """Require explicit generic pins; no actual package identity is invented."""
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "window",
        "reference_specification", "parameters", "controls", "design", "inputs",
        "holding_verification_identity", "promotion_allowed", *cohort.frontier.POWER_FLAGS}
        and spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] == MODE
        and spec["promotion_allowed"] is False and all(spec[name] is False for name in cohort.frontier.POWER_FLAGS)
        and spec["controls"] == list(cohort.CONTROL_IDS) and spec["design"] == cohort.frontier.DESIGN
        and spec["reference_specification"] == saved.REFERENCE, "fixed BS quote diagnostic contract required")
    cohort._same(spec["parameters"], PARAMETERS, "canonical cohort parameters required")
    cohort._same(spec["parameters"], reference["parameters"], "reference parameter representations changed")
    cohort._same(spec["window"], saved.WINDOW, "spent discovery window required")
    declared, identity = spec["inputs"], spec["holding_verification_identity"]
    saved.require(type(declared) is dict and set(declared) == {*SAVED_ROLES, "scan_database"}, "exact BS quote input roles required")
    paths = []
    for role in SAVED_ROLES:
        row = declared[role]
        saved.require(type(row) is dict and set(row) == {"path", "sha256"}, "exact saved input pin required")
        saved._pin(row["sha256"])
    scan = declared["scan_database"]
    saved.require(type(scan) is dict and set(scan) == {"path", "mode", "columns"} and scan["mode"] == "ro"
        and scan["columns"] == list(prices.READ_COLUMNS), "exact read-only typed scan required")
    for row in declared.values():
        saved.require(type(row["path"]) is str and Path(row["path"]).is_absolute()
            and ".." not in Path(row["path"]).parts, "absolute input path required")
        paths.append(str(saved._safe_path(Path(row["path"]))))
    saved.require(len(paths) == len(set(paths)), "input aliases or duplicate reads refused")
    saved.require(len({str(Path(declared[role]["path"]).parent) for role in cohort.HOLDING_ROLES}) == 1
        and all(Path(declared[role]["path"]).name == name for role, name in cohort.HOLDING_ROLES.items()),
        "one exact selected holding package required")
    saved.require(type(identity) is dict and set(identity) == cohort.IDENTITY_FIELDS, "explicit holding verification identity required")
    for key in ("expected_commit", "expected_normal_commit", "collector_commit"):
        cohort._commit(identity[key])
    saved.require(identity["collector_commit"] == cohort.OPERATIONAL_COMMIT, "unchanged operational collector identity required")
    for key in cohort.IDENTITY_FIELDS - {"expected_commit", "expected_normal_commit", "collector_commit", "dependency_sha256", "file_sha256", "input_sha256"}:
        saved._pin(identity[key])
    for key, names in (("file_sha256", cohort.HOLDING_FILES), ("input_sha256", cohort.HOLDING_INPUT_ROLES),
                       ("dependency_sha256", ("normal", "holding"))):
        saved.require(type(identity[key]) is dict and set(identity[key]) == set(names), "exact holding verification pin membership required")
        for pin in identity[key].values(): saved._pin(pin)
    saved.require(identity["file_sha256"]["specification.json"] == identity["specification_sha256"]
        and all(declared[role]["sha256"] == identity["file_sha256"][name] for role, name in cohort.HOLDING_ROLES.items()),
        "selected holding byte pins differ from complete verified package")


@dataclass(frozen=True)
class PreparedOriginal:
    declarations: tuple
    original: object
    scope: dict
    ordinary: dict
    input_sha256: dict


def prepare_original(spec: dict, inputs: saved.PinnedInputs) -> PreparedOriginal:
    """Reuse strict receipt, full-family and typed restoration checks unchanged."""
    validate_spec(spec, {"parameters": PARAMETERS})
    declared, identity = spec["inputs"], spec["holding_verification_identity"]
    def load(role):
        row = declared[role]
        return inputs.json(Path(row["path"]), row["sha256"], role)
    receipt = load("holding_verification")
    cohort._receipt(receipt, identity)
    result, declarations, scope, ordinary = (load("holding_result"), cohort._declarations(load("cohort_declarations")),
        load("holding_read_scope"), load("ordinary_exit_scope"))
    cohort._family(result, scope, receipt, declarations, ordinary, identity)
    audit = load("holding_input_audit")
    cohort._same(audit.get("summary"), result.get("holding_summary"), "holding audit result summary changed")
    cohort._same(audit.get("summary"), receipt.get("holding_summary"), "independent typed summary changed")
    original = restore_holding_inputs(audit, scope, quote_snapshot_sha256=receipt["quote_snapshot_sha256"],
        activity_snapshot_sha256=receipt["activity_snapshot_sha256"], read_scope_sha256=declared["holding_read_scope"]["sha256"])
    proofs = cohort.cohort_proof_selections(declarations, original.calendar, spec["parameters"])
    ordinary = validate_exit_bounds(ordinary, proofs["D1"], proofs["D2"], original, spec["parameters"],
        scope["requested_code_dates"], scope["issue_isins"], input_sha256=identity["input_sha256"])
    return PreparedOriginal(declarations, original, scope, ordinary, identity["input_sha256"])


def _day(value):
    saved.require(type(value) is str, "explicit canonical ISO event date required")
    day = date.fromisoformat(value)
    saved.require(day.isoformat() == value, "canonical ISO event date required")
    return day


def extension_coordinates(plan: dict, prepared: PreparedOriginal, spec: dict) -> dict[str, list[str]]:
    """Project isolated canonical event date roles over ALL full original origins.

    This metadata projection mirrors the existing action decoder's full-window
    successor requests. It reads no prices or exits to select the union. Chains
    require a separately reviewed producer; source truth and plan completeness
    remain explicit caller obligations, then final connector checks exact scope.
    """
    original, declared = prepared.original, spec["inputs"]
    saved.require(type(plan) is dict and set(plan) == {"schema", "original_quote_snapshot_sha256",
        "original_read_scope_sha256", "activity_snapshot_sha256", "cohort_declarations_sha256",
        "ordinary_exit_scope_sha256", "events", "requested_code_dates"} and plan["schema"] == PLAN_SCHEMA
        and plan["original_quote_snapshot_sha256"] == original.quote_snapshot_sha256
        and plan["original_read_scope_sha256"] == original.read_scope_sha256
        and plan["activity_snapshot_sha256"] == original.activity_snapshot_sha256
        and plan["cohort_declarations_sha256"] == declared["cohort_declarations"]["sha256"]
        and plan["ordinary_exit_scope_sha256"] == declared["ordinary_exit_scope"]["sha256"],
        "price-free plan must bind the verified new full BS holding package")
    saved.require(type(plan["events"]) is list, "explicit price-free event list required")
    calendar, identities = original.calendar, dict(prepared.scope["issue_isins"])
    full = prepared.ordinary["entries"]
    requested = set(original.requested_coordinates)
    additions, ids, parents, successors = set(), set(), set(), set()
    fields = {
        "compulsory_spin_off": {"successor_code", "successor_isin", "last_eligible_entry_on", "record_on", "effective_on",
            "retained_available_on", "new_available_on", "old_basis_on", "retained_basis_on", "new_basis_on"},
        "compulsory_stock_exchange": {"successor_code", "successor_isin", "effective_on", "available_on", "old_basis_on", "new_basis_on"},
        "final_cash_payment": {"last_trading_on", "record_on", "paid_on", "basis_on"}}
    for row in plan["events"]:
        saved.require(type(row) is dict and row.get("kind") in fields
            and set(row) == {"event_id", "kind", "code", "isin", *fields[row["kind"]]}, "exact canonical price-free event fields required")
        event_id, kind, code, isin = (row[name] for name in ("event_id", "kind", "code", "isin"))
        saved.require(type(event_id) is str and bool(event_id) and event_id == event_id.strip() and event_id not in ids
            and type(code) is str and re.fullmatch(r"[0-9A-Z]{6}", code)
            and identities.get(code) == isin and code not in parents, "event/origin identity or duplicate event mismatch")
        days = {name: _day(row[name]) for name in fields[kind] if name.endswith("_on")}
        anchors = [(code, days["basis_on"])] if kind == "final_cash_payment" else [(code, days["old_basis_on"])]
        if kind == "final_cash_payment":
            first, on = days["last_trading_on"], days["paid_on"]
            saved.require(first < days["record_on"] <= on and days["basis_on"] == first, "final cash date roles disagree")
        else:
            successor, successor_isin = row["successor_code"], row["successor_isin"]
            saved.require(type(successor) is str and re.fullmatch(r"[0-9A-Z]{6}", successor) and successor != code
                and type(successor_isin) is str and re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", successor_isin)
                and (successor not in identities or identities[successor] == successor_isin), "successor identity conflict")
            identities[successor] = successor_isin
            on = days["effective_on"]
            first = days["last_eligible_entry_on"] if kind == "compulsory_spin_off" else on
            available = days["new_available_on"] if kind == "compulsory_spin_off" else days["available_on"]
            saved.require(days["old_basis_on"] < on <= available and days["new_basis_on"] == available, "exchange basis/availability date roles disagree")
            anchors.append((successor, days["new_basis_on"]))
            if kind == "compulsory_spin_off":
                saved.require(first <= days["record_on"] <= on <= days["retained_available_on"]
                    and days["retained_basis_on"] == days["retained_available_on"], "spin-off retained/record date roles disagree")
                anchors.append((code, days["retained_basis_on"]))
            additions.update((successor, day) for parent, day in requested if parent == code and day >= max(on, available))
            successors.add(successor)
        saved.require(any(entry["code"] == code and entry["isin"] == isin
            and _day(entry["entry_on"]) <= on and first <= _day(entry["original_end_on"]) for entry in full),
            "event lifetime outside full original potential-origin windows")
        saved.require(all(day in calendar[:-1] for _, day in anchors), "basis and availability coordinates require bounded spent sessions")
        additions.update(anchors)
        ids.add(event_id); parents.add(code)
    saved.require(not parents & successors, "chained events require a separately reviewed coordinate producer")
    additions.difference_update(requested)  # Original requested absences remain immutable.
    saved.require(all(day in calendar[:-1] and date.fromisoformat(saved.WINDOW["start"]) <= day <= date.fromisoformat(saved.WINDOW["end"])
        for _, day in additions), "extension outside bounded spent calendar")
    requested = {code: [str(day) for candidate, day in sorted(additions) if candidate == code]
                 for code in sorted({code for code, _ in additions})}
    cohort._same(plan["requested_code_dates"], requested, "registered coordinates differ from exact full successor and basis union")
    return requested


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path, *, scan_loader=None) -> dict:
    prepared = prepare_original(spec, inputs)
    declared, original = spec["inputs"], prepared.original
    row = declared["coordinate_plan"]
    plan = inputs.json(Path(row["path"]), row["sha256"], "coordinate_plan")
    requested = extension_coordinates(plan, prepared, spec)
    scope = dict(schema=quotes.holding.SCHEMA + ":read-scope-v1", producer_schema=SCHEMA,
        scope_kind="complete_BS_full_successor_and_basis_union_minus_original_requests",
        calendar_dates=[day.strftime("%Y%m%d") for day in original.calendar], requested_code_dates=requested,
        activity_snapshot_sha256=original.activity_snapshot_sha256, original_quote_snapshot_sha256=original.quote_snapshot_sha256,
        original_read_scope_sha256=original.read_scope_sha256, original_input_sha256=prepared.input_sha256,
        holding_package_input_sha256={role: declared[role]["sha256"] for role in SAVED_ROLES},
        coordinate_plan_sha256=row["sha256"], scan_database=declared["scan_database"],
        input_manifest_before_database=list(inputs.manifest), new_source_requests=0, automatic_retries=0, returns_computed=False)
    scope_raw = saved.encoded(scope); scope_sha = saved.digest(scope_raw)
    saved.write_exclusive(output / "read-scope.json", scope_raw)
    output_pins = {"read-scope.json": scope_sha}
    composite, component, summary = original, None, {"requested_codes": 0, "requested_code_dates": 0,
        "absent_requested_code_dates": 0, "unrequested_code_dates": 0, "state_counts": {}}
    if requested:
        loader = prices.load_scan if scan_loader is None else scan_loader
        scan = loader(Path(declared["scan_database"]["path"]),
            {"calendar_dates": scope["calendar_dates"], "intersection_code_dates": requested}, inputs, scope_sha)
        component = adapt_holding_scan(scan, original.calendar, requested, quote_snapshot_sha256=scan["typed_snapshot_sha256"],
            read_scope_sha256=scope_sha, activity_snapshot_sha256=original.activity_snapshot_sha256)
        del scan["rows"]
        audit = quotes._audit(scan, component, requested, scope_sha)
        output_pins["holding-input-audit.json"] = holding._write_audit(output / "holding-input-audit.json", audit)
        # This envelope is new evidence bound to the genuine BS original. No old
        # envelope, hash, requested absence or quote row is relabeled/replaced.
        envelope = dict(schema="activity-quote-extension-envelope-v1", original_quote_snapshot_sha256=original.quote_snapshot_sha256,
            read_scope=scope, holding_input_audit=audit)
        output_pins["quote-extension.json"] = holding._write_audit(output / "quote-extension.json", envelope)
        summary = audit["summary"]
        del scan, audit, envelope
        composite = extend_holding_quotes(original, component)
        saved.write_exclusive(output / "composite-lineage.json", composite.fingerprint_json)
        output_pins["composite-lineage.json"] = saved.digest(composite.fingerprint_json)
    return dict(schema=SCHEMA, status="cohort_quote_extension_inputs_audited", summary=summary,
        original_quote_snapshot_sha256=original.quote_snapshot_sha256, original_read_scope_sha256=original.read_scope_sha256,
        activity_snapshot_sha256=original.activity_snapshot_sha256, extension_quote_snapshot_sha256=None if component is None else component.quote_snapshot_sha256,
        composite_quote_snapshot_sha256=composite.quote_snapshot_sha256, read_scope_sha256=scope_sha,
        holding_package_input_sha256=scope["holding_package_input_sha256"], original_input_sha256=prepared.input_sha256,
        coordinate_plan_sha256=row["sha256"], output_sha256=output_pins, database_access=bool(requested),
        original_database_reread=False, original_inputs_preserved=True, original_quote_requests_preserved=True,
        independent_output_verification="pending", new_source_requests=0, automatic_retries=0,
        D1_completed_studies=0, D2_completed_studies=0, performance_studies_added=0, **dict.fromkeys(FALSE_FLAGS, False))


def _secondary_receipt(output: Path, name: str, primary: BaseException, errors=None, *, operation=None, secondary=None) -> None:
    """Best effort, error types only; failure evidence must not mask its cause."""
    try:
        if errors is None: errors = [dict(operation=operation, error_type=type(secondary).__name__)]
        saved.write_exclusive(output / name, saved.encoded(dict(schema=SCHEMA + ":secondary-failure-v1",
            status="failed", primary_error_type=type(primary).__name__, secondary_errors=errors,
            automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0, performance_studies_added=0)))
    except BaseException:
        pass  # An unavailable cleanup journal cannot replace the primary failure.


def _preserve_failure(output, error, inputs, started, source_pin, spec_pin):
    """Independently attempt every artifact, including payload construction."""
    failures = []
    artifacts = (
        ("resource-manifest.json", lambda: holding._resources(started)),
        ("input-manifest.json", lambda: inputs.manifest),
        ("failure.json", lambda: dict(status="failed", error_type=type(error).__name__,
            error_frames=[dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                for frame in traceback.extract_tb(error.__traceback__)], actual_inputs_attempted=inputs.manifest,
            source_manifest_sha256=source_pin, specification_sha256=spec_pin, partial_package_preserved=True,
            automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0, performance_studies_added=0,
            **dict.fromkeys(FALSE_FLAGS, False))))
    for name, payload in artifacts:
        try:
            if not (output / name).exists(): saved.write_exclusive(output / name, saved.encoded(payload()))
        except BaseException as secondary:
            try:
                failures.append(dict(operation=name, error_type=type(secondary).__name__))
            except BaseException:
                pass  # Continue independent artifact attempts even if metadata allocation fails.
    if failures: _secondary_receipt(output, "failure-cleanup.json", error, failures)


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None, freeze=None, evaluator=None):
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH, "run committed BS quote caller from root")
    freezer = saved.freeze_sources if freeze is None else freeze
    sources, raw, spec = freezer(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive BS quote output already exists")
    audit_path = Path(spec["inputs"]["holding_input_audit"]["path"])
    def bounded_reader(path): return cohort._read_large(path) if path == audit_path else saved.read_private(path)
    inputs = saved.PinnedInputs(bounded_reader if reader is None else reader)
    evaluate_saved = evaluate_inputs if evaluator is None else evaluator
    source_pin, spec_pin = saved.digest(saved.encoded(sources)), saved.digest(raw)
    primary_error = None
    def evaluate():
        nonlocal primary_error
        created, started = False, time.monotonic()
        try:
            saved.private_directory(output.parent); output.mkdir(mode=0o700); created = True; saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = evaluate_saved(spec, inputs, output)
            after, after_raw, _ = freezer(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            resources, manifest = saved.encoded(holding._resources(started)), saved.encoded(inputs.manifest)
            saved.write_exclusive(output / "resource-manifest.json", resources)
            saved.write_exclusive(output / "input-manifest.json", manifest)
            result.update(source_manifest_sha256=source_pin, specification_sha256=spec_pin,
                resource_manifest_sha256=saved.digest(resources), input_manifest_sha256=saved.digest(manifest))
            body = saved.encoded(result); saved.write_exclusive(output / "result.json", body)
            return dict(status=result["status"], output_dir=str(output), result_sha256=saved.digest(body),
                summary=result["summary"], output_sha256=result["output_sha256"], D1_completed_studies=0, D2_completed_studies=0,
                performance_studies_added=0, automatic_retries=0)
        except BaseException as error:
            primary_error = error
            if created:
                try:
                    _preserve_failure(output, error, inputs, started, source_pin, spec_pin)
                except BaseException:
                    pass  # Even allocating diagnostic metadata can fail under MemoryError.
            raise
    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=spec_pin,
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "mode": MODE,
                "source_manifest_sha256": source_pin, "declared_inputs": spec["inputs"], "output_dir": str(output),
                "performance_studies_added": 0, "automatic_retries": 0}, evaluate=evaluate)
    except BaseException as error:
        if primary_error is None:
            primary_error = error
        elif error is not primary_error:
            _secondary_receipt(output, "logging-failure.json", primary_error,
                operation="canonical_failure_append", secondary=error)
            raise primary_error from None
        raise
    finally:
        try:
            os.umask(previous)
        except BaseException as secondary:
            if primary_error is None: raise
            _secondary_receipt(output, "umask-cleanup-failure.json", primary_error,
                operation="restore_umask", secondary=secondary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("spec", "output-dir", "runs-path"): parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    def expired(signum, frame): raise TimeoutError("registered BS quote wall limit exceeded")
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2,) * 2); resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
    signal.signal(signal.SIGALRM, expired); signal.alarm(900)
    primary_error = None
    try:
        priority = os.getpriority(os.PRIO_PROCESS, 0)
        saved.require(priority <= 10 and os.nice(10 - priority) == 10, "exact nice10 required")
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except BaseException as error:
        primary_error = error
        if not isinstance(error, Exception): raise
        try:
            print(saved.encoded(dict(status="failed", error_type=type(error).__name__, automatic_retries=0)).decode())
        except BaseException:
            raise error from None
        return 1
    finally:
        try:
            signal.alarm(0)
        except BaseException as secondary:
            if primary_error is None: raise
            _secondary_receipt(args.output_dir, "alarm-cleanup-failure.json", primary_error,
                operation="cancel_wall_alarm", secondary=secondary)
    print(saved.encoded(record["result"]).decode()); return 0


if __name__ == "__main__":
    raise SystemExit(main())
