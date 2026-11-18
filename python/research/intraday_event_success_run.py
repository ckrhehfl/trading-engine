"""Closed ES profitable-fill ranking with one adapted TCN fit per fold.

Current-fold evaluation labels remain unopened until the fitted artifact,
all three forecasts and the causal representative roster are durable.
"""

import json
import os

from research import experiment_log
from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_dataset_run as run
from research import intraday_event_fold_artifacts as artifacts
from research import intraday_event_model_receipts as receipts
from research import intraday_event_native as native
from research import intraday_event_factorized_run as ep
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_factorized_reference as ep_reference
from research import intraday_event_mean_run as eq
from research import intraday_event_mean_baseline as mean
from research import intraday_event_success as success
from research import intraday_event_success_metrics as metrics
from research import intraday_event_success_reference as reference


UNIT = "ES-C-PROFITABLE-FILL-1"
PLAN = ".planning/rd-es-profitable-fill-ranking.md"
ARMS = ("rate", "direct", "success")
MODES = ("success-qualification", "success-qualification-audit", "success-actual", "success-audit", "success-summary")
ATTEMPTS = ep.ATTEMPTS
EP_ACTUAL_PINS = ("ep_fold_report_sha256", "ep_fold_audit_sha256", "ep_summary_sha256")
MODEL = "success-model.json"
COMPONENTS = "success-components.jsonl"
RATE = "rate.json"
REPRESENTATIVES = "representatives.jsonl"
METRICS = "metrics.json"


def _validate_args(args):
    invented = "qualification" in args.mode
    summary = args.mode == "success-summary"
    wire.require(args.mode in MODES and all(getattr(args, name, None) is None for name in (
        "kind", "scope", "shard", "attempt", "inner", "summary_sha256", "entry_value_report_sha256",
        "entry_value_audit_sha256", "rank_failure_start", "rank_failure_end", "rank_failure_sha256",
        "factorized_qualification_sha256", "factorized_attempt", "mean_qualification_sha256", "mean_attempt",
        "calibration_inner_qualification_sha256", "calibration_qualification_sha256", "calibration_attempt"))
        and (args.fold is None if summary else type(args.fold) is int and args.fold in range(4))
        and (not invented or args.fold == 3)
        and (args.success_attempt is None if invented or summary else args.success_attempt in (None, *ATTEMPTS))
        and all(wire.valid_sha(getattr(args, name)) for name in ("invented_inputs_sha256",
            "ep_invented_report_sha256", "ep_invented_audit_sha256")), "ES fixed comparison coordinate or pins differ")
    report_pins, audit_pins, summary_pin = (getattr(args, name) for name in EP_ACTUAL_PINS)
    wire.require((args.dataset_binding_sha256 is None and args.success_qualification_sha256 is None
            and report_pins is None and audit_pins is None and summary_pin is None) if invented else
        wire.valid_sha(args.dataset_binding_sha256) and wire.valid_sha(args.success_qualification_sha256)
        and wire.valid_sha(summary_pin) and all(type(pins) in (list, tuple) and len(pins) == 4
            and all(wire.valid_sha(pin) for pin in pins) for pins in (report_pins, audit_pins)),
        "ES actual dataset, qualification and predecessor pins are required only for actual modes")


def _identity(source, runtime, args):
    invented = "qualification" in args.mode
    fold = args.fold
    return {"id": UNIT + ("-summary" if fold is None else f"-f{fold}"), "source": source,
        "merge": source["merge"], "runtime_sha256": runtime, "registration_sha256": source["sources"][PLAN],
        "invented": invented, "fold": fold, "attempt": "qualification1" if invented else (args.success_attempt or "actual1"),
        "invented_inputs_sha256": args.invented_inputs_sha256,
        "dataset_binding_sha256": args.dataset_binding_sha256, "qualification_sha256": args.success_qualification_sha256,
        "ep_invented_report_sha256": args.ep_invented_report_sha256, "ep_invented_audit_sha256": args.ep_invented_audit_sha256,
        "ep_fold_report_sha256": None if invented else list(args.ep_fold_report_sha256),
        "ep_fold_audit_sha256": None if invented else list(args.ep_fold_audit_sha256),
        "ep_summary_sha256": args.ep_summary_sha256, "arms": list(ARMS),
        "fit_attempt_count": int(fold is not None), "scope": "common_chart_peer"}


_slot = ep._slot
_journal = ep._journal


def _prediction_file(arm):
    return f"{arm}-predictions.jsonl"


def _saved(output, payload):
    wire.require(set(payload["predictions"]) == set(ARMS), "ES ranking arm roster differs")
    predictions = {arm: ep._read_rows(output, payload["predictions"][arm], _prediction_file(arm)) for arm in ARMS}
    model = ep._read_document(output, payload["model"], MODEL)
    wire.require(model["arm"] == "direct", "ES success scorer must use the original direct learner")
    model["predictions"] = predictions["success"]
    model["components"] = ep._read_rows(output, payload["components"], COMPONENTS)
    rate = ep._read_document(output, payload["rate"], RATE)
    representatives = ep._read_rows(output, payload["representatives"], REPRESENTATIVES)
    return rate, model, predictions, representatives


def _fit_counts(model):
    artifact = model["artifact"]
    wire.require(model["fit_artifacts"] == int(artifact is not None), "ES fit artifact count differs")
    count = 0 if artifact is None else artifact["native_joint_fit_count"]
    wire.require(type(count) is int and count in (0, 1), "ES native fit count differs")
    completed = int(artifact is not None and artifact["training_diagnostics"]["status"] == "complete")
    return {"fit_attempt_count": 1, "fit_count": count, "completed_native_fit_count": completed,
            "fitted_artifact_count": int(artifact is not None)}


def _verify_runtime(runtime, identity):
    wire.require(runtime["verified"] is True and runtime["kind"] == "tcn"
        and runtime["runtime_sha256"] == identity["runtime_sha256"], "ES TCN runtime differs")


def _fit_receipts(output, identity, model):
    wire.require(run._doc(output / "fit-attempt.json") == identity, "ES fit-attempt receipt differs")
    if model["artifact"] is not None:
        wire.require(run._doc(output / "fit-start.json") == identity, "ES durable fit start differs")
    else:
        wire.require(not os.path.lexists(output / "fit-start.json"), "ES empty training fabricated a fit start")


def _publication(output, identity, *, verify_files=True):
    payload = eq._envelope(output, identity, journal=_journal(output, identity))
    if identity["fold"] is not None:
        wire.require(payload["fold"] == identity["fold"] and payload["fit_attempt_count"] == identity["fit_attempt_count"],
            "ES completed fold differs")
        _verify_runtime(payload["runtime"], identity)
        if verify_files:
            _, model, _, _ = _saved(output, payload)
            _fit_receipts(output, identity, model)
            wire.require(all(payload.get(name) == value for name, value in _fit_counts(model).items()),
                "ES published fit counts differ")
            # Admit report bytes without decoding their outcomes at a later fold's start.
            pin = payload["metrics"]
            wire.require(pin["filename"] == METRICS and wire.valid_sha(pin["sha256"])
                and type(pin["bytes"]) is int and 0 < pin["bytes"] <= artifacts.MAX_DOCUMENT
                and wire.file_digest(output / METRICS, artifacts.MAX_DOCUMENT, lambda _: None)
                    == (pin["sha256"], pin["bytes"]), "ES published metrics changed")
    return payload


def _audited(output, identity):
    payload = _publication(output, identity)
    return payload, eq._audit_receipt(output, identity, payload)


def _qualification(identity):
    qualified = {**identity, "id": UNIT + "-f3", "invented": True, "fold": 3, "attempt": "qualification1",
        "fit_attempt_count": 1, "dataset_binding_sha256": None, "qualification_sha256": None,
        **dict.fromkeys(EP_ACTUAL_PINS)}
    payload, pin = _audited(_slot(qualified), qualified)
    wire.require(pin == identity["qualification_sha256"] and payload["fold"] == 3
        and payload["geometry"] == {"sessions": 257, "coordinates": 5158}
        and payload["fit_count"] == payload["completed_native_fit_count"] == 1,
        "ES same-release largest-fold qualification differs")


def _partial_artifacts(output, identity):
    allowed = {"start.json", "failure.json", "produce-scratch", "fit-attempt.json", "fit-start.json",
        MODEL, COMPONENTS, RATE, REPRESENTATIVES, METRICS, *map(_prediction_file, ARMS)}
    names = {path.name for path in output.iterdir()}
    wire.require(names <= allowed, "ES failed publication is ambiguous")
    pins = {}
    for name in sorted(names - {"start.json", "failure.json", "produce-scratch"}):
        digest, size = wire.file_digest(output / name, artifacts.MAX_FILE, lambda _: None)
        pins[name] = {"sha256": digest, "bytes": size}
    if "produce-scratch" in names:
        wire.safe_path(output / "produce-scratch")
        wire.require((output / "produce-scratch").is_dir(), "ES failed scratch path differs")
    for name in ("fit-attempt.json", "fit-start.json"):
        if name in names:
            wire.require(run._doc(output / name) == identity, "ES retained fit receipt differs")
    started = "fit-start.json" in names
    wire.require(not started or "fit-attempt.json" in names, "ES retained fit start has no attempt")
    return {"files": pins, "fit_started": started}


def _retry_predecessors(identity):
    if identity["invented"] or identity["fold"] is None:
        return []
    current = ATTEMPTS.index(identity["attempt"])
    pins = []
    for index, attempt in enumerate(ATTEMPTS):
        previous = {**identity, "attempt": attempt}
        output = _slot(previous)
        if index > current:
            wire.require(not os.path.lexists(output), "ES later sibling attempt already exists")
        elif index < current:
            failure = run._doc(output / "failure.json")
            wire.require(run._doc(output / "start.json") == previous and failure["identity"] == previous
                and failure["error_type"] == "TimeoutError" and failure.get("recovery_supported") is True
                and failure["partial"] == _partial_artifacts(output, previous),
                "ES retry requires an authenticated same-input technical timeout")
            span = failure["journal"]
            wire.require(receipts._proof(_journal(output, previous), previous, previous["id"], None,
                span["start"], span["end"], failed=True) == span, "ES retained failure journal differs")
            pins.append({"failure_sha256": wire.digest(wire.encode(failure)),
                         "fit_started": failure["partial"]["fit_started"]})
    return pins


def _project(output, identity, shards, binding, *, payload=None):
    audit = payload is not None
    fold = identity["fold"]
    scratch = output / ("audit-scratch" if audit else "produce-scratch")
    wire.exclusive_directory(scratch)
    admitted = native.activate("tcn", scratch)
    _, _, old, results, _ = eq._old_fold(identity)
    direct = results["direct"]["predictions"]
    predecessor = {"report_sha256": wire.digest(wire.encode(old)),
        "audit_sha256": identity["ep_invented_audit_sha256"] if identity["invented"] else identity["ep_fold_audit_sha256"][fold]}
    data = workflow.load_fold(shards, fold)
    roster = workflow.decision_roster(shards, data)
    phases = mean.training_phases(shards, data)
    targets = mean.training_targets(shards, data)
    adapted, rate = success.prepare(data, phases, targets)
    calendar = data.base.calendar
    context = {"fold": fold, "inputs_sha256": binding, "calendar": list(calendar), "support": data.base.support,
        "geometry": {"sessions": len(calendar),
                     "coordinates": sum(manifest["counts"]["coordinates"] for _, manifest in shards)},
        "predecessor": predecessor}
    if audit:
        saved_rate, model, predictions, representatives = _saved(output, payload)
        _fit_receipts(output, identity, model)
        wire.require(all(payload.get(name) == value for name, value in {**context, **_fit_counts(model)}.items())
            and predictions["direct"] == direct and saved_rate == rate, "ES audited fit context or predecessor differs")
        reference.verify_training_inputs(shards, data, phases, targets)
        reference.verify_training(data, phases, targets, adapted, saved_rate)
        ep_reference.verify_fit(model, adapted)
        workflow.verify_unit(model, adapted)
        reference.verify_projection(data, roster, saved_rate, predictions["rate"])
        reference.verify_representatives(calendar, fold, roster.episodes, roster.candidates, predictions, representatives)
        saved_metrics = ep._read_document(output, payload["metrics"], METRICS)
        reference.verify_report(calendar, fold, representatives, eq._evaluation_records(shards, data, roster), saved_metrics)
        runtime = native.verify_after(admitted)
        _verify_runtime(runtime, identity)
        wire.require(runtime == payload["runtime"], "ES producer and audit native readback differ")
        return None

    rate_pin = artifacts._document(output, RATE, rate)
    wire.exclusive_bytes(output / "fit-attempt.json", wire.encode(identity))

    def before_fit():
        wire.exclusive_bytes(output / "fit-start.json", wire.encode(identity))

    model = workflow.fit_unit("direct", adapted, before_fit=before_fit)
    _fit_receipts(output, identity, model)
    model_pin = artifacts._document(output, MODEL,
        {name: value for name, value in model.items() if name not in ("predictions", "components")})
    components = artifacts._stream(output, COMPONENTS, model["components"])
    predictions = {"rate": success.project(data, roster, rate), "direct": direct, "success": model["predictions"]}
    files = {arm: artifacts._stream(output, _prediction_file(arm), predictions[arm]) for arm in ARMS}
    representatives = metrics.select_representatives(calendar, fold, roster.episodes, roster.candidates, predictions)
    representative_pin = artifacts._stream(output, REPRESENTATIVES, representatives)
    # The stored native fit, all score streams and causal representatives are
    # durable before consuming this fold's first evaluation label.
    totals = metrics.summarize(calendar, fold, representatives, eq._evaluation_records(shards, data, roster))
    metric_pin = artifacts._document(output, METRICS, totals)
    runtime = native.verify_after(admitted)
    _verify_runtime(runtime, identity)
    return {**context, **_fit_counts(model), "rate": rate_pin, "model": model_pin, "components": components,
        "predictions": files, "representatives": representative_pin, "metrics": metric_pin, "runtime": runtime}


def _audited_fold(identity, fold):
    base = {**identity, "id": UNIT + f"-f{fold}", "fold": fold, "fit_attempt_count": 1}
    present = [attempt for attempt in ATTEMPTS if os.path.lexists(_slot({**base, "attempt": attempt}))]
    wire.require(present and present == list(ATTEMPTS[:len(present)]), "ES preceding fold audit is required")
    selected = {**base, "attempt": present[-1]}
    predecessors = _retry_predecessors(selected)
    output = _slot(selected)
    payload, pin = _audited(output, selected)
    wire.require(payload["predecessor_timeouts"] == predecessors, "ES fold timeout evidence differs")
    return output, payload, pin


def _prerequisites(identity):
    if not identity["invented"]:
        _qualification(identity)
        if identity["fold"] is not None:
            for fold in range(identity["fold"]):
                _audited_fold(identity, fold)


def _verify_ep_summary(identity, calendar, binding):
    old_output = run.NATIVE / "output" / (ep.UNIT + "-summary")
    old_identity = run._doc(old_output / "start.json")
    eq._old_source(old_identity, identity)
    wire.require(old_identity["id"] == ep.UNIT + "-summary" and old_identity["fold"] is None
        and old_identity["invented"] is False and old_identity["fit_attempt_count"] == 0,
        "ES EP summary identity differs")
    old = ep._publication(old_output, old_identity)
    expected = [{"fold": fold, "report_sha256": identity["ep_fold_report_sha256"][fold],
                 "audit_sha256": identity["ep_fold_audit_sha256"][fold]} for fold in range(4)]
    wire.require(wire.digest(wire.encode(old)) == identity["ep_summary_sha256"] and old["fold_audits"] == expected
        and old["calendar"] == calendar and old["inputs_sha256"] == binding, "ES EP summary binding differs")


def _summary(identity):
    audited = [_audited_fold(identity, fold) for fold in range(4)]
    publications = [payload for _, payload, _ in audited]
    folds = [{"fold": fold, "report_sha256": wire.digest(wire.encode(payload)), "audit_sha256": pin}
             for fold, (_, payload, pin) in enumerate(audited)]
    calendar, binding = publications[0]["calendar"], publications[0]["inputs_sha256"]
    wire.require(all(row["calendar"] == calendar and row["inputs_sha256"] == binding for row in publications),
        "ES summary fold bindings differ")
    _verify_ep_summary(identity, calendar, binding)
    reports = [ep._read_document(output, payload["metrics"], METRICS) for output, payload, _ in audited]
    pooled = metrics.pool(calendar, reports)
    reference.verify_pool(calendar, reports, pooled)
    counters = ("fit_attempt_count", "fit_count", "completed_native_fit_count", "fitted_artifact_count")
    return {"fold_audits": folds, "calendar": calendar, "inputs_sha256": binding,
        **{name: sum(row[name] for row in publications) for name in counters},
        "additional_failed_fit_starts": sum(item["fit_started"] for row in publications for item in row["predecessor_timeouts"]),
        "ep_summary_sha256": identity["ep_summary_sha256"],
        "fold_metrics": [{"fold": fold, "report": report} for fold, report in enumerate(reports)], "pooled": pooled}


def _inputs(identity):
    return ep._invented_inputs(identity) if identity["invented"] else ep._actual_inputs(identity)


def _audit(output, identity):
    wire.exclusive_bytes(output / "audit-start.json", wire.encode(identity))
    try:
        done = run._doc(output / "completion.json")
        wire.require(done["identity"] == identity, "ES audit completion identity differs")
        run.audit_budget(done["result"]["resources"])
        payload = _publication(output, identity, verify_files=False)
        _prerequisites(identity)
        wire.require(payload["predecessor_timeouts"] == _retry_predecessors(identity), "ES audit timeout evidence differs")
        _project(output, identity, *_inputs(identity), payload=payload)
        ep._source_after(identity["source"], identity["runtime_sha256"])
        measured = run.usage()
        run.verify_usage(payload["resources"], measured)
        wire.exclusive_bytes(output / "audit.json", wire.encode({"identity": identity,
            "report_sha256": wire.digest(wire.encode(payload)), "independently_verified": True, "resources": measured}))
    except Exception as error:
        wire.exclusive_bytes(output / "audit-failure.json", wire.encode({"identity": identity,
            "error_type": type(error).__name__, "resources": run.usage()}))
        raise


def _produce(output, identity):
    wire.exclusive_directory(output)
    wire.exclusive_bytes(output / "start.json", wire.encode(identity))
    journal = _journal(output, identity)
    start = 0 if identity["invented"] else wire.regular(journal).st_size

    def evaluate():
        predecessors = _retry_predecessors(identity)
        _prerequisites(identity)
        value = _summary(identity) if identity["fold"] is None else _project(output, identity, *_inputs(identity))
        ep._source_after(identity["source"], identity["runtime_sha256"])
        measured = run.usage()
        run.verify_usage(measured, dict.fromkeys(measured, 0))
        payload = {"identity": identity, **value, "resources": measured, "predecessor_timeouts": predecessors}
        raw = wire.encode(payload)
        wire.require(len(raw) <= artifacts.MAX_DOCUMENT, "ES report bound exceeded")
        wire.exclusive_bytes(output / "report.json", raw)
        return {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": measured}

    try:
        terminal = experiment_log.run_discovery_trial(study_id=identity["id"],
            specification_sha256=identity["registration_sha256"], parameters=identity, window=run.WINDOW,
            evaluate=evaluate, runs_path=journal, code_version_provider=lambda: identity["merge"])
        result = terminal["result"]
        span = receipts._proof(journal, identity, identity["id"], result, start, wire.regular(journal).st_size)
        wire.exclusive_bytes(output / "completion.json", wire.encode({"identity": identity, "result": result, "journal": span}))
    except Exception as error:
        failure = {"identity": identity, "error_type": type(error).__name__, "resources": run.usage()}
        if type(error) is TimeoutError:
            try:
                failure["journal"] = receipts._proof(journal, identity, identity["id"], None,
                    start, wire.regular(journal).st_size, failed=True)
                failure["partial"] = _partial_artifacts(output, identity)
                failure["recovery_supported"] = True
            except (ValueError, OSError):
                failure["recovery_supported"] = False
        wire.exclusive_bytes(output / "failure.json", wire.encode(failure))
        raise


def execute(args, registrations, source, started):
    run.STARTED_CLOCK = started
    run.budget()
    runtime = run.runtime_pin()
    _validate_args(args)
    eq._public_predecessors(source)
    bridge.restrict_local_network()
    wire.prohibit_ef_tasks()
    identity = _identity(source, runtime, args)
    output = _slot(identity)
    audit = args.mode.endswith("-audit")
    with run._locks():
        if audit:
            _audit(output, identity)
        else:
            _produce(output, identity)
    print(json.dumps({"unit": identity["id"], "audit": audit, "status": "completed", "output": str(output)}, sort_keys=True))
