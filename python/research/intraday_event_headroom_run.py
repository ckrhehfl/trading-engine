"""Local WSL readout of one fixed headroom screen on prior audited EL ledgers."""

from collections import Counter
from fractions import Fraction
import json


UNIT = "EL-C-HEADROOM-1"
PLAN = ".planning/rd-el-scenario-comparison.md"
PREDECESSOR = "9b9f50ff278aaccedf41f229b7c9a4f206dcac30"


def execute(args, source, started):
    from research import intraday_campaign_wsl_bridge as bridge
    from research import intraday_campaign_wsl_transport as wire
    from research import experiment_log
    from research import intraday_event_dataset_run as run
    from research import intraday_event_fold_artifacts as artifacts
    from research import intraday_event_headroom as headroom
    from research import intraday_event_headroom_reference as reference
    from research import intraday_event_model_contract as contract
    from research import intraday_event_model_inputs as inputs
    from research import intraday_event_model_launch as launch
    from research import intraday_event_model_receipts as receipts
    from research.intraday_event_headroom import require

    run.STARTED_CLOCK = started
    run.budget()
    runtime = run.runtime_pin()
    require(args.mode in ("headroom", "headroom-audit")
            and all(getattr(args, name) is None for name in ("kind", "scope", "fold", "shard", "attempt"))
            and wire.valid_sha(args.summary_sha256) and wire.valid_sha(args.dataset_binding_sha256),
            "Headroom accepts only exact predecessor hashes and its fixed scope")
    root = run.NATIVE / "output"
    original = root / f"{contract.UNIT}-summary"
    output = root / UNIT
    identity = {"id": UNIT, "source": source, "merge": source["merge"], "invented": False,
                "runtime_sha256": runtime, "registration_sha256": source["sources"][PLAN],
                "summary_sha256": args.summary_sha256, "dataset_binding_sha256": args.dataset_binding_sha256}

    bridge.restrict_local_network()
    wire.prohibit_ef_tasks()
    with run._locks():
        report_path = output / "report.json"
        journal = run.NATIVE / "canonical" / "experiments.jsonl"
        if args.mode == "headroom":
            wire.exclusive_directory(output)
            wire.exclusive_bytes(output / "start.json", wire.encode(identity))
        else:
            require(run._doc(output / "start.json") == identity, "Headroom audit start identity differs")
            payload = run._doc(report_path)
            completion = run._doc(output / "completion.json")
            result = completion["result"]
            span = completion["journal"]
            encoded = wire.encode(payload)
            require(payload["identity"] == completion["identity"] == identity
                    and result == {"report_sha256": wire.digest(encoded), "report_bytes": len(encoded),
                                   "resources": payload["resources"]}
                    and run._proof(identity, result, span["start"], span["end"], "completed", unit=UNIT) == span,
                    "Headroom production report or terminal journal differs")
            run.audit_budget(payload["resources"])
            wire.exclusive_bytes(output / "audit-start.json", wire.encode(identity))

        def evaluate():
            raw = run._read(original / "report.json", artifacts.MAX_DOCUMENT)
            require(wire.digest(raw) == args.summary_sha256 and run._doc(original / "completion.json") == {
                "report_sha256": args.summary_sha256, "fold_audit_count": len(contract.ROSTER)},
                "Original sealed EL summary differs")
            summary = json.loads(raw)
            release = summary["release"]
            require(release["merge"] == PREDECESSOR and release["runtime_sha256"] == runtime
                and all(source["sources"].get(name) == pin for name, pin in release["sources"].items()
                        if name != "python/research/intraday_event_model_run.py")
                and run._doc(original / "start.json") == {
                name: summary[name] for name in ("release", "inputs_sha256", "case_report_sha256")},
                "Original model release differs")
            binding = contract.validate_dataset_binding(run._doc(run.NATIVE / "canonical" /
                f"{contract.UNIT}-dataset-binding.json"), args.dataset_binding_sha256)
            require(binding.inputs_sha256 == summary["inputs_sha256"], "Original model and dataset bindings differ")
            shards = launch._admit_actual(binding)
            report = summary["report"]
            calendar = report["evaluation_sessions"]
            require(len(calendar) == contract.FOLDS[-1][2] - contract.FOLDS[0][1], "Evaluation calendar differs")
            days = set(calendar)
            first_test = contract.FOLDS[0][1]
            selected_shards = [(path, row["dataset"]) for path, row, _ in shards
                               if row["dataset"]["session_indices"][1] > first_test]

            def query_rows():
                for path, manifest in selected_shards:
                    for row in inputs.query_rows(path, manifest):
                        if (row["session"] in days and row["query_kind"] == "entry"
                                and row["frame"]["family"] == headroom.FAMILY and row["frame"]["state"] == "CONFIRMED"):
                            yield row

            decision_path = output / "causal-decisions.json"
            decisions = {}
            if args.mode == "headroom":
                for row in query_rows():
                    require(row["key"] not in decisions, "Duplicate confirmation key")
                    decisions[row["key"]] = headroom.decision(row)
                decision_raw = wire.encode(decisions)
                require(len(decision_raw) <= run.MAX_REPORT, "Headroom causal decisions exceed registered bound")
                wire.exclusive_bytes(decision_path, decision_raw)
            else:
                decision_raw = run._read(decision_path, run.MAX_REPORT)
                decisions = json.loads(decision_raw)
                require(type(decisions) is dict and wire.encode(decisions) == decision_raw
                        and payload["decisions_sha256"] == wire.digest(decision_raw), "Headroom causal decision seal differs")
                pullbacks = {}
                for path, manifest in selected_shards:
                    for row in inputs._rows(path, manifest, "event-states.jsonl", "event_state"):
                        frame = row["frame"]
                        if row["session"] in days and frame["family"] == headroom.FAMILY and frame["kind"] == "first_pullback":
                            event = frame["event_id"]
                            require(event not in pullbacks, "Duplicate archived first pullback")
                            pullbacks[event] = row
                seen = set()
                for row in query_rows():
                    frame = row["frame"]
                    event, clock = frame["event_id"], frame["decision_offset"]
                    key, prior = f"{event}@{clock}", pullbacks.get(event)
                    saved, label = decisions[key], row["label_query"]
                    require(key == row["key"] and key not in seen and prior is not None
                            and (prior["session"], prior["symbol"], prior["frame"]["overlap_group_id"])
                            == (row["session"], row["symbol"], frame["overlap_group_id"])
                            and prior["frame"]["offset"] == frame["conditions"]["pullback_at"]
                            and prior["frame"]["decision_offset"] <= clock
                            and (saved["event_id"], saved["session"], saved["symbol"], saved["cluster_id"], saved["decision_offset"])
                            == (event, row["session"], row["symbol"], frame["overlap_group_id"], clock)
                            and Fraction(saved["peak"]) == Fraction(prior["frame"]["level"]) == Fraction(frame["level"])
                            and saved["reference"] == row["reference_close"] == label["reference_close"]
                            and label["decision_offset"] == clock,
                            "Saved decision differs from independently matched first pullback or query")
                    px, high = saved["reference"], Fraction(prior["frame"]["level"])
                    verified = "unknown" if px is None else "pass" if high * 250 >= Fraction(px) * 251 else "reject"
                    require(saved["status"] == verified, "Saved decision differs from independent predicate")
                    seen.add(key)
                require(seen == decisions.keys(), "Causal decision roster differs")

            decision_sha = wire.digest(decision_raw)
            _, invented_binding, _ = launch._invented_inputs(release)
            kind, scope = headroom.ARM[:2]
            fold_support = [row for row in report["fold_support"] if (row["kind"], row["scope"]) == (kind, scope)]
            require([row["fold"] for row in fold_support] == list(range(len(contract.FOLDS)))
                    and all(row["policy_support"]["holding"] is True for row in fold_support),
                    "All predecessor folds must support the original adaptive exit")
            qualification = launch._qualification_identity(release, (kind, scope, 3), invented_binding)
            coordinates = [row for row in summary["fold_audits"] if tuple(row["coordinate"][:2]) == (kind, scope)]
            require([row["coordinate"][2] for row in coordinates] == list(range(len(contract.FOLDS))),
                    "Predecessor fold roster differs")

            def ledger_rows():
                for saved in coordinates:
                    coords = tuple(saved["coordinate"])
                    old = launch._identity(release, coords, False, binding.inputs_sha256, qualification,
                                           summary["case_report_sha256"])
                    path, old, publication, audit_pin = receipts.audited_actual(old)
                    manifest = publication["outcome"]["manifest"]
                    require((audit_pin, manifest["sha256"]) == (saved["audit_sha256"], saved["manifest_sha256"]),
                            "Predecessor fold audit or manifest differs")
                    pin = artifacts.read_manifest(path, manifest, old)["files"]["ledger.jsonl"]
                    _, start, end = contract.FOLDS[coords[2]]
                    fold_days = set(calendar[start - first_test:end - first_test])
                    for row in artifacts._read_rows(path, pin):
                        if row["family"] == headroom.FAMILY and tuple(row[name] for name in headroom.ARM_FIELDS) == headroom.ARM:
                            require(row["session"] in fold_days, "Predecessor ledger escaped its fold")
                            yield row

            if args.mode == "headroom":
                totals = headroom.summarize(calendar, decisions, ledger_rows())
                causal_counts = dict(Counter(row["status"] for row in decisions.values()))
                [original_arm] = [row for row in report["cash"]["groups"] if row["dimension"] == "family"
                                  and row["value"] == headroom.FAMILY
                                  and tuple(row[name] for name in headroom.ARM_FIELDS) == headroom.ARM]
                original_checks = {"commits": "commits", "fills": "fills", "unknown_cash": "unknown_cash_rows",
                                   "net_cash": "known_subset_net_cash", "gross_cash": "known_subset_gross_cash",
                                   "entry_notional": "known_fill_notional"}
                require(totals["opportunities"] == report["original_episodes_by_family"][headroom.FAMILY]
                        and all(Fraction(totals["baseline"][new]) == Fraction(original_arm[old])
                                for new, old in original_checks.items()),
                        "Unfiltered comparison does not reproduce the predecessor")
                measured = run.usage()
                raw_report = wire.encode({"identity": identity, "decisions_sha256": decision_sha,
                                          "causal_confirmation_counts": causal_counts, "totals": totals, "resources": measured})
                wire.exclusive_bytes(report_path, raw_report)
                return {"report_sha256": wire.digest(raw_report), "report_bytes": len(raw_report), "resources": measured}

            require(payload["decisions_sha256"] == decision_sha, "Headroom outcome binding differs")
            require(payload["causal_confirmation_counts"] == dict(Counter(row["status"] for row in decisions.values())),
                    "Headroom causal coverage counts differ")
            reference.verify(calendar, decisions, ledger_rows(), payload)
            measured = run.usage()
            run.verify_usage(payload["resources"], measured)
            proof = {"identity": identity, "audit": "producer_independent", "report_sha256": wire.digest(wire.encode(payload)),
                     "resources": measured}
            wire.exclusive_bytes(output / "audit.json", wire.encode(proof))
            return None

        try:
            if args.mode == "headroom":
                start = wire.regular(journal).st_size
                terminal = experiment_log.run_discovery_trial(study_id=UNIT,
                    specification_sha256=identity["registration_sha256"], parameters=identity, window=run.WINDOW,
                    evaluate=evaluate, runs_path=journal, code_version_provider=lambda: identity["merge"])
                result = terminal["result"]
                span = run._proof(identity, result, start, wire.regular(journal).st_size, "completed", unit=UNIT)
                wire.exclusive_bytes(output / "completion.json", wire.encode({"identity": identity, "journal": span, "result": result}))
            else:
                evaluate()
        except Exception as error:
            wire.exclusive_bytes(output / f"{args.mode}-failure.json", wire.encode({"identity": identity,
                "error_type": type(error).__name__}))
            raise

    print(f"{UNIT} {args.mode} complete")
