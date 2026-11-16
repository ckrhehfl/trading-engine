"""Closed local EN joins over existing, independently audited EL publications."""

from collections import defaultdict
from itertools import zip_longest
import json

from research import experiment_log
from research import intraday_event_attribution_run as original
from research import intraday_event_value_audit as core
from research import intraday_event_value_reference as reference
from research import intraday_event_value_fixtures as fixtures


run, wire = original.run, original.wire
artifacts, inputs = original.artifacts, original.inputs
contract, launch, receipts = original.contract, original.launch, original.receipts
UNIT = "EN-C-VALUE-AUDIT-1"
QUALIFICATION = UNIT + "-qualification"
PLAN = ".planning/rd-en-entry-value-audit.md"
MODES = ("entry-value", "entry-value-audit", "entry-value-qualification", "entry-value-qualification-audit")
MODEL = original.MODEL
FAMILY = original.FAMILY


def _identity(source, runtime, *, invented, args=None, qualification=None):
    return {"id": QUALIFICATION if invented else UNIT, "source": source, "merge": source["merge"],
        "invented": invented, "runtime_sha256": runtime, "registration_sha256": source["sources"][PLAN],
        "summary_sha256": None if invented else args.summary_sha256,
        "dataset_binding_sha256": None if invented else args.dataset_binding_sha256,
        "qualification_sha256": qualification, "new_fit_count": 0, "new_policy_count": 0}


def _publication(output, identity, *, actual):
    completed = run._doc(output / "completion.json")
    raw = run._read(output / "report.json", artifacts.MAX_DOCUMENT)
    payload = json.loads(raw)
    result = completed["result"]
    wire.require(wire.encode(payload) == raw and completed["identity"] == identity == payload["identity"]
        and run._doc(output / "start.json") == identity
        and result == {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": payload["resources"]},
        "EN publication identity differs")
    if actual:
        span = completed["journal"]
        wire.require(run._proof(identity, result, span["start"], span["end"], "completed", unit=UNIT) == span,
                     "EN canonical journal differs")
    return payload


def _qualification(source, runtime):
    output = run.NATIVE / "output" / QUALIFICATION
    identity = _identity(source, runtime, invented=True)
    payload = _publication(output, identity, actual=False)
    audit = run._doc(output / "audit.json")
    wire.require(audit["identity"] == identity and audit["report_sha256"] == wire.digest(wire.encode(payload))
                 and audit["independently_verified"] is True, "EN same-release qualification audit missing")
    run.verify_usage(payload["resources"], audit["resources"])
    wire.require(sum(1 for _ in artifacts._read_rows(output, payload["episodes"])) == fixtures.FULL_EVENTS,
                 "EN full invented episode geometry differs")
    return wire.digest(wire.encode(audit))


def _document(output, filename, value, *, audit, expected=None):
    raw = wire.encode(value)
    pin = {"filename": filename, "bytes": len(raw), "sha256": wire.digest(raw)}
    wire.require(len(raw) <= artifacts.MAX_DOCUMENT, "EN document size exceeded")
    if audit:
        wire.require(pin == expected and run._read(output / filename, artifacts.MAX_DOCUMENT) == raw,
                     "EN sealed causal document differs")
    else:
        wire.exclusive_bytes(output / filename, raw)
    return pin


def _stream(output, filename, rows, *, audit, expected=None, verify=None):
    """The audit passes original inputs to a separate verifier, never a producer."""
    saved_rows = []
    if audit:
        sentinel = object()
        for original_row, saved in zip_longest(rows, artifacts._read_rows(output, expected), fillvalue=sentinel):
            wire.require(original_row is not sentinel and saved is not sentinel, "EN stream denominator differs")
            verify(original_row, saved)
            saved_rows.append(saved)
        return expected, saved_rows
    def collect():
        for row in rows:
            saved_rows.append(row)
            yield row
    return artifacts._stream(output, filename, collect()), saved_rows


def _fold_publications(summary):
    old = summary["release"]
    _, invented_binding, _ = launch._invented_inputs(old)
    qualification = launch._qualification_identity(old, (*MODEL, 3), invented_binding)
    saved = [item for item in summary["fold_audits"] if tuple(item["coordinate"][:2]) == MODEL]
    wire.require([item["coordinate"][2] for item in saved] == list(range(4)), "EN original model folds differ")
    result = []
    for item in saved:
        coords = tuple(item["coordinate"])
        identity = launch._identity(old, coords, False, summary["inputs_sha256"], qualification, summary["case_report_sha256"])
        path, identity, publication, audit_pin = receipts.audited_actual(identity)
        manifest_pin = publication["outcome"]["manifest"]
        wire.require((audit_pin, manifest_pin["sha256"]) == (item["audit_sha256"], item["manifest_sha256"]),
                     "EN archived fold audit differs")
        manifest = artifacts.read_manifest(path, manifest_pin, identity)
        result.append((coords[2], path, manifest))
    return result


def _actual_inputs(args, source, runtime, *, audit):
    summary, shards, calendar = original._predecessor(args, source, runtime)
    roster = original.causal_roster(shards, calendar)
    wire.require(len(roster) == summary["report"]["original_episodes_by_family"][FAMILY],
                 "EN original episode denominator differs")
    first = contract.FOLDS[0][1]
    fold_by_day = {day: fold for fold, (_, start, end) in enumerate(contract.FOLDS)
                   for day in calendar[start - first:end - first]}
    publications = _fold_publications(summary)
    predictions = {}
    source_pins = []
    for fold, path, manifest in publications:
        pin = manifest["files"]["predictions.jsonl"]
        source_pins.append({"fold": fold, "predictions": pin, "manifest_identity": manifest["identity"]})
        for row in artifacts._read_rows(path, pin):
            event = row["key"].rsplit("@", 1)[0]
            if event not in roster:
                continue
            wire.require(row["key"] not in predictions and fold_by_day.get(row["session"]) == fold
                         and roster[event]["session"] == row["session"], "EN prediction key/fold differs")
            predictions[row["key"]] = row
    days = set(calendar)
    relevant = [(path, manifest) for path, manifest in shards if manifest["session_indices"][1] > first]
    def rows(filename, kind):
        for path, manifest in relevant:
            for row in inputs._rows(path, manifest, filename, kind):
                if row["session"] in days:
                    yield row
    coordinates = lambda: rows("coordinate-support.jsonl", "coordinate")
    support_rows = lambda: (row for row in rows("event-support.jsonl", "support_runs") if row["family"] == FAMILY)
    support = original.summarize_source_support(coordinates(), support_rows(), roster)
    if audit:
        original.reference.verify_source_support(coordinates(), support_rows(), roster, support)
    def causal_inputs():
        seen, expected_predictions = set(), set()
        for row in rows("queries.jsonl", "query"):
            frame = row["frame"]
            if row["query_kind"] != "entry" or frame["family"] != FAMILY:
                continue
            event, key = frame["event_id"], row["key"]
            wire.require(event in roster and key not in seen, "EN causal query identity/duplicate differs")
            seen.add(key)
            if key in predictions:
                expected_predictions.add(key)
            yield roster[event], row, predictions.get(key), fold_by_day[row["session"]]
        wire.require(expected_predictions == predictions.keys(), "EN saved entry prediction lacks its causal query")
    def outcomes(causal, choices):
        # Called only after both causal streams and every choice have been sealed.
        by_key = {row["key"]: row for row in causal}
        targets = {}
        for row in rows("labels.jsonl", "labels"):
            if row["key"] in by_key:
                wire.require(row["key"] not in targets and row["query_kind"] == "entry"
                             and (row["session"], row["symbol"]) == (by_key[row["key"]]["session"], by_key[row["key"]]["symbol"]),
                             "EN target key/coordinate differs")
                targets[row["key"]] = row
        wire.require(targets.keys() == by_key.keys(), "EN target denominator differs")
        ledgers, statuses, diagnostics = {}, {}, []
        for fold, path, manifest in publications:
            comparison = artifacts._read_document(path, manifest["files"]["comparison.json"])
            expected_days = [day for day in calendar if fold_by_day[day] == fold]
            wire.require((comparison["kind"], comparison["scope"], comparison["fold"]) == (*MODEL, fold)
                         and comparison["evaluation_sessions"] == expected_days
                         and comparison["model_status"] in ("completed", "unsupported_empty_training"),
                         "EN saved comparison identity differs")
            expected_keys = {row["key"] for row in causal if row["fold"] == fold and row["scope_supported"]}
            actual_keys = {key for key, row in predictions.items() if fold_by_day[row["session"]] == fold}
            wire.require(actual_keys == (expected_keys if comparison["model_status"] == "completed" else set()),
                         "EN saved forecast support differs")
            statuses[fold] = comparison["head_statuses"]
            diagnostics.append({name: comparison[name] for name in
                ("fold", "model_status", "head_statuses", "fit_counts", "training_diagnostics")})
            policies = {policy["control"]: policy for policy in comparison["policies"]}
            traces = defaultdict(list)
            for item in policies["model"]["trace"]:
                if item[0] in roster:
                    traces[item[0]].append(item)
            for event, item in roster.items():
                if fold_by_day[item["session"]] == fold:
                    wire.require(choices[event]["trace"] == traces[event], "EN causal choice differs from saved policy trace")
            for row in artifacts._read_rows(path, manifest["files"]["ledger.jsonl"]):
                arm = tuple(row[name] for name in ("control", "entry_policy", "management"))
                if row["family"] != FAMILY or arm not in fixtures.ARMS:
                    continue
                event = row["original_episode_id"]
                wire.require(event in roster and fold_by_day[row["session"]] == fold
                    and (row["model"], row["scope"]) == MODEL
                    and row["policy_sha256"] == policies[row["control"]]["sha256"], "EN ledger identity/policy differs")
                arms = ledgers.setdefault(event, {})
                wire.require(arm not in arms, "EN duplicate original policy ledger")
                arms[arm] = row
        wire.require(ledgers.keys() == roster.keys() and all(set(books) == set(fixtures.ARMS) for books in ledgers.values()),
                     "EN complete original ledger denominator differs")
        return targets, ledgers, statuses, diagnostics
    return calendar, roster, fold_by_day, source_pins, support, causal_inputs, outcomes


def _invented_inputs():
    calendar, cases = fixtures.full_cases()
    cases = tuple(cases)
    roster = {case["event"]["event_id"]: case["event"] for case in cases}
    folds = {case["event"]["session"]: case["fold"] for case in cases}
    def causal_inputs():
        for case in cases:
            for row in case["wrappers"]:
                yield case["event"], row, case["predictions"].get(row["key"]), case["fold"]
    def outcomes(causal, choices):
        targets, ledgers, statuses = {}, {}, {}
        for case in cases:
            event = case["event"]["event_id"]
            wire.require(choices[event]["trace"] == case["trace"], "EN invented causal trace differs")
            targets.update(case["labels"])
            ledgers[event] = {tuple(key.split("|")): value for key, value in case["ledgers"].items()}
            # Invented cases deliberately exercise head status changes per event.
            statuses[event] = case["head_statuses"]
        return targets, ledgers, statuses, []
    return calendar, roster, folds, [], {"invented": True, "original_events": len(roster)}, causal_inputs, outcomes


def _project(output, batch, *, audit, payload, identity):
    calendar, roster, fold_by_day, source_pins, support, causal_inputs, outcomes = batch
    roster_pin = _document(output, "causal-roster.json", {"calendar": list(calendar), "events": roster,
        "fold_by_day": fold_by_day, "source_pins": source_pins, "source_support": support},
        audit=audit, expected=None if payload is None else payload["causal_roster"])
    raw_causal = causal_inputs()
    causal_pin, causal = _stream(output, "causal.jsonl",
        raw_causal if audit else (core.causal_query(*row) for row in raw_causal),
        audit=audit, expected=None if payload is None else payload["causal"],
        verify=lambda args, saved: reference.verify_causal(*args, saved))
    by_event = defaultdict(list)
    for row in causal:
        by_event[row["event_id"]].append(row)
    choice_inputs = ((event, by_event[event["event_id"]]) for event in roster.values())
    choice_pin, choice_rows = _stream(output, "choices.jsonl",
        choice_inputs if audit else (core.select_event(*args) for args in choice_inputs),
        audit=audit, expected=None if payload is None else payload["choices"],
        verify=lambda args, saved: reference.verify_choice(*args, saved))
    choices = {row["event_id"]: row for row in choice_rows}
    wire.require(choices.keys() == roster.keys(), "EN choices lost original episodes")
    targets, ledgers, statuses, diagnostics = outcomes(causal, choices)
    query_inputs = ((row, targets[row["key"]], choices[row["event_id"]]) for row in causal)
    query_pin, queries = _stream(output, "query-errors.jsonl",
        query_inputs if audit else (core.measure_query(*args) for args in query_inputs),
        audit=audit, expected=None if payload is None else payload["queries"],
        verify=lambda args, saved: reference.verify_query(*args, saved))
    measured = defaultdict(dict)
    for row in queries:
        measured[row["event_id"]][row["key"]] = row
    episode_inputs = ((event, choices[event_id], measured[event_id], ledgers[event_id],
        statuses[event_id] if identity["invented"] else statuses[fold_by_day[event["session"]]],
        fold_by_day[event["session"]]) for event_id, event in roster.items())
    episode_pin, episodes = _stream(output, "episode-errors.jsonl",
        episode_inputs if audit else (core.measure_episode(*args) for args in episode_inputs),
        audit=audit, expected=None if payload is None else payload["episodes"],
        verify=lambda args, saved: reference.verify_episode(*args, saved))
    if audit:
        wire.require(payload["calendar"] == list(calendar) and payload["source_support"] == support
                     and payload["fit_diagnostics"] == diagnostics, "EN context/diagnostics differ")
        reference.verify_summary(calendar, queries, episodes, diagnostics, payload["totals"])
        return None
    totals = core.summarize(calendar, queries, episodes, diagnostics)
    usage = run.usage()
    report = {"identity": identity, "calendar": list(calendar), "causal_roster": roster_pin,
        "causal": causal_pin, "choices": choice_pin, "queries": query_pin, "episodes": episode_pin,
        "source_support": support, "fit_diagnostics": diagnostics, "totals": totals, "resources": usage}
    raw = wire.encode(report)
    wire.require(len(raw) <= artifacts.MAX_DOCUMENT, "EN report bound exceeded")
    wire.exclusive_bytes(output / "report.json", raw)
    return {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": usage}


def execute(args, registrations, source, started):
    run.STARTED_CLOCK = started
    run.budget()
    runtime = run.runtime_pin()
    wire.require(args.mode in MODES and all(getattr(args, name) is None for name in ("kind", "scope", "fold", "shard", "attempt")),
                 "EN accepts only its fixed saved-model audit")
    invented, audit = "qualification" in args.mode, args.mode.endswith("-audit")
    wire.require((invented and args.summary_sha256 is None and args.dataset_binding_sha256 is None)
        or (not invented and wire.valid_sha(args.summary_sha256) and wire.valid_sha(args.dataset_binding_sha256)),
        "EN original pins required only for actual audit")
    original.bridge.restrict_local_network()
    wire.prohibit_ef_tasks()
    with run._locks():
        qualification = None if invented else _qualification(source, runtime)
        identity = _identity(source, runtime, invented=invented, args=args, qualification=qualification)
        output = run.NATIVE / "output" / identity["id"]
        payload = None
        if audit:
            payload = _publication(output, identity, actual=not invented)
            run.audit_budget(payload["resources"])
            wire.exclusive_bytes(output / "audit-start.json", wire.encode(identity))
        else:
            wire.exclusive_directory(output)
            wire.exclusive_bytes(output / "start.json", wire.encode(identity))
        def evaluate():
            batch = _invented_inputs() if invented else _actual_inputs(args, source, runtime, audit=audit)
            result = _project(output, batch, audit=audit, payload=payload, identity=identity)
            if audit:
                usage = run.usage()
                run.verify_usage(payload["resources"], usage)
                wire.exclusive_bytes(output / "audit.json", wire.encode({"identity": identity,
                    "report_sha256": wire.digest(wire.encode(payload)), "independently_verified": True, "resources": usage}))
            return result
        try:
            if invented or audit:
                result = evaluate()
                if not audit:
                    wire.exclusive_bytes(output / "completion.json", wire.encode({"identity": identity, "result": result}))
            else:
                journal = run.NATIVE / "canonical" / "experiments.jsonl"
                start = wire.regular(journal).st_size
                terminal = experiment_log.run_discovery_trial(study_id=UNIT, specification_sha256=identity["registration_sha256"],
                    parameters=identity, window=run.WINDOW, evaluate=evaluate, runs_path=journal,
                    code_version_provider=lambda: identity["merge"])
                result = terminal["result"]
                span = run._proof(identity, result, start, wire.regular(journal).st_size, "completed", unit=UNIT)
                wire.exclusive_bytes(output / "completion.json", wire.encode({"identity": identity, "result": result, "journal": span}))
        except Exception as error:
            wire.exclusive_bytes(output / f"{args.mode}-failure.json", wire.encode({"identity": identity, "error_type": type(error).__name__}))
            raise
    print(json.dumps({"unit": identity["id"], "audit": audit, "status": "completed", "output": str(output)}, sort_keys=True))
