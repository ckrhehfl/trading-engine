"""Closed local EM holding comparisons over authenticated original episodes."""

from contextlib import contextmanager
from dataclasses import replace
from itertools import zip_longest
import json

from research import experiment_log
from research import intraday_event_attribution_run as original
from research import intraday_event_exit_followup as core
from research import intraday_event_exit_followup_reference as reference
from research.intraday_event_scenarios import MinuteBar

run, wire = original.run, original.wire
artifacts, inputs = original.artifacts, original.inputs
UNIT = "EM-C-HOLDING-FOLLOWUP-1"
QUALIFICATION = UNIT + "-qualification"
PLAN = ".planning/rd-em-exit-horizon.md"
DIAGNOSTIC_MERGE = "de60033146f1bd8f0015041a45ff2a5e5dad4da9"
DIAGNOSTIC_REPORT = "5e181688fa603eb198996add23b899aa3e3ddef0eec421922ccd37d4f5b0a8cc"
DIAGNOSTIC_AUDIT = "3ffa7b103f6a8af4b1958fa185dd7ef79b09dc65e86ec7a38c1d1e06033ff4b0"
SCIENTIFIC_COMPARISONS = ("received_cost_close_exit30", "fixed30_vs_fixed60_clock_eligible")
MODES = ("exit-followup", "exit-followup-audit", "exit-followup-qualification", "exit-followup-qualification-audit")


def invented_cases():
    calendar, cases = original.fixtures.full_cases()
    def extended():
        for index, (roster, ledgers, market) in enumerate(cases):
            if index % 3:
                bars = dict(market.bars)
                for offset in range(market.session_length, 100):
                    price = 101 if index % 2 else 99
                    bars[offset] = MinuteBar(offset, price, price, price, price, offset + 1)
                market = replace(market, session_length=100, bars=bars)
            yield roster, ledgers, market
    return calendar, extended()


def _identity(source, runtime, *, invented, args=None, qualification=None):
    return {"id": QUALIFICATION if invented else UNIT, "source": source, "merge": source["merge"],
        "invented": invented, "runtime_sha256": runtime, "registration_sha256": source["sources"][PLAN],
        "scientific_comparison_ids": list(SCIENTIFIC_COMPARISONS), "scientific_comparison_count": 2,
        "reported_contrasts": sorted(core.COMPARISONS),
        "summary_sha256": None if invented else args.summary_sha256,
        "dataset_binding_sha256": None if invented else args.dataset_binding_sha256,
        "diagnostic_report_sha256": None if invented else DIAGNOSTIC_REPORT,
        "diagnostic_audit_sha256": None if invented else DIAGNOSTIC_AUDIT,
        "qualification_sha256": qualification}


def _publication(output, identity, *, actual):
    completed = run._doc(output / "completion.json")
    raw = run._read(output / "report.json", artifacts.MAX_DOCUMENT)
    payload = json.loads(raw)
    result = completed["result"]
    wire.require(wire.encode(payload) == raw and completed["identity"] == identity == payload["identity"]
        and run._doc(output / "start.json") == identity
        and result == {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": payload["resources"]},
        "EM follow-up publication differs")
    if actual:
        span = completed["journal"]
        wire.require(run._proof(identity, result, span["start"], span["end"], "completed", unit=UNIT) == span,
                     "EM follow-up canonical journal differs")
    return payload


def _qualification(source, runtime):
    output = run.NATIVE / "output" / QUALIFICATION
    identity = _identity(source, runtime, invented=True)
    payload = _publication(output, identity, actual=False)
    audit = run._doc(output / "audit.json")
    wire.require(audit["identity"] == identity and audit["report_sha256"] == wire.digest(wire.encode(payload))
                 and audit["independently_verified"] is True, "EM follow-up qualification audit missing")
    run.verify_usage(payload["resources"], audit["resources"])
    wire.require(sum(1 for _ in artifacts._read_rows(output, payload["events"])) == original.fixtures.FULL_EVENTS,
                 "EM follow-up qualification geometry differs")
    return wire.digest(wire.encode(audit))


def _diagnostic(args, source, runtime):
    output = run.NATIVE / "output" / original.UNIT
    audit_raw = run._read(output / "audit.json", artifacts.MAX_DOCUMENT)
    wire.require(wire.digest(audit_raw) == DIAGNOSTIC_AUDIT, "EM prerequisite diagnostic audit differs")
    audit = json.loads(audit_raw)
    identity = audit["identity"]
    wire.require(identity["merge"] == DIAGNOSTIC_MERGE and identity["runtime_sha256"] == runtime
        and identity["summary_sha256"] == args.summary_sha256
        and identity["dataset_binding_sha256"] == args.dataset_binding_sha256
        and all(source["sources"].get(name) == pin for name, pin in identity["source"]["sources"].items()
                if name != "python/research/intraday_event_model_run.py"), "EM diagnostic implementation/input differs")
    payload, _ = original._publication(output, identity, actual=True)
    wire.require(audit["report_sha256"] == wire.digest(wire.encode(payload)) == DIAGNOSTIC_REPORT
                 and audit["independently_verified"] is True, "EM diagnostic output differs")
    run.verify_usage(payload["resources"], audit["resources"])
    return output, payload


@contextmanager
def actual_cases(args, source, runtime, prior, output, *, audit, payload):
    diagnostic_path, diagnostic = _diagnostic(args, source, runtime)
    summary, shards, calendar = original._predecessor(args, source, runtime)
    roster = original.causal_roster(shards, calendar)
    wire.require(len(roster) == diagnostic["totals"]["opportunities"]
        == summary["report"]["original_episodes_by_family"][original.FAMILY], "EM follow-up original roster differs")
    days = set(calendar)
    def source_rows(name, kind):
        for path, manifest in shards:
            if manifest["session_indices"][1] <= original.contract.FOLDS[0][1]:
                continue
            for row in inputs._rows(path, manifest, name, kind):
                if row["session"] in days and (kind != "support_runs" or row["family"] == original.FAMILY):
                    yield row
    lengths = {}
    def coordinates():
        for row in source_rows("coordinate-support.jsonl", "coordinate"):
            lengths[row["session"], row["symbol"]] = row["continuous_length"]
            yield row
    support_rows = lambda: source_rows("event-support.jsonl", "support_runs")
    support = diagnostic["source_support"]
    if audit:
        original.reference.verify_source_support(coordinates(), support_rows(), roster, support)
    else:
        wire.require(original.summarize_source_support(coordinates(), support_rows(), roster) == support,
                     "EM original support differs")
    clock_gates = {}
    for event, causal in roster.items():
        clock = causal["confirmation_decision_offset"]
        length = lengths[causal["session"], causal["symbol"]]
        # This is only the clock component. Source no-commit remains no-commit.
        clock_gates[event] = {"session_length": length,
            "clock_admissible": clock is not None and clock + 1 + 60 + 3 < length}
    raw = wire.encode({"events": roster, "source_support": support, "clock_gates": clock_gates})
    seal = {"filename": "causal-roster.json", "bytes": len(raw), "sha256": wire.digest(raw)}
    if audit:
        wire.require(payload["causal_roster"] == seal
            and run._read(output / seal["filename"], artifacts.MAX_DOCUMENT) == raw, "EM follow-up causal seal differs")
    else:
        wire.exclusive_bytes(output / seal["filename"], raw)
    with run.original_inputs(output, prior, False, audit) as (universe, reader):
        markets = inputs.SessionMarkets(universe, reader, calendar, audit=audit)
        def rows():
            sentinel = object()
            saved = artifacts._read_rows(diagnostic_path, diagnostic["events"])
            for pair, old in zip_longest(original._fold_ledgers(summary, roster, calendar), saved, fillvalue=sentinel):
                wire.require(pair is not sentinel and old is not sentinel, "EM diagnostic event denominator differs")
                causal, ledgers = pair
                wire.require(old["roster"] == causal, "EM diagnostic causal event differs")
                market = markets[causal["session"], causal["symbol"]]
                wire.require(market.session_length == clock_gates[causal["event_id"]]["session_length"],
                             "EM original session clock differs")
                yield causal, ledgers, market
        yield calendar, seal, rows(), support, clock_gates


def _project(output, calendar, rows, *, audit, payload, identity, seal=None, support=None, clock_gates=None):
    records = []
    lag = 1 if identity["invented"] else 3
    def check_gate(record, case):
        if clock_gates is not None:
            causal, ledgers, _ = case
            committed = ledgers[original.core.PATH_ARM]["action"] != "no_commit"
            expected = committed and clock_gates[causal["event_id"]]["clock_admissible"]
            wire.require(record["eligibility"]["eligible"] is expected, "EM outcome changed sealed clock gate")
    if audit:
        sentinel = object()
        for case, saved in zip_longest(rows, artifacts._read_rows(output, payload["events"]), fillvalue=sentinel):
            wire.require(case is not sentinel and saved is not sentinel, "EM follow-up event stream length differs")
            reference.verify_event(*case, saved, minimum_receipt_lag=lag)
            check_gate(saved, case)
            records.append(saved)
        reference.verify_summary(calendar, records, payload["totals"])
        return None
    def measured():
        for case in rows:
            record = core.measure_event(*case, minimum_receipt_lag=lag)
            check_gate(record, case)
            records.append(record)
            yield record
    pin = artifacts._stream(output, "events.jsonl", measured())
    totals = core.summarize(calendar, records)
    usage = run.usage()
    report = {"identity": identity, "calendar": list(calendar), "causal_roster": seal,
              "source_support": support, "events": pin, "totals": totals, "resources": usage}
    raw = wire.encode(report)
    wire.require(len(raw) <= artifacts.MAX_DOCUMENT, "EM follow-up report bound exceeded")
    wire.exclusive_bytes(output / "report.json", raw)
    return {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": usage}


def execute(args, registrations, source, started):
    run.STARTED_CLOCK = started
    run.budget()
    runtime = run.runtime_pin()
    wire.require(args.mode in MODES and all(getattr(args, name) is None for name in ("kind", "scope", "fold", "shard", "attempt")),
                 "EM follow-up accepts only its two fixed comparisons")
    invented, audit = "qualification" in args.mode, args.mode.endswith("-audit")
    wire.require((invented and args.summary_sha256 is None and args.dataset_binding_sha256 is None)
        or (not invented and wire.valid_sha(args.summary_sha256) and wire.valid_sha(args.dataset_binding_sha256)),
        "EM follow-up predecessor pins differ")
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
            if invented:
                calendar, cases = invented_cases()
                result = _project(output, calendar, cases, audit=audit, payload=payload, identity=identity)
            else:
                with actual_cases(args, source, runtime, registrations[run.ORIGINAL], output, audit=audit, payload=payload) as batch:
                    calendar, seal, cases, support, gates = batch
                    if audit:
                        wire.require(list(calendar) == payload["calendar"] and support == payload["source_support"],
                                     "EM follow-up support/calendar differs")
                    result = _project(output, calendar, cases, audit=audit, payload=payload, identity=identity,
                                      seal=seal, support=support, clock_gates=gates)
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
