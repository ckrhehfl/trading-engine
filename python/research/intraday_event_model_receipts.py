"""Publish and independently audit one fixed EL model unit.

The closed caller admits the release and inputs, holds the shared research
locks and applies the base worker limits. Slots are exclusive across source
releases so a partial publication cannot silently spend another fit.
"""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

from research import experiment_log
from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_dataset_run as dataset
from research import intraday_event_fold_artifacts as artifacts
from research.intraday_event_evaluation import CONTROLS
from research.intraday_event_execution import ENTRY_POLICIES, MANAGEMENT_POLICIES
from research.intraday_event_model_contract import unit_id


def _coordinate(identity):
    unit = unit_id(identity["kind"], identity["scope"], identity["fold"])
    wire.require(type(identity["invented"]) is bool and type(identity["merge"]) is str
                 and re.fullmatch("[0-9a-f]{40}", identity["merge"]) is not None
                 and all(wire.valid_sha(identity[name]) for name in
                         ("registration_sha256", "runtime_sha256", "inputs_sha256")),
                 "EL model execution identity differs")
    allowed = ("qualification1",) if identity["invented"] else ("actual1", "actual2")
    attempt = identity.get("attempt", allowed[0])
    wire.require(attempt in allowed, "EL model attempt must be fixed")
    directory = f"{unit}-{identity['merge']}-{attempt}" if identity["invented"] else f"{unit}-{attempt}"
    return unit, directory


def slot_for(identity):
    return dataset.NATIVE / "output" / _coordinate(identity)[1]


def _paths(slot, identity):
    slot = Path(slot)
    unit, directory = _coordinate(identity)
    wire.require(slot == dataset.NATIVE / "output" / directory, "EL model slot differs")
    marker = dataset.NATIVE / "canonical" / f"{directory}-unresolved.json"
    journal = (slot / "experiments.jsonl" if identity["invented"]
               else dataset.NATIVE / "canonical" / "experiments.jsonl")
    return unit, marker, journal


def _proof(path, identity, unit, result, start, end, *, failed=False):
    # Exact two-record range from the single-writer Discovery journal.
    before = wire.file_identity(wire.regular(path))
    wire.require(type(start) is int and type(end) is int and 0 <= start < end <= before[4]
                 and end - start < dataset.MIB, "EL model journal bounds differ")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == before, "EL model journal changed")
        stream.seek(start)
        raw = stream.read(end - start)
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == before, "EL model journal changed")
    wire.require(wire.file_identity(wire.regular(path)) == before, "EL model journal changed")
    lines = raw.splitlines(keepends=True)
    wire.require(len(raw) == end - start and len(lines) == 2 and all(line.endswith(b"\n") for line in lines),
                 "EL model journal pair missing")
    first, last = map(json.loads, lines)
    terminal = (last["status"] == "failed" and last.get("error_type") == "TimeoutError" and "result" not in last
                if failed else last["status"] == "completed" and last.get("result") == result)
    wire.require(first["status"] == "started" and terminal and first["run_id"] == last["run_id"]
                 and all(record["record_type"] == "discovery_trial" and record["study_id"] == unit
                         and record["parameters"] == identity and record["window"] == dataset.WINDOW
                         and record["promotion_allowed"] is False and record["promotion_trial_increment"] == 0
                         and record["code_version"] == identity["merge"]
                         and record["specification_sha256"] == identity["registration_sha256"]
                         for record in (first, last)), "EL model journal identity differs")
    proof = {"start": start, "end": end, "run_id": last["run_id"], "terminal_sha256": sha256(lines[1]).hexdigest()}
    if failed:
        proof["start_sha256"] = sha256(lines[0]).hexdigest()
    return proof


def _only_private_children(path, allowed):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode) or stat.S_IMODE(mode) != 0o700:
        return False
    with os.scandir(path) as entries:
        return all(child.name in allowed for child in entries)


def _prefit_only(slot, *, saved):
    # Native admission creates an empty tmp directory before its runtime checks.
    wire.safe_path(slot)
    scratch = slot / "produce-scratch"
    allowed = {scratch.name, "timeout.json"} if saved else {scratch.name}
    return (_only_private_children(slot, allowed) and _only_private_children(scratch, {"tmp"})
            and (not os.path.lexists(scratch / "tmp") or _only_private_children(scratch / "tmp", set())))


def _timeout_receipt(identity, proof):
    return {"schema": "EL-model-prefit-timeout-v1", "identity": identity, "journal": proof,
            "error_type": "TimeoutError"}


def _actual_state(identity):
    """Return the first failed timeout pin only for an admitted second attempt."""
    if identity["invented"]:
        return None
    base = {key: value for key, value in identity.items() if key != "attempt"}
    if identity.get("attempt") != "actual2":
        second = {**base, "attempt": "actual2"}
        slot = slot_for(second)
        _, marker, _ = _paths(slot, second)
        wire.require(not os.path.lexists(slot) and not os.path.lexists(marker), "EL model sibling attempt already exists")
        return None
    slot = slot_for(base)
    unit, marker, journal = _paths(slot, base)
    wire.require(os.path.lexists(marker) and os.path.lexists(slot / "timeout.json")
                 and _prefit_only(slot, saved=True), "EL model retry requires a controlled timeout without ambiguous publication")
    original = dataset._doc(marker)
    wire.require(wire.encode(original) in (wire.encode(base), wire.encode({**base, "attempt": "actual1"})),
                 "EL model timeout identity differs")
    saved = dataset._doc(slot / "timeout.json")
    span = saved["journal"]
    proof = _proof(journal, original, unit, None, span["start"], span["end"], failed=True)
    wire.require(wire.encode(saved) == wire.encode(_timeout_receipt(original, proof)), "EL model failed journal differs")
    return wire.digest(wire.encode(saved))


def _verified_runtime(runtime, identity):
    wire.require(type(runtime) is dict and runtime["verified"] is True
                 and runtime["kind"] == identity["kind"] and runtime["runtime_sha256"] == identity["runtime_sha256"],
                 "EL model runtime readback differs")


def _manifest(slot, outcome, identity):
    wire.require(type(outcome) is dict and set(outcome) == {"manifest", "runtime"},
                 "EL model production outcome differs")
    _verified_runtime(outcome["runtime"], identity)
    return artifacts.read_manifest(slot, outcome["manifest"], identity)


def _verify_artifact_bytes(slot, manifest):
    for name, pin in manifest["files"].items():
        maximum = artifacts.MAX_FILE if name in artifacts.STREAMS else artifacts.MAX_DOCUMENT
        wire.require(wire.file_digest(slot / name, maximum, lambda _: None) == (pin["sha256"], pin["bytes"]),
                     "EL model saved artifact bytes differ")


def _write_report(slot, name, report):
    raw = wire.encode(report)
    wire.require(len(raw) <= dataset.MAX_REPORT, "EL model report exceeds size bound")
    wire.exclusive_bytes(slot / name, raw)
    return raw


def _read_report(slot, name, expected_hash, expected_size=None):
    raw = dataset._read(slot / name, dataset.MAX_REPORT)
    wire.require(sha256(raw).hexdigest() == expected_hash and (expected_size is None or len(raw) == expected_size),
                 "EL model saved report differs")
    report = json.loads(raw)
    wire.require(type(report) is dict and wire.encode(report) == raw, "EL model report is noncanonical")
    return report


def produce(slot, identity, compute, *, instrument_fit=False):
    """Durably start, then invoke compute once and bind its manifest.

    The closed caller can opt in to compute(scratch, mark_fit_started), with
    the mark required immediately before its first fit. Otherwise record the
    mark before compute(scratch), so no subsequent timeout can be retried.
    """
    slot = Path(slot)
    identity = json.loads(wire.encode(identity))
    unit, marker, journal = _paths(slot, identity)
    wire.require(not os.path.lexists(marker), "EL model attempt unresolved")
    predecessor = _actual_state(identity)
    wire.exclusive_directory(slot)
    wire.exclusive_bytes(marker, wire.encode(identity))
    start = 0 if identity["invented"] else wire.regular(journal).st_size
    prefit = False

    def mark_fit_started():
        nonlocal prefit
        # Disable recovery even if a signal interrupts the durable write.
        prefit = False
        wire.exclusive_bytes(slot / "fit-start.json", wire.encode(identity))

    def evaluate():
        nonlocal prefit
        scratch = slot / "produce-scratch"
        wire.exclusive_directory(scratch)
        if instrument_fit:
            prefit = True
            outcome = compute(scratch, mark_fit_started)
        else:
            mark_fit_started()
            outcome = compute(scratch)
        wire.require(not prefit and dataset._doc(slot / "fit-start.json") == identity,
                     "EL model fit-start receipt differs")
        _manifest(slot, outcome, identity)
        measured = dataset.usage()
        dataset.verify_usage(measured, dict.fromkeys(measured, 0))
        report = {"schema": "EL-model-publication-v1", "identity": identity, "outcome": outcome}
        raw = _write_report(slot, "report.json", report)
        result = {"report_sha256": sha256(raw).hexdigest(), "report_bytes": len(raw),
                  "manifest_sha256": outcome["manifest"]["sha256"], "resources": measured}
        if predecessor is not None:
            result["predecessor_timeout_sha256"] = predecessor
        return result

    try:
        terminal = experiment_log.run_discovery_trial(study_id=unit,
            specification_sha256=identity["registration_sha256"], parameters=identity, window=dataset.WINDOW,
            evaluate=evaluate, runs_path=journal, code_version_provider=lambda: identity["merge"])
    except TimeoutError as error:
        if (type(error) is TimeoutError and prefit and not identity["invented"] and predecessor is None
                and _prefit_only(slot, saved=False)):
            proof = _proof(journal, identity, unit, None, start, wire.regular(journal).st_size, failed=True)
            wire.exclusive_bytes(slot / "timeout.json", wire.encode(_timeout_receipt(identity, proof)))
        raise
    result = terminal["result"]
    proof = _proof(journal, identity, unit, result, start, wire.regular(journal).st_size)
    wire.exclusive_bytes(slot / "completion.json", wire.encode({"identity": identity, "result": result, "journal": proof}))
    wire.require(dataset._doc(marker) == identity, "EL model attempt marker changed")
    marker.unlink()
    wire.fsync_directory(marker.parent)
    return _read_report(slot, "report.json", result["report_sha256"], result["report_bytes"])


def _publication(slot, identity):
    unit, marker, journal = _paths(slot, identity)
    wire.require(not os.path.lexists(marker), "EL model publication unresolved")
    predecessor = _actual_state(identity)
    done = dataset._doc(slot / "completion.json")
    wire.require(done["identity"] == identity and dataset._doc(slot / "fit-start.json") == identity,
                 "EL model completion identity differs")
    result, span = done["result"], done["journal"]
    wire.require(result.get("predecessor_timeout_sha256") == predecessor, "EL model publication timeout proof differs")
    wire.require(_proof(journal, identity, unit, result, span["start"], span["end"]) == span,
                 "EL model terminal proof differs")
    report = _read_report(slot, "report.json", result["report_sha256"], result["report_bytes"])
    wire.require(report["schema"] == "EL-model-publication-v1" and report["identity"] == identity
                 and report["outcome"]["manifest"]["sha256"] == result["manifest_sha256"],
                 "EL model publication identity differs")
    manifest = _manifest(slot, report["outcome"], identity)
    return report, done, manifest


def _audit_start(done):
    return {"identity": done["identity"], "completion_sha256": wire.digest(wire.encode(done)),
            "manifest_sha256": done["result"]["manifest_sha256"]}


def _validate_audit(outcome, identity, manifest, manifest_sha256):
    wire.require(type(outcome) is dict and set(outcome) == {"audit", "runtime"}, "EL model audit outcome differs")
    _verified_runtime(outcome["runtime"], identity)
    proof = outcome["audit"]
    predictions = manifest["files"]["predictions.jsonl"]["rows"]
    ledgers = manifest["files"]["ledger.jsonl"]["rows"]
    checks = proof["cash_checks"]
    arms = len(ENTRY_POLICIES) * len(MANAGEMENT_POLICIES)
    wire.require(proof["identity"] == identity and proof["manifest_sha256"] == manifest_sha256
                 and proof["forecasts"] == {"verified": True, "predictions": predictions,
                                             "native_optimizer_reimplemented": False}
                 and type(proof["forecasts"]["predictions"]) is int
                 and type(proof["ledger_rows"]) is int and proof["ledger_rows"] == ledgers
                 and type(checks) is list and len(checks) == len(CONTROLS)
                 and all(type(row["episodes"]) is int and row["episodes"] >= 0
                         and type(row["ledgers"]) is int and row["ledgers"] == row["episodes"] * arms
                         and row["episodes"] == checks[0]["episodes"]
                         and all(type(count) is int and count > 0 for count in row["outcome_counts"].values())
                         and sum(row["outcome_counts"].values()) == row["ledgers"] for row in checks)
                 and sum(row["ledgers"] for row in checks) == ledgers, "EL model independent verification coverage differs")


def _audit_receipt(done, audit_report_sha256, resources):
    return {**_audit_start(done), "report_sha256": done["result"]["report_sha256"],
            "audit_report_sha256": audit_report_sha256, "resources": resources, "independently_verified": True}


def audit(slot, identity, compute):
    """Independently verify the same fold via compute(scratch, manifest_pin)."""
    slot = Path(slot)
    identity = json.loads(wire.encode(identity))
    report, done, manifest = _publication(slot, identity)
    start = _audit_start(done)
    wire.exclusive_bytes(slot / "audit-start.json", wire.encode(start))
    try:
        dataset.audit_budget(done["result"]["resources"])
        _verify_artifact_bytes(slot, manifest)
        scratch = slot / "audit-scratch"
        wire.exclusive_directory(scratch)
        outcome = compute(scratch, report["outcome"]["manifest"])
        _validate_audit(outcome, identity, manifest, start["manifest_sha256"])
        measured = dataset.usage()
        dataset.verify_usage(done["result"]["resources"], measured)
        raw = _write_report(slot, "audit-report.json", {"binding": start, "outcome": outcome})
        receipt = _audit_receipt(done, wire.digest(raw), measured)
        wire.exclusive_bytes(slot / "audit.json", wire.encode(receipt))
    except Exception as error:
        wire.exclusive_bytes(slot / "audit-failure.json", wire.encode({
            "binding": start, "error_type": type(error).__name__, "resources": dataset.usage()}))
        raise
    return receipt


def audited(slot, identity):
    """Check the full saved output without acquiring inputs or running the model."""
    slot = Path(slot)
    report, done, manifest = _publication(slot, identity)
    receipt = dataset._doc(slot / "audit.json")
    start = _audit_start(done)
    wire.require(not os.path.lexists(slot / "audit-failure.json") and dataset._doc(slot / "audit-start.json") == start
                 and receipt == _audit_receipt(done, receipt["audit_report_sha256"], receipt["resources"]),
                 "EL model audit receipt differs")
    dataset.verify_usage(done["result"]["resources"], receipt["resources"])
    saved = _read_report(slot, "audit-report.json", receipt["audit_report_sha256"])
    wire.require(saved["binding"] == start, "EL model audit report binding differs")
    _validate_audit(saved["outcome"], identity, manifest, start["manifest_sha256"])
    _verify_artifact_bytes(slot, manifest)
    return report, wire.digest(wire.encode(receipt))


def audited_actual(identity):
    """Return the exclusively audited actual slot, identity, report and audit pin."""
    wire.require(identity["invented"] is False and identity.get("attempt", "actual1") == "actual1",
                 "EL base actual identity required")
    second = {**identity, "attempt": "actual2"}
    slot = slot_for(second)
    _, marker, _ = _paths(slot, second)
    if os.path.lexists(slot) or os.path.lexists(marker):
        identity = second
    else:
        slot = slot_for(identity)
    report, pin = audited(slot, identity)
    return slot, identity, report, pin
