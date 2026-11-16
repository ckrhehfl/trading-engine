"""Closed local EM diagnostic over admitted EL events, ledgers and minute paths.

No fitting, trading, network acquisition or new universe selection is here.
Actual starts precede original-content reads. Invented qualification exercises
the same record projection/storage/audit; unchanged upstream authentication is
covered by its existing independently audited source contract.
"""

from collections import defaultdict
from contextlib import contextmanager
from itertools import zip_longest
import json

from research import experiment_log
from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_attribution as core
from research import intraday_event_attribution_reference as reference
from research import intraday_event_attribution_fixtures as fixtures
from research.intraday_event_attribution_support import summarize_source_support
from research import intraday_event_dataset_run as run
from research import intraday_event_fold_artifacts as artifacts
from research import intraday_event_model_contract as contract
from research import intraday_event_model_inputs as inputs
from research import intraday_event_model_launch as launch
from research import intraday_event_model_receipts as receipts


UNIT = "EM-C-ATTRIBUTION-1"
PLAN = ".planning/rd-em-causal-attribution.md"
PREDECESSOR = "9b9f50ff278aaccedf41f229b7c9a4f206dcac30"
FAMILY = "C_FIRSTPULLBACK"
MODEL = ("tcn", "common_chart_peer")
QUALIFICATION = UNIT + "-qualification"
MODES = ("attribution", "attribution-audit", "attribution-qualification", "attribution-qualification-audit")


def _publication(output, identity, *, actual):
    completed = run._doc(output / "completion.json")
    raw = run._read(output / "report.json", artifacts.MAX_DOCUMENT)
    payload = json.loads(raw)
    result = completed["result"]
    wire.require(wire.encode(payload) == raw and completed["identity"] == identity == payload["identity"]
                 and run._doc(output / "start.json") == identity
                 and result == {"report_sha256": wire.digest(raw), "report_bytes": len(raw),
                                "resources": payload["resources"]}, "EM publication identity differs")
    if actual:
        span = completed["journal"]
        wire.require(run._proof(identity, result, span["start"], span["end"], "completed", unit=UNIT) == span,
                     "EM canonical trial differs")
    return payload, completed


def _qualification(source, runtime):
    output = run.NATIVE / "output" / QUALIFICATION
    identity = _identity(source, runtime, invented=True)
    payload, _ = _publication(output, identity, actual=False)
    audited = run._doc(output / "audit.json")
    wire.require(audited["identity"] == identity and audited["report_sha256"] == wire.digest(wire.encode(payload))
                 and audited["independently_verified"] is True, "EM same-release invented audit missing")
    run.verify_usage(payload["resources"], audited["resources"])
    # The sealed record stream is part of the qualification, not merely a receipt.
    wire.require(sum(1 for _ in artifacts._read_rows(output, payload["events"])) == fixtures.FULL_EVENTS,
                 "EM invented qualification geometry differs")
    return wire.digest(wire.encode(audited))


def _identity(source, runtime, *, invented, args=None, qualification=None):
    return {"id": QUALIFICATION if invented else UNIT, "source": source, "merge": source["merge"],
            "invented": invented, "runtime_sha256": runtime, "registration_sha256": source["sources"][PLAN],
            "summary_sha256": None if invented else args.summary_sha256,
            "dataset_binding_sha256": None if invented else args.dataset_binding_sha256,
            "qualification_sha256": qualification}


def _predecessor(args, source, runtime):
    original = run.NATIVE / "output" / f"{contract.UNIT}-summary"
    raw = run._read(original / "report.json", artifacts.MAX_DOCUMENT)
    wire.require(wire.digest(raw) == args.summary_sha256 and run._doc(original / "completion.json") == {
        "report_sha256": args.summary_sha256, "fold_audit_count": len(contract.ROSTER)}, "EM original EL summary differs")
    summary = json.loads(raw)
    old = summary["release"]
    wire.require(old["merge"] == PREDECESSOR and old["runtime_sha256"] == runtime
        and all(source["sources"].get(name) == pin for name, pin in old["sources"].items()
                if name != "python/research/intraday_event_model_run.py")
        and run._doc(original / "start.json") == {name: summary[name] for name in
            ("release", "inputs_sha256", "case_report_sha256")}, "EM prior reviewed implementation differs")
    binding = contract.validate_dataset_binding(run._doc(run.NATIVE / "canonical" /
        f"{contract.UNIT}-dataset-binding.json"), args.dataset_binding_sha256)
    wire.require(binding.inputs_sha256 == summary["inputs_sha256"], "EM original dataset binding differs")
    admitted = launch._admit_actual(binding)
    shards = tuple((path, report["dataset"]) for path, report, _ in admitted)
    calendar = inputs.calendar_from_coordinates(shards)[contract.FOLDS[0][1]:]
    wire.require(list(calendar) == summary["report"]["evaluation_sessions"], "EM original evaluation calendar differs")
    return summary, shards, calendar


def causal_roster(shards, calendar):
    """Project only independently audited causal event/query streams."""
    days, events, confirmations = set(calendar), {}, {}
    relevant = [(path, manifest) for path, manifest in shards
                if manifest["session_indices"][1] > contract.FOLDS[0][1]]
    terminals = {}
    for path, manifest in relevant:
        for row in inputs._rows(path, manifest, "event-states.jsonl", "event_state"):
            frame = row["frame"]
            if row["session"] not in days or frame["family"] != FAMILY:
                continue
            event = frame["event_id"]
            if frame["kind"] == "at_onset":
                wire.require(event not in events, "EM duplicate original C episode")
                events[event] = {"event_id": event, "session": row["session"], "symbol": row["symbol"],
                    "cluster_id": frame["overlap_group_id"], "onset_decision_offset": frame["decision_offset"],
                    "terminal_state": None, "confirmation_key": None, "confirmation_decision_offset": None,
                    "confirmation_reference": None}
            if frame["terminal"]:
                wire.require(event not in terminals, "EM duplicate terminal event state")
                terminals[event] = (row["session"], row["symbol"], frame["overlap_group_id"], frame["state"])
        for row in inputs.query_rows(path, manifest):
            frame = row["frame"]
            if (row["session"] not in days or row["query_kind"] != "entry"
                    or frame["family"] != FAMILY or frame["state"] != "CONFIRMED"):
                continue
            event, clock, label = frame["event_id"], frame["decision_offset"], row["label_query"]
            wire.require(event not in confirmations and row["key"] == f"{event}@{clock}"
                and label["event_id"] == event and label["decision_offset"] == clock
                and label["reference_close"] == row["reference_close"]
                and (row["reference_close"] is None or label["reference_offset"] < clock),
                "EM confirmation reference/receipt differs")
            confirmations[event] = (row["session"], row["symbol"], frame["overlap_group_id"], row["key"],
                                    clock, row["reference_close"])
    wire.require(set(terminals) <= events.keys() and set(confirmations) <= events.keys(), "EM event roster lost onsets")
    for event, item in events.items():
        coordinates = item["session"], item["symbol"], item["cluster_id"]
        if event in terminals:
            wire.require(terminals[event][:3] == coordinates, "EM terminal coordinate differs")
            item["terminal_state"] = terminals[event][3]
        if event in confirmations:
            matched = confirmations[event]
            wire.require(matched[:3] == coordinates and matched[4] >= item["onset_decision_offset"],
                         "EM confirmation lost original event")
            item.update(confirmation_key=matched[3], confirmation_decision_offset=matched[4], confirmation_reference=matched[5])
    return dict(sorted(events.items(), key=lambda pair: (pair[1]["session"], pair[1]["symbol"], pair[0])))


def _fold_ledgers(summary, roster, calendar):
    old = summary["release"]
    _, invented_binding, _ = launch._invented_inputs(old)
    qualification = launch._qualification_identity(old, (*MODEL, 3), invented_binding)
    coordinates = [item for item in summary["fold_audits"] if tuple(item["coordinate"][:2]) == MODEL]
    wire.require([item["coordinate"][2] for item in coordinates] == list(range(len(contract.FOLDS))),
                 "EM prior model fold order differs")
    first = contract.FOLDS[0][1]
    for saved in coordinates:
        coords = tuple(saved["coordinate"])
        identity = launch._identity(old, coords, False, summary["inputs_sha256"], qualification, summary["case_report_sha256"])
        path, identity, publication, audit_pin = receipts.audited_actual(identity)
        pin = publication["outcome"]["manifest"]
        wire.require((audit_pin, pin["sha256"]) == (saved["audit_sha256"], saved["manifest_sha256"]),
                     "EM archived fold audit differs")
        manifest = artifacts.read_manifest(path, pin, identity)
        _, start, end = contract.FOLDS[coords[2]]
        dates = set(calendar[start - first:end - first])
        expected = {event: row for event, row in roster.items() if row["session"] in dates}
        groups = defaultdict(dict)
        for row in artifacts._read_rows(path, manifest["files"]["ledger.jsonl"]):
            if row["family"] != FAMILY or row["control"] not in ("onset", "confirmation"):
                continue
            arm = tuple(row[name] for name in ("control", "entry_policy", "management"))
            event = row["original_episode_id"]
            wire.require((row["model"], row["scope"]) == MODEL and event in expected
                         and arm in core.ARM_KEYS and arm not in groups[event], "EM archived arm or roster differs")
            groups[event][arm] = row
        wire.require(groups.keys() == expected.keys() and all(set(arms) == set(core.ARM_KEYS) for arms in groups.values()),
                     "EM original event/arm denominator differs")
        for event, causal in expected.items():
            yield causal, groups[event]


@contextmanager
def actual_cases(args, source, runtime, prior, output, *, audit, payload):
    summary, shards, calendar = _predecessor(args, source, runtime)
    roster = causal_roster(shards, calendar)
    wire.require(len(roster) == summary["report"]["original_episodes_by_family"][FAMILY],
                 "EM complete causal roster differs from prior summary")
    days = set(calendar)
    def source_rows(name, kind):
        for path, manifest in shards:
            if manifest["session_indices"][1] <= contract.FOLDS[0][1]:
                continue
            for row in inputs._rows(path, manifest, name, kind):
                if row["session"] in days and (kind != "support_runs" or row["family"] == FAMILY):
                    yield row
    coordinates = lambda: source_rows("coordinate-support.jsonl", "coordinate")
    support_rows = lambda: source_rows("event-support.jsonl", "support_runs")
    if audit:
        saved_causal = json.loads(run._read(output / "causal-roster.json", artifacts.MAX_DOCUMENT))
        support = saved_causal["source_support"]
        reference.verify_source_support(coordinates(), support_rows(), roster, support)
    else:
        support = summarize_source_support(coordinates(), support_rows(), roster)
    raw = wire.encode({"events": roster, "source_support": support})
    roster_pin = {"filename": "causal-roster.json", "bytes": len(raw), "sha256": wire.digest(raw)}
    if audit:
        wire.require(payload["causal_roster"] == roster_pin and run._read(output / roster_pin["filename"], artifacts.MAX_DOCUMENT) == raw,
                     "EM causal roster seal changed")
    else:
        wire.exclusive_bytes(output / roster_pin["filename"], raw)
    # Reading old fold cash is downstream of the full causal seal.
    with run.original_inputs(output, prior, False, audit) as (universe, reader):
        markets = inputs.SessionMarkets(universe, reader, calendar, audit=audit)
        def rows():
            for causal, arms in _fold_ledgers(summary, roster, calendar):
                yield causal, arms, markets[causal["session"], causal["symbol"]]
        yield calendar, roster_pin, rows(), support


def _project(output, calendar, rows, *, audit, payload, identity, causal_pin, support=None):
    records = []
    if audit:
        sentinel = object()
        for case, saved in zip_longest(rows, artifacts._read_rows(output, payload["events"]), fillvalue=sentinel):
            wire.require(case is not sentinel and saved is not sentinel, "EM event stream length differs")
            reference.verify_event(*case, saved)
            records.append(saved)
        reference.verify_summary(calendar, records, payload["totals"])
        return None
    def measured():
        for case in rows:
            record = core.measure_event(*case)
            records.append(record)
            yield record
    events_pin = artifacts._stream(output, "events.jsonl", measured())
    totals = core.summarize(calendar, records)
    measured_usage = run.usage()
    report = {"identity": identity, "calendar": list(calendar), "causal_roster": causal_pin, "source_support": support,
              "events": events_pin, "totals": totals, "resources": measured_usage}
    raw = wire.encode(report)
    wire.require(len(raw) <= artifacts.MAX_DOCUMENT, "EM aggregate report bound exceeded")
    wire.exclusive_bytes(output / "report.json", raw)
    return {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": measured_usage}


def execute(args, registrations, source, started):
    run.STARTED_CLOCK = started
    run.budget()
    runtime = run.runtime_pin()
    wire.require(args.mode in MODES and all(getattr(args, name) is None for name in ("kind", "scope", "fold", "shard", "attempt")),
                 "EM attribution accepts only its fixed comparison scope")
    invented, audit = "qualification" in args.mode, args.mode.endswith("-audit")
    wire.require((invented and args.summary_sha256 is None and args.dataset_binding_sha256 is None)
        or (not invented and wire.valid_sha(args.summary_sha256) and wire.valid_sha(args.dataset_binding_sha256)),
        "EM exact predecessor pins required only for actual work")
    bridge.restrict_local_network()
    wire.prohibit_ef_tasks()
    with run._locks():
        qualification = None if invented else _qualification(source, runtime)
        identity = _identity(source, runtime, invented=invented, args=args, qualification=qualification)
        output = run.NATIVE / "output" / identity["id"]
        payload = None
        if audit:
            payload, _ = _publication(output, identity, actual=not invented)
            run.audit_budget(payload["resources"])
            wire.exclusive_bytes(output / "audit-start.json", wire.encode(identity))
        else:
            wire.exclusive_directory(output)
            wire.exclusive_bytes(output / "start.json", wire.encode(identity))

        def evaluate():
            if invented:
                calendar, cases = fixtures.full_cases()
                result = _project(output, calendar, cases, audit=audit, payload=payload, identity=identity, causal_pin=None)
            else:
                with actual_cases(args, source, runtime, registrations[run.ORIGINAL], output, audit=audit, payload=payload) as batch:
                    calendar, causal_pin, cases, support = batch
                    if audit:
                        wire.require(list(calendar) == payload["calendar"], "EM audit calendar differs")
                        wire.require(support == payload["source_support"], "EM source support summary differs")
                    result = _project(output, calendar, cases, audit=audit, payload=payload, identity=identity, causal_pin=causal_pin, support=support)
            if audit:
                measured = run.usage()
                run.verify_usage(payload["resources"], measured)
                proof = {"identity": identity, "report_sha256": wire.digest(wire.encode(payload)),
                         "independently_verified": True, "resources": measured}
                wire.exclusive_bytes(output / "audit.json", wire.encode(proof))
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
