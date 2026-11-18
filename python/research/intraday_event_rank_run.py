"""Closed EO saved-record ranking; no original-market or fitted-model adapter."""

import json

from research import experiment_log
from research import intraday_event_value_run as prior
from research import intraday_event_rank as core
from research import intraday_event_rank_reference as reference
from research import intraday_event_rank_fixtures as fixtures


run, wire, artifacts = prior.run, prior.wire, prior.artifacts
UNIT = "EO-C-RANK-AUDIT-1"
QUALIFICATION = UNIT + "-qualification"
PLAN = ".planning/rd-eo-within-context-ranking.md"
PREDECESSOR = "42d5307767efb7d4c86c85f847183a82e2f3baf1"
DISPATCHER = "python/research/intraday_event_model_run.py"
MODES = ("entry-rank", "entry-rank-audit", "entry-rank-qualification", "entry-rank-qualification-audit")


def _identity(source, runtime, *, invented, args=None, qualification=None):
    return {"id": QUALIFICATION if invented else UNIT, "source": source, "merge": source["merge"],
        "invented": invented, "runtime_sha256": runtime, "registration_sha256": source["sources"][PLAN],
        "entry_value_report_sha256": None if invented else args.entry_value_report_sha256,
        "entry_value_audit_sha256": None if invented else args.entry_value_audit_sha256,
        "qualification_sha256": qualification, "new_fit_count": 0, "new_policy_count": 0}


def _publication(output, identity, *, actual):
    completed = run._doc(output / "completion.json")
    raw = run._read(output / "report.json", artifacts.MAX_DOCUMENT)
    payload = json.loads(raw)
    result = completed["result"]
    wire.require(wire.encode(payload) == raw and completed["identity"] == identity == payload["identity"]
        and run._doc(output / "start.json") == identity
        and result == {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": payload["resources"]},
        "EO publication identity differs")
    if actual:
        span = completed["journal"]
        wire.require(run._proof(identity, result, span["start"], span["end"], "completed", unit=UNIT) == span,
                     "EO canonical journal differs")
    return payload


def _qualification(source, runtime):
    output = run.NATIVE / "output" / QUALIFICATION
    identity = _identity(source, runtime, invented=True)
    payload = _publication(output, identity, actual=False)
    audit = run._doc(output / "audit.json")
    wire.require(run._doc(output / "audit-start.json") == identity and audit["identity"] == identity
        and audit["report_sha256"] == wire.digest(wire.encode(payload))
        and audit["independently_verified"] is True, "EO same-release qualification audit missing")
    run.verify_usage(payload["resources"], audit["resources"])
    roster = artifacts._read_document(output, payload["causal_roster"])
    wire.require(roster["calendar"] == payload["calendar"] and len(roster["events"]) * 4 == fixtures.FULL_ROWS,
                 "EO invented roster geometry differs")
    for name in ("representatives", "joined"):
        wire.require(sum(1 for _ in artifacts._read_rows(output, payload[name])) == fixtures.FULL_ROWS,
                     "EO whole-size invented geometry differs")
    return wire.digest(wire.encode(audit))


def _prior_release(old, source, runtime):
    wire.require(old["merge"] == PREDECESSOR and old["sources"]
        and all(source["sources"].get(name) == pin for name, pin in old["sources"].items() if name != DISPATCHER),
        "EO unchanged EN reviewed source differs")
    commit, tree, parents = run._git("show", "-s", "--format=%H%n%T%n%P", old["merge"]).decode().splitlines()
    wire.require(commit == old["merge"] and tree == old["tree"] and len(parents.split()) == 2
        and parents.split()[1] == old["reviewed"] and parents.split()[0] != old["reviewed"]
        and run._git("rev-parse", old["reviewed"] + "^{tree}").decode().strip() == tree,
        "EO EN predecessor is not its reviewed normal merge")
    run._git("merge-base", "--is-ancestor", old["merge"], source["merge"])


def _actual_inputs(args, source, runtime):
    # execute() has already persisted its start and the canonical trial start.
    output = run.NATIVE / "output" / prior.UNIT
    raw = run._read(output / "report.json", artifacts.MAX_DOCUMENT)
    wire.require(wire.digest(raw) == args.entry_value_report_sha256, "EO prior report pin differs")
    identity = json.loads(raw)["identity"]
    wire.require(identity["id"] == prior.UNIT and identity["invented"] is False
        and identity["runtime_sha256"] == runtime and identity["merge"] == PREDECESSOR,
        "EO original EN identity/runtime differs")
    _prior_release(identity["source"], source, runtime)
    payload = prior._publication(output, identity, actual=True)
    wire.require(wire.encode(payload) == raw, "EO predecessor changed during admission")
    audit = run._doc(output / "audit.json")
    wire.require(wire.digest(wire.encode(audit)) == args.entry_value_audit_sha256
        and run._doc(output / "audit-start.json") == identity and audit["identity"] == identity
        and audit["report_sha256"] == args.entry_value_report_sha256
        and audit["independently_verified"] is True, "EO prior independent audit differs")
    run.verify_usage(payload["resources"], audit["resources"])
    for name, filename in (("causal_roster", "causal-roster.json"), ("causal", "causal.jsonl"),
                           ("queries", "query-errors.jsonl")):
        wire.require(payload[name]["filename"] == filename, "EO predecessor artifact name differs")
    roster = artifacts._read_document(output, payload["causal_roster"])
    wire.require(roster["calendar"] == payload["calendar"]
        and roster["source_support"] == payload["source_support"], "EO predecessor roster context differs")
    causal = list(artifacts._read_rows(output, payload["causal"]))
    def outcomes():
        return artifacts._read_rows(output, payload["queries"])
    provenance = {"report_sha256": args.entry_value_report_sha256, "audit_sha256": args.entry_value_audit_sha256,
        "causal_roster": payload["causal_roster"], "causal": payload["causal"], "queries": payload["queries"]}
    return roster, causal, outcomes, provenance


def _invented_inputs():
    roster, causal, outcomes = fixtures.full_fixture()
    return roster, causal, outcomes, {"invented": True}


def _queries(causal, outcomes):
    original = {row["key"]: row for row in causal}
    wire.require(len(original) == len(causal), "EO duplicate original causal key")
    rows, seen = [], set()
    for row in outcomes():
        key = row["key"]
        wire.require(key in original and key not in seen and row["schema"] == "EN-query-value-v1"
            and all(row.get(name) == value for name, value in original[key].items() if name != "schema"),
            "EO original measured/causal projection differs")
        seen.add(key)
        rows.append(row)
    wire.require(seen == original.keys(), "EO original measured denominator differs")
    return rows


def _project(output, batch, *, audit, payload, identity):
    roster, causal, outcomes, provenance = batch
    roster_pin = prior._document(output, "causal-roster.json", roster,
        audit=audit, expected=None if payload is None else payload["causal_roster"])
    if audit:
        representatives = list(artifacts._read_rows(output, payload["representatives"]))
        reference.verify_selection(roster, causal, representatives)
        representative_pin = payload["representatives"]
    else:
        representatives = core.select_representatives(roster, causal)
        representative_pin = artifacts._stream(output, "representatives.jsonl", representatives)
    # No outcome factory is invoked until the causal representatives are sealed.
    queries = _queries(causal, outcomes)
    if audit:
        joined = list(artifacts._read_rows(output, payload["joined"]))
        reference.verify_join(representatives, queries, joined)
        wire.require(payload["provenance"] == provenance and payload["calendar"] == roster["calendar"]
            and payload["source_support"] == roster["source_support"], "EO copied context differs")
        reference.verify_summary(roster["calendar"], joined, payload["totals"])
        return None
    joined = core.join_targets(representatives, queries)
    joined_pin = artifacts._stream(output, "joined.jsonl", joined)
    totals = core.summarize(roster["calendar"], joined)
    usage = run.usage()
    report = {"identity": identity, "calendar": roster["calendar"], "causal_roster": roster_pin,
        "representatives": representative_pin, "joined": joined_pin, "provenance": provenance,
        "source_support": roster["source_support"], "totals": totals, "resources": usage}
    raw = wire.encode(report)
    wire.require(len(raw) <= artifacts.MAX_DOCUMENT, "EO report bound exceeded")
    wire.exclusive_bytes(output / "report.json", raw)
    return {"report_sha256": wire.digest(raw), "report_bytes": len(raw), "resources": usage}


def execute(args, registrations, source, started):
    run.STARTED_CLOCK = started
    run.budget()
    runtime = run.runtime_pin()
    wire.require(args.mode in MODES and all(getattr(args, name) is None for name in
        ("kind", "scope", "fold", "shard", "attempt", "summary_sha256", "dataset_binding_sha256")),
        "EO accepts only its fixed saved-record audit")
    invented, audit = "qualification" in args.mode, args.mode.endswith("-audit")
    pins = (args.entry_value_report_sha256, args.entry_value_audit_sha256)
    wire.require(all(value is None for value in pins) if invented else all(wire.valid_sha(value) for value in pins),
                 "EO prior report/audit pins required only for actual audit")
    prior.original.bridge.restrict_local_network()
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
            batch = _invented_inputs() if invented else _actual_inputs(args, source, runtime)
            result = _project(output, batch, audit=audit, payload=payload, identity=identity)
            if audit:
                usage = run.usage()
                run.verify_usage(payload["resources"], usage)
                wire.exclusive_bytes(output / "audit.json", wire.encode({"identity": identity,
                    "report_sha256": wire.digest(wire.encode(payload)), "independently_verified": True,
                    "resources": usage}))
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
