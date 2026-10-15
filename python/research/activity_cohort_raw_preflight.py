"""Logged BS raw acquisition over a registered full-family price-free plan.

Original KRX responses retain their body pins, source receipts, row positions
and retrieval times. This caller redecodes them into a new raw artifact bound
to the explicitly supplied BS composite lineage. It restores no KIS prices and
executes no book. Source truth, rights, coverage and independent verification
remain separate obligations. The old BQ caller and its artifacts stay intact.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import os
from pathlib import Path
import re
import resource
import signal
import time
import traceback

from research import activity_cohort_preflight as cohort
from research import activity_cohort_raw_plan as planning
from research import activity_exit_preflight as logged
from research import activity_preflight as saved
from research import activity_raw_anchor_preflight as anchors
from research import activity_raw_episode_preflight as acquisition
from research import activity_raw_quote_view as view
from research import experiment_log
from research.activity_pair_calibration import PARAMETERS
from research.activity_raw_episode import extract_raw_episode_targets, validate_raw_observation


SCHEMA = "activity-cohort-raw-preflight-v1"
MODE = "discovery_cohort_raw_acquisition_diagnostic"
SOURCE_PATH = "python/research/activity_cohort_raw_preflight.py"
METADATA_SCHEMA = "activity-cohort-raw-planning-inputs-v1"
METADATA_FIELDS = {"schema", "declarations", "calendar_dates", "raw_event_plan", "input_sha256",
    "prior_responses", "prior_inventory_sha256"}
REGISTRATION_FIELDS = {"acquisition_plan_sha256", "unit_plan_sha256", "counts", "new_request_matrix"}
FALSE_FLAGS = tuple(name for name in planning.FALSE_FLAGS
    if name not in {"api_access", "credential_access", "raw_response_bodies_read"}) + (
        "independent_output_verified", "resource_enforcement_independently_verified")


def cache_input_declarations(candidates: list[dict]) -> dict:
    """Declare only selected original bodies and distinct receipts, without IO."""
    result, receipts, paths = {}, {}, set()
    for number, row in enumerate(candidates, 1):
        path = row["response_path"]
        saved.require(path not in paths, "cached response paths must be unique")
        paths.add(path)
        result[f"cache_response_{number:04d}"] = dict(path=path, sha256=row["response_sha256"])
        receipt_path, pin = row["receipt_path"], row["receipt_sha256"]
        saved.require(receipt_path not in receipts or receipts[receipt_path] == pin, "conflicting original receipt pins")
        receipts[receipt_path] = pin
    saved.require(not paths & set(receipts), "response/receipt path aliases are refused")
    for number, (path, pin) in enumerate(sorted(receipts.items()), 1):
        result[f"cache_receipt_{number:04d}"] = dict(path=path, sha256=pin)
    return result


def validate_spec(spec: dict, reference: dict) -> None:
    """Require explicit plan/matrix, original input pins, ceiling and resources."""
    saved.require(type(spec) is dict and set(spec) == {"schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs", "request_contract", "registration", "resources"}
        and spec["schema"] == spec["study_id"] == SCHEMA and spec["mode"] == MODE
        and spec["promotion_allowed"] is False and saved.encoded(spec["window"]) == saved.encoded(saved.WINDOW)
        and spec["reference_specification"] == saved.REFERENCE
        and saved.encoded(spec["parameters"]) == saved.encoded(PARAMETERS)
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"]), "fixed BS raw contract required")
    contract = spec["request_contract"]
    saved.require(type(contract) is dict and set(contract) == {*acquisition.HTTP_CONTRACT, "request_count_ceiling"}
        and saved.encoded({key: value for key, value in contract.items() if key != "request_count_ceiling"})
        == saved.encoded(acquisition.HTTP_CONTRACT)
        and type(contract["request_count_ceiling"]) is int and contract["request_count_ceiling"] >= 0,
        "existing fixed-host HTTP contract and explicit request ceiling required")
    limits = spec["resources"]
    saved.require(type(limits) is dict and set(limits) == {"address_space_bytes", "cpu_seconds", "wall_seconds", "nice"}
        and all(type(limits[name]) is int and limits[name] > 0 for name in ("address_space_bytes", "cpu_seconds", "wall_seconds"))
        and type(limits["nice"]) is int and 0 <= limits["nice"] <= 19, "explicit positive resource limits required")
    declared = spec["inputs"]
    saved.require(type(declared) is dict and {"planning_metadata", "composite_lineage"} <= set(declared)
        and all(name in ("planning_metadata", "composite_lineage") or re.fullmatch(r"cache_(?:response|receipt)_[0-9]{4}", name)
            for name in declared), "exact metadata, lineage and original-cache input roles required")
    paths = []
    for row in declared.values():
        saved.require(type(row) is dict and set(row) == {"path", "sha256"}
            and type(row["path"]) is str and Path(row["path"]).is_absolute()
            and ".." not in Path(row["path"]).parts, "explicit absolute input pin required")
        saved._pin(row["sha256"])
        paths.append(str(saved._safe_path(Path(row["path"]))))
    saved.require(len(paths) == len(set(paths)), "input aliases or duplicate reads refused")
    registration = spec["registration"]
    saved.require(type(registration) is dict and set(registration) == REGISTRATION_FIELDS, "exact full plan registration required")
    saved._pin(registration["acquisition_plan_sha256"])
    if registration["unit_plan_sha256"] is not None: saved._pin(registration["unit_plan_sha256"])
    saved.require(type(registration["counts"]) is dict and type(registration["new_request_matrix"]) is list
        and len(registration["new_request_matrix"]) <= contract["request_count_ceiling"], "registered request ceiling exceeded")


def _canonical(inputs, declaration, role):
    raw = inputs.read(Path(declaration["path"]), declaration["sha256"], role)
    value = saved.strict_json(raw)
    saved.require(saved.encoded(value) == raw, "canonical saved metadata required")
    return value


def _lineage(value, pin, metadata):
    links = metadata["input_sha256"]
    saved.require(type(value) is dict and set(value) == {"schema", "activity_snapshot_sha256", "calendar_dates",
        "atomic_source_vintage_certified", "original", "extension"}
        and value["schema"] == "activity-composite-quote-extension-v1"
        and value["atomic_source_vintage_certified"] is False
        and value["activity_snapshot_sha256"] == links["activity_snapshot_sha256"]
        and value["calendar_dates"] == metadata["calendar_dates"] and pin == links["base_quote_snapshot_sha256"],
        "exact new composite activity/calendar lineage required")
    for name in ("original", "extension"):
        row = value[name]
        saved.require(type(row) is dict and set(row) == {"quote_snapshot_sha256", "read_scope_sha256",
            "snapshot_received_at", "fingerprint_sha256"}, "exact retained component lineage required")
        for key in ("quote_snapshot_sha256", "read_scope_sha256", "fingerprint_sha256"): saved._pin(row[key])
        saved.require(row["fingerprint_sha256"] == row["quote_snapshot_sha256"]
            and type(row["snapshot_received_at"]) is str and datetime.fromisoformat(row["snapshot_received_at"]).utcoffset() is not None,
            "retained component fingerprint or receipt timestamp changed")
    saved.require(value["original"]["quote_snapshot_sha256"] == links["original_quote_snapshot_sha256"]
        and value["original"]["read_scope_sha256"] == links["original_read_scope_sha256"]
        and value["original"]["quote_snapshot_sha256"] != value["extension"]["quote_snapshot_sha256"]
        and value["original"]["read_scope_sha256"] != value["extension"]["read_scope_sha256"], "original/new-base identities changed")


def prepare_inputs(spec: dict, inputs: saved.PinnedInputs) -> dict:
    """Recompute all origins and the whole request matrix before any raw body."""
    validate_spec(spec, {"parameters": PARAMETERS})
    declared = spec["inputs"]
    metadata = _canonical(inputs, declared["planning_metadata"], "planning_metadata")
    saved.require(type(metadata) is dict and set(metadata) == METADATA_FIELDS and metadata["schema"] == METADATA_SCHEMA
        and all(type(metadata[name]) is list for name in ("calendar_dates", "raw_event_plan", "prior_responses")),
        "exact full-family planning metadata required")
    calendar = tuple(date.fromisoformat(value) for value in metadata["calendar_dates"])
    saved.require([str(day) for day in calendar] == metadata["calendar_dates"], "canonical full original calendar required")
    plans = planning.build_cohort_raw_plan(cohort._declarations(metadata["declarations"]), calendar,
        tuple(metadata["raw_event_plan"]), parameters=spec["parameters"], input_sha256=metadata["input_sha256"],
        prior_responses=tuple(metadata["prior_responses"]), prior_inventory_sha256=metadata["prior_inventory_sha256"])
    plan, unit = plans["acquisition_plan"], plans["unit_plan"]
    registered = dict(acquisition_plan_sha256=saved.digest(saved.encoded(plan)),
        unit_plan_sha256=None if unit is None else saved.digest(saved.encoded(unit)), counts=plan["counts"],
        new_request_matrix=plan["uncached_request_candidates"])
    saved.require(saved.encoded(registered) == saved.encoded(spec["registration"]), "recomputed whole plan differs from registration")
    expected = cache_input_declarations(plan["cached_response_candidates"])
    saved.require(saved.encoded({name: row for name, row in declared.items() if name.startswith("cache_")})
        == saved.encoded(expected), "exact selected original response/receipt declarations required")
    saved.require(unit is not None or not plan["request_matrix"], "unresolved raw episode cannot start acquisition")
    lineage = _canonical(inputs, declared["composite_lineage"], "composite_lineage")
    _lineage(lineage, declared["composite_lineage"]["sha256"], metadata)
    return plans


def _pointer(value, pointer):
    saved.require(type(pointer) is str and pointer.startswith("/"), "explicit original receipt JSON pointer required")
    for part in pointer[1:].split("/"):
        saved.require(re.search(r"~(?![01])", part) is None, "invalid original receipt pointer escape")
        part = part.replace("~1", "/").replace("~0", "~")
        if type(value) is list:
            saved.require(re.fullmatch(r"0|[1-9][0-9]*", part) is not None and int(part) < len(value), "receipt array coordinate outside source")
            value = value[int(part)]
        else:
            saved.require(type(value) is dict and part in value, "receipt coordinate missing from original source")
            value = value[part]
    saved.require(type(value) is dict, "original response receipt record required")
    return value


def _cached_rows(inputs, entry, receipts, number):
    """Rejoin only known original AQ/AR, BP or raw-acquisition receipt forms."""
    original = _pointer(receipts[entry["receipt_path"]], entry["receipt_coordinate"])
    item = {name: entry[name] for name in ("service", "bas_dd", "targets")}
    if {"service", "requested_bas_dd", "schema_valid", "response_sha256", "at"} <= set(original):
        kind = "aq_ar"
        saved.require(original["service"] == entry["service"] and original["requested_bas_dd"] == entry["bas_dd"]
            and original["schema_valid"] is True and original["response_sha256"] == entry["response_sha256"]
            and original["at"] == entry["retrieved_at"], "original AQ/AR receipt coordinate mismatch")
    elif {"number", "service", "bas_dd", "read_status", "http_status", "file", "bytes", "rows", "sha256", "retrieved_at"} == set(original):
        kind = "raw"
        saved.require(type(original["number"]) is int and original["number"] > 0
            and original["service"] == entry["service"] and original["bas_dd"] == entry["bas_dd"]
            and original["read_status"] == "retained" and original["http_status"] == 200
            and original["file"] == Path(entry["response_path"]).name
            and type(original["bytes"]) is int and 0 < original["bytes"] <= acquisition.probe.MAX_RESPONSE_BYTES
            and type(original["rows"]) is int and original["rows"] >= 0
            and original["sha256"] == entry["response_sha256"] and original["retrieved_at"] == entry["retrieved_at"],
            "original raw acquisition response receipt mismatch")
    elif original.get("kind") == "raw_close" and original.get("code") in planning.TARGET_CODES:
        kind = "bp"
        saved.require(original.get("source") == "KRX OpenAPI " + entry["service"]
            and original.get("requested_bas_dd") == entry["bas_dd"] and original.get("response_sha256") == entry["response_sha256"]
            and original.get("retrieved_at") == entry["retrieved_at"], "original BP receipt coordinate mismatch")
    else:
        raise ValueError("unsupported original response receipt form")
    body = inputs.read(Path(entry["response_path"]), entry["response_sha256"], f"cache_response_{number:04d}")
    saved.require(len(body) <= acquisition.probe.MAX_RESPONSE_BYTES, "cached response exceeds existing ceiling")
    rows = saved.parse_envelope(body)
    if kind == "aq_ar":
        summary = saved.formation.summarize_trading_day(rows, entry["service"], entry["bas_dd"])
        saved.require(summary["schema_valid"] and summary == {key: value for key, value in original.items()
            if key not in {"at", "response_sha256", "number", "logical_number"}}, "original AQ/AR body summary changed")
    elif kind == "raw":
        saved.require(original["bytes"] == len(body) and original["rows"] == len(rows), "original raw body lengths changed")
    else:
        rebuilt = anchors.extract_targets(rows, {**item, "targets": [original["code"]]},
            entry["retrieved_at"], entry["response_sha256"])
        saved.require(saved.encoded(rebuilt[0]) == saved.encoded(original), "original BP target/body lineage mismatch")
    acquisition._check_target_market(rows, item)
    return extract_raw_episode_targets(rows, item, entry["retrieved_at"], entry["response_sha256"])


def evaluate_inputs(spec: dict, inputs: saved.PinnedInputs, output: Path, *, state=None) -> dict:
    """Persist the scope, redecode cached bodies, then fetch only the fixed delta."""
    state = {"requests": [], "observations": [], "credential_access_attempted": False} if state is None else state
    plans = prepare_inputs(spec, inputs)
    plan, unit = plans["acquisition_plan"], plans["unit_plan"]
    pins = {}
    for name, value in (("acquisition-plan.json", plan), ("cache-manifest.json", plan["cached_response_candidates"]),
        ("request-manifest.json", dict(contract=spec["request_contract"], matrix=plan["uncached_request_candidates"]))):
        body = saved.encoded(value); saved.write_exclusive(output / name, body); pins[name] = saved.digest(body)
    if unit is not None:
        unit_raw = saved.encoded(unit); saved.write_exclusive(output / "unit-plan.json", unit_raw)
        pins["unit-plan.json"] = saved.digest(unit_raw)
    saved.write_exclusive(output / "requests.jsonl", b"")
    receipts = {row["path"]: inputs.json(Path(row["path"]), row["sha256"], role)
        for role, row in spec["inputs"].items() if role.startswith("cache_receipt_")}
    for number, entry in enumerate(plan["cached_response_candidates"], 1):
        state["observations"].extend(_cached_rows(inputs, entry, receipts, number))
    del receipts
    matrix = plan["uncached_request_candidates"]
    if matrix:
        state["credential_access_attempted"] = True
        secret = acquisition.probe.credential()
        opener = acquisition.probe.make_opener(header_case_check=True)
        for number, item in enumerate(matrix, 1):
            if number > 1: time.sleep(acquisition.HTTP_CONTRACT["interval_seconds"])
            state["observations"].extend(acquisition._fetch(opener, item, secret, output, state["requests"], number))
    state["observations"].sort(key=lambda row: (row.code, row.requested_bas_dd))
    observations = tuple(state["observations"])
    wanted = [(code, day.replace("-", "")) for code, days in sorted(plan["requested_code_dates"].items()) for day in days]
    saved.require([(row.code, row.requested_bas_dd) for row in observations] == wanted, "exact complete new raw coordinate union required")
    for row in observations: validate_raw_observation(row)
    if unit is not None:
        artifact = saved.encoded(dict(schema=view.ARTIFACT_SCHEMA, activity_snapshot_sha256=unit["activity_snapshot_sha256"],
            base_quote_snapshot_sha256=unit["base_quote_snapshot_sha256"], calendar_sha256=unit["calendar_sha256"],
            unit_plan_sha256=pins["unit-plan.json"], rows=view._value(observations)))
        saved.write_exclusive(output / "raw-episode-quotes.json", artifact); pins["raw-episode-quotes.json"] = saved.digest(artifact)
    requests = saved.encoded(state["requests"]); saved.write_exclusive(output / "new-response-manifest.json", requests)
    pins["new-response-manifest.json"] = saved.digest(requests)
    pins["requests.jsonl"] = saved.digest(saved.read_private(output / "requests.jsonl"))
    summary = dict(plan["counts"], observed_rows=sum(row.status == "observed" for row in observations),
        unresolved_rows=sum(row.status != "observed" for row in observations),
        planning_unresolved_obligations=len(plan["unresolved"]),
        no_trade_zero_price_rows=sum(row.state == "observed_no_trade_zero_prices" for row in observations))
    return dict(schema=SCHEMA, status="completed_with_unresolved" if summary["unresolved_rows"] or plan["unresolved"] else "completed",
        summary=summary, output_sha256=pins, original_input_sha256=plan["input_sha256"],
        cached_response_bodies_redecoded=len(plan["cached_response_candidates"]), requests_attempted=len(state["requests"]),
        requests_unattempted=len(matrix) - len(state["requests"]), new_source_requests=len(state["requests"]),
        api_access=bool(state["requests"]), credential_access=state["credential_access_attempted"],
        raw_response_bodies_read=bool(observations), independent_output_verification="pending",
        automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0, performance_studies_added=0,
        **dict.fromkeys(FALSE_FLAGS, False))


def _secondary(output, name, primary, errors):
    try:
        saved.write_exclusive(output / name, saved.encoded(dict(status="failed", primary_error_type=type(primary).__name__,
            secondary_errors=errors, automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0)))
    except BaseException:
        pass


def _preserve_failure(output, error, inputs, state, started, source_pin, spec_pin):
    errors = []
    artifacts = (("resource-manifest.json", lambda: logged._resources(started)),
        ("input-manifest.json", lambda: inputs.manifest), ("new-response-manifest.json", lambda: state["requests"]),
        ("partial-raw-observations.json", lambda: view._value(tuple(state["observations"]))),
        ("failure.json", lambda: dict(status="failed", error_type=type(error).__name__,
            error_frames=[dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                for frame in traceback.extract_tb(error.__traceback__)], actual_inputs_attempted=inputs.manifest,
            requests_recorded=len(state["requests"]), credential_access_attempted=state["credential_access_attempted"],
            source_manifest_sha256=source_pin, specification_sha256=spec_pin, partial_package_preserved=True,
            automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0, performance_studies_added=0,
            **dict.fromkeys(FALSE_FLAGS, False))))
    for name, payload in artifacts:
        try:
            if not (output / name).exists(): saved.write_exclusive(output / name, saved.encoded(payload()))
        except BaseException as secondary:
            try: errors.append(dict(operation=name, error_type=type(secondary).__name__))
            except BaseException: pass
    if errors: _secondary(output, "failure-cleanup.json", error, errors)


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path, *, reader=None, freeze=None, evaluator=None):
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root and Path(__file__).resolve() == root / SOURCE_PATH, "run committed BS raw caller from root")
    freezer = saved.freeze_sources if freeze is None else freeze
    sources, raw, spec = freezer(root, spec_path, spec_validator=validate_spec)
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    saved.require(not output.exists(), "exclusive BS raw output already exists")
    inputs = saved.PinnedInputs(saved.read_private if reader is None else reader)
    execute = evaluate_inputs if evaluator is None else evaluator
    state = {"requests": [], "observations": [], "credential_access_attempted": False}
    source_pin, spec_pin = saved.digest(saved.encoded(sources)), saved.digest(raw)
    primary_error = None
    def evaluate():
        nonlocal primary_error
        created, started = False, time.monotonic()
        try:
            saved.private_directory(output.parent); output.mkdir(mode=0o700); created = True; saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            result = execute(spec, inputs, output, state=state)
            after, after_raw, _ = freezer(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            resources = logged._resources(started); resources["registered_limits"] = spec["resources"]
            resources, manifest = saved.encoded(resources), saved.encoded(inputs.manifest)
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
                try: _preserve_failure(output, error, inputs, state, started, source_pin, spec_pin)
                except BaseException: pass
            raise
    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=spec_pin,
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "mode": MODE,
                "source_manifest_sha256": source_pin, "declared_inputs": spec["inputs"], "output_dir": str(output),
                "request_contract": spec["request_contract"], "registration": spec["registration"], "resources": spec["resources"],
                "performance_studies_added": 0, "automatic_retries": 0}, evaluate=evaluate)
    except BaseException as error:
        if primary_error is not None and error is not primary_error:
            _secondary(output, "logging-failure.json", primary_error,
                [dict(operation="canonical_failure_append", error_type=type(error).__name__)])
            raise primary_error from None
        primary_error = error
        raise
    finally:
        try: os.umask(previous)
        except BaseException as secondary:
            if primary_error is None: raise
            _secondary(output, "umask-cleanup-failure.json", primary_error,
                [dict(operation="restore_umask", error_type=type(secondary).__name__)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("spec", "output-dir", "runs-path"): parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    # A committed specification is engineering source, never an actual data read.
    spec = saved.strict_json(args.spec.read_bytes()); validate_spec(spec, {"parameters": PARAMETERS})
    limits = spec["resources"]
    def expired(signum, frame): raise TimeoutError("registered BS raw wall limit exceeded")
    resource.setrlimit(resource.RLIMIT_AS, (limits["address_space_bytes"],) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_seconds"],) * 2)
    signal.signal(signal.SIGALRM, expired); signal.alarm(limits["wall_seconds"])
    primary_error = None
    try:
        priority = os.getpriority(os.PRIO_PROCESS, 0)
        saved.require(priority <= limits["nice"] and os.nice(limits["nice"] - priority) == limits["nice"], "registered nice required")
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except BaseException as error:
        primary_error = error
        if not isinstance(error, Exception): raise
        try: print(saved.encoded(dict(status="failed", error_type=type(error).__name__, automatic_retries=0)).decode())
        except BaseException: raise error from None
        return 1
    finally:
        try: signal.alarm(0)
        except BaseException as secondary:
            if primary_error is None: raise
            _secondary(args.output_dir, "alarm-cleanup-failure.json", primary_error,
                [dict(operation="cancel_wall_alarm", error_type=type(secondary).__name__)])
    print(saved.encoded(record["result"]).decode()); return 0


if __name__ == "__main__":
    raise SystemExit(main())
