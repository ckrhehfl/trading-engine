"""One registered saved-only ED audit repair; importing performs no IO.

Actual execution only reads externally pinned failed evidence. The original
producer and journal remain unchanged; only invented profiles create fixtures.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sys

from research import intraday_ed_disclosure_unit as original
from research import experiment_log
from research.research_budget import BudgetError, ResearchBudget

bridge, wire = original.bridge, original.wire
SPEC = "configs/research/discovery/intraday-ed-disclosure-saved-repair-v1.json"
SOURCE_NAMES = tuple(sorted((*original.SOURCE_NAMES, SPEC,
    "python/research/intraday_ed_disclosure_repair.py",
    "python/tests/test_intraday_ed_disclosure_repair.py")))
REMOTE, COLLECTOR, EVIDENCE = original.REMOTE, original.COLLECTOR, original.EVIDENCE
ACTUAL_OUTPUT = EVIDENCE / "intraday-ed-disclosure-saved-repair-20261031-v1"
ORIGIN_OUTPUT = original.ACTUAL_OUTPUT
CONTROLLERS = {"var/ed-disclosure-repair-launch.py", "var/ed-inventory-runtime.py"}
LIMITS, GEOMETRY, RUNTIME = original.LIMITS, original.GEOMETRY, original.RUNTIME
MAX_FILE, MAX_PUBLICATION, MAX_SPAN = original.MAX_FILE, original.MAX_PUBLICATION, original.MAX_SPAN
BASE_ARTIFACTS = {"registration.json", "source-manifest.json", "scope.json", "report.json",
                  "canonical-failure-span.jsonl", "publication-failure.json"}


def runtime_contract() -> dict:
    return {**original.runtime_contract(), "source": "python/research/intraday_ed_disclosure_repair.py",
        "tests": "python/tests/test_intraday_ed_disclosure_repair.py", "source_names": list(SOURCE_NAMES),
        "network_requests": 0, "producer_calls_actual": 0, "original_evidence_unchanged": True}


def source_bundle(root: Path) -> dict[str, bytes]:
    return {name: bridge.bounded_read(root / name, MAX_FILE) for name in SOURCE_NAMES}


def validate_origin(value: dict, *, ready: bool) -> None:
    wire.require(isinstance(value, dict) and set(value) == {"directory", "merge_commit", "reviewed_commit",
        "tree", "source_hashes", "controller_sources", "artifacts", "run_id", "canonical_offsets",
        "runtime_sha256", "origin_manifest_sha256"},
        "origin fields differ")
    wire.require(value["directory"] == str(ORIGIN_OUTPUT), "fixed failed origin differs")
    for key in ("runtime_sha256", "origin_manifest_sha256"):
        wire.require((value[key] is None and not ready) or wire.valid_sha(value[key]), "origin external pin differs")
    wire.require(value["runtime_sha256"] in (None, RUNTIME), "original runtime differs")
    for key in ("merge_commit", "reviewed_commit", "tree"):
        item = value[key]
        wire.require((item is None and not ready) or
                     (type(item) is str and re.fullmatch("[0-9a-f]{40}", item)), "origin Git pin differs")
    for key, names in (("source_hashes", set(original.SOURCE_NAMES)),
                       ("controller_sources", {"var/ed-disclosure-launch.py", "var/ed-inventory-runtime.py"})):
        item = value[key]
        wire.require((item is None and not ready) or (isinstance(item, dict) and set(item) == names
                     and all(wire.valid_sha(pin) for pin in item.values())), "origin source closure differs")
    artifacts = value["artifacts"]
    wire.require((artifacts is None and not ready) or (isinstance(artifacts, dict)
        and BASE_ARTIFACTS <= set(artifacts) and len(artifacts) <= 222
        and all(wire.valid_sha(pin) for pin in artifacts.values())), "origin artifact pins incomplete")
    if artifacts is not None:
        for name in artifacts:
            original.artifact_name(name)
            wire.require(name in BASE_ARTIFACTS or re.fullmatch(
                r"attempt-\d{3}-(?:attempt\.json|response\.json|raw\.html|page\.json)", name),
                "unexpected original artifact")
    wire.require((value["run_id"] is None and not ready) or (type(value["run_id"]) is str
        and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["run_id"])),
        "origin run identity differs")
    offsets = value["canonical_offsets"]
    # The failed unit retained a pinned span, but published no journal offsets.
    wire.require(offsets is None or (type(offsets) is list and len(offsets) == 2
        and all(type(n) is int for n in offsets) and 0 <= offsets[0] < offsets[1]
        and offsets[1] - offsets[0] <= MAX_SPAN), "origin canonical offsets differ")


def contract(raw: bytes, *, actual: bool = False) -> dict:
    spec = json.loads(raw)
    wire.require(spec["id"] == "intraday-ed-disclosure-saved-repair-v1" and spec["campaign"] == "ED"
        and spec["window"] == {"symbol": "KRX:*", "interval": "filing_metadata",
            "start": "2025-09-04", "end": "2026-09-23", "timezone": "Asia/Seoul"}
        and spec["network_requests"] == spec["producer_calls_actual"] == spec["credential_reads"] == 0
        and spec["original_evidence_unchanged"] is True and spec["collector_changes"] is False
        and spec["new_forward_access"] is False and spec["promotion_allowed"] is False
        and spec["maximum_saved_attempts"] == 54 and spec["maximum_raw_bytes"] == 54 * wire.MIB
        and spec["public_runtime"] == runtime_contract(), "saved repair contract differs")
    wire.require(type(spec["actual_runner_ready"]) is bool, "explicit readiness required")
    execution = spec["execution"]
    wire.require(execution == {"resource_profile": "ec-ordinary300-v1", "cpu_seconds": 300,
        "address_space_bytes": 512 * wire.MIB, "wall_seconds": 1200, "nice": 10,
        "concurrent_units": 1, "automatic_actual_retries": 0,
        "canonical_journal": str(COLLECTOR / "runs/experiments.jsonl"),
        "final_runtime_binding_before_actual": True}, "ordinary execution differs")
    validate_origin(spec["origin"], ready=spec["actual_runner_ready"] or actual)
    wire.require(not actual or spec["actual_runner_ready"] is True, "actual origin not registered ready")
    return spec


def validate_qualification(value: dict) -> None:
    wire.require(isinstance(value, dict) and set(value) == {"remote_output", "remote_publication_sha256"}
        and wire.valid_sha(value["remote_publication_sha256"]), "repair profile qualification differs")
    path = Path(value["remote_output"])
    wire.require(path.parent == EVIDENCE and re.fullmatch(
        r"intraday-ed-disclosure-saved-repair-profile-v[1-9][0-9]*", path.name), "repair profile path differs")


def validate_config(config: dict) -> None:
    wire.require(type(config.get("invented_only")) is bool, "invented flag required")
    wire.require(set(config) == {"invented_only", "output", "merge_commit", "reviewed_commit", "tree",
        "source_hashes", "controller_sources", "attempt", "deadline_epoch"}
        | (set() if config["invented_only"] else {"qualification"}), "repair configuration fields differ")
    for key in ("merge_commit", "reviewed_commit", "tree"):
        wire.require(type(config[key]) is str and re.fullmatch("[0-9a-f]{40}", config[key]), "Git identity differs")
    wire.require(isinstance(config["source_hashes"], dict) and set(config["source_hashes"]) == set(SOURCE_NAMES)
        and all(wire.valid_sha(pin) for pin in config["source_hashes"].values()), "repair source closure differs")
    wire.require(isinstance(config["controller_sources"], dict) and set(config["controller_sources"]) == CONTROLLERS
        and all(wire.valid_sha(pin) for pin in config["controller_sources"].values()), "repair controllers differ")
    wire.require(type(config["deadline_epoch"]) in (int, float)
        and math.isfinite(config["deadline_epoch"]), "fixed deadline required")
    out = Path(config["output"])
    wire.require(out.parent == EVIDENCE and (bool(re.fullmatch(
        r"intraday-ed-disclosure-saved-repair-profile-v[1-9][0-9]*", out.name))
        if config["invented_only"] else out == ACTUAL_OUTPUT), "fixed repair output differs")
    wire.require(config["attempt"] == "ED-disclosure-saved-repair-" +
        (out.name.rsplit("-", 1)[1] if config["invented_only"] else "actual-v1"), "repair attempt differs")
    if not config["invented_only"]:
        validate_qualification(config["qualification"])


def verify_sources(config: dict, root: Path, helpers) -> None:
    wire.require({name: wire.file_digest(root / name, MAX_FILE, lambda _: None)[0]
        for name in SOURCE_NAMES} == config["source_hashes"], "repair source bytes differ")
    # The existing helper supplies the reviewed, clean normal-merge proof.
    original.verify_sources({**config, "source_hashes": {name: config["source_hashes"][name]
        for name in original.SOURCE_NAMES}}, root, helpers)


def invented_listing(code: str, page: int) -> bytes:
    raw = original.invented_listing(code, page)
    start = raw.index(b"<!--")
    # Same byte ceiling; the nested pagination previously defeated the audit.
    wrapped = b"<div class='outer'><div>" + raw[:start] + b"</div></div>"
    return wrapped + b"<!--" + b" " * (wire.MIB - len(wrapped) - 7) + b"-->"


def invented_origin(config: dict, put, checkpoint) -> dict:
    """Trusted fixture generation only; never called by the actual branch."""
    from research.intraday_ed_disclosure_acquisition import HttpResponse, run_listing_acquisition
    old = {**config, "invented_only": True, "output": str(EVIDENCE / "intraday-ed-disclosure-profile-v999999"),
        "attempt": "ED-disclosure-v999999", "source_hashes": {name: wire.digest(b"invented-old:" + name.encode())
        for name in original.SOURCE_NAMES}, "controller_sources": {name: wire.digest(name.encode())
        for name in ("var/ed-disclosure-launch.py", "var/ed-inventory-runtime.py")}}
    old.pop("qualification", None)
    old.update(merge_commit="1" * 40, reviewed_commit="2" * 40, tree="3" * 40)
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    def clock():
        nonlocal now
        now += timedelta(seconds=2)
        return now
    artifacts = {}
    def save(name, raw):
        put("origin-" + name, raw)
        artifacts[name] = wire.digest(raw)
    save("registration.json", wire.encode(old))
    save("source-manifest.json", wire.encode(old["source_hashes"]))
    run_listing_acquisition([f"{n:06d}" for n in range(1, GEOMETRY["codes"] + 1)], write_new=save,
        transport=lambda query: HttpResponse(200, "https://dart.fss.or.kr/dsab001/search.ax",
            invented_listing(query.query_code, query.page)), clock=clock, sleep=lambda _: None,
        checkpoint=checkpoint)
    record = {"run_id": "00000000-0000-0000-0000-000000000001", "study_id": "intraday-ed-disclosure-support-v1",
        "specification_sha256": old["source_hashes"][original.SPEC], "code_version": old["merge_commit"],
        "parameters": {"invented_only": True, "source_hashes": old["source_hashes"],
            "controller_sources": old["controller_sources"], "public_runtime": original.runtime_contract()}}
    span = wire.encode({**record, "status": "started"}) + wire.encode({**record, "status": "failed",
        "error_type": "AcquisitionRefusal"})
    save("canonical-failure-span.jsonl", span)
    save("publication-failure.json", wire.encode({"status": "failed", "error_type": "AcquisitionRefusal",
        "automatic_retries": 0, "partial_evidence_preserved": True, "unresolved_marker_retained": True}))
    return {"directory": str(ORIGIN_OUTPUT), **{key: old[key] for key in
        ("merge_commit", "reviewed_commit", "tree", "source_hashes", "controller_sources")},
        "artifacts": artifacts, "run_id": record["run_id"], "canonical_offsets": None,
        "runtime_sha256": RUNTIME, "origin_manifest_sha256": wire.digest(b"invented failure manifest")}


def verify_origin(origin: dict, read, checkpoint, *, invented: bool) -> dict:
    """Check external pins before independent raw reconstruction, without writes."""
    from research.intraday_ed_disclosure_acquisition import verify_saved_acquisition
    validate_origin(origin, ready=True)
    def pinned(name):
        original.artifact_name(name)
        wire.require(name in origin["artifacts"], "original artifact not externally pinned")
        checkpoint()
        raw = read(name)
        wire.require(type(raw) is bytes and len(raw) <= MAX_FILE
            and wire.digest(raw) == origin["artifacts"][name], "original artifact hash differs")
        return raw
    for name in origin["artifacts"]:
        pinned(name)
    registration = json.loads(pinned("registration.json"))
    original.validate_config(registration)
    wire.require(registration["invented_only"] is invented and all(registration[key] == origin[key]
        for key in ("merge_commit", "reviewed_commit", "tree", "source_hashes", "controller_sources"))
        and (invented or registration["output"] == origin["directory"]), "original registration differs")
    wire.require(json.loads(pinned("source-manifest.json")) == origin["source_hashes"], "original source manifest differs")
    span = pinned("canonical-failure-span.jsonl")
    records = [json.loads(line) for line in span.splitlines()]
    offsets = origin["canonical_offsets"]
    wire.require((offsets is None or offsets[1] - offsets[0] == len(span))
        and len(span) <= MAX_SPAN and len(records) == 2 and [r["status"] for r in records] == ["started", "failed"]
        and all(r["run_id"] == origin["run_id"] and r["code_version"] == origin["merge_commit"]
            and r["study_id"] == "intraday-ed-disclosure-support-v1"
            and r["specification_sha256"] == origin["source_hashes"][original.SPEC]
            and r["parameters"]["source_hashes"] == origin["source_hashes"]
            and r["parameters"]["controller_sources"] == origin["controller_sources"]
            and r["parameters"]["invented_only"] is invented for r in records), "original canonical failure differs")
    failure = json.loads(pinned("publication-failure.json"))
    wire.require(failure == {"status": "failed", "error_type": "AcquisitionRefusal", "automatic_retries": 0,
        "partial_evidence_preserved": True, "unresolved_marker_retained": True}
        and records[1]["error_type"] == "AcquisitionRefusal", "original failed publication differs")
    report = json.loads(pinned("report.json"))
    expected = BASE_ARTIFACTS | {prefix + suffix for prefix in report["attempts"]
        for suffix in ("-attempt.json", "-response.json", "-raw.html", "-page.json")}
    wire.require(set(origin["artifacts"]) == expected, "original artifact closure differs")
    verified = verify_saved_acquisition(pinned)
    wire.require(verified["verified"] is True and verified["state"] == "complete", "saved acquisition incomplete")
    if invented:
        original.check_profile_geometry(verified)
        codes = [f"{n:06d}" for n in range(1, GEOMETRY["codes"] + 1)]
        wire.require(json.loads(pinned("scope.json"))["codes"] == codes, "invented code scope differs")
        pairs, raw_bytes = [], 0
        for prefix in report["attempts"]:
            query = json.loads(pinned(prefix + "-attempt.json"))["request"]
            raw = pinned(prefix + "-raw.html")
            wire.require(raw == invented_listing(query["query_code"], query["page"]), "invented raw fixture differs")
            pairs.append((query["query_code"], query["page"]))
            raw_bytes += len(raw)
        wire.require(pairs == [(code, page) for code in codes for page in range(1, GEOMETRY["pages_per_code"] + 1)]
            and raw_bytes == GEOMETRY["maximum_raw_bytes"], "invented whole geometry differs")
    return verified


def qualify_actual(config: dict, checkpoint) -> None:
    value = config["qualification"]
    directory = Path(value["remote_output"])
    raw = bridge.bounded_read(directory / "publication.json", MAX_PUBLICATION)
    wire.require(wire.digest(raw) == value["remote_publication_sha256"], "repair profile publication pin differs")
    publication = json.loads(raw)
    wire.require(publication["status"] == "completed" and publication["invented_only"] is True
        and all(publication[key] == config[key] for key in
            ("source_hashes", "controller_sources", "merge_commit", "tree"))
        and publication["runtime_sha256"] == RUNTIME and publication["geometry"] == GEOMETRY
        and publication["verification_complete"] is True and publication["automatic_retries"] == 0
        and publication["collector_before"] == publication["collector_after"]
        == {"head": original.COLLECTOR_COMMIT, "dirty": ""}, "same-source repair whole profile required")
    original.validate_budget(publication["budget"])
    original.validate_helpers(publication["git_helpers"])
    wire.require(isinstance(publication["artifacts"], dict) and len(publication["artifacts"]) <= 229,
        "repair profile artifact ceiling exceeded")
    def read(name):
        original.artifact_name(name)
        wire.require(name in publication["artifacts"], "repair profile artifact not pinned")
        checkpoint()
        result = bridge.bounded_read(directory / name, MAX_FILE)
        wire.require(wire.digest(result) == publication["artifacts"][name], "repair profile artifact hash differs")
        return result
    for name in publication["artifacts"]:
        read(name)
    wire.require({"registration.json", "source-manifest.json", "origin-manifest.json", "verification.json",
        "report.json", "canonical-span.jsonl", "budget.json"} <= set(publication["artifacts"]),
        "repair profile receipt set incomplete")
    registration = json.loads(read("registration.json"))
    validate_config(registration)
    wire.require(registration["invented_only"] is True and registration["output"] == str(directory)
        and all(registration[key] == config[key] for key in
            ("source_hashes", "controller_sources", "merge_commit", "reviewed_commit", "tree"))
        and json.loads(read("source-manifest.json")) == config["source_hashes"]
        and json.loads(read("budget.json")) == publication["budget"], "repair profile registration differs")
    origin = json.loads(read("origin-manifest.json"))
    wire.require(set(publication["artifacts"]) == {"registration.json", "source-manifest.json",
        "origin-manifest.json", "verification.json", "report.json", "canonical-span.jsonl", "budget.json"}
        | {"origin-" + name for name in origin["artifacts"]}, "repair profile artifact closure differs")
    verified = verify_origin(origin, lambda name: read("origin-" + name), checkpoint, invented=True)
    wire.require(verified == json.loads(read("verification.json")) == publication["verified_support"],
        "repair profile reconstruction differs")
    report = json.loads(read("report.json"))
    wire.require(report == repair_report(origin, verified), "repair profile report differs")
    records = [json.loads(line) for line in read("canonical-span.jsonl").splitlines()]
    wire.require(len(records) == 2 and [r["status"] for r in records] == ["started", "completed"]
        and all(r["run_id"] == publication["run_id"] and r["code_version"] == config["merge_commit"]
            and r["specification_sha256"] == config["source_hashes"][SPEC]
            and r["parameters"]["source_hashes"] == config["source_hashes"]
            and r["parameters"]["controller_sources"] == config["controller_sources"] for r in records)
        and records[1]["result"]["report_sha256"] == publication["artifacts"]["report.json"]
        and records[1]["result"]["verified_support"] == verified, "repair profile canonical completion differs")


def repair_report(origin: dict, verified: dict) -> dict:
    return {"schema": "ed-disclosure-saved-repair-v1", "origin_directory": origin["directory"],
        "origin_run_id": origin["run_id"], "origin_status": "failed", "original_journal_unchanged": True,
        "producer_report_sha256": origin["artifacts"]["report.json"], "verified_support": verified,
        "network_requests": 0, "producer_calls": 0, "new_performance_evaluations": 0,
        "source_event_at": None, "available_at": None, "historical_completeness_certified": False,
        "historical_issuer_binding_certified": False,
        "claim": "saved raw independently agrees with original producer projection; current query support only"}


def run_unit(config: dict) -> dict:
    validate_config(config)
    root = Path(__file__).resolve().parents[2]
    wire.require(root == REMOTE, "fixed isolated research checkout required")
    budget = ResearchBudget(**LIMITS["worker"])
    original.fixed_environment()
    wire.fixed_deadline(config["deadline_epoch"])
    sources = source_bundle(root)
    spec = contract(sources[SPEC], actual=not config["invented_only"])
    original.contract(sources[original.SPEC])
    helpers = bridge.GitHelpers(root, "coordinator")
    verify_sources(config, root, helpers)
    def collector_state():
        helpers.root = COLLECTOR
        try:
            return {"head": helpers.git("rev-parse", "HEAD"),
                "dirty": helpers.git("status", "--porcelain", "--untracked-files=no")}
        finally:
            helpers.root = root
    before = collector_state()
    wire.require(before == {"head": original.COLLECTOR_COMMIT, "dirty": ""}, "collector source differs")
    invented = config["invented_only"]
    out = Path(config["output"])
    wire.native_cache(out, root)
    bridge.require_free_disk(out.parent)
    checkpoint = lambda: budget.checkpoint("disclosure_saved_repair")
    if not invented:
        qualify_actual(config, checkpoint)
    marker = EVIDENCE / "intraday-ed-disclosure-saved-repair-unresolved.json"
    log = out / "profile-experiments.jsonl" if invented else COLLECTOR / "runs/experiments.jsonl"
    lock = EVIDENCE / "invented-research.lock" if invented else COLLECTOR / "runs/intraday-research.lock"
    with wire.research_locks([EVIDENCE / "ed-campaign.lock", lock], marker, config["attempt"]):
        wire.exclusive_directory(out)
        artifacts = {}
        def put(name, raw):
            original.artifact_name(name)
            wire.require(type(raw) is bytes and len(raw) <= MAX_FILE, "artifact bytes exceeded")
            wire.exclusive_bytes(out / name, raw)
            artifacts[name] = wire.digest(raw)
        position = None
        try:
            put("registration.json", wire.encode(config))
            put("source-manifest.json", wire.encode(config["source_hashes"]))
            position = wire.regular(log).st_size if log.exists() else 0
            def evaluate():
                origin = (invented_origin(config, put, checkpoint) if invented else spec["origin"])
                put("origin-manifest.json", wire.encode(origin))
                read = (lambda name: bridge.bounded_read(out / ("origin-" + name), MAX_FILE)) if invented else (
                    lambda name: bridge.bounded_read(Path(origin["directory"]) / name, MAX_FILE))
                verified = verify_origin(origin, read, checkpoint, invented=invented)
                put("verification.json", wire.encode(verified))
                put("report.json", wire.encode(repair_report(origin, verified)))
                checkpoint()
                return {"report_sha256": artifacts["report.json"], "verification_complete": True,
                    "verified_support": verified, "network_requests": 0, "new_performance_evaluations": 0,
                    "credential_reads": 0}
            record = experiment_log.run_discovery_trial(study_id=spec["id"],
                specification_sha256=config["source_hashes"][SPEC], parameters={"invented_only": invented,
                    "source_hashes": config["source_hashes"], "controller_sources": config["controller_sources"],
                    "public_runtime": runtime_contract()},
                window={key: spec["window"][key] for key in ("symbol", "interval", "start", "end")},
                evaluate=evaluate, runs_path=log, code_version_provider=lambda: helpers.git("rev-parse", "HEAD"))
            span = original.journal_span(log, position, record["run_id"])
            wire.require(record["code_version"] == config["merge_commit"], "repair journal source differs")
            put("canonical-span.jsonl", span)
            verify_sources(config, root, helpers)
            wire.require(collector_state() == before, "collector source changed")
            original.validate_helpers(helpers.receipt)
            for name, expected in artifacts.items():
                wire.require(wire.file_digest(out / name, MAX_FILE, lambda _: checkpoint())[0] == expected,
                    "stored repair artifact differs")
            receipt = budget.finish()
            original.validate_budget(receipt)
            put("budget.json", wire.encode(receipt))
            publication = {"status": "completed", "invented_only": invented, "run_id": record["run_id"],
                "source_hashes": config["source_hashes"], "controller_sources": config["controller_sources"],
                "runtime_sha256": RUNTIME, "geometry": GEOMETRY if invented else None,
                "merge_commit": config["merge_commit"], "tree": config["tree"], "verification_complete": True,
                "verified_support": record["result"]["verified_support"], "collector_before": before,
                "collector_after": before, "budget": receipt, "git_helpers": helpers.receipt,
                "artifacts": artifacts, "canonical_offsets": [position, position + len(span)], "automatic_retries": 0}
            raw = wire.encode(publication)
            wire.exclusive_bytes(out / "publication.json", raw)
            wire.require(bridge.bounded_read(out / "publication.json", MAX_PUBLICATION) == raw,
                "repair publication readback differs")
            wire.clear_terminal_marker(marker, config["attempt"], record)
            return {key: publication[key] for key in ("status", "invented_only", "run_id", "source_hashes",
                "controller_sources", "runtime_sha256", "geometry", "verification_complete", "verified_support",
                "budget", "git_helpers")} | {"remote_output": str(out), "remote_publication_sha256": wire.digest(raw),
                "report_sha256": artifacts["report.json"]}
        except BaseException as error:
            failure = {"status": "failed", "error_type": type(error).__name__, "automatic_retries": 0,
                "partial_evidence_preserved": True, "unresolved_marker_retained": True}
            if isinstance(error, BudgetError):
                failure["budget"] = error.receipt
            if position is not None and log.exists():
                with log.open("rb") as stream:
                    stream.seek(position)
                    partial = stream.read(MAX_SPAN + 1)
                if len(partial) <= MAX_SPAN:
                    put("canonical-failure-span.jsonl", partial)
            put("publication-failure.json", wire.encode(failure))
            raise


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--config-base64")
    parser.add_argument("--expected-config-sha256")
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    if args.validate:
        wire.require(args.config_base64 is None and args.expected_config_sha256 is None,
            "validate has no execution configuration")
        sources = source_bundle(Path(__file__).resolve().parents[2])
        contract(sources[SPEC])
        original.contract(sources[original.SPEC])
        result = {"status": "source_only_validated", "source_hashes": {name: wire.digest(raw)
            for name, raw in sources.items()}, "public_runtime": runtime_contract(), "actual_accesses": 0}
    else:
        wire.require(type(args.config_base64) is str and len(args.config_base64) <= 131072
            and wire.valid_sha(args.expected_config_sha256), "bounded pinned configuration required")
        raw = base64.b64decode(args.config_base64, validate=True)
        wire.require(wire.digest(raw) == args.expected_config_sha256, "configuration pin differs")
        config = json.loads(raw)
        wire.require(wire.encode(config) == raw, "canonical configuration required")
        result = run_unit(config)
    print(wire.encode(result).decode().strip())


if __name__ == "__main__":
    main()
