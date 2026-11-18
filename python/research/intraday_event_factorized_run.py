"""Closed EP matched fits over the original audited EL dataset publications.

Every fold is one budgeted unit containing two fits and a separate no-refit
audit. The only input adapters are the existing sealed dataset readers. Public
Git is checked before prohibiting children; private receipts are checked after
the durable start. This entry point does not open an original market reader.
"""

import json
import os
from pathlib import Path

from research import experiment_log
from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_dataset_run as run
from research import intraday_event_fold_artifacts as artifacts
from research import intraday_event_model_contract as contract
from research import intraday_event_model_launch as launch
from research import intraday_event_model_receipts as receipts
from research import intraday_event_native as native
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_factorized_metrics as metrics
from research import intraday_event_factorized_reference as reference


UNIT = "EP-C-FACTORIZED-1"
PLAN = ".planning/rd-ep-factorized-entry.md"
DISPATCHER = "python/research/intraday_event_model_run.py"
ARMS = ("direct", "factorized")
MODES = ("factorized-qualification", "factorized-qualification-audit",
         "factorized-actual", "factorized-audit", "factorized-summary")
ATTEMPTS = ("actual1", "actual2")
# Public source identities, never private execution or market-content hashes.
FIXTURE_RELEASE = {"merge": "9b9f50ff278aaccedf41f229b7c9a4f206dcac30",
    "reviewed": "715c6a015485c44f758c4388a65f562ed56243c0",
    "tree": "585a4c6eccd45740b39815a94481781fbc38bdcb"}
DATASET_RELEASE = {"merge": "305230dae6f2498fd55a0d36e2210213d835f258",
    "reviewed": "a513b3ca644204bc8e2a3ee78a49e372916a27b6",
    "tree": "5c9f5fcf5fb4a48c27b73955444989600d0eec9f"}


def _public_predecessors(source):
    for release in (FIXTURE_RELEASE, DATASET_RELEASE):
        commit, tree, parents = run._git("show", "-s", "--format=%H%n%T%n%P", release["merge"]).decode().splitlines()
        wire.require(commit == release["merge"] and tree == release["tree"] and len(parents.split()) == 2
            and parents.split()[1] == release["reviewed"] and parents.split()[0] != release["reviewed"]
            and run._git("rev-parse", release["reviewed"] + "^{tree}").decode().strip() == tree,
            "EP predecessor is not its reviewed normal merge")
        run._git("merge-base", "--is-ancestor", release["merge"], source["merge"])


def _old_release(old, expected, source, runtime):
    wire.require(all(old.get(name) == pin for name, pin in expected.items())
        and old.get("runtime_sha256") == runtime and old.get("sources")
        and all(source["sources"].get(path) == pin for path, pin in old["sources"].items() if path != DISPATCHER),
        "EP unchanged predecessor source/runtime differs")


def _source_after(source, runtime):
    for path, pin in source["sources"].items():
        wire.require(wire.file_digest(run.REPO / path, 2 * run.MIB, lambda _: None)[0] == pin,
                     "EP source changed during execution")
    wire.require(run.runtime_pin() == runtime, "EP interpreter changed during execution")


def _identity(source, runtime, args, *, invented=False, fold=None, summary=False, attempt=None):
    unit = UNIT + ("-summary" if summary else f"-f{fold}")
    return {"id": unit, "source": source, "merge": source["merge"], "runtime_sha256": runtime,
        "registration_sha256": source["sources"][PLAN], "invented": invented, "fold": fold,
        "attempt": "qualification1" if invented else (attempt or args.factorized_attempt or "actual1"),
        "invented_inputs_sha256": args.invented_inputs_sha256,
        "dataset_binding_sha256": None if invented else args.dataset_binding_sha256,
        "qualification_sha256": None if invented else args.factorized_qualification_sha256,
        "arms": list(ARMS), "fit_attempt_count": 0 if summary else 2, "scope": "common_chart_peer"}


def _slot(identity):
    name = identity["id"]
    if identity["invented"]:
        name += "-" + identity["merge"] + "-qualification1"
    elif not name.endswith("-summary"):
        name += "-" + identity["attempt"]
    return run.NATIVE / "output" / name


def _journal(output, identity):
    return output / "experiments.jsonl" if identity["invented"] else run.NATIVE / "canonical/experiments.jsonl"


def _invented_inputs(identity):
    source, runtime = identity["source"], identity["runtime_sha256"]
    first = launch._invented_slot(FIXTURE_RELEASE, 0)
    old = run._doc(first / "dataset-report.json")["identity"]["release"]
    _old_release(old, FIXTURE_RELEASE, source, runtime)
    shards, binding, _ = launch._invented_inputs(old)
    wire.require(binding == identity["invented_inputs_sha256"] and len(shards) == 9
        and sum(manifest["counts"]["coordinates"] for _, manifest in shards) == 5158,
        "EP full invented dataset binding or geometry differs")
    return shards, binding


def _actual_inputs(identity):
    # Called only after this EP unit's start and canonical journal start.
    value = run._doc(run.NATIVE / "canonical" / f"{contract.UNIT}-dataset-binding.json")
    binding = contract.validate_dataset_binding(value, identity["dataset_binding_sha256"])
    _old_release(binding.release, DATASET_RELEASE, identity["source"], identity["runtime_sha256"])
    admitted = launch._admit_actual(binding)
    return tuple((path, report["dataset"]) for path, report, _ in admitted), binding.inputs_sha256


def _read_document(output, pin, name):
    wire.require(type(pin) is dict and pin.get("filename") == name and wire.valid_sha(pin.get("sha256"))
        and type(pin.get("bytes")) is int and 0 < pin["bytes"] <= artifacts.MAX_DOCUMENT,
        "EP fixed document pin differs")
    return artifacts._read_document(output, pin)


def _read_rows(output, pin, name):
    wire.require(type(pin) is dict and pin.get("filename") == name and wire.valid_sha(pin.get("sha256"))
        and type(pin.get("bytes")) is int and 0 <= pin["bytes"] <= artifacts.MAX_FILE
        and type(pin.get("rows")) is int and 0 <= pin["rows"] <= pin["bytes"],
        "EP fixed stream pin differs")
    return list(artifacts._read_rows(output, pin))


def _saved_arms(output, payload):
    wire.require(set(payload["arms"]) == set(ARMS), "EP matched arm roster differs")
    results, policies = {}, {}
    for arm in ARMS:
        pins = payload["arms"][arm]
        wire.require(set(pins) == {"model", "predictions", "components", "policy"}, "EP arm file roster differs")
        result = _read_document(output, pins["model"], f"{arm}-model.json")
        result["predictions"] = _read_rows(output, pins["predictions"], f"{arm}-predictions.jsonl")
        result["components"] = _read_rows(output, pins["components"], f"{arm}-components.jsonl")
        results[arm] = result
        policies[arm] = _read_document(output, pins["policy"], f"{arm}-policy.json")
    return results, policies


def _fit_counts(results):
    native_fits = completed = artifacts_count = 0
    for arm in ARMS:
        result = results[arm]
        artifact = result["artifact"]
        wire.require(result["fit_artifacts"] == int(artifact is not None), "EP artifact fit count differs")
        if artifact is not None:
            count = artifact["native_joint_fit_count"]
            wire.require(type(count) is int and count in (0, 1), "EP native fit count differs")
            native_fits += count
            completed += int(artifact["training_diagnostics"]["status"] == "complete")
            artifacts_count += 1
    return {"fit_attempt_count": 2, "fit_count": native_fits,
            "completed_native_fit_count": completed, "fitted_artifact_count": artifacts_count}


def _publication(output, identity):
    done = run._doc(output / "completion.json")
    raw = run._read(output / "report.json", artifacts.MAX_DOCUMENT)
    payload = json.loads(raw)
    result = {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": payload["resources"]}
    wire.require(wire.encode(payload) == raw and done["identity"] == identity == payload["identity"]
        and run._doc(output / "start.json") == identity and done["result"] == result,
        "EP publication identity differs")
    span = done["journal"]
    wire.require(receipts._proof(_journal(output, identity), identity, identity["id"], result,
        span["start"], span["end"]) == span, "EP journal proof differs")
    if identity["fit_attempt_count"]:
        wire.require(payload["fit_attempt_count"] == 2 and payload["fold"] == identity["fold"]
            and payload["runtime"]["verified"] is True and payload["runtime"]["kind"] == "tcn"
            and payload["runtime"]["runtime_sha256"] == identity["runtime_sha256"],
            "EP matched fit count or native receipt differs")
        for arm in ARMS:
            wire.require(run._doc(output / f"{arm}-fit-attempt.json") == {"identity": identity, "arm": arm},
                         "EP fit attempt differs")
        results, _ = _saved_arms(output, payload)
        for arm in ARMS:
            if results[arm]["artifact"] is None:
                wire.require(not os.path.lexists(output / f"{arm}-fit-start.json"), "EP unsupported fit fabricated a start")
            else:
                wire.require(run._doc(output / f"{arm}-fit-start.json") == {"identity": identity, "arm": arm},
                             "EP fit start differs")
        wire.require(all(payload.get(key) == value for key, value in _fit_counts(results).items()),
                     "EP native fit counters differ")
        _read_rows(output, payload["representatives"], "representatives.jsonl")
        _read_document(output, payload["metrics"], "metrics.json")
    return payload


def _audited(output, identity):
    payload = _publication(output, identity)
    audit = run._doc(output / "audit.json")
    wire.require(run._doc(output / "audit-start.json") == identity
        and audit["identity"] == identity and audit["independently_verified"] is True
        and audit["report_sha256"] == wire.digest(wire.encode(payload))
        and not os.path.lexists(output / "audit-failure.json"), "EP independent audit differs")
    run.verify_usage(payload["resources"], audit["resources"])
    return payload, wire.digest(wire.encode(audit))


def _qualification(identity):
    qualified = {**identity, "id": UNIT + "-f3", "invented": True, "fold": 3,
        "attempt": "qualification1", "dataset_binding_sha256": None,
        "qualification_sha256": None, "fit_attempt_count": 2}
    payload, pin = _audited(_slot(qualified), qualified)
    wire.require(pin == identity["qualification_sha256"]
        and payload["geometry"]["sessions"] == 257 and payload["geometry"]["coordinates"] == 5158
        and payload["fit_count"] == payload["completed_native_fit_count"] == 2 and payload["fold"] == 3,
        "EP same-release largest-fold qualification differs")
    return pin


def _partial_artifacts(output, identity):
    """Authenticate retained partial files without treating them as results."""
    allowed = {"start.json", "failure.json", "produce-scratch", "representatives.jsonl", "metrics.json"}
    for arm in ARMS:
        allowed.update(f"{arm}-{name}" for name in
            ("fit-attempt.json", "fit-start.json", "model.json", "predictions.jsonl", "components.jsonl", "policy.json"))
    names = {path.name for path in output.iterdir()}
    wire.require(names <= allowed, "EP failed publication is ambiguous; reconcile retained files before retry")
    pins, fit_starts = {}, []
    for name in sorted(names - {"start.json", "failure.json", "produce-scratch"}):
        digest, size = wire.file_digest(output / name, artifacts.MAX_FILE, lambda _: None)
        pins[name] = {"sha256": digest, "bytes": size}
    for arm in ARMS:
        if f"{arm}-fit-start.json" in names:
            wire.require(run._doc(output / f"{arm}-fit-start.json") == {"identity": identity, "arm": arm},
                         "EP failed fit boundary is ambiguous")
            fit_starts.append(arm)
    if "produce-scratch" in names:
        wire.safe_path(output / "produce-scratch")
        wire.require((output / "produce-scratch").is_dir(), "EP failed scratch path differs")
    return {"files": pins, "fit_starts": fit_starts}


def _retry_predecessors(identity):
    """One unchanged retry; two technical failures require implementation repair."""
    if identity["invented"] or identity["id"].endswith("-summary"):
        return []
    current = ATTEMPTS.index(identity["attempt"])
    pins = []
    for index, attempt in enumerate(ATTEMPTS):
        previous = {**identity, "attempt": attempt}
        output = _slot(previous)
        if index > current:
            wire.require(not os.path.lexists(output), "EP later sibling attempt already exists")
        elif index < current:
            failure = run._doc(output / "failure.json")
            wire.require(run._doc(output / "start.json") == previous and failure["identity"] == previous
                and failure["error_type"] == "TimeoutError" and failure.get("recovery_supported") is True
                and failure["partial"] == _partial_artifacts(output, previous),
                "EP retry requires an authenticated same-input technical timeout")
            span = failure["journal"]
            wire.require(receipts._proof(_journal(output, previous), previous, previous["id"], None,
                span["start"], span["end"], failed=True) == span, "EP retained failure journal differs")
            pins.append({"failure_sha256": wire.digest(wire.encode(failure)),
                         "fit_start_count": len(failure["partial"]["fit_starts"])})
    return pins


def _project(output, identity, shards, binding, *, payload=None):
    audit = payload is not None
    scratch = output / ("audit-scratch" if audit else "produce-scratch")
    wire.exclusive_directory(scratch)
    admitted = native.activate("tcn", scratch)
    data = workflow.load_fold(shards, identity["fold"])
    roster = workflow.decision_roster(shards, data)
    geometry = {"sessions": len(data.base.calendar),
        "coordinates": sum(manifest["counts"]["coordinates"] for _, manifest in shards)}
    if audit:
        results, policies = _saved_arms(output, payload)
        reference.verify_matched(results)
        for arm in ARMS:
            reference.verify_fit(results[arm], data)
            workflow.verify_unit(results[arm], data)
            reference.verify_policy(roster.episodes, roster.candidates, results[arm]["predictions"], policies[arm])
        representatives = _read_rows(output, payload["representatives"], "representatives.jsonl")
        reference.verify_representatives(data.base.calendar, identity["fold"], roster.episodes,
            roster.candidates, {arm: results[arm]["predictions"] for arm in ARMS}, representatives,
            components_by_arm={arm: results[arm]["components"] for arm in ARMS})
        saved_metrics = _read_document(output, payload["metrics"], "metrics.json")
        reference.verify_report(data.base.calendar, identity["fold"], representatives,
            workflow.evaluation_records(shards, data, roster), policies, saved_metrics)
        wire.require(payload["geometry"] == geometry and payload["inputs_sha256"] == binding
            and payload["support"] == data.base.support, "EP audited context differs")
        runtime = native.verify_after(admitted)
        wire.require(runtime["verified"] is True and runtime["kind"] == "tcn"
            and runtime["runtime_sha256"] == identity["runtime_sha256"], "EP native audit readback differs")
        return None

    results, policies, files = {}, {}, {}
    for arm in ARMS:
        wire.exclusive_bytes(output / f"{arm}-fit-attempt.json", wire.encode({"identity": identity, "arm": arm}))
        def before_fit(arm=arm):
            wire.exclusive_bytes(output / f"{arm}-fit-start.json", wire.encode({"identity": identity, "arm": arm}))
        result = workflow.fit_unit(arm, data, before_fit=before_fit)
        if result["artifact"] is not None:
            wire.require(run._doc(output / f"{arm}-fit-start.json") == {"identity": identity, "arm": arm},
                         "EP learner skipped its durable fit boundary")
        policies[arm] = workflow.freeze_policy(data, roster, result)
        results[arm] = result
        model = {name: value for name, value in result.items() if name not in ("predictions", "components")}
        files[arm] = {"model": artifacts._document(output, f"{arm}-model.json", model),
            "predictions": artifacts._stream(output, f"{arm}-predictions.jsonl", result["predictions"]),
            "components": artifacts._stream(output, f"{arm}-components.jsonl", result["components"]),
            "policy": artifacts._document(output, f"{arm}-policy.json", workflow.serialize_policy(policies[arm]))}
    # Both matched artifacts and all causal choices are now immutable.
    reference.verify_matched(results)
    representatives = metrics.select_representatives(data.base.calendar, identity["fold"], roster.episodes,
        roster.candidates, {arm: results[arm]["predictions"] for arm in ARMS},
        components_by_arm={arm: results[arm]["components"] for arm in ARMS})
    representative_pin = artifacts._stream(output, "representatives.jsonl", representatives)
    records = tuple(workflow.evaluation_records(shards, data, roster))
    reports = {arm: workflow.join_policy_targets(policies[arm], records) for arm in ARMS}
    totals = metrics.summarize(data.base.calendar, identity["fold"], representatives, records, reports)
    metric_pin = artifacts._document(output, "metrics.json", totals)
    runtime = native.verify_after(admitted)
    wire.require(runtime["verified"] is True and runtime["kind"] == "tcn"
        and runtime["runtime_sha256"] == identity["runtime_sha256"], "EP native readback differs")
    return {"fold": identity["fold"], **_fit_counts(results), "inputs_sha256": binding, "arms": files,
        "representatives": representative_pin, "metrics": metric_pin, "geometry": geometry,
        "calendar": list(data.base.calendar), "support": data.base.support, "runtime": runtime}


def _summary(output, identity):
    _qualification(identity)
    folds, publications, reports = [], [], []
    for fold in range(4):
        directory, payload, pin = _audited_fold(identity, fold)
        folds.append({"fold": fold, "audit_sha256": pin, "report_sha256": wire.digest(wire.encode(payload))})
        publications.append(payload)
        reports.append(_read_document(directory, payload["metrics"], "metrics.json"))
    wire.require(len({row["inputs_sha256"] for row in publications}) == 1
        and all(row["calendar"] == publications[0]["calendar"] for row in publications)
        and sum(row["fit_attempt_count"] for row in publications) == 8, "EP summary fold bindings differ")
    pooled = metrics.pool(publications[0]["calendar"], reports)
    reference.verify_pool(publications[0]["calendar"], reports, pooled)
    return {"fold_audits": folds, "fit_attempt_count": 8,
        "fit_count": sum(row["fit_count"] for row in publications),
        "completed_native_fit_count": sum(row["completed_native_fit_count"] for row in publications),
        "calendar": publications[0]["calendar"],
        "inputs_sha256": publications[0]["inputs_sha256"],
        "additional_failed_fit_starts": sum(item["fit_start_count"] for row in publications
            for item in row["predecessor_timeouts"]),
        "fold_metrics": [{"fold": fold, "report": reports[fold]} for fold in range(4)], "pooled": pooled}


def _audited_fold(identity, fold):
    base = {**identity, "id": UNIT + f"-f{fold}", "fold": fold, "fit_attempt_count": 2}
    present = [attempt for attempt in ATTEMPTS if os.path.lexists(_slot({**base, "attempt": attempt}))]
    wire.require(present and present == list(ATTEMPTS[:len(present)]), "EP preceding fold audit is required")
    selected = {**base, "attempt": present[-1]}
    _retry_predecessors(selected)
    directory = _slot(selected)
    payload, pin = _audited(directory, selected)
    return directory, payload, pin


def _previous_folds(identity):
    for fold in range(identity["fold"]):
        _audited_fold(identity, fold)


def execute(args, registrations, source, started):
    run.STARTED_CLOCK = started
    run.budget()
    runtime = run.runtime_pin()
    invented = "qualification" in args.mode
    audit = args.mode in ("factorized-qualification-audit", "factorized-audit")
    summary = args.mode == "factorized-summary"
    wire.require(args.mode in MODES and all(getattr(args, name, None) is None for name in
        ("kind", "scope", "shard", "attempt", "summary_sha256", "entry_value_report_sha256",
         "entry_value_audit_sha256", "rank_failure_start", "rank_failure_end", "rank_failure_sha256"))
        and (args.fold is None if summary else type(args.fold) is int and args.fold in range(4))
        and (not invented or args.fold == 3) and wire.valid_sha(args.invented_inputs_sha256)
        and (args.factorized_attempt is None if invented or summary else args.factorized_attempt in (None, *ATTEMPTS)),
        "EP only admits its fixed four-fold matched comparison")
    wire.require((args.dataset_binding_sha256 is None and args.factorized_qualification_sha256 is None) if invented
        else wire.valid_sha(args.dataset_binding_sha256) and wire.valid_sha(args.factorized_qualification_sha256),
        "EP actual binding and qualification pins required only for actual modes")
    _public_predecessors(source)
    bridge.restrict_local_network()
    wire.prohibit_ef_tasks()
    identity = _identity(source, runtime, args, invented=invented, fold=args.fold, summary=summary)
    output = _slot(identity)
    with run._locks():
        if audit:
            wire.exclusive_bytes(output / "audit-start.json", wire.encode(identity))
            try:
                done = run._doc(output / "completion.json")
                wire.require(done["identity"] == identity, "EP audit completion identity differs")
                run.audit_budget(done["result"]["resources"])
                payload = _publication(output, identity)
                if not invented:
                    _qualification(identity)
                    _previous_folds(identity)
                shards, binding = _invented_inputs(identity) if invented else _actual_inputs(identity)
                _project(output, identity, shards, binding, payload=payload)
                _source_after(source, runtime)
                measured = run.usage()
                run.verify_usage(payload["resources"], measured)
                wire.exclusive_bytes(output / "audit.json", wire.encode({"identity": identity,
                    "report_sha256": wire.digest(wire.encode(payload)), "independently_verified": True,
                    "resources": measured}))
            except Exception as error:
                wire.exclusive_bytes(output / "audit-failure.json", wire.encode({"identity": identity,
                    "error_type": type(error).__name__, "resources": run.usage()}))
                raise
        else:
            wire.exclusive_directory(output)
            wire.exclusive_bytes(output / "start.json", wire.encode(identity))
            journal = _journal(output, identity)
            start = 0 if invented else wire.regular(journal).st_size
            def evaluate():
                predecessors = _retry_predecessors(identity)
                if summary:
                    value = _summary(output, identity)
                else:
                    if not invented:
                        _qualification(identity)
                        _previous_folds(identity)
                    shards, binding = _invented_inputs(identity) if invented else _actual_inputs(identity)
                    value = _project(output, identity, shards, binding)
                _source_after(source, runtime)
                measured = run.usage()
                run.verify_usage(measured, dict.fromkeys(measured, 0))
                payload = {"identity": identity, **value, "resources": measured, "predecessor_timeouts": predecessors}
                raw = wire.encode(payload)
                wire.require(len(raw) <= artifacts.MAX_DOCUMENT, "EP report bound exceeded")
                wire.exclusive_bytes(output / "report.json", raw)
                return {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": measured}
            try:
                terminal = experiment_log.run_discovery_trial(study_id=identity["id"],
                    specification_sha256=identity["registration_sha256"], parameters=identity, window=run.WINDOW,
                    evaluate=evaluate, runs_path=journal, code_version_provider=lambda: identity["merge"])
                result = terminal["result"]
                span = receipts._proof(journal, identity, identity["id"], result, start, wire.regular(journal).st_size)
                wire.exclusive_bytes(output / "completion.json", wire.encode({"identity": identity,
                    "result": result, "journal": span}))
            except Exception as error:
                failure = {"identity": identity, "error_type": type(error).__name__,
                    "fit_started": any(os.path.lexists(output / f"{arm}-fit-start.json") for arm in ARMS),
                    "resources": run.usage()}
                if type(error) is TimeoutError:
                    failure["journal"] = receipts._proof(journal, identity, identity["id"], None,
                        start, wire.regular(journal).st_size, failed=True)
                    try:
                        failure["partial"] = _partial_artifacts(output, identity)
                        failure["recovery_supported"] = True
                    except (ValueError, OSError):
                        failure["recovery_supported"] = False
                wire.exclusive_bytes(output / "failure.json", wire.encode(failure))
                raise
    print(json.dumps({"unit": identity["id"], "audit": audit, "status": "completed", "output": str(output)}, sort_keys=True))
