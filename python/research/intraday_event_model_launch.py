"""Orchestrate released EL inputs, model receipts, and private readout."""

from hashlib import sha256
import json
import os

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_case_report as cases
from research import intraday_event_dataset as dataset
from research import intraday_event_dataset_run as run
from research import intraday_event_evaluation as evaluation
from research import intraday_event_fold_artifacts as artifacts
from research import intraday_event_model_contract as contract
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_receipts as receipts
from research import intraday_event_policy_witness as witness
from research import intraday_event_pooled_report as pooled


def _invented_slot(release, shard):
    return run.NATIVE / "output" / f"{contract.UNIT}-invented-data-{release['merge']}-s{shard:02}"


def _invented_source_pins(release):
    directory = _invented_slot(release, 0) / "invented-inputs"
    return {name: wire.file_digest(directory / name, 512 * run.MIB, lambda _: None)[0]
            for name in ("CB", "CC", "CX", "raw-byte-chunks.sqlite", "index-seal.json")}


def _invented_manifest(release, shard):
    slot = _invented_slot(release, shard)
    report = run._doc(slot / "dataset-report.json")
    identity = {"release": release, "invented": True, "shard": shard}
    wire.require(report["identity"] == identity and run._doc(slot / "start.json") == identity
                 and set(report["invented_sources"]) == {"CB", "CC", "CX", "raw-byte-chunks.sqlite", "index-seal.json"}
                 and all(wire.valid_sha(pin) for pin in report["invented_sources"].values()),
                 "EL invented shard identity differs")
    manifest = report["dataset"]
    wire.require(manifest["session_indices"] == list(run.SHARDS[shard]), "EL invented shard geometry differs")
    run.verify_dataset_files(slot, manifest)
    return slot, report


def _invented_audit(release, shard):
    slot, report = _invented_manifest(release, shard)
    proof = run._doc(slot / "independent-audit.json")
    wire.require(run._doc(slot / "audit-start.json") == report["identity"]
                 and proof["identity"] == report["identity"] and proof["independent"] is True
                 and proof["report_sha256"] == wire.digest(wire.encode(report)), "EL invented shard audit differs")
    run.verify_usage(report["resources"], proof["resources"])
    return (slot, report["dataset"]), wire.digest(wire.encode(proof)), report["invented_sources"]


def _invented_inputs(release):
    audited = tuple(_invented_audit(release, i) for i in range(len(run.SHARDS)))
    shards, hashes, source_pins = zip(*audited, strict=True)
    wire.require(sum(manifest["counts"]["coordinates"] for _, manifest in shards) == 5158,
                 "EL invented model population differs")
    expected = _invented_source_pins(release)
    wire.require(all(pins == expected for pins in source_pins), "EL invented source pins differ across shard audits")
    return shards, wire.digest(wire.encode({"invented_shard_audits": hashes})), expected


def _invented_dataset(release, prior, shard, audit):
    identity = {"release": release, "invented": True, "shard": shard}
    slot = _invented_slot(release, shard)
    if audit:
        _, report = _invented_manifest(release, shard)
        run.audit_budget(report["resources"])
        wire.exclusive_bytes(slot / "audit-start.json", wire.encode(identity))
    else:
        if shard:
            _invented_audit(release, shard - 1)
        wire.exclusive_directory(slot)
        wire.exclusive_bytes(slot / "start.json", wire.encode(identity))
    seed = _invented_slot(release, 0)
    source_pins = _invented_source_pins(release) if audit or shard else None
    if audit:
        wire.require(report["invented_sources"] == source_pins, "EL invented source changed before shard audit")
    elif shard:
        wire.require(_invented_manifest(release, shard - 1)[1]["invented_sources"] == source_pins,
                     "EL invented source changed between shards")
    with run.original_inputs(seed, prior, True, audit or shard != 0) as (universe, reader):
        result = dataset.build_dataset(slot, universe, reader, audit=audit, session_indices=run.SHARDS[shard])
    published_pins = _invented_source_pins(release)
    wire.require(source_pins is None or source_pins == published_pins, "EL invented source changed during shard operation")
    if not shard:
        run.qualification_coverage(result)
    measured = run.usage()
    if audit:
        wire.require(report["dataset"] == result, "EL independent invented dataset differs")
        run.verify_usage(report["resources"], measured)
        wire.exclusive_bytes(slot / "independent-audit.json", wire.encode({"identity": identity,
            "independent": True, "report_sha256": wire.digest(wire.encode(report)), "resources": measured}))
    else:
        wire.exclusive_bytes(slot / "dataset-report.json", wire.encode({"identity": identity, "dataset": result,
            "invented_sources": published_pins, "resources": measured}))
    return slot


def _identity(release, coordinate, invented, binding, qualification=None, case_pin=None):
    kind, scope, fold = coordinate
    return {**release, "kind": kind, "scope": scope, "fold": fold, "invented": invented,
            "inputs_sha256": binding, "qualification_sha256": qualification, "case_report_sha256": case_pin}


def _cases_receipt(directory, release, binding, *, verify_bytes):
    receipt = run._doc(directory / "completion.json")
    pin = receipt["report_sha256"]
    identity = {"release": release, "inputs_sha256": binding}
    wire.require(wire.valid_sha(pin) and receipt == {**identity, "report_sha256": pin}
                 and run._doc(directory / "start.json") == identity,
                 "EL outcome-blind case readout differs")
    if verify_bytes:
        wire.require(wire.digest(run._read(directory / "report.json", run.MAX_REPORT)) == pin,
                     "EL case report bytes differ")
    return pin


def _admit_actual(binding):
    release = binding.release
    support = run.verify_support_predecessor(release["support_audit_sha256"])
    failed = release["original_failed_attempt"]
    wire.require(run.verify_failed_predecessor(failed["marker_sha256"], failed["terminal_sha256"],
                 failed["start"], failed["end"], release["registration_sha256"], support) == failed,
                 "EL original failure proof differs from the admitted recovery")
    admitted, digest = inputs.admit_dataset(release, binding.qualification_audit_sha256, binding.shard_audit_sha256)
    wire.require(digest == binding.inputs_sha256, "EL model input receipt binding differs")
    return admitted


def _qualification_identity(release, coordinate, invented_binding):
    kind, scope, _ = coordinate
    # Full chart is the largest chart-only input. The other fixture exercises
    # the peer-support mask and the extra context columns of the same release.
    scope = "common_chart_peer" if scope == "common_chart_peer" else "full_chart"
    qualification = (kind, scope, len(contract.FOLDS) - 1)
    wire.require(qualification in contract.QUALIFICATION_ROSTER, "EL unregistered qualification mapping")
    identity = _identity(release, qualification, True, invented_binding)
    slot = receipts.slot_for(identity)
    _, pin = receipts.audited(slot, identity)
    wire.require(run._doc(slot / "policy-witness.json") == witness.verify(), "EL invented policy witness differs")
    return pin


def _verified_invented_model(slot, identity, outcome):
    manifest = artifacts.read_manifest(slot, outcome["manifest"], identity)
    model = artifacts._read_document(slot, manifest["files"]["model.json"])
    artifact = model["artifact"]
    fit_counts = {"empirical": "fit_head_count", "lightgbm": "native_fit_head_count", "tcn": "native_joint_fit_count"}
    wire.require(model["status"] == "completed" and artifact is not None
                 and artifact[fit_counts[identity["kind"]]] > 0
                 and manifest["files"]["predictions.jsonl"]["rows"] > 0
                 and manifest["files"]["ledger.jsonl"]["rows"] > 0,
                 "EL invented qualification did not exercise its native fit, predictions and cash replay")
    support = evaluation.policy_support({name: head["status"] for name, head in artifact["heads"].items()})
    wire.require(all(support.values()), "EL invented qualification did not fit both entry and holding policy heads")


def _summary(slot, release, admitted, binding, invented_binding, case_pin):
    shards = tuple((slot, report["dataset"]) for slot, report, _ in admitted)
    audit_pins = []

    def reports():
        for coordinate in contract.ROSTER:
            qualification = _qualification_identity(release, coordinate, invented_binding)
            identity = _identity(release, coordinate, False, binding, qualification, case_pin)
            fold_slot, identity, report, audit_pin = receipts.audited_actual(identity)
            manifest = report["outcome"]["manifest"]
            audit_pins.append({"coordinate": coordinate, "audit_sha256": audit_pin, "manifest_sha256": manifest["sha256"]})
            yield artifacts.read_comparison(fold_slot, manifest, identity, stream_ledgers=True)

    summary = pooled.pool_cash(inputs.calendar_from_coordinates(shards), reports())
    raw = wire.encode({"release": release, "inputs_sha256": binding, "case_report_sha256": case_pin,
                       "fold_audits": audit_pins, "report": summary})
    wire.require(len(audit_pins) == len(contract.ROSTER) and len(raw) <= artifacts.MAX_DOCUMENT,
                 "EL pooled report receipt or size bound differs")
    wire.exclusive_bytes(slot / "report.json", raw)
    wire.exclusive_bytes(slot / "completion.json", wire.encode({"report_sha256": wire.digest(raw),
                                                                "fold_audit_count": len(audit_pins)}))


def _require_actual_audit_paths(release, binding, case_pin):
    """Check readiness without opening actual reports or spending the summary slot."""
    for coordinate in contract.ROSTER:
        identity = _identity(release, coordinate, False, binding, case_pin=case_pin)
        base = receipts.slot_for(identity)
        retry = receipts.slot_for({**identity, "attempt": "actual2"})
        selected = retry if os.path.lexists(retry) else base
        wire.require(all(os.path.lexists(selected / name) for name in ("completion.json", "audit.json"))
                     and not os.path.lexists(run.NATIVE / "canonical" / f"{selected.name}-unresolved.json"),
                     "EL summary requires every actual fold audit before opening its slot")


def execute(args, registrations, release, started, check_merge):
    run.STARTED_CLOCK = started
    run.budget()
    contract.validate_spec(registrations[contract.SPEC])
    run.validate_spec(registrations[run.SPEC])
    run.verify_contract(registrations[run.SPEC])
    wire.require(release["sources"][run.SPEC] == contract.DATASET_REGISTRATION_SHA256
                 and release["sources"][run.ORIGINAL] == run.ORIGINAL_SHA256, "EL parent registration pins differ")
    release = {**release, "runtime_sha256": run.runtime_pin()}
    coordinate = args.kind, args.scope, args.fold
    dataset_mode = args.mode.startswith("invented-dataset")
    fold_mode = args.mode in ("qualification", "qualification-audit", "actual", "actual-audit")
    actual = args.mode in ("cases", "actual", "actual-audit", "summary")
    roster = contract.ROSTER if actual else contract.QUALIFICATION_ROSTER
    wire.require((coordinate in roster if fold_mode else coordinate == (None, None, None))
                 and (args.shard in range(len(run.SHARDS)) if dataset_mode else args.shard is None)
                 and (args.attempt is None or args.mode in ("actual", "actual-audit"))
                 and (wire.valid_sha(args.dataset_binding_sha256) if actual else args.dataset_binding_sha256 is None),
                 "EL launch outside registered scope")

    binding = None
    if actual:
        path = run.NATIVE / "canonical" / f"{contract.UNIT}-dataset-binding.json"
        raw = run._read(path)
        value = json.loads(raw)
        wire.require(wire.encode(value) == raw and sha256(raw).hexdigest() == args.dataset_binding_sha256,
                     "EL private dataset binding bytes differ")
        binding = contract.validate_dataset_binding(value, args.dataset_binding_sha256)
        upstream = binding.release
        check_merge(upstream["merge"], upstream["reviewed"], upstream["tree"])
        wire.require(upstream["runtime_sha256"] == release["runtime_sha256"]
                     and all(release["sources"].get(name) == pin for name, pin in upstream["sources"].items()),
                     "EL model source or runtime differs from the audited dataset dependency")

    bridge.restrict_local_network()
    wire.prohibit_ef_tasks()
    prior = registrations[run.ORIGINAL]
    case_slot = run.NATIVE / "output" / f"{contract.UNIT}-cases"
    with run._locks():
        if dataset_mode:
            slot = _invented_dataset(release, prior, args.shard, args.mode.endswith("-audit"))
        elif args.mode == "cases":
            slot = case_slot
            for shard in range(len(run.SHARDS)):
                slots = [run._slot(run.attempt_name(base, shard)) for base in run.ATTEMPTS]
                existing = [candidate for candidate in slots if os.path.lexists(candidate)]
                wire.require(existing and all(os.path.lexists(existing[-1] / name) for name in ("completion.json", "audit.json")),
                             "EL cases require all actual dataset shard audits before opening the case slot")
            identity = {"release": release, "inputs_sha256": binding.inputs_sha256}
            wire.exclusive_directory(slot)
            wire.exclusive_bytes(slot / "start.json", wire.encode(identity))
            raw = wire.encode(cases.assemble_cases(_admit_actual(binding)))
            wire.require(len(raw) <= run.MAX_REPORT, "EL cases exceed the private report bound")
            wire.exclusive_bytes(slot / "report.json", raw)
            wire.exclusive_bytes(slot / "completion.json", wire.encode({**identity, "report_sha256": wire.digest(raw)}))
        else:
            invented_shards, invented_binding, source_pins = _invented_inputs(release)
            if not actual:
                digest = invented_binding
                identity = _identity(release, coordinate, True, digest)
            else:
                digest = binding.inputs_sha256
                case_pin = _cases_receipt(case_slot, release, digest, verify_bytes=False)
                if args.mode == "summary":
                    _require_actual_audit_paths(release, digest, case_pin)
                    slot = run.NATIVE / "output" / f"{contract.UNIT}-summary"
                    wire.exclusive_directory(slot)
                    wire.exclusive_bytes(slot / "start.json", wire.encode({"release": release, "inputs_sha256": digest,
                                                                          "case_report_sha256": case_pin}))
                    _cases_receipt(case_slot, release, digest, verify_bytes=True)
                    _summary(slot, release, _admit_actual(binding), digest, invented_binding, case_pin)
                    print(json.dumps({"mode": args.mode, "status": "completed", "path": str(slot)}, sort_keys=True))
                    return
                qualified = _qualification_identity(release, coordinate, invented_binding)
                identity = _identity(release, coordinate, False, digest, qualified, case_pin)
                if args.attempt == "actual2":
                    identity = {**identity, "attempt": "actual2"}

            slot = receipts.slot_for(identity)

            def compute(scratch, *, manifest=None, mark_fit_started=None):
                # The receipts layer calls this after the durable start and budget checks.
                if actual:
                    _cases_receipt(case_slot, release, digest, verify_bytes=True)
                    shards = tuple((directory, report["dataset"]) for directory, report, _ in _admit_actual(binding))
                else:
                    shards = invented_shards
                with run.original_inputs(_invented_slot(release, 0), prior, not actual, True) as (universe, reader):
                    outcome = artifacts.run_admitted_fold(slot, identity, shards, universe, reader, dataset_binding=digest,
                                                          scratch=scratch, audit_manifest=manifest,
                                                          mark_fit_started=mark_fit_started)
                    if not actual:
                        wire.require(_invented_source_pins(release) == source_pins,
                                     "EL invented replay source changed during model computation")
                    if not actual and manifest is None:
                        _verified_invented_model(slot, identity, outcome)
                        wire.exclusive_bytes(slot / "policy-witness.json", wire.encode(witness.verify()))
                    elif not actual:
                        wire.require(run._doc(slot / "policy-witness.json") == witness.verify(),
                                     "EL invented policy witness differs in audit")
                    return outcome

            if args.mode.endswith("-audit"):
                receipts.audit(slot, identity, lambda scratch, manifest: compute(scratch, manifest=manifest))
            else:
                receipts.produce(slot, identity, lambda scratch, mark_fit_started: compute(
                    scratch, mark_fit_started=mark_fit_started), instrument_fit=True)

    print(json.dumps({"mode": args.mode, "status": "completed", "path": str(slot)}, sort_keys=True))
