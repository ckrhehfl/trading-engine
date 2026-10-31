"""One native ED disclosure unit; importing this module performs no IO.

The private controller supplies a pinned configuration, never executable source.
This entrypoint reuses the discovery journal, native exclusive storage, locks,
budget and bounded Git helper. It neither dispatches SSH nor retries a unit.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.experiment_log import run_discovery_trial
from research.research_budget import BudgetError, ResearchBudget


SPEC = "configs/research/discovery/intraday-ed-disclosure-support-v1.json"
SOURCE_NAMES = tuple(sorted((
    SPEC, "python/research/__init__.py", "python/research/experiment_log.py",
    "python/research/research_budget.py", "python/research/intraday_campaign_wsl_bridge.py",
    "python/research/intraday_campaign_wsl_transport.py",
    "python/research/intraday_ed_disclosures.py",
    "python/tests/test_intraday_ed_disclosures.py",
    "python/research/intraday_ed_disclosure_acquisition.py",
    "python/tests/test_intraday_ed_disclosure_acquisition.py",
    "python/research/intraday_ed_disclosure_unit.py",
    "python/tests/test_intraday_ed_disclosure_unit.py", "python/pyproject.toml", "python/uv.lock",
)))
REMOTE = Path("/home/minjun4897/research-checkouts/activity-discovery-20261005")
COLLECTOR = Path("/home/minjun4897/trading-engine")
COLLECTOR_COMMIT = "4ce85d714890b87f1a57ae89d4942660e41c0483"
EVIDENCE = Path("/home/minjun4897/research-evidence")
INVENTORY = EVIDENCE / "intraday-ed-input-inventory-20261031-v1/inventory.json"
INVENTORY_SHA = "6fdaf6eca605f8c47a7214b0238022bda4ddfe2b5b4add7a4a87297e1b3f1474"
ACTUAL_OUTPUT = EVIDENCE / "intraday-ed-disclosure-support-20261031-v1"
RUNTIME = "e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f"
MAX_FILE = 16 * wire.MIB
MAX_PUBLICATION = 4 * wire.MIB
MAX_SPAN = 2 * wire.MIB
GEOMETRY = {"codes": 18, "pages_per_code": 3, "rows_per_page": 100,
            "requests": 54, "rows": 5400, "maximum_raw_bytes": 54 * wire.MIB}
LIMITS = {
    "worker": {"cpu_seconds": 180, "address_space_bytes": 352 * wire.MIB},
    "remote_git": {"cpu_seconds": 10, "address_space_bytes": 32 * wire.MIB},
    "controller": {"cpu_seconds": 35, "address_space_bytes": 32 * wire.MIB},
    "local_git": {"cpu_seconds": 5, "address_space_bytes": 32 * wire.MIB},
    "ssh_client": {"cpu_seconds": 20, "address_space_bytes": 32 * wire.MIB},
}


def runtime_contract() -> dict:
    return {"source": "python/research/intraday_ed_disclosure_unit.py",
            "tests": "python/tests/test_intraday_ed_disclosure_unit.py",
            "research_root": str(REMOTE), "collector_commit": COLLECTOR_COMMIT,
            "runtime_sha256": RUNTIME, "resource_profile": "ec-ordinary300-v1-subset",
            "role_limits": LIMITS, "hard_cpu_sum_seconds": 250,
            "hard_address_space_sum_bytes": 480 * wire.MIB,
            "unused_coordinator_reserve": {"cpu_seconds": 50, "address_space_bytes": 32 * wire.MIB},
            "source_names": list(SOURCE_NAMES), "geometry": GEOMETRY}


def source_bundle(root: Path) -> dict[str, bytes]:
    """Fixed public roster; no directory traversal or private-file import."""
    return {name: bridge.bounded_read(root / name, MAX_FILE) for name in SOURCE_NAMES}


def contract(raw: bytes, *, actual: bool = False) -> dict:
    spec = json.loads(raw)
    wire.require(spec["id"] == "intraday-ed-disclosure-support-v1" and spec["campaign"] == "ED"
                 and spec["window"] == {"symbol": "KRX:*", "interval": "filing_metadata",
                     "start": "2025-09-04", "end": "2026-09-23", "timezone": "Asia/Seoul"},
                 "disclosure registration identity/window differs")
    wire.require(spec["symbol_source"]["inventory_file_sha256"] == INVENTORY_SHA
                 and spec["symbol_source"]["maximum_symbols"] == 18,
                 "inventory source differs")
    wire.require(spec["bounds"] == {"maximum_requests": 54, "maximum_pages_per_code": 3,
        "page_size": 100, "maximum_rows_per_code": 300, "maximum_response_bytes": wire.MIB,
        "request_timeout_seconds": 20, "minimum_response_to_next_request_seconds": 1.1,
        "automatic_retries": 0, "redirects": False, "concurrent_requests": 1}, "request bounds differ")
    execution = spec["execution"]
    expected = {"resource_profile": "ec-ordinary300-v1", "cpu_seconds": 300,
        "address_space_bytes": 512 * wire.MIB, "wall_seconds": 1200, "nice": 10,
        "concurrent_units": 1, "automatic_actual_retries": 0,
        "canonical_journal": str(COLLECTOR / "runs/experiments.jsonl"),
        "final_runtime_binding_before_actual": True}
    wire.require(all(execution.get(key) == value for key, value in expected.items()),
                 "ordinary execution contract differs")
    wire.require(spec["collector_changes"] is False and spec["credential_reads"] == 0
                 and spec["new_forward_access"] is False and spec["promotion_allowed"] is False,
                 "disclosure scope differs")
    if actual:
        wire.require(spec["actual_runner_ready"] is True
                     and spec.get("public_runtime") == runtime_contract(),
                     "actual public runtime not registered ready")
    return spec


def validate_config(config: dict) -> None:
    keys = {"invented_only", "output", "merge_commit", "reviewed_commit", "tree",
            "source_hashes", "controller_sources", "attempt", "deadline_epoch"}
    wire.require(type(config.get("invented_only")) is bool, "invented flag required")
    wire.require(set(config) == keys | (set() if config["invented_only"] else {"qualification"}),
                 "configuration fields differ")
    for name in ("merge_commit", "reviewed_commit", "tree"):
        wire.require(type(config[name]) is str and re.fullmatch("[0-9a-f]{40}", config[name]),
                     "Git identity differs")
    wire.require(isinstance(config["source_hashes"], dict)
                 and set(config["source_hashes"]) == set(SOURCE_NAMES)
                 and all(wire.valid_sha(value) for value in config["source_hashes"].values()),
                 "fixed source roster/pins differ")
    wire.require(isinstance(config["controller_sources"], dict)
                 and set(config["controller_sources"]) == {
                     "var/ed-disclosure-launch.py", "var/ed-inventory-runtime.py"}
                 and all(wire.valid_sha(value) for value in config["controller_sources"].values()),
                 "controller source roster/pins differ")
    wire.require(type(config["deadline_epoch"]) in (int, float)
                 and math.isfinite(config["deadline_epoch"]), "fixed deadline required")
    out = Path(config["output"])
    wire.require(out.parent == EVIDENCE and (bool(re.fullmatch(
        r"intraday-ed-disclosure-profile-v[1-9][0-9]*", out.name)) if config["invented_only"]
        else out == ACTUAL_OUTPUT), "fixed native output differs")
    expected_attempt = "ED-disclosure-" + (out.name.rsplit("-", 1)[1]
                       if config["invented_only"] else "actual-v1")
    wire.require(config["attempt"] == expected_attempt, "single-attempt identity differs")
    if not config["invented_only"]:
        validate_qualification(config["qualification"])


def validate_qualification(value: dict) -> None:
    wire.require(isinstance(value, dict) and set(value) == {"remote_output", "remote_publication_sha256"}
                 and wire.valid_sha(value["remote_publication_sha256"]), "profile qualification differs")
    path = Path(value["remote_output"])
    wire.require(path.parent == EVIDENCE and re.fullmatch(
        r"intraday-ed-disclosure-profile-v[1-9][0-9]*", path.name), "profile native path differs")


def verify_sources(config: dict, root: Path, helpers: bridge.GitHelpers) -> None:
    wire.require({name: wire.file_digest(root / name, MAX_FILE, lambda _: None)[0]
                  for name in SOURCE_NAMES} == config["source_hashes"], "source bytes differ")
    wire.require(helpers.git("rev-parse", "HEAD") == config["merge_commit"]
                 and helpers.git("rev-parse", "HEAD^{tree}") == config["tree"]
                 and not helpers.git("status", "--porcelain", "--untracked-files=no"),
                 "merged clean source differs")
    parents = helpers.git("show", "-s", "--format=%P", config["merge_commit"]).split()
    wire.require(len(parents) == 2 and parents[1] == config["reviewed_commit"]
                 and helpers.git("rev-parse", config["reviewed_commit"] + "^{tree}") == config["tree"],
                 "normal reviewed same-tree merge required")
    helpers.git("merge-base", "--is-ancestor", config["merge_commit"], "origin/main")


def validate_budget(receipt: dict, role: str = "worker") -> None:
    caps = LIMITS[role]
    wire.require(receipt["status"] == "complete" and receipt["violations"] == []
                 and receipt["scope"] == "single_process_including_threads"
                 and receipt["limits"] == {**caps, "wall_seconds": 1200, "nice": 10}
                 and "operator_cpu_scope" not in receipt
                 and receipt["readback"] == {"rlimit_cpu": [caps["cpu_seconds"]] * 2,
                     "rlimit_as": [caps["address_space_bytes"]] * 2, "nice": 10}, "role budget differs")
    absolute = receipt["absolute"]
    for value, cap in ((absolute["cpu_process_seconds"], caps["cpu_seconds"]),
        (absolute["cpu_user_seconds"] + absolute["cpu_system_seconds"], caps["cpu_seconds"]),
        (receipt["delta"]["wall_seconds"], 1200)):
        wire.require(type(value) in (int, float) and math.isfinite(value) and 0 <= value < cap,
                     "resource exhausted")
    wire.require(0 < absolute["address_space_bytes"] <= caps["address_space_bytes"], "address space exceeded")


def validate_helpers(receipt: dict, role: str = "remote_git") -> None:
    caps = LIMITS[role]
    wire.require(type(receipt["cpu_seconds"]) in (int, float) and math.isfinite(receipt["cpu_seconds"])
                 and 0 <= receipt["cpu_seconds"] < caps["cpu_seconds"]
                 and receipt["count"] == len(receipt["readbacks"]), "Git helper reserve exceeded")
    for item in receipt["readbacks"]:
        wire.require(type(item["cpu_seconds"]) is int and 0 < item["cpu_seconds"] <= caps["cpu_seconds"]
                     and item["address_space_bytes"] == caps["address_space_bytes"], "Git helper cap differs")


def fixed_environment() -> None:
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    Path("/proc/self/oom_score_adj").write_text("1000")
    wire.require(len(os.sched_getaffinity(0)) == 1
                 and Path("/proc/self/oom_score_adj").read_text().strip() == "1000", "CPU/OOM priority differs")
    executable = Path(sys.executable).resolve()
    wire.require(str(executable) == "/usr/bin/python3.12"
                 and wire.file_digest(executable, 32 * wire.MIB, lambda _: None)[0] == RUNTIME,
                 "pinned Python runtime differs")


def inventory_codes(raw: bytes) -> list[str]:
    """Project only fixed saved metadata; no database or price field access."""
    wire.require(len(raw) <= MAX_FILE and wire.digest(raw) == INVENTORY_SHA, "saved inventory pin differs")
    rows = json.loads(raw)["inventory"]["klines"]["rows"]
    symbols = {row["symbol"] for row in rows if row["namespace"] == "KRX"}
    wire.require(all(type(symbol) is str and re.fullmatch("KRX:[0-9]{6}", symbol) for symbol in symbols),
                 "saved symbol projection differs")
    codes = sorted(symbol[4:] for symbol in symbols)
    wire.require(len(codes) == GEOMETRY["codes"], "exact eighteen saved symbols required")
    return codes


def invented_listing(code: str, page: int) -> bytes:
    """Full registered shape, invented IDs only; no real response is a fixture."""
    issuer = f"{int(code):08d}"
    rows = []
    for number in range((page - 1) * 100 + 1, page * 100 + 1):
        receipt = f"20260315{int(code) * 1000 + number:06d}"
        rows.append(f"<tr><td>{number}</td><td><a onclick=\"openCorpInfoNew('{issuer}', 'winCorpInfo', "
            f"'/dsae001/selectPopup.ax');\">발명회사</a></td><td><a href='/dsaf001/main.do?rcpNo={receipt}' "
            f"id='r_{receipt}' onclick=\"openReportViewer('{receipt}','');return false;\">"
            "사업보고서 (2025.12)</a></td><td>제출인</td><td>2026.03.15</td><td></td></tr>")
    raw = (f"<div class='pageInfo'>[{page}/3] [총 300건]</div><table><tbody id='tbody'>"
           + "".join(rows) + "</tbody></table>").encode()
    # Exercise the whole registered raw-byte ceiling without adding HTML nodes.
    return raw + b"<!--" + b" " * (wire.MIB - len(raw) - 7) + b"-->"


def journal_span(log: Path, position: int, run_id: str) -> bytes:
    wire.regular(log)
    with log.open("rb") as stream:
        stream.seek(position)
        raw = stream.read(MAX_SPAN + 1)
    wire.require(len(raw) <= MAX_SPAN, "canonical span exceeded")
    records = [json.loads(line) for line in raw.splitlines()]
    wire.require(len(records) == 2 and [r["status"] for r in records] == ["started", "completed"]
                 and all(r["run_id"] == run_id for r in records), "durable canonical completion differs")
    return raw


def artifact_name(name: str) -> None:
    wire.require(type(name) is str and re.fullmatch("[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", name),
                 "flat exclusive artifact name required")


def qualify_actual(config: dict, checkpoint) -> None:
    """Read the original profile publication and reconstruct saved raw again."""
    from research.intraday_ed_disclosure_acquisition import verify_saved_acquisition
    qualification = config["qualification"]
    directory = Path(qualification["remote_output"])
    raw = bridge.bounded_read(directory / "publication.json", MAX_PUBLICATION)
    wire.require(wire.digest(raw) == qualification["remote_publication_sha256"], "profile publication pin differs")
    publication = json.loads(raw)
    wire.require(publication["status"] == "completed" and publication["invented_only"] is True
                 and publication["source_hashes"] == config["source_hashes"]
                 and publication["controller_sources"] == config["controller_sources"]
                 and publication["runtime_sha256"] == RUNTIME and publication["geometry"] == GEOMETRY
                 and publication["merge_commit"] == config["merge_commit"]
                 and publication["tree"] == config["tree"] and publication["verification_complete"] is True
                 and publication["collector_before"] == publication["collector_after"]
                 == {"head": COLLECTOR_COMMIT, "dirty": ""} and publication["automatic_retries"] == 0,
                 "same-source whole profile required")
    validate_budget(publication["budget"])
    validate_helpers(publication["git_helpers"])
    for name, expected in publication["artifacts"].items():
        artifact_name(name)
        checkpoint()
        wire.require(wire.valid_sha(expected) and wire.file_digest(directory / name, MAX_FILE,
                     lambda _: checkpoint())[0] == expected, "profile artifact pin differs")
    wire.require({"registration.json", "source-manifest.json", "scope.json", "report.json",
                  "verification.json", "canonical-span.jsonl", "budget.json"}
                 <= set(publication["artifacts"]), "profile artifact set incomplete")

    def read(name):
        artifact_name(name)
        wire.require(name in publication["artifacts"], "profile artifact not pinned")
        return bridge.bounded_read(directory / name, MAX_FILE)

    verified = verify_saved_acquisition(read)
    wire.require(verified == json.loads(read("verification.json")) == publication["verified_support"],
                 "profile independent reconstruction differs")
    check_profile_geometry(verified)
    check_profile_artifacts(read)
    registration = json.loads(read("registration.json"))
    validate_config(registration)
    wire.require(registration["invented_only"] is True and registration["output"] == str(directory)
                 and all(registration[key] == config[key] for key in
                         ("source_hashes", "controller_sources", "merge_commit", "reviewed_commit", "tree")),
                 "profile original registration differs")
    wire.require(json.loads(read("source-manifest.json")) == config["source_hashes"]
                 and json.loads(read("budget.json")) == publication["budget"], "profile receipt differs")
    records = [json.loads(line) for line in read("canonical-span.jsonl").splitlines()]
    wire.require(len(records) == 2 and [r["status"] for r in records] == ["started", "completed"]
                 and all(r["run_id"] == publication["run_id"] and r["code_version"] == config["merge_commit"]
                     and r["specification_sha256"] == config["source_hashes"][SPEC]
                     and r["parameters"]["source_hashes"] == config["source_hashes"]
                     and r["parameters"]["controller_sources"] == config["controller_sources"] for r in records)
                 and records[1]["result"]["report_sha256"] == publication["artifacts"]["report.json"]
                 and records[1]["result"]["verified_support"] == verified,
                 "profile canonical source/completion differs")


def check_profile_geometry(verified: dict) -> None:
    # These fields are produced by the raw-only verifier, never the producer.
    wire.require(verified.get("verified") is True and verified.get("state") == "complete"
                 and verified.get("wire_attempts") == GEOMETRY["requests"]
                 and verified.get("complete_listing_rows") == GEOMETRY["rows"]
                 and verified.get("complete_codes") == GEOMETRY["codes"]
                 and verified.get("unknown_codes") == 0, "full invented geometry differs")


def check_profile_artifacts(read) -> None:
    """Profile qualification also proves exact invented identities and raw scale."""
    expected_codes = [f"{number:06d}" for number in range(1, GEOMETRY["codes"] + 1)]
    wire.require(json.loads(read("scope.json"))["codes"] == expected_codes,
                 "invented profile code identities differ")
    report = json.loads(read("report.json"))
    expected_pairs = [(code, page) for code in expected_codes
                      for page in range(1, GEOMETRY["pages_per_code"] + 1)]
    pairs, raw_bytes = [], 0
    for prefix in report["attempts"]:
        request = json.loads(read(prefix + "-attempt.json"))["request"]
        code, page = request["query_code"], request["page"]
        raw = read(prefix + "-raw.html")
        wire.require(raw == invented_listing(code, page), "invented profile raw fixture differs")
        pairs.append((code, page))
        raw_bytes += len(raw)
    wire.require(pairs == expected_pairs and raw_bytes == GEOMETRY["maximum_raw_bytes"],
                 "full invented raw byte geometry differs")


def run_unit(config: dict) -> dict:
    """Explicit once-only native CLI dispatch, including preserved failures."""
    from research.intraday_ed_disclosure_acquisition import (
        HttpResponse, https_listing_transport, run_listing_acquisition, verify_saved_acquisition,
    )
    validate_config(config)
    root = Path(__file__).resolve().parents[2]
    wire.require(root == REMOTE, "fixed isolated GCP research checkout required")
    budget = ResearchBudget(**LIMITS["worker"])
    fixed_environment()
    wire.fixed_deadline(config["deadline_epoch"])
    sources = source_bundle(root)
    spec = contract(sources[SPEC], actual=not config["invented_only"])
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
    wire.require(before == {"head": COLLECTOR_COMMIT, "dirty": ""}, "collector source differs")
    invented = config["invented_only"]
    out = Path(config["output"])
    wire.native_cache(out, root)
    bridge.require_free_disk(out.parent)
    checkpoint = lambda: budget.checkpoint("disclosure_unit")
    if not invented:
        qualify_actual(config, checkpoint)
    marker = EVIDENCE / "intraday-ed-disclosure-unresolved.json"
    log = out / "profile-experiments.jsonl" if invented else COLLECTOR / "runs/experiments.jsonl"
    lock = EVIDENCE / "invented-research.lock" if invented else COLLECTOR / "runs/intraday-research.lock"
    with wire.research_locks([EVIDENCE / "ed-campaign.lock", lock], marker, config["attempt"]):
        wire.exclusive_directory(out)
        artifacts = {}

        def put(name, raw):
            artifact_name(name)
            wire.require(type(raw) is bytes and len(raw) <= MAX_FILE, "artifact bytes exceeded")
            wire.exclusive_bytes(out / name, raw)
            artifacts[name] = wire.digest(raw)

        position = None
        try:
            put("registration.json", wire.encode(config))
            put("source-manifest.json", wire.encode(config["source_hashes"]))
            position = wire.regular(log).st_size if log.exists() else 0

            def evaluate():
                # The existing logger fsyncs STARTED before entering this closure.
                codes = ([f"{n:06d}" for n in range(1, 19)] if invented else
                         inventory_codes(bridge.bounded_read(INVENTORY, MAX_FILE)))
                transport = https_listing_transport
                if invented:
                    def transport(request):
                        return HttpResponse(200, "https://dart.fss.or.kr/dsab001/search.ax",
                                            invented_listing(request.query_code, request.page))
                report = run_listing_acquisition(codes, write_new=put, transport=transport,
                    clock=lambda: datetime.now(timezone.utc), sleep=time.sleep, checkpoint=checkpoint)
                verified = verify_saved_acquisition(lambda name: bridge.bounded_read(out / name, MAX_FILE))
                if invented:
                    check_profile_geometry(verified)
                    check_profile_artifacts(lambda name: bridge.bounded_read(out / name, MAX_FILE))
                put("verification.json", wire.encode(verified))
                wire.require(verified.get("verified") is True and verified.get("state") == "complete",
                             "acquisition terminated; preserved evidence is not completion")
                wire.require(json.loads(bridge.bounded_read(out / "report.json", MAX_FILE)) == report,
                             "stored producer report differs")
                checkpoint()
                return {"report_sha256": artifacts["report.json"], "verification_complete": True,
                        "verified_support": verified, "new_performance_evaluations": 0, "credential_reads": 0}

            record = run_discovery_trial(study_id=spec["id"], specification_sha256=config["source_hashes"][SPEC],
                parameters={"invented_only": invented, "source_hashes": config["source_hashes"],
                            "controller_sources": config["controller_sources"],
                            "public_runtime": runtime_contract()},
                window={key: spec["window"][key] for key in ("symbol", "interval", "start", "end")},
                evaluate=evaluate, runs_path=log, code_version_provider=lambda: helpers.git("rev-parse", "HEAD"))
            span = journal_span(log, position, record["run_id"])
            wire.require(record["code_version"] == config["merge_commit"], "journal source differs")
            put("canonical-span.jsonl", span)
            verify_sources(config, root, helpers)
            wire.require(collector_state() == before, "collector source changed")
            validate_helpers(helpers.receipt)
            for name, expected in artifacts.items():
                wire.require(wire.file_digest(out / name, MAX_FILE, lambda _: checkpoint())[0] == expected,
                             "stored artifact readback differs")
            receipt = budget.finish()
            validate_budget(receipt)
            put("budget.json", wire.encode(receipt))
            publication = {"status": "completed", "invented_only": invented, "run_id": record["run_id"],
                "source_hashes": config["source_hashes"], "runtime_sha256": RUNTIME,
                "controller_sources": config["controller_sources"],
                "geometry": GEOMETRY if invented else None, "merge_commit": config["merge_commit"],
                "tree": config["tree"], "verification_complete": True, "collector_before": before,
                "verified_support": record["result"]["verified_support"],
                "collector_after": before, "budget": receipt, "git_helpers": helpers.receipt,
                "artifacts": artifacts, "canonical_offsets": [position, position + len(span)],
                "automatic_retries": 0}
            raw = wire.encode(publication)
            wire.exclusive_bytes(out / "publication.json", raw)
            wire.require(wire.digest(bridge.bounded_read(out / "publication.json", MAX_PUBLICATION))
                         == wire.digest(raw), "publication readback differs")
            wire.clear_terminal_marker(marker, config["attempt"], record)
            return {"status": "completed", "invented_only": invented, "run_id": record["run_id"],
                "remote_output": str(out), "remote_publication_sha256": wire.digest(raw),
                "report_sha256": artifacts["report.json"], "verification_complete": True,
                "verified_support": publication["verified_support"],
                "runtime_sha256": RUNTIME, "geometry": publication["geometry"],
                "budget": receipt, "git_helpers": helpers.receipt, "source_hashes": config["source_hashes"],
                "controller_sources": config["controller_sources"]}
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
    parser.add_argument("--validate", action="store_true", help="source only; no journal, input or network access")
    parser.add_argument("--config-base64")
    parser.add_argument("--expected-config-sha256")
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    if args.validate:
        wire.require(args.config_base64 is None and args.expected_config_sha256 is None, "validate has no execution config")
        sources = source_bundle(Path(__file__).resolve().parents[2])
        contract(sources[SPEC])
        result = {"status": "source_only_validated", "source_hashes": {name: wire.digest(raw)
                  for name, raw in sources.items()}, "public_runtime": runtime_contract(), "actual_accesses": 0}
    else:
        wire.require(type(args.config_base64) is str and len(args.config_base64) <= 65536
                     and wire.valid_sha(args.expected_config_sha256), "bounded pinned configuration required")
        raw = base64.b64decode(args.config_base64, validate=True)
        wire.require(wire.digest(raw) == args.expected_config_sha256, "configuration pin differs")
        config = json.loads(raw)
        wire.require(wire.encode(config) == raw, "canonical configuration required")
        result = run_unit(config)
    print(wire.encode(result).decode().strip())


if __name__ == "__main__":
    main()
