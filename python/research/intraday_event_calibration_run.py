"""Closed ER chronological OOF fitting and training-only value calibration.

Two separate early native units precede the four outer comparisons. Stored EP
forecasts are admitted without their economic summaries. New outer policies are
sealed before evaluation targets; an invented historical fixture is never an
actual predecessor. Existing market, target and source publications stay intact.
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
from research import intraday_event_mean_reference as mean_reference
from research import intraday_event_calibration_oof as oof
from research import intraday_event_calibration_oof_reference as oof_reference
from research import intraday_event_calibration as calibration
from research import intraday_event_calibration_metrics as metrics
from research import intraday_event_calibration_reference as reference


UNIT = "ER-C-CALIBRATION-1"
PLAN = ".planning/rd-er-train-only-calibration.md"
ARMS = ("mean", "direct", "calibrated")
MODES = ("calibration-inner-qualification", "calibration-inner-qualification-audit",
         "calibration-inner-actual", "calibration-inner-audit", "calibration-qualification",
         "calibration-qualification-audit", "calibration-actual", "calibration-audit", "calibration-summary")
ATTEMPTS = ("actual1", "actual2")
EQ_RELEASE = {"merge": "e3cb4b6e9804e64138dde8c948e625371a399888",
    "reviewed": "7fd9d7203c6111c644c9345e2a6dabe0937206d6",
    "tree": "54eef56063ef523d792ec1c8981c352017713894"}
EP_PINS = ("ep_invented_report_sha256", "ep_invented_audit_sha256", "ep_fold_report_sha256",
           "ep_fold_audit_sha256", "ep_summary_sha256")


def _public_predecessors(source):
    eq._public_predecessors(source)
    release = EQ_RELEASE
    commit, tree, parents = run._git("show", "-s", "--format=%H%n%T%n%P", release["merge"]).decode().splitlines()
    wire.require(commit == release["merge"] and tree == release["tree"] and len(parents.split()) == 2
        and parents.split()[1] == release["reviewed"] and parents.split()[0] != release["reviewed"]
        and run._git("rev-parse", release["reviewed"] + "^{tree}").decode().strip() == tree,
        "ER predecessor is not its reviewed normal merge")
    run._git("merge-base", "--is-ancestor", release["merge"], source["merge"])
    for path in (eq.PLAN, *(f"python/research/intraday_event_mean_{name}.py"
                           for name in ("baseline", "metrics", "reference", "run"))):
        wire.require(wire.digest(run._git("show", release["merge"] + ":" + path)) == source["sources"].get(path),
            "ER original EQ control source differs")


def _identity(source, runtime, args):
    invented = "qualification" in args.mode
    role = "inner" if "-inner-" in args.mode else "summary" if args.mode.endswith("-summary") else "outer"
    suffix = f"-inner{args.inner}" if role == "inner" else "-summary" if role == "summary" else f"-f{args.fold}"
    return {"id": UNIT + suffix, "role": role, "source": source, "merge": source["merge"],
        "runtime_sha256": runtime, "registration_sha256": source["sources"][PLAN],
        "invented": invented, "inner": args.inner, "fold": args.fold,
        "attempt": "qualification1" if invented else (args.calibration_attempt or "actual1"),
        "invented_inputs_sha256": args.invented_inputs_sha256,
        "dataset_binding_sha256": args.dataset_binding_sha256,
        "inner_qualification_sha256": args.calibration_inner_qualification_sha256,
        "qualification_sha256": args.calibration_qualification_sha256,
        **{name: getattr(args, name) for name in EP_PINS}, "scope": "common_chart_peer"}


def _inner_identity(identity, inner, *, invented=False, attempt="actual1"):
    return {**identity, "id": UNIT + f"-inner{inner}", "role": "inner", "inner": inner, "fold": None,
        "invented": invented, "attempt": "qualification1" if invented else attempt,
        "dataset_binding_sha256": None if invented else identity["dataset_binding_sha256"],
        "inner_qualification_sha256": None if invented else identity["inner_qualification_sha256"],
        "qualification_sha256": None, **dict.fromkeys(EP_PINS)}


def _outer_qualification_identity(identity):
    return {**identity, "id": UNIT + "-f3", "role": "outer", "inner": None, "fold": 3,
        "invented": True, "attempt": "qualification1", "dataset_binding_sha256": None,
        "qualification_sha256": None, "ep_fold_report_sha256": None,
        "ep_fold_audit_sha256": None, "ep_summary_sha256": None}


def _slot(identity):
    name = identity["id"]
    if identity["invented"]:
        name += "-" + identity["merge"] + "-qualification1"
    elif identity["role"] != "summary":
        name += "-" + identity["attempt"]
    return run.NATIVE / "output" / name


def _journal(output, identity):
    return output / "experiments.jsonl" if identity["invented"] else run.NATIVE / "canonical/experiments.jsonl"


def _saved_inner(output, payload):
    pins = payload["model_files"]
    wire.require(set(pins) == {"model", "predictions", "components"}, "ER inner file roster differs")
    result = ep._read_document(output, pins["model"], "model.json")
    for field in ("predictions", "components"):
        result[field] = ep._read_rows(output, pins[field], field + ".jsonl")
    return result


def _saved_outer(output, payload):
    wire.require(set(payload["arms"]) == set(ARMS), "ER three-arm roster differs")
    predictions, policies = {}, {}
    for arm in ARMS:
        pins = payload["arms"][arm]
        wire.require(set(pins) == {"predictions", "policy"}, "ER outer file roster differs")
        predictions[arm] = ep._read_rows(output, pins["predictions"], f"{arm}-predictions.jsonl")
        policies[arm] = ep._read_document(output, pins["policy"], f"{arm}-policy.json")
    return (ep._read_document(output, payload["mean"], "mean.json"),
            ep._read_document(output, payload["calibration"], "calibration.json"),
            ep._read_rows(output, payload["oof_predictions"], "oof-predictions.jsonl"),
            ep._read_document(output, payload["oof_provenance"], "oof-provenance.json"),
            predictions, policies, ep._read_rows(output, payload["representatives"], "representatives.jsonl"))


def _fit_counts(result):
    artifact = result["artifact"]
    wire.require(result["fit_artifacts"] == int(artifact is not None), "ER inner artifact count differs")
    count = 0 if artifact is None else artifact["native_joint_fit_count"]
    wire.require(type(count) is int and count in (0, 1), "ER inner native fit count differs")
    completed = int(artifact is not None and artifact["training_diagnostics"]["status"] == "complete")
    return {"fit_attempt_count": 1, "fit_count": count, "completed_native_fit_count": completed}


def _publication(output, identity):
    payload = eq._envelope(output, identity, journal=_journal(output, identity))
    if identity["role"] == "inner":
        result = _saved_inner(output, payload)
        wire.require(run._doc(output / "fit-attempt.json") == identity
            and all(payload.get(name) == value for name, value in _fit_counts(result).items())
            and payload["inner"] == identity["inner"] and payload["runtime"]["verified"] is True
            and payload["runtime"]["kind"] == "tcn"
            and payload["runtime"]["runtime_sha256"] == identity["runtime_sha256"],
            "ER inner publication contract differs")
        if result["artifact"] is None:
            wire.require(not os.path.lexists(output / "fit-start.json"), "ER empty inner fit fabricated a start")
        else:
            wire.require(run._doc(output / "fit-start.json") == identity, "ER inner durable fit start differs")
    elif identity["role"] == "outer":
        wire.require(payload["fit_attempt_count"] == payload["fit_count"] == 0
            and payload["fold"] == identity["fold"], "ER outer cannot add native fits")
        _saved_outer(output, payload)
        ep._read_document(output, payload["metrics"], "metrics.json")
    return payload


def _audited(output, identity):
    payload = _publication(output, identity)
    return payload, eq._audit_receipt(output, identity, payload)


def _inner_qualification(identity):
    qualified = _inner_identity(identity, 1, invented=True)
    payload, pin = _audited(_slot(qualified), qualified)
    wire.require(pin == identity["inner_qualification_sha256"]
        and payload["geometry"] == {"sessions": 257, "coordinates": 5158}
        and payload["inner"] == 1 and payload["fit_count"] == payload["completed_native_fit_count"] == 1,
        "ER same-release largest inner qualification differs")
    return pin


def _outer_qualification(identity):
    qualified = _outer_qualification_identity(identity)
    payload, pin = _audited(_slot(qualified), qualified)
    wire.require(pin == identity["qualification_sha256"]
        and payload["geometry"] == {"sessions": 257, "coordinates": 5158}
        and payload["fold"] == 3, "ER same-release largest outer qualification differs")
    return pin


def _partial_artifacts(output, identity):
    allowed = {"start.json", "failure.json"}
    if identity["role"] == "inner":
        allowed.update(("produce-scratch", "fit-attempt.json", "fit-start.json", "model.json",
                        "predictions.jsonl", "components.jsonl"))
    else:
        allowed.update(("mean.json", "calibration.json", "oof-predictions.jsonl", "oof-provenance.json",
                        "representatives.jsonl", "metrics.json"))
        allowed.update(f"{arm}-{suffix}" for arm in ARMS for suffix in ("predictions.jsonl", "policy.json"))
    names = {path.name for path in output.iterdir()}
    wire.require(names <= allowed, "ER failed publication is ambiguous")
    pins = {}
    for name in sorted(names - {"start.json", "failure.json", "produce-scratch"}):
        digest, size = wire.file_digest(output / name, artifacts.MAX_FILE, lambda _: None)
        pins[name] = {"sha256": digest, "bytes": size}
    if "produce-scratch" in names:
        wire.safe_path(output / "produce-scratch")
        wire.require((output / "produce-scratch").is_dir(), "ER failed scratch path differs")
    if "fit-start.json" in names:
        wire.require(run._doc(output / "fit-start.json") == identity, "ER retained inner fit start differs")
    return {"files": pins, "fit_started": "fit-start.json" in names}


def _retry_predecessors(identity):
    if identity["invented"] or identity["role"] == "summary":
        return []
    current, pins = ATTEMPTS.index(identity["attempt"]), []
    for index, attempt in enumerate(ATTEMPTS):
        previous = {**identity, "attempt": attempt}
        output = _slot(previous)
        if index > current:
            wire.require(not os.path.lexists(output), "ER later sibling attempt already exists")
        elif index < current:
            failure = run._doc(output / "failure.json")
            wire.require(run._doc(output / "start.json") == previous and failure["identity"] == previous
                and failure["error_type"] == "TimeoutError" and failure.get("recovery_supported") is True
                and failure["partial"] == _partial_artifacts(output, previous),
                "ER retry requires an authenticated same-input technical timeout")
            span = failure["journal"]
            wire.require(receipts._proof(_journal(output, previous), previous, previous["id"], None,
                span["start"], span["end"], failed=True) == span, "ER retained failure journal differs")
            pins.append({"failure_sha256": wire.digest(wire.encode(failure)),
                         "fit_started": failure["partial"]["fit_started"]})
    return pins


def _audited_unit(identity):
    present = [attempt for attempt in ATTEMPTS if os.path.lexists(_slot({**identity, "attempt": attempt}))]
    wire.require(present and present == list(ATTEMPTS[:len(present)]), "ER preceding unit audit is required")
    selected = {**identity, "attempt": present[-1]}
    _retry_predecessors(selected)
    directory = _slot(selected)
    payload, pin = _audited(directory, selected)
    return directory, payload, pin


def _prerequisites(identity):
    if identity["role"] == "inner":
        if not identity["invented"]:
            _inner_qualification(identity)
            if identity["inner"] == 1:
                _audited_unit(_inner_identity(identity, 0))
        return
    _inner_qualification(identity)
    if not identity["invented"]:
        _outer_qualification(identity)
        for inner in range(2):
            _audited_unit(_inner_identity(identity, inner))
        stop = 4 if identity["role"] == "summary" else identity["fold"]
        for fold in range(stop):
            _audited_unit({**identity, "id": UNIT + f"-f{fold}", "role": "outer", "fold": fold})


def _geometry(shards, calendar):
    return {"sessions": len(calendar), "coordinates": sum(manifest["counts"]["coordinates"] for _, manifest in shards)}


def _project_inner(output, identity, shards, binding, *, payload=None):
    scratch = output / ("audit-scratch" if payload is not None else "produce-scratch")
    wire.exclusive_directory(scratch)
    admitted = native.activate("tcn", scratch)
    data = oof.load_inner(shards, identity["inner"])
    geometry = _geometry(shards, data.calendar)
    if payload is not None:
        result = _saved_inner(output, payload)
        oof_reference.verify_inputs(shards, data)
        oof_reference.verify_fit(result, data)
        oof.verify_unit(result, data)
        wire.require(payload["geometry"] == geometry and payload["inputs_sha256"] == binding
            and payload["support"] == data.support and payload["calendar"] == list(data.calendar),
            "ER audited inner context differs")
        runtime = native.verify_after(admitted)
        wire.require(runtime["verified"] is True and runtime["kind"] == "tcn"
            and runtime["runtime_sha256"] == identity["runtime_sha256"], "ER inner audit native runtime differs")
        return None
    wire.exclusive_bytes(output / "fit-attempt.json", wire.encode(identity))
    def before_fit():
        wire.exclusive_bytes(output / "fit-start.json", wire.encode(identity))
    result = oof.fit_unit(data, before_fit=before_fit)
    if result["artifact"] is not None:
        wire.require(run._doc(output / "fit-start.json") == identity, "ER learner skipped its fit boundary")
    files = {"model": artifacts._document(output, "model.json",
                {key: value for key, value in result.items() if key not in ("predictions", "components")}),
             "predictions": artifacts._stream(output, "predictions.jsonl", result["predictions"]),
             "components": artifacts._stream(output, "components.jsonl", result["components"])}
    runtime = native.verify_after(admitted)
    wire.require(runtime["verified"] is True and runtime["kind"] == "tcn"
        and runtime["runtime_sha256"] == identity["runtime_sha256"], "ER inner native runtime differs")
    return {"inner": identity["inner"], **_fit_counts(result), "model_files": files,
        "inputs_sha256": binding, "geometry": geometry, "calendar": list(data.calendar),
        "support": data.support, "runtime": runtime}


def _check_oof_block(result, data, train_stop, end):
    calendar = data.base.calendar
    expected = [(row.key, row.session) for row in data.base.training
                if calendar[train_stop] <= row.session <= calendar[end - 1]]
    predictions = result["predictions"]
    wire.require(result["arm"] == "direct" and result["kind"] == "tcn" and result["scope"] == "common_chart_peer"
        and result["training_sessions"] == list(calendar[:train_stop])
        and result["evaluation_sessions"] == list(calendar[train_stop:end])
        and sorted((row["key"], row["session"]) for row in predictions) == sorted(expected)
        and len({row["key"] for row in predictions}) == len(predictions),
        "ER historical prediction block is not the fixed past-only complete cohort")
    if result["artifact"] is not None:
        wire.require(result["artifact"]["eval_first_session"] == calendar[train_stop],
            "ER historical learner cutoff differs")


def _oof_predictions(identity, data):
    if identity["invented"]:
        values = oof.invented_oof(data)
        return values, {"kind": "explicit_invented_chronological_forecast_fixture",
            "invented": True, "native_fits": 0,
            "claim": "consumer qualification only; not historical EP predictions or a native provenance audit"}
    values, blocks = [], []
    for inner, (_, train_stop, end) in enumerate(((0, 49, 73), (0, 73, 97))):
        directory, report, audit = _audited_unit(_inner_identity(identity, inner))
        result = _saved_inner(directory, report)
        _check_oof_block(result, data, train_stop, end)
        values.extend(result["predictions"])
        blocks.append({"kind": "ER-inner", "inner": inner, "train_stop": train_stop, "end": end,
            "report_sha256": wire.digest(wire.encode(report)), "audit_sha256": audit,
            "predictions_sha256": report["model_files"]["predictions"]["sha256"]})
    for fold in range(identity["fold"]):
        _, _, report, results, _ = eq._old_fold({**identity, "fold": fold})
        _, train_stop, end = workflow.FOLDS[fold]
        result = results["direct"]
        _check_oof_block(result, data, train_stop, end)
        values.extend(result["predictions"])
        blocks.append({"kind": "EP-direct", "fold": fold, "train_stop": train_stop, "end": end,
            "report_sha256": wire.digest(wire.encode(report)),
            "audit_sha256": identity["ep_fold_audit_sha256"][fold],
            "predictions_sha256": report["arms"]["direct"]["predictions"]["sha256"]})
    return values, {"kind": "authenticated_chronological_out_of_training_predictions",
                    "invented": False, "blocks": blocks}


def _project_outer(output, identity, shards, binding, *, payload=None):
    old_output, old_identity, old_report, results, old_policies = eq._old_fold(identity)
    data = workflow.load_fold(shards, identity["fold"])
    roster = workflow.decision_roster(shards, data)
    phases = mean.training_phases(shards, data)
    targets = mean.training_targets(shards, data)
    historical, provenance = _oof_predictions(identity, data)
    direct = results["direct"]["predictions"]
    geometry = _geometry(shards, data.base.calendar)
    predecessor = {"report_sha256": wire.digest(wire.encode(old_report)),
        "audit_sha256": identity["ep_invented_audit_sha256"] if identity["invented"] else
                        identity["ep_fold_audit_sha256"][identity["fold"]]}
    if payload is not None:
        saved_mean, fitted, saved_oof, saved_provenance, predictions, policies, representatives = _saved_outer(output, payload)
        wire.require(saved_oof == list(historical) and saved_provenance == provenance
            and predictions["direct"] == direct and policies["direct"] == old_policies["direct"],
            "ER predecessor/OOF forecasts or original direct policy changed")
        mean_reference.verify_training_inputs(shards, data, phases, targets)
        mean_reference.verify_baseline(data, phases, targets, saved_mean)
        mean_reference.verify_predictions(data, roster, saved_mean, predictions["mean"])
        reference.verify_calibration(data, phases, targets, saved_oof, fitted)
        reference.verify_predictions(data, roster, fitted, direct, predictions["calibrated"])
        mean_reference.verify_policy(roster.episodes, roster.candidates, predictions["mean"], policies["mean"])
        ep_reference.verify_policy(roster.episodes, roster.candidates, direct, policies["direct"])
        reference.verify_policy(roster.episodes, roster.candidates, predictions["calibrated"], policies["calibrated"])
        reference.verify_representatives(data.base.calendar, identity["fold"], roster.episodes,
            roster.candidates, predictions, representatives)
        eq._old_outcomes(old_output, old_identity, old_report)
        saved_metrics = ep._read_document(output, payload["metrics"], "metrics.json")
        reference.verify_report(data.base.calendar, identity["fold"], representatives,
            eq._evaluation_records(shards, data, roster), policies, saved_metrics)
        wire.require(payload["inputs_sha256"] == binding and payload["geometry"] == geometry
            and payload["support"] == data.base.support and payload["predecessor"] == predecessor,
            "ER audited outer context differs")
        return None
    historical_pin = artifacts._stream(output, "oof-predictions.jsonl", historical)
    provenance_pin = artifacts._document(output, "oof-provenance.json", provenance)
    baseline = mean.estimate(data, phases, targets)
    fitted = calibration.estimate(data, phases, targets, historical)
    predictions = {"mean": mean.project(data, roster, baseline), "direct": direct,
        "calibrated": calibration.project(data, roster, fitted, direct)}
    policies = {"mean": mean.freeze_policy(data, roster, predictions["mean"]),
        "direct": workflow.freeze_policy(data, roster, results["direct"]),
        "calibrated": calibration.freeze_policy(data, roster, predictions["calibrated"])}
    wire.require(workflow.serialize_policy(policies["direct"]) == old_policies["direct"],
        "ER original direct policy differs")
    files = {arm: {"predictions": artifacts._stream(output, f"{arm}-predictions.jsonl", predictions[arm]),
        "policy": artifacts._document(output, f"{arm}-policy.json", workflow.serialize_policy(policies[arm]))}
        for arm in ARMS}
    mean_pin = artifacts._document(output, "mean.json", baseline)
    fitted_pin = artifacts._document(output, "calibration.json", fitted)
    representatives = metrics.select_representatives(data.base.calendar, identity["fold"], roster.episodes,
        roster.candidates, predictions)
    rep_pin = artifacts._stream(output, "representatives.jsonl", representatives)
    # Every historical prediction, fitted mapping and causal outer choice is now
    # immutable. Current-fold outcomes and old economics are still unopened.
    eq._old_outcomes(old_output, old_identity, old_report)
    records = tuple(eq._evaluation_records(shards, data, roster))
    reports = {"mean": mean.join_policy_targets(policies["mean"], records),
        "direct": workflow.join_policy_targets(policies["direct"], records),
        "calibrated": calibration.join_policy_targets(policies["calibrated"], records)}
    totals = metrics.summarize(data.base.calendar, identity["fold"], representatives, records, reports)
    return {"fold": identity["fold"], "fit_attempt_count": 0, "fit_count": 0,
        "inputs_sha256": binding, "mean": mean_pin, "calibration": fitted_pin,
        "oof_predictions": historical_pin, "oof_provenance": provenance_pin, "arms": files,
        "representatives": rep_pin, "metrics": artifacts._document(output, "metrics.json", totals),
        "predecessor": predecessor, "geometry": geometry, "calendar": list(data.base.calendar), "support": data.base.support}


def _summary(output, identity):
    inner_receipts, publications, reports, folds = [], [], [], []
    for inner in range(2):
        _, payload, audit = _audited_unit(_inner_identity(identity, inner))
        inner_receipts.append({"inner": inner, "report_sha256": wire.digest(wire.encode(payload)),
            "audit_sha256": audit, **{name: payload[name] for name in
                ("fit_attempt_count", "fit_count", "completed_native_fit_count")},
            "additional_failed_fit_starts": sum(row["fit_started"] for row in payload["predecessor_timeouts"])})
    for fold in range(4):
        unit = {**identity, "id": UNIT + f"-f{fold}", "role": "outer", "fold": fold}
        directory, payload, audit = _audited_unit(unit)
        publications.append(payload)
        reports.append(ep._read_document(directory, payload["metrics"], "metrics.json"))
        folds.append({"fold": fold, "report_sha256": wire.digest(wire.encode(payload)), "audit_sha256": audit})
    wire.require(len({row["inputs_sha256"] for row in publications}) == 1
        and all(row["calendar"] == publications[0]["calendar"] for row in publications),
        "ER four outer publications differ")
    old_output = run.NATIVE / "output" / (ep.UNIT + "-summary")
    old_identity = run._doc(old_output / "start.json")
    eq._old_source(old_identity, identity)
    wire.require(old_identity["id"] == ep.UNIT + "-summary" and old_identity["fold"] is None
        and old_identity["invented"] is False and old_identity["fit_attempt_count"] == 0,
        "ER EP summary identity differs")
    old = ep._publication(old_output, old_identity)
    expected = [{"fold": fold, "report_sha256": identity["ep_fold_report_sha256"][fold],
                 "audit_sha256": identity["ep_fold_audit_sha256"][fold]} for fold in range(4)]
    wire.require(wire.digest(wire.encode(old)) == identity["ep_summary_sha256"]
        and old["fold_audits"] == expected and old["calendar"] == publications[0]["calendar"]
        and old["inputs_sha256"] == publications[0]["inputs_sha256"], "ER EP summary binding differs")
    pooled = metrics.pool(publications[0]["calendar"], reports)
    reference.verify_pool(publications[0]["calendar"], reports, pooled)
    return {"inner_audits": inner_receipts, "fold_audits": folds,
        "fit_attempt_count": sum(row["fit_attempt_count"] for row in inner_receipts),
        "fit_count": sum(row["fit_count"] for row in inner_receipts),
        "completed_native_fit_count": sum(row["completed_native_fit_count"] for row in inner_receipts),
        "additional_failed_fit_starts": sum(row["additional_failed_fit_starts"] for row in inner_receipts),
        "calendar": publications[0]["calendar"], "inputs_sha256": publications[0]["inputs_sha256"],
        "fold_metrics": [{"fold": fold, "report": reports[fold]} for fold in range(4)], "pooled": pooled}


def _validate_args(args):
    invented = "qualification" in args.mode
    inner = "-inner-" in args.mode
    summary = args.mode == "calibration-summary"
    wire.require(args.mode in MODES and all(getattr(args, name, None) is None for name in
        ("kind", "scope", "shard", "attempt", "summary_sha256", "entry_value_report_sha256",
         "entry_value_audit_sha256", "rank_failure_start", "rank_failure_end", "rank_failure_sha256",
         "factorized_qualification_sha256", "factorized_attempt", "mean_qualification_sha256", "mean_attempt"))
        and wire.valid_sha(args.invented_inputs_sha256)
        and (args.calibration_attempt is None if invented or summary else args.calibration_attempt in (None, *ATTEMPTS)),
        "ER only admits its fixed calibration campaign")
    wire.require((type(args.inner) is int and args.inner in range(2) and args.fold is None
                  and (not invented or args.inner == 1)) if inner else
                 args.inner is None and (args.fold is None if summary else type(args.fold) is int and args.fold in range(4))
                  and (not invented or args.fold == 3), "ER fixed unit coordinate differs")
    wire.require(args.dataset_binding_sha256 is None if invented else wire.valid_sha(args.dataset_binding_sha256),
        "ER actual dataset binding required only for actual modes")
    if inner:
        wire.require(all(getattr(args, name) is None for name in EP_PINS)
            and args.calibration_qualification_sha256 is None
            and (args.calibration_inner_qualification_sha256 is None if invented else
                 wire.valid_sha(args.calibration_inner_qualification_sha256)), "ER inner qualification pins differ")
    else:
        wire.require(wire.valid_sha(args.calibration_inner_qualification_sha256)
            and wire.valid_sha(args.ep_invented_report_sha256) and wire.valid_sha(args.ep_invented_audit_sha256),
            "ER outer requires its inner qualification and invented EP pins")
        wire.require((args.calibration_qualification_sha256 is None and args.ep_summary_sha256 is None
                and args.ep_fold_report_sha256 is None and args.ep_fold_audit_sha256 is None) if invented else
            wire.valid_sha(args.calibration_qualification_sha256) and wire.valid_sha(args.ep_summary_sha256)
            and all(type(value) in (list, tuple) and len(value) == 4 and all(wire.valid_sha(pin) for pin in value)
                    for value in (args.ep_fold_report_sha256, args.ep_fold_audit_sha256)),
            "ER outer actual predecessor pins differ")


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
    project = _project_inner if identity["role"] == "inner" else _project_outer
    with run._locks():
        if audit:
            wire.exclusive_bytes(output / "audit-start.json", wire.encode(identity))
            try:
                done = run._doc(output / "completion.json")
                wire.require(done["identity"] == identity, "ER audit completion identity differs")
                run.audit_budget(done["result"]["resources"])
                payload = _publication(output, identity)
                _prerequisites(identity)
                shards, binding = ep._invented_inputs(identity) if identity["invented"] else ep._actual_inputs(identity)
                project(output, identity, shards, binding, payload=payload)
                ep._source_after(source, runtime)
                measured = run.usage()
                run.verify_usage(payload["resources"], measured)
                wire.exclusive_bytes(output / "audit.json", wire.encode({"identity": identity,
                    "report_sha256": wire.digest(wire.encode(payload)), "independently_verified": True, "resources": measured}))
            except Exception as error:
                wire.exclusive_bytes(output / "audit-failure.json", wire.encode({"identity": identity,
                    "error_type": type(error).__name__, "resources": run.usage()}))
                raise
        else:
            wire.exclusive_directory(output)
            wire.exclusive_bytes(output / "start.json", wire.encode(identity))
            journal = _journal(output, identity)
            start = 0 if identity["invented"] else wire.regular(journal).st_size
            def evaluate():
                predecessors = _retry_predecessors(identity)
                _prerequisites(identity)
                if identity["role"] == "summary":
                    value = _summary(output, identity)
                else:
                    shards, binding = ep._invented_inputs(identity) if identity["invented"] else ep._actual_inputs(identity)
                    value = project(output, identity, shards, binding)
                ep._source_after(source, runtime)
                measured = run.usage()
                run.verify_usage(measured, dict.fromkeys(measured, 0))
                payload = {"identity": identity, **value, "resources": measured, "predecessor_timeouts": predecessors}
                raw = wire.encode(payload)
                wire.require(len(raw) <= artifacts.MAX_DOCUMENT, "ER report bound exceeded")
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
                        failure["partial"] = _partial_artifacts(output, identity)
                        failure["recovery_supported"] = True
                    except (ValueError, OSError):
                        failure["recovery_supported"] = False
                wire.exclusive_bytes(output / "failure.json", wire.encode(failure))
                raise
    print(json.dumps({"unit": identity["id"], "audit": audit, "status": "completed", "output": str(output)}, sort_keys=True))
