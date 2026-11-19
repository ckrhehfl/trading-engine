"""Closed ET lifecycle for the immediate-action score-to-value comparison."""

import json
import os

from research import experiment_log
from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_calibration_oof as oof
from research import intraday_event_calibration_oof_reference as oof_reference
from research import intraday_event_calibration_run as er
from research import intraday_event_dataset_run as run
from research import intraday_event_factorized_run as ep
from research import intraday_event_factorized_workflow as workflow
from research import intraday_event_fold_artifacts as artifacts
from research import intraday_event_immediate_oof as early
from research import intraday_event_immediate_policy as policy
from research import intraday_event_immediate_reference as reference
from research import intraday_event_immediate_value as value
from research import intraday_event_mean_baseline as mean
from research import intraday_event_mean_run as eq
from research import intraday_event_model_receipts as receipts
from research import intraday_event_native as native
from research import intraday_event_success_reference as es_reference
from research import intraday_event_success_run as es


UNIT = "ET-C-IMMEDIATE-NET-1"
PLAN = ".planning/rd-et-immediate-score-value.md"
ARMS = ("direct", "mapped")
ATTEMPTS = ep.ATTEMPTS
MODES = ("immediate-inner-qualification", "immediate-inner-qualification-audit",
    "immediate-inner-actual", "immediate-inner-audit", "immediate-qualification",
    "immediate-qualification-audit", "immediate-actual", "immediate-audit", "immediate-summary")
ES_RELEASE = {"merge": "64feccfe82625c97aad0c33b630a5a304eda4567",
    "reviewed": "d8749967f50c55b260e006c903bb37ddb37349e6",
    "tree": "13607043a4b431e9c369af579d624935aafc6a3c"}
INVENTED_PINS = tuple(f"{prefix}_invented_{kind}_sha256"
                      for prefix in ("ep", "es") for kind in ("report", "audit"))
FOLD_PINS = tuple(f"{prefix}_fold_{kind}_sha256"
                  for prefix in ("ep", "es") for kind in ("report", "audit"))
PREDECESSOR_PINS = (*INVENTED_PINS, *FOLD_PINS, "ep_summary_sha256")
FIT_COUNTS = ("fit_attempt_count", "fit_count", "completed_native_fit_count", "fitted_artifact_count")
GEOMETRY = {"sessions": 257, "coordinates": 5158}

_slot = er._slot
_journal = er._journal


def _public_predecessors(source):
    eq._public_predecessors(source)
    release = ES_RELEASE
    commit, tree, parents = run._git("show", "-s", "--format=%H%n%T%n%P", release["merge"]).decode().splitlines()
    parents = parents.split()
    wire.require(commit == release["merge"] and tree == release["tree"] and len(parents) == 2
        and parents[1] == release["reviewed"] and parents[0] != release["reviewed"]
        and run._git("rev-parse", release["reviewed"] + "^{tree}").decode().strip() == tree,
        "ET success predecessor is not its reviewed normal merge")
    run._git("merge-base", "--is-ancestor", release["merge"], source["merge"])


def _validate_args(args):
    invented = "qualification" in args.mode
    inner = "-inner-" in args.mode
    summary = args.mode == "immediate-summary"
    wire.require(args.mode in MODES and all(getattr(args, field, None) is None for field in (
        "kind", "scope", "shard", "attempt", "summary_sha256", "entry_value_report_sha256",
        "entry_value_audit_sha256", "rank_failure_start", "rank_failure_end", "rank_failure_sha256",
        "factorized_qualification_sha256", "factorized_attempt", "mean_qualification_sha256", "mean_attempt",
        "calibration_inner_qualification_sha256", "calibration_qualification_sha256", "calibration_attempt",
        "success_qualification_sha256", "success_attempt"))
        and wire.valid_sha(args.invented_inputs_sha256)
        and (args.immediate_attempt is None if invented or summary else args.immediate_attempt in (None, *ATTEMPTS)),
        "ET fixed comparison modes and pins required")
    wire.require((type(args.inner) is int and args.inner in range(2) and args.fold is None
                  and (not invented or args.inner == 1)) if inner else
                 args.inner is None and (args.fold is None if summary else type(args.fold) is int and args.fold in range(4))
                 and (not invented or args.fold == 3), "ET fixed unit coordinate differs")
    wire.require(args.dataset_binding_sha256 is None if invented else wire.valid_sha(args.dataset_binding_sha256),
        "ET dataset binding required only for actual modes")
    if inner:
        required = (*INVENTED_PINS, "immediate_inner_qualification_sha256", "immediate_qualification_sha256")
        wire.require(all(getattr(args, name) is None for name in (*FOLD_PINS, "ep_summary_sha256"))
            and all(getattr(args, name) is None if invented else wire.valid_sha(getattr(args, name))
                    for name in required), "ET inner qualification pins differ")
        return
    wire.require(wire.valid_sha(args.immediate_inner_qualification_sha256)
        and all(wire.valid_sha(getattr(args, name)) for name in INVENTED_PINS),
        "ET outer requires the inner qualification and invented predecessor pins")
    actual = (*FOLD_PINS, "ep_summary_sha256", "immediate_qualification_sha256")
    wire.require(all(getattr(args, name) is None for name in actual) if invented else
        wire.valid_sha(args.ep_summary_sha256) and wire.valid_sha(args.immediate_qualification_sha256)
        and all(type(pins) in (list, tuple) and len(pins) == 4 and all(wire.valid_sha(pin) for pin in pins)
                for pins in (getattr(args, name) for name in FOLD_PINS)), "ET actual predecessor pins differ")


def _identity(source, runtime, args):
    invented = "qualification" in args.mode
    role = "inner" if "-inner-" in args.mode else "summary" if args.mode.endswith("-summary") else "outer"
    suffix = f"-inner{args.inner}" if role == "inner" else "-summary" if role == "summary" else f"-f{args.fold}"
    pins = {name: getattr(args, name) for name in PREDECESSOR_PINS}
    for name in FOLD_PINS:
        if pins[name] is not None:
            pins[name] = list(pins[name])
    return {"id": UNIT + suffix, "role": role, "source": source, "merge": source["merge"],
        "runtime_sha256": runtime, "registration_sha256": source["sources"][PLAN],
        "invented": invented, "inner": args.inner, "fold": args.fold,
        "attempt": "qualification1" if invented else (args.immediate_attempt or "actual1"),
        "invented_inputs_sha256": args.invented_inputs_sha256, "dataset_binding_sha256": args.dataset_binding_sha256,
        "inner_qualification_sha256": args.immediate_inner_qualification_sha256,
        "qualification_sha256": args.immediate_qualification_sha256, **pins, "scope": "common_chart_peer"}


def _inner_identity(identity, inner, *, invented=False):
    return {**identity, "id": UNIT + f"-inner{inner}", "role": "inner", "inner": inner, "fold": None,
        "invented": invented, "attempt": "qualification1" if invented else "actual1",
        "dataset_binding_sha256": None if invented else identity["dataset_binding_sha256"],
        "inner_qualification_sha256": None if invented else identity["inner_qualification_sha256"],
        "qualification_sha256": None if invented else identity["qualification_sha256"],
        **dict.fromkeys((*FOLD_PINS, "ep_summary_sha256")),
        **{name: None if invented else identity[name] for name in INVENTED_PINS}}


def _outer_identity(identity, fold):
    return {**identity, "id": UNIT + f"-f{fold}", "role": "outer", "inner": None, "fold": fold}


def _publication(output, identity):
    wire.require(not os.path.lexists(output / "failure.json"), "ET ambiguous publication")
    payload = eq._envelope(output, identity, journal=_journal(output, identity))
    if identity["role"] == "inner":
        wire.require(payload["inner"] == identity["inner"] and payload["fit_attempt_count"] == 1,
            "ET early publication coordinate differs")
        es._verify_runtime(payload["runtime"], identity)
    elif identity["role"] == "outer":
        wire.require(payload["fold"] == identity["fold"] and all(payload[name] == 0 for name in FIT_COUNTS),
            "ET outer cannot add native fits")
    return payload


def _audited(output, identity):
    payload = _publication(output, identity)
    return payload, eq._audit_receipt(output, identity, payload)


def _partial_artifacts(output, identity):
    allowed = {"start.json", "failure.json"}
    if identity["role"] == "inner":
        allowed.update(("produce-scratch", "fit-attempt.json", "fit-start.json", "model.json",
                        "predictions.jsonl", "components.jsonl"))
    else:
        allowed.update(("mapping.json", "oof-predictions.jsonl", "oof-provenance.json",
                        "current-success-predictions.jsonl", "metrics.json"))
        allowed.update(f"{arm}-{suffix}" for arm in ARMS for suffix in ("predictions.jsonl", "policy.json"))
    names = {path.name for path in output.iterdir()}
    wire.require(names <= allowed, "ET failed publication is ambiguous")
    pins = {}
    for name in sorted(names - {"start.json", "failure.json", "produce-scratch"}):
        digest, size = wire.file_digest(output / name, artifacts.MAX_FILE, lambda _: None)
        pins[name] = {"sha256": digest, "bytes": size}
    if "produce-scratch" in names:
        wire.safe_path(output / "produce-scratch")
        wire.require((output / "produce-scratch").is_dir(), "ET failed scratch path differs")
    for name in ("fit-attempt.json", "fit-start.json"):
        if name in names:
            wire.require(run._doc(output / name) == identity, "ET retained fit receipt differs")
    started = "fit-start.json" in names
    wire.require(not started or "fit-attempt.json" in names, "ET retained fit start has no attempt")
    return {"files": pins, "fit_started": started}


def _retry_predecessors(identity):
    if identity["invented"] or identity["role"] == "summary":
        return []
    if identity["attempt"] == "actual1":
        wire.require(not os.path.lexists(_slot({**identity, "attempt": "actual2"})),
            "ET later sibling attempt already exists")
        return []
    previous = {**identity, "attempt": "actual1"}
    output = _slot(previous)
    failure = run._doc(output / "failure.json")
    wire.require(run._doc(output / "start.json") == previous and failure["identity"] == previous
        and failure["error_type"] == "TimeoutError" and failure.get("recovery_supported") is True
        and failure["partial"] == _partial_artifacts(output, previous),
        "ET retry requires an authenticated same-input technical timeout")
    span = failure["journal"]
    wire.require(receipts._proof(_journal(output, previous), previous, previous["id"], None,
        span["start"], span["end"], failed=True) == span, "ET retained timeout journal differs")
    return [{"failure_sha256": wire.digest(wire.encode(failure)), "fit_started": failure["partial"]["fit_started"]}]


def _audited_unit(identity):
    present = [attempt for attempt in ATTEMPTS if os.path.lexists(_slot({**identity, "attempt": attempt}))]
    wire.require(present and present == list(ATTEMPTS[:len(present)]), "ET preceding unit audit is required")
    selected = {**identity, "attempt": present[-1]}
    output = _slot(selected)
    payload, pin = _audited(output, selected)
    wire.require(payload["predecessor_timeouts"] == _retry_predecessors(selected), "ET preceding timeout evidence differs")
    return output, payload, pin


def _prerequisites(identity):
    role = identity["role"]
    if role != "inner" or not identity["invented"]:
        qualified = _inner_identity(identity, 1, invented=True)
        report, pin = _audited(_slot(qualified), qualified)
        wire.require(pin == identity["inner_qualification_sha256"] and report["geometry"] == GEOMETRY
            and all(report[name] == 1 for name in FIT_COUNTS), "ET largest inner qualification differs")
    if identity["invented"]:
        return
    qualified = {**_outer_identity(identity, 3), "invented": True, "attempt": "qualification1",
        "dataset_binding_sha256": None, "qualification_sha256": None, "ep_summary_sha256": None,
        **dict.fromkeys(FOLD_PINS)}
    report, pin = _audited(_slot(qualified), qualified)
    wire.require(pin == identity["qualification_sha256"] and report["geometry"] == GEOMETRY,
        "ET largest outer qualification differs")
    if role == "inner":
        if identity["inner"] == 1:
            _audited_unit(_inner_identity(identity, 0))
        return
    if role == "outer":
        for inner in range(2):
            _audited_unit(_inner_identity(identity, inner))
        for fold in range(identity["fold"]):
            _audited_unit(_outer_identity(identity, fold))


def _project_inner(output, identity, shards, binding, *, payload=None):
    scratch = output / ("audit-scratch" if payload is not None else "produce-scratch")
    wire.exclusive_directory(scratch)
    admitted = native.activate("tcn", scratch)
    data = early.load_inner(shards, identity["inner"])
    context = {"inner": identity["inner"], "inputs_sha256": binding,
        "geometry": er._geometry(shards, data.calendar), "calendar": list(data.calendar), "support": data.support}
    if payload is not None:
        result = er._saved_inner(output, payload)
        es._fit_receipts(output, identity, result)
        wire.require(all(payload.get(key) == item for key, item in {**context, **es._fit_counts(result)}.items()),
            "ET audited early context differs")
        early.verify_inputs(shards, data)
        oof_reference.verify_fit(result, data)
        early.verify_unit(result, data)
        runtime = native.verify_after(admitted)
        es._verify_runtime(runtime, identity)
        wire.require(runtime == payload["runtime"], "ET producer and audit native readback differ")
        return None
    wire.exclusive_bytes(output / "fit-attempt.json", wire.encode(identity))

    def before_fit():
        wire.exclusive_bytes(output / "fit-start.json", wire.encode(identity))

    result = early.fit_unit(data, before_fit=before_fit)
    es._fit_receipts(output, identity, result)
    files = {"model": artifacts._document(output, "model.json",
                {key: item for key, item in result.items() if key not in ("predictions", "components")}),
             "predictions": artifacts._stream(output, "predictions.jsonl", result["predictions"]),
             "components": artifacts._stream(output, "components.jsonl", result["components"])}
    runtime = native.verify_after(admitted)
    es._verify_runtime(runtime, identity)
    return {**context, **es._fit_counts(result), "model_files": files, "runtime": runtime}


def _predecessor_pin(identity, prefix, kind, fold):
    if identity["invented"]:
        return identity[f"{prefix}_invented_{kind}_sha256"]
    return identity[f"{prefix}_fold_{kind}_sha256"][fold]


def _verify_predecessor(output, old, report, identity, prefix, fold):
    report_pin = _predecessor_pin(identity, prefix, "report", fold)
    audit_pin = _predecessor_pin(identity, prefix, "audit", fold)
    wire.require(not os.path.lexists(output / "failure.json") and wire.digest(wire.encode(report)) == report_pin
        and eq._audit_receipt(output, old, report) == audit_pin, "ET predecessor publication or audit differs")
    return {"report_sha256": report_pin, "audit_sha256": audit_pin}


def _forecast_payload(output, model_pin, model_name, prediction_pin, prediction_name):
    result = ep._read_document(output, model_pin, model_name)
    predictions = ep._read_rows(output, prediction_pin, prediction_name)
    artifact = result["artifact"]
    wire.require(result["schema"] == "EP-model-unit-v1" and result["arm"] == "direct"
        and result["predictions_sha256"] == wire.digest(wire.encode(predictions)[:-1])
        and result["artifact_sha256"] == (None if artifact is None else wire.digest(wire.encode(artifact)[:-1])),
        "ET stored forecast or native artifact differs")
    # The audit already replayed the saved weights. Keep only cutoff metadata here.
    result["artifact"] = None if artifact is None else {"eval_first_session": artifact["eval_first_session"]}
    result["predictions"] = predictions
    return result


def _success_forecast(identity, fold):
    invented = identity["invented"]
    coordinate = {"id": es.UNIT + f"-f{fold}", "merge": ES_RELEASE["merge"], "invented": invented}
    attempts = ("qualification1",) if invented else ATTEMPTS
    present = [attempt for attempt in attempts if os.path.lexists(es._slot({**coordinate, "attempt": attempt}))]
    wire.require(present and present == list(attempts[:len(present)]), "ET fixed ES predecessor is missing")
    output = es._slot({**coordinate, "attempt": present[-1]})
    old = run._doc(output / "start.json")
    source = old["source"]
    wire.require(all(source.get(key) == item for key, item in ES_RELEASE.items()) and source.get("sources")
        and all(identity["source"]["sources"].get(path) == pin
                for path, pin in source["sources"].items() if path != ep.DISPATCHER)
        and all(old.get(key) == item for key, item in coordinate.items())
        and old["fold"] == fold and old["attempt"] == present[-1] and old["fit_attempt_count"] == 1
        and old["scope"] == identity["scope"] and old["arms"] == list(es.ARMS)
        and old["registration_sha256"] == source["sources"].get(es.PLAN)
        and all(old[key] == identity[key] for key in ("runtime_sha256", "invented_inputs_sha256",
            "dataset_binding_sha256", *er.EP_PINS)), "ET unchanged ES source, inputs or EP identity differs")
    report = es._publication(output, old, verify_files=False)
    provenance = _verify_predecessor(output, old, report, identity, "es", fold)
    prediction_pin = report["predictions"]["success"]
    result = _forecast_payload(output, report["model"], es.MODEL, prediction_pin, "success-predictions.jsonl")
    return report, result, {**provenance, "predictions_sha256": prediction_pin["sha256"]}


def _direct_forecast(identity):
    invented, fold = identity["invented"], identity["fold"]
    coordinate = {"id": ep.UNIT + f"-f{fold}", "merge": eq.EP_RELEASE["merge"], "invented": invented}
    attempt = "qualification1" if invented else "actual1"
    output = ep._slot({**coordinate, "attempt": attempt})
    old = run._doc(output / "start.json")
    eq._old_source(old, identity)
    wire.require(all(old.get(key) == item for key, item in coordinate.items()) and old["fold"] == fold
        and old["attempt"] == attempt and old["fit_attempt_count"] == 2, "ET fixed EP predecessor differs")
    report = eq._envelope(output, old, journal=ep._journal(output, old))
    wire.require(report["fold"] == fold and report["fit_attempt_count"] == 2, "ET EP fold report differs")
    es._verify_runtime(report["runtime"], identity)
    provenance = _verify_predecessor(output, old, report, identity, "ep", fold)
    direct = report["arms"]["direct"]
    result = _forecast_payload(output, direct["model"], "direct-model.json", direct["predictions"], "direct-predictions.jsonl")
    return report, result, {**provenance, "predictions_sha256": direct["predictions"]["sha256"]}


def _check_current(report, model, data, binding):
    base = data.base
    _, stop, end = workflow.FOLDS[base.fold]
    wire.require(report["inputs_sha256"] == binding and report["calendar"] == list(base.calendar)
        and report["support"] == model["support"] == base.support and model["fold"] == base.fold
        and model["kind"] == "tcn" and model["scope"] == base.scope
        and model["training_sessions"] == list(base.calendar[:stop])
        and model["evaluation_sessions"] == list(base.calendar[stop:end])
        and [(row["key"], row["session"]) for row in model["predictions"]]
            == [(row.key, row.session) for row in base.evaluation]
        and (model["artifact"] is None or model["artifact"]["eval_first_session"] == base.calendar[stop]),
        "ET current predecessor roster, source or cutoff differs")


def _oof_predictions(identity, data, binding):
    if identity["invented"]:
        return list(oof.invented_oof(data)), {"kind": "explicit_invented_chronological_forecast_fixture",
            "invented": True, "native_fits": 0}
    values, blocks = [], []
    calendar = list(data.base.calendar)
    for inner, (_, stop, end) in enumerate(early.SPLITS):
        output, report, audit = _audited_unit(_inner_identity(identity, inner))
        wire.require(report["inputs_sha256"] == binding and report["calendar"] == calendar, "ET early inputs differ")
        model = er._saved_inner(output, report)
        es._fit_receipts(output, report["identity"], model)
        wire.require(all(report[name] == count for name, count in es._fit_counts(model).items()),
            "ET early fit counters differ")
        er._check_oof_block(model, data, stop, end)
        values.extend(model["predictions"])
        blocks.append({"kind": "ET-inner", "inner": inner, "train_stop": stop, "end": end,
            "report_sha256": wire.digest(wire.encode(report)), "audit_sha256": audit,
            "predictions_sha256": report["model_files"]["predictions"]["sha256"]})
    for fold in range(identity["fold"]):
        report, model, provenance = _success_forecast(identity, fold)
        wire.require(report["inputs_sha256"] == binding and report["calendar"] == calendar
            and report["support"] == model["support"] and model["fold"] == fold, "ET historical ES inputs differ")
        _, stop, end = workflow.FOLDS[fold]
        er._check_oof_block(model, data, stop, end)
        values.extend(model["predictions"])
        blocks.append({"kind": "ES-success", "fold": fold, "train_stop": stop, "end": end, **provenance})
    return values, {"kind": "authenticated_chronological_out_of_training_predictions",
                    "invented": False, "blocks": blocks}


def _saved_outer(output, payload):
    wire.require(set(payload["arms"]) == set(ARMS), "ET immediate arm roster differs")
    predictions, policies = {}, {}
    for arm in ARMS:
        pins = payload["arms"][arm]
        wire.require(set(pins) == {"predictions", "policy"}, "ET outer file roster differs")
        predictions[arm] = ep._read_rows(output, pins["predictions"], f"{arm}-predictions.jsonl")
        policies[arm] = ep._read_document(output, pins["policy"], f"{arm}-policy.json")
    return (ep._read_document(output, payload["mapping"], "mapping.json"),
            ep._read_rows(output, payload["oof_predictions"], "oof-predictions.jsonl"),
            ep._read_document(output, payload["oof_provenance"], "oof-provenance.json"),
            ep._read_rows(output, payload["current_success"], "current-success-predictions.jsonl"),
            predictions, policies)


def _project_outer(output, identity, shards, binding, *, payload=None):
    fold = identity["fold"]
    ep_report, direct_model, direct_provenance = _direct_forecast(identity)
    es_report, success_model, success_provenance = _success_forecast(identity, fold)
    data = workflow.load_fold(shards, fold)
    _check_current(ep_report, direct_model, data, binding)
    _check_current(es_report, success_model, data, binding)
    direct, current = direct_model["predictions"], success_model["predictions"]
    roster = workflow.decision_roster(shards, data)
    phases = mean.training_phases(shards, data)
    targets = mean.training_targets(shards, data)
    historical, provenance = _oof_predictions(identity, data, binding)
    calendar = data.base.calendar
    context = {"fold": fold, "calendar": list(calendar), "support": data.base.support,
        "inputs_sha256": binding, "geometry": er._geometry(shards, calendar),
        "predecessors": {"direct": direct_provenance, "success": success_provenance}}
    if payload is not None:
        mapping, saved_oof, saved_provenance, saved_current, forecasts, policies = _saved_outer(output, payload)
        wire.require(all(payload.get(key) == item for key, item in context.items()) and saved_oof == historical
            and saved_provenance == provenance and saved_current == current and forecasts["direct"] == direct,
            "ET audited context or predecessor forecasts differ")
        es_reference.verify_training_inputs(shards, data, phases, targets)
        value.verify(data, phases, targets, saved_oof, mapping)
        value.verify_projection(data, roster, mapping, current, forecasts["mapped"])
        reference.verify_policies(data, roster, forecasts, policies)
        totals = ep._read_document(output, payload["metrics"], "metrics.json")
        reference.verify_report(calendar, fold, roster, policies, eq._evaluation_records(shards, data, roster), totals)
        return None

    mapping = value.estimate(data, phases, targets, historical)
    forecasts = {"direct": direct, "mapped": value.project(data, roster, mapping, current)}
    policies = policy.freeze(data, roster, forecasts)
    sealed = {"mapping": artifacts._document(output, "mapping.json", mapping),
        "oof_predictions": artifacts._stream(output, "oof-predictions.jsonl", historical),
        "oof_provenance": artifacts._document(output, "oof-provenance.json", provenance),
        "current_success": artifacts._stream(output, "current-success-predictions.jsonl", current),
        "arms": {arm: {"predictions": artifacts._stream(output, f"{arm}-predictions.jsonl", forecasts[arm]),
                       "policy": artifacts._document(output, f"{arm}-policy.json", policies[arm])} for arm in ARMS}}
    # Only the current target prefix is decoded after every causal artifact is durable.
    totals = policy.summarize(calendar, fold, roster, policies, eq._evaluation_records(shards, data, roster))
    return {**context, **dict.fromkeys(FIT_COUNTS, 0), **sealed,
        "metrics": artifacts._document(output, "metrics.json", totals)}


def _summary(identity):
    inner_audits, fold_audits, publications, reports = [], [], [], []
    for inner in range(2):
        _, report, audit = _audited_unit(_inner_identity(identity, inner))
        publications.append(report)
        inner_audits.append({"inner": inner, "report_sha256": wire.digest(wire.encode(report)),
            "audit_sha256": audit, **{name: report[name] for name in FIT_COUNTS}})
    for fold in range(4):
        output, report, audit = _audited_unit(_outer_identity(identity, fold))
        publications.append(report)
        reports.append(ep._read_document(output, report["metrics"], "metrics.json"))
        fold_audits.append({"fold": fold, "report_sha256": wire.digest(wire.encode(report)), "audit_sha256": audit})
    calendar, binding = publications[0]["calendar"], publications[0]["inputs_sha256"]
    wire.require(all(row["calendar"] == calendar and row["inputs_sha256"] == binding for row in publications),
        "ET six publication bindings differ")
    es._verify_ep_summary(identity, calendar, binding)
    pooled = policy.pool(calendar, reports)
    reference.verify_pool(calendar, reports, pooled)
    return {"inner_audits": inner_audits, "fold_audits": fold_audits, "calendar": calendar, "inputs_sha256": binding,
        **{name: sum(row[name] for row in publications) for name in FIT_COUNTS},
        "additional_failed_fit_starts": sum(row["fit_started"] for report in publications
            for row in report["predecessor_timeouts"]),
        "fold_metrics": [{"fold": fold, "report": report} for fold, report in enumerate(reports)], "pooled": pooled}


def _project(output, identity, *, payload=None):
    if identity["role"] == "summary":
        wire.require(payload is None, "ET summary uses its independent pooled witness during publication")
        return _summary(identity)
    shards, binding = ep._invented_inputs(identity) if identity["invented"] else ep._actual_inputs(identity)
    project = _project_inner if identity["role"] == "inner" else _project_outer
    return project(output, identity, shards, binding, payload=payload)


def _audit(output, identity):
    done = run._doc(output / "completion.json")
    wire.require(run._doc(output / "start.json") == identity and done["identity"] == identity,
        "ET audit producer identity differs")
    wire.exclusive_bytes(output / "audit-start.json", wire.encode(identity))
    try:
        run.audit_budget(done["result"]["resources"])
        payload = _publication(output, identity)
        _prerequisites(identity)
        wire.require(payload["predecessor_timeouts"] == _retry_predecessors(identity), "ET audit timeout evidence differs")
        _project(output, identity, payload=payload)
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
    retained_timeout = None
    if identity["attempt"] == "actual2":
        previous = _slot({**identity, "attempt": "actual1"})
        wire.safe_path(previous)
        wire.require(previous.is_dir() and (previous / "failure.json").is_file()
            and not os.path.lexists(previous / "completion.json") and not os.path.lexists(previous / "report.json"),
            "ET retry requires a preceding failed slot without ambiguous publication")
        retained_timeout = _retry_predecessors(identity)
    wire.exclusive_directory(output)
    wire.exclusive_bytes(output / "start.json", wire.encode(identity))
    journal = _journal(output, identity)
    start = 0 if identity["invented"] else wire.regular(journal).st_size

    def evaluate():
        predecessors = _retry_predecessors(identity)
        wire.require(retained_timeout is None or predecessors == retained_timeout, "ET retry receipt changed")
        _prerequisites(identity)
        project = _project(output, identity)
        ep._source_after(identity["source"], identity["runtime_sha256"])
        measured = run.usage()
        run.verify_usage(measured, dict.fromkeys(measured, 0))
        raw = wire.encode({"identity": identity, **project, "resources": measured, "predecessor_timeouts": predecessors})
        wire.require(len(raw) <= artifacts.MAX_DOCUMENT, "ET report bound exceeded")
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
    _public_predecessors(source)
    bridge.restrict_local_network()
    wire.prohibit_ef_tasks()
    identity = _identity(source, runtime, args)
    output = _slot(identity)
    audit = args.mode.endswith("-audit")
    with run._locks():
        (_audit if audit else _produce)(output, identity)
    print(json.dumps({"unit": identity["id"], "audit": audit, "status": "completed", "output": str(output)}, sort_keys=True))
