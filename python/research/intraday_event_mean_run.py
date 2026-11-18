"""Closed EQ training-situation mean comparison over audited EP predictions.

No native runtime is activated and no old learner is fitted or inferred again.
Old outcome documents are opened only after the new baseline and causal choices
are durable. The four-fold EP summary is admitted only in the final EQ summary.
"""

import json
import os

from research import experiment_log
from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_dataset_run as run
from research import intraday_event_fold_artifacts as artifacts
from research import intraday_event_model_receipts as receipts
from research import intraday_event_model_inputs as inputs
from research import intraday_event_factorized_run as ep
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_factorized_reference as ep_reference
from research import intraday_event_mean_baseline as baseline
from research import intraday_event_mean_metrics as metrics
from research import intraday_event_mean_reference as reference


UNIT = "EQ-C-PHASE-MEAN-1"
PLAN = ".planning/rd-eq-situation-mean.md"
ARMS = ("mean", "direct", "factorized")
MODES = ("phase-mean-qualification", "phase-mean-qualification-audit",
         "phase-mean-actual", "phase-mean-audit", "phase-mean-summary")
ATTEMPTS = ("actual1", "actual2")
EP_RELEASE = {"merge": "223bd78c881ad75fe480bee53f26cf8ff78f460a",
    "reviewed": "1641fb8d32c558e71163396f552df0b4aa876e98",
    "tree": "b87618b19fabf7ee6478ee8bdd686268e896ca74"}


def _public_predecessors(source):
    ep._public_predecessors(source)
    release = EP_RELEASE
    commit, tree, parents = run._git("show", "-s", "--format=%H%n%T%n%P", release["merge"]).decode().splitlines()
    wire.require(commit == release["merge"] and tree == release["tree"] and len(parents.split()) == 2
        and parents.split()[1] == release["reviewed"] and parents.split()[0] != release["reviewed"]
        and run._git("rev-parse", release["reviewed"] + "^{tree}").decode().strip() == tree,
        "EQ predecessor is not its reviewed normal merge")
    run._git("merge-base", "--is-ancestor", release["merge"], source["merge"])


def _identity(source, runtime, args, *, invented=False, fold=None, summary=False):
    return {"id": UNIT + ("-summary" if summary else f"-f{fold}"), "source": source,
        "merge": source["merge"], "runtime_sha256": runtime, "registration_sha256": source["sources"][PLAN],
        "invented": invented, "fold": fold, "attempt": "qualification1" if invented else (args.mean_attempt or "actual1"),
        "invented_inputs_sha256": args.invented_inputs_sha256,
        "dataset_binding_sha256": None if invented else args.dataset_binding_sha256,
        "qualification_sha256": None if invented else args.mean_qualification_sha256,
        "ep_invented_report_sha256": args.ep_invented_report_sha256,
        "ep_invented_audit_sha256": args.ep_invented_audit_sha256,
        "ep_fold_report_sha256": None if invented else list(args.ep_fold_report_sha256),
        "ep_fold_audit_sha256": None if invented else list(args.ep_fold_audit_sha256),
        "ep_summary_sha256": None if invented else args.ep_summary_sha256,
        "arms": list(ARMS), "native_fit_count": 0, "native_inference_count": 0,
        "scope": "common_chart_peer"}


def _slot(identity):
    name = identity["id"]
    if identity["invented"]:
        name += "-" + identity["merge"] + "-qualification1"
    elif not name.endswith("-summary"):
        name += "-" + identity["attempt"]
    return run.NATIVE / "output" / name


def _journal(output, identity):
    return output / "experiments.jsonl" if identity["invented"] else run.NATIVE / "canonical/experiments.jsonl"


def _envelope(output, identity, *, journal=None):
    """Authenticate a publication without opening any referenced outcome file."""
    raw = run._read(output / "report.json", artifacts.MAX_DOCUMENT)
    payload = json.loads(raw)
    done = run._doc(output / "completion.json")
    result = {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": payload["resources"]}
    wire.require(wire.encode(payload) == raw and done["identity"] == identity == payload["identity"]
        and run._doc(output / "start.json") == identity and done["result"] == result,
        "EQ publication identity differs")
    span = done["journal"]
    wire.require(receipts._proof(journal or _journal(output, identity), identity, identity["id"], result,
        span["start"], span["end"]) == span, "EQ journal proof differs")
    return payload


def _audit_receipt(output, identity, payload):
    audit = run._doc(output / "audit.json")
    wire.require(run._doc(output / "audit-start.json") == identity and audit["identity"] == identity
        and audit["independently_verified"] is True
        and audit["report_sha256"] == wire.digest(wire.encode(payload))
        and not os.path.lexists(output / "audit-failure.json"), "EQ independent audit differs")
    run.verify_usage(payload["resources"], audit["resources"])
    return wire.digest(wire.encode(audit))


def _old_source(old, identity):
    source = old["source"]
    wire.require(all(source.get(name) == pin for name, pin in EP_RELEASE.items())
        and old["merge"] == EP_RELEASE["merge"] and source.get("sources")
        and all(identity["source"]["sources"].get(path) == pin
                for path, pin in source["sources"].items() if path != ep.DISPATCHER)
        and old["runtime_sha256"] == identity["runtime_sha256"]
        and old["registration_sha256"] == source["sources"].get(ep.PLAN)
        and old["invented_inputs_sha256"] == identity["invented_inputs_sha256"]
        and old["dataset_binding_sha256"] == identity["dataset_binding_sha256"]
        and old["scope"] == "common_chart_peer" and old["arms"] == list(ep.ARMS),
        "EQ unchanged EP source/runtime/input binding differs")


def _old_fold(identity):
    """Stage one: no metrics, summary, labels or native inference here."""
    fold = identity["fold"]
    name = ep.UNIT + f"-f{fold}"
    name += "-" + EP_RELEASE["merge"] + "-qualification1" if identity["invented"] else "-actual1"
    output = run.NATIVE / "output" / name
    old = run._doc(output / "start.json")
    _old_source(old, identity)
    wire.require(old["id"] == ep.UNIT + f"-f{fold}" and old["fold"] == fold
        and old["invented"] is identity["invented"] and old["fit_attempt_count"] == 2
        and old["attempt"] == ("qualification1" if identity["invented"] else "actual1"),
        "EQ EP fixed predecessor fold differs")
    payload = _envelope(output, old, journal=ep._journal(output, old))
    report_pin = identity["ep_invented_report_sha256"] if identity["invented"] else identity["ep_fold_report_sha256"][fold]
    audit_pin = identity["ep_invented_audit_sha256"] if identity["invented"] else identity["ep_fold_audit_sha256"][fold]
    wire.require(wire.digest(wire.encode(payload)) == report_pin and _audit_receipt(output, old, payload) == audit_pin,
        "EQ pinned EP publication differs")
    results, policies = ep._saved_arms(output, payload)
    return output, old, payload, results, policies


def _old_outcomes(output, old, payload):
    """Stage two is legal only after the current EQ causal artifacts are sealed."""
    wire.require(ep._publication(output, old) == payload, "EQ EP publication changed after baseline seal")


def _saved(output, payload):
    wire.require(set(payload["arms"]) == set(ARMS), "EQ three-arm roster differs")
    predictions, policies = {}, {}
    for arm in ARMS:
        pins = payload["arms"][arm]
        wire.require(set(pins) == {"predictions", "policy"}, "EQ arm file roster differs")
        predictions[arm] = ep._read_rows(output, pins["predictions"], f"{arm}-predictions.jsonl")
        policies[arm] = ep._read_document(output, pins["policy"], f"{arm}-policy.json")
    artifact = ep._read_document(output, payload["baseline"], "baseline.json")
    representatives = ep._read_rows(output, payload["representatives"], "representatives.jsonl")
    return artifact, predictions, policies, representatives


def _publication(output, identity):
    payload = _envelope(output, identity)
    wire.require(payload["native_fit_count"] == payload["native_inference_count"] == 0,
        "EQ cannot add native fits or inference")
    if identity["fold"] is not None:
        wire.require(payload["fold"] == identity["fold"], "EQ fold differs")
        _saved(output, payload)
        ep._read_document(output, payload["metrics"], "metrics.json")
    return payload


def _audited(output, identity):
    payload = _publication(output, identity)
    return payload, _audit_receipt(output, identity, payload)


def _qualification_identity(identity):
    return {**identity, "id": UNIT + "-f3", "invented": True, "fold": 3, "attempt": "qualification1",
        "dataset_binding_sha256": None, "qualification_sha256": None,
        "ep_fold_report_sha256": None, "ep_fold_audit_sha256": None, "ep_summary_sha256": None}


def _qualification(identity):
    qualified = _qualification_identity(identity)
    payload, pin = _audited(_slot(qualified), qualified)
    wire.require(pin == identity["qualification_sha256"] and payload["fold"] == 3
        and payload["geometry"] == {"sessions": 257, "coordinates": 5158},
        "EQ same-release largest-fold qualification differs")
    return pin


def _partial_artifacts(output):
    allowed = {"start.json", "failure.json", "baseline.json", "representatives.jsonl", "metrics.json"}
    allowed.update(f"{arm}-{name}" for arm in ARMS for name in ("predictions.jsonl", "policy.json"))
    names = {path.name for path in output.iterdir()}
    wire.require(names <= allowed, "EQ failed publication is ambiguous")
    pins = {}
    for name in sorted(names - {"start.json", "failure.json"}):
        digest, size = wire.file_digest(output / name, artifacts.MAX_FILE, lambda _: None)
        pins[name] = {"sha256": digest, "bytes": size}
    return pins


def _retry_predecessors(identity):
    if identity["invented"] or identity["fold"] is None:
        return []
    current = ATTEMPTS.index(identity["attempt"])
    pins = []
    for index, attempt in enumerate(ATTEMPTS):
        previous = {**identity, "attempt": attempt}
        output = _slot(previous)
        if index > current:
            wire.require(not os.path.lexists(output), "EQ later sibling attempt already exists")
        elif index < current:
            failure = run._doc(output / "failure.json")
            wire.require(run._doc(output / "start.json") == previous and failure["identity"] == previous
                and failure["error_type"] == "TimeoutError" and failure.get("recovery_supported") is True
                and failure["partial"] == _partial_artifacts(output),
                "EQ retry requires an authenticated same-input technical timeout")
            span = failure["journal"]
            wire.require(receipts._proof(_journal(output, previous), previous, previous["id"], None,
                span["start"], span["end"], failed=True) == span, "EQ retained failure journal differs")
            pins.append(wire.digest(wire.encode(failure)))
    return pins


def _evaluation_records(shards, data, roster):
    """Decode no labels beyond this fold, even in an overlapping final shard.

    Authenticate complete original bytes but derive the permitted decode prefix
    from causal queries. The first later-fold label is never a boundary probe.
    This lazy adapter is consumed only after the current causal seals exist.
    """
    wire.require(type(data) is workflow.FactorizedData, "EQ original fold data required")
    _, _, end = workflow.FOLDS[data.base.fold]
    cutoff = data.base.calendar[end] if end < len(data.base.calendar) else None
    wanted = {candidate.key: candidate for candidate in roster.candidates}
    wire.require(len(wanted) == len(roster.candidates), "EQ duplicate causal evaluation key")
    seen = set()
    for slot, manifest in inputs._test_shards(shards, data.base.fold):
        prefix = []
        queries = inputs.query_rows(slot, manifest)
        if cutoff is None:
            prefix.extend((row["key"], row["session"], row["symbol"]) for row in queries)
        else:
            for _ in inputs._training_prefix(queries, cutoff, prefix):
                pass
        for row in inputs._labels_for_prefix(slot, manifest, prefix):
            key = row["key"]
            if key not in wanted:
                continue
            candidate = wanted[key]
            wire.require(key not in seen and row["query_kind"] == "entry"
                and (row["session"], row["symbol"]) == (candidate.episode.session, candidate.episode.symbol)
                and (row["targets"]["event_id"], row["targets"]["decision_offset"])
                    == (candidate.episode.event_id, candidate.frame.decision_offset),
                "EQ original C evaluation target identity or clock differs")
            workflow.validate_saved_record(row, candidate.query, session_length=candidate.label_query.session_length,
                                           label_query=candidate.label_query)
            seen.add(key)
            yield row
    wire.require(seen == set(wanted), "EQ original C evaluation target roster incomplete")


def _project(output, identity, shards, binding, *, payload=None):
    old_output, old_identity, old_payload, results, old_policies = _old_fold(identity)
    data = workflow.load_fold(shards, identity["fold"])
    roster = workflow.decision_roster(shards, data)
    phases = baseline.training_phases(shards, data)
    exact_targets = baseline.training_targets(shards, data)
    geometry = {"sessions": len(data.base.calendar),
        "coordinates": sum(manifest["counts"]["coordinates"] for _, manifest in shards)}
    components = {arm: results[arm]["components"] for arm in ep.ARMS}
    old_pins = {"report_sha256": wire.digest(wire.encode(old_payload)),
        "audit_sha256": identity["ep_invented_audit_sha256"] if identity["invented"]
                        else identity["ep_fold_audit_sha256"][identity["fold"]]}
    if payload is not None:
        artifact, predictions, policies, representatives = _saved(output, payload)
        reference.verify_training_inputs(shards, data, phases, exact_targets)
        reference.verify_baseline(data, phases, exact_targets, artifact)
        reference.verify_predictions(data, roster, artifact, predictions["mean"])
        reference.verify_policy(roster.episodes, roster.candidates, predictions["mean"], policies["mean"])
        for arm in ep.ARMS:
            wire.require(predictions[arm] == results[arm]["predictions"] and policies[arm] == old_policies[arm],
                "EQ copied EP predictions/policy changed")
            ep_reference.verify_policy(roster.episodes, roster.candidates, predictions[arm], policies[arm])
        reference.verify_representatives(data.base.calendar, identity["fold"], roster.episodes,
            roster.candidates, predictions, representatives, components_by_arm=components)
        _old_outcomes(old_output, old_identity, old_payload)
        saved_metrics = ep._read_document(output, payload["metrics"], "metrics.json")
        reference.verify_report(data.base.calendar, identity["fold"], representatives,
            _evaluation_records(shards, data, roster), policies, saved_metrics)
        wire.require(payload["geometry"] == geometry and payload["inputs_sha256"] == binding
            and payload["support"] == data.base.support and payload["predecessor"] == old_pins,
            "EQ audited context differs")
        return None

    artifact = baseline.estimate(data, phases, exact_targets)
    predictions = {"mean": baseline.project(data, roster, artifact),
                   **{arm: results[arm]["predictions"] for arm in ep.ARMS}}
    policies = {"mean": baseline.freeze_policy(data, roster, predictions["mean"])}
    for arm in ep.ARMS:
        policies[arm] = workflow.freeze_policy(data, roster, results[arm])
        wire.require(workflow.serialize_policy(policies[arm]) == old_policies[arm], "EQ original EP policy changed")
    artifact_pin = artifacts._document(output, "baseline.json", artifact)
    files = {arm: {"predictions": artifacts._stream(output, f"{arm}-predictions.jsonl", predictions[arm]),
        "policy": artifacts._document(output, f"{arm}-policy.json", workflow.serialize_policy(policies[arm]))}
        for arm in ARMS}
    representatives = metrics.select_representatives(data.base.calendar, identity["fold"], roster.episodes,
        roster.candidates, predictions, components_by_arm=components)
    representative_pin = artifacts._stream(output, "representatives.jsonl", representatives)
    # The complete new estimator, forecasts, choices and representative roster
    # are durable before either old statistics or evaluation targets are parsed.
    _old_outcomes(old_output, old_identity, old_payload)
    records = tuple(_evaluation_records(shards, data, roster))
    reports = {"mean": baseline.join_policy_targets(policies["mean"], records),
        **{arm: workflow.join_policy_targets(policies[arm], records) for arm in ep.ARMS}}
    totals = metrics.summarize(data.base.calendar, identity["fold"], representatives, records, reports)
    metric_pin = artifacts._document(output, "metrics.json", totals)
    return {"fold": identity["fold"], "native_fit_count": 0, "native_inference_count": 0,
        "inputs_sha256": binding, "baseline": artifact_pin, "arms": files, "predecessor": old_pins,
        "representatives": representative_pin, "metrics": metric_pin, "geometry": geometry,
        "calendar": list(data.base.calendar), "support": data.base.support}


def _audited_fold(identity, fold):
    base = {**identity, "id": UNIT + f"-f{fold}", "fold": fold}
    present = [attempt for attempt in ATTEMPTS if os.path.lexists(_slot({**base, "attempt": attempt}))]
    wire.require(present and present == list(ATTEMPTS[:len(present)]), "EQ preceding fold audit is required")
    selected = {**base, "attempt": present[-1]}
    _retry_predecessors(selected)
    output = _slot(selected)
    payload, pin = _audited(output, selected)
    return output, payload, pin


def _previous_folds(identity):
    for fold in range(identity["fold"]):
        _audited_fold(identity, fold)


def _summary(output, identity):
    _qualification(identity)
    folds, publications, reports = [], [], []
    for fold in range(4):
        directory, payload, pin = _audited_fold(identity, fold)
        folds.append({"fold": fold, "audit_sha256": pin, "report_sha256": wire.digest(wire.encode(payload))})
        publications.append(payload)
        reports.append(ep._read_document(directory, payload["metrics"], "metrics.json"))
    wire.require(len({row["inputs_sha256"] for row in publications}) == 1
        and all(row["calendar"] == publications[0]["calendar"] for row in publications),
        "EQ summary fold bindings differ")
    # All four new baselines and their independent audits exist before the EP
    # summary, which embeds outcomes from every fold, is decoded here.
    old_output = run.NATIVE / "output" / (ep.UNIT + "-summary")
    old_identity = run._doc(old_output / "start.json")
    _old_source(old_identity, identity)
    wire.require(old_identity["id"] == ep.UNIT + "-summary" and old_identity["fold"] is None
        and old_identity["invented"] is False and old_identity["fit_attempt_count"] == 0,
        "EQ EP summary identity differs")
    old = ep._publication(old_output, old_identity)
    expected = [{"fold": fold, "report_sha256": identity["ep_fold_report_sha256"][fold],
                 "audit_sha256": identity["ep_fold_audit_sha256"][fold]} for fold in range(4)]
    wire.require(wire.digest(wire.encode(old)) == identity["ep_summary_sha256"]
        and old["fold_audits"] == expected and old["inputs_sha256"] == publications[0]["inputs_sha256"]
        and old["calendar"] == publications[0]["calendar"], "EQ EP summary pins differ")
    pooled = metrics.pool(publications[0]["calendar"], reports)
    reference.verify_pool(publications[0]["calendar"], reports, pooled)
    return {"fold_audits": folds, "native_fit_count": 0, "native_inference_count": 0,
        "calendar": publications[0]["calendar"], "inputs_sha256": publications[0]["inputs_sha256"],
        "ep_summary_sha256": identity["ep_summary_sha256"],
        "fold_metrics": [{"fold": fold, "report": reports[fold]} for fold in range(4)], "pooled": pooled}


def execute(args, registrations, source, started):
    run.STARTED_CLOCK = started
    run.budget()
    runtime = run.runtime_pin()
    invented = "qualification" in args.mode
    audit = args.mode in ("phase-mean-qualification-audit", "phase-mean-audit")
    summary = args.mode == "phase-mean-summary"
    wire.require(args.mode in MODES and all(getattr(args, name, None) is None for name in
        ("kind", "scope", "shard", "attempt", "summary_sha256", "entry_value_report_sha256",
         "entry_value_audit_sha256", "rank_failure_start", "rank_failure_end", "rank_failure_sha256",
         "factorized_qualification_sha256", "factorized_attempt"))
        and (args.fold is None if summary else type(args.fold) is int and args.fold in range(4))
        and (not invented or args.fold == 3) and wire.valid_sha(args.invented_inputs_sha256)
        and wire.valid_sha(args.ep_invented_report_sha256) and wire.valid_sha(args.ep_invented_audit_sha256)
        and (args.mean_attempt is None if invented or summary else args.mean_attempt in (None, *ATTEMPTS)),
        "EQ only admits its fixed saved-prediction comparison")
    pins = (args.ep_fold_report_sha256, args.ep_fold_audit_sha256)
    wire.require((args.dataset_binding_sha256 is None and args.mean_qualification_sha256 is None
            and args.ep_summary_sha256 is None and all(value is None for value in pins)) if invented else
        wire.valid_sha(args.dataset_binding_sha256) and wire.valid_sha(args.mean_qualification_sha256)
        and wire.valid_sha(args.ep_summary_sha256) and all(type(value) in (list, tuple) and len(value) == 4
            and all(wire.valid_sha(pin) for pin in value) for value in pins),
        "EQ actual predecessor and qualification pins required only for actual modes")
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
                wire.require(done["identity"] == identity, "EQ audit completion identity differs")
                run.audit_budget(done["result"]["resources"])
                payload = _publication(output, identity)
                if not invented:
                    _qualification(identity)
                    _previous_folds(identity)
                shards, binding = ep._invented_inputs(identity) if invented else ep._actual_inputs(identity)
                _project(output, identity, shards, binding, payload=payload)
                ep._source_after(source, runtime)
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
                    shards, binding = ep._invented_inputs(identity) if invented else ep._actual_inputs(identity)
                    value = _project(output, identity, shards, binding)
                ep._source_after(source, runtime)
                measured = run.usage()
                run.verify_usage(measured, dict.fromkeys(measured, 0))
                payload = {"identity": identity, **value, "resources": measured, "predecessor_timeouts": predecessors}
                raw = wire.encode(payload)
                wire.require(len(raw) <= artifacts.MAX_DOCUMENT, "EQ report bound exceeded")
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
                failure = {"identity": identity, "error_type": type(error).__name__, "resources": run.usage()}
                if type(error) is TimeoutError:
                    failure["journal"] = receipts._proof(journal, identity, identity["id"], None,
                        start, wire.regular(journal).st_size, failed=True)
                    try:
                        failure["partial"] = _partial_artifacts(output)
                        failure["recovery_supported"] = True
                    except (ValueError, OSError):
                        failure["recovery_supported"] = False
                wire.exclusive_bytes(output / "failure.json", wire.encode(failure))
                raise
    print(json.dumps({"unit": identity["id"], "audit": audit, "status": "completed", "output": str(output)}, sort_keys=True))
