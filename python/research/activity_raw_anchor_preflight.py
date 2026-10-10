"""One logged three-request KRX diagnostic; no unit or performance inference.

Run committed source/specification from the repository root. Retained private
current-API responses are observations, never historical availability proof.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from data import krx_openapi_probe as probe
from research import activity_exit_preflight as exit_scope
from research import activity_preflight as saved
from research import experiment_log


SCHEMA = "activity-raw-anchor-preflight-v1"
REQUEST_MATRIX = (
    {"service": "stk_bydd_trd", "bas_dd": "20210428", "targets": ["003550"]},
    {"service": "stk_bydd_trd", "bas_dd": "20210527", "targets": ["003550", "383800"]},
    {"service": "stk_isu_base_info", "bas_dd": "20210527", "targets": ["383800"]},
)
REQUEST_CONTRACT = {"host": probe.HOST, "matrix": list(REQUEST_MATRIX),
    "timeout_seconds": 20, "max_response_bytes": 8 * 1024 * 1024,
    "interval_seconds": 1.1, "max_requests": 3, "automatic_retries": 0,
    "authentication_header": "AUTH_KEY"}
FALSE_FLAGS = ("source_truth_certified", "historical_vintage_certified",
    "historical_publication_certified", "event_coverage_certified",
    "price_basis_certified", "units_certified", "custody_delivery_certified",
    "books_computed", "returns_computed", "promotion_allowed")
SAFE_FAILURE_REASONS = frozenset({"unexpected_response", "http_failure", "access_failure",
    "redirect_rejected", "transport_failure", "response_too_large", "credential_echo",
    "invalid_json", "invalid_envelope", "missing_or_malformed_credential"})


def validate_spec(spec: dict, reference: dict) -> None:
    """There is no host/date/market/range/request-count configuration surface."""
    saved.require(type(spec) is dict and set(spec) == {
        "schema", "study_id", "mode", "promotion_allowed", "window",
        "reference_specification", "parameters", "inputs", "request_contract"}
        and spec["schema"] == spec["study_id"] == SCHEMA
        and spec["mode"] == "discovery_raw_anchor_diagnostic"
        and spec["promotion_allowed"] is False and spec["window"] == saved.WINDOW
        and spec["reference_specification"] == saved.REFERENCE
        and type(spec["parameters"]) is dict
        and saved.encoded(spec["parameters"]) == saved.encoded(reference["parameters"])
        and type(spec["inputs"]) is dict and not spec["inputs"]
        and saved.encoded(spec["request_contract"]) == saved.encoded(REQUEST_CONTRACT),
        "fixed raw-anchor specification mismatch")


def _metadata(item: dict, retrieved_at: str, response_sha256: str) -> dict:
    return {"observation_date": None, "requested_bas_dd": item["bas_dd"],
        "available_at": None, "retrieved_at": retrieved_at,
        "source": f"KRX OpenAPI {item['service']}", "is_final": None,
        "data_vintage": "current_retrieval_sha256:" + response_sha256,
        "evidence_level": "observed_current_api_not_historical",
        "response_sha256": response_sha256}


def extract_targets(rows: list[dict], item: dict, retrieved_at: str,
                    response_sha256: str) -> list[dict]:
    """Only a validated unique trade row has an observation date.

    Absent, duplicate, date-mismatched and otherwise invalid targets retain a
    null observation date. Request coordinates never substitute for observations.
    """
    trade = item["service"] == "stk_bydd_trd"
    field = "ISU_CD" if trade else "ISU_SRT_CD"
    result = []
    for code in item["targets"]:
        matches = [(number, row) for number, row in enumerate(rows) if row.get(field) == code]
        target = {"code": code, "kind": "raw_close" if trade else "issue_identity",
            "status": "unresolved", "reason": "target_absent_cause_unknown",
            "row_position": None, **_metadata(item, retrieved_at, response_sha256)}
        if trade:
            target["returned_BAS_DD"] = None
        if len(matches) > 1:
            target["reason"] = "duplicate_target"
        elif matches:
            position, row = matches[0]
            target["row_position"] = position
            if trade:
                value = row.get("TDD_CLSPRC")
                target["returned_BAS_DD"] = row.get("BAS_DD")
                if row.get("BAS_DD") != item["bas_dd"]:
                    target["reason"] = "observation_date_mismatch"
                elif not isinstance(value, str) or not probe.NUMBER.fullmatch(value):
                    target["reason"] = "invalid_raw_close"
                else:
                    number = Decimal(value.replace(",", ""))
                    if not number.is_finite() or number <= 0:
                        target["reason"] = "invalid_raw_close"
                    else:
                        target.update(status="observed", reason=None, raw_close=format(number, "f"),
                            original_raw_close=value,
                            observation_date=datetime.strptime(row["BAS_DD"], "%Y%m%d").date().isoformat())
            else:
                isin, listed = row.get("ISU_CD"), row.get("LIST_DD")
                # A duplicated ISIN under another short code breaks the join too.
                if (not isinstance(isin, str) or not re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", isin)
                        or sum(other.get("ISU_CD") == isin for other in rows) != 1):
                    target["reason"] = "invalid_or_ambiguous_isin_join"
                else:
                    try:
                        if not isinstance(listed, str) or not re.fullmatch(r"[0-9]{8}", listed):
                            raise ValueError
                        datetime.strptime(listed, "%Y%m%d")
                        if listed > item["bas_dd"]:
                            raise ValueError
                    except ValueError:
                        target["reason"] = "invalid_listing_date"
                    else:
                        target.update(status="observed", reason=None, isin=isin, returned_LIST_DD=listed)
        result.append(target)
    return result


def _fetch(opener, item: dict, secret: str, output: Path, manifest: list[dict],
           targets: list[dict], number: int) -> None:
    record = {"number": number, "service": item["service"], "bas_dd": item["bas_dd"]}
    entry = {**record, "read_status": "started"}
    manifest.append(entry)
    probe.persist(output / "requests.jsonl", {**record, "event": "attempt", "at": probe.utc_now()}, append=True)
    url = f"{probe.HOST}/svc/apis/sto/{item['service']}?basDd={item['bas_dd']}"
    status = None
    try:
        with opener.open(Request(url, headers={"AUTH_KEY": secret}, method="GET"), timeout=20) as response:
            status = response.status
            if status != 200 or response.geturl() != url:
                raise probe.ProbeRefusal("unexpected_response")
            body = response.read(probe.MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = error.code
        error.close()  # Error bodies, headers and exception strings are never retained.
        reason = "redirect_rejected" if 300 <= status < 400 else (
            "access_failure" if status in (401, 403) else "http_failure")
        raise probe.ProbeRefusal(reason) from None
    except (URLError, TimeoutError, OSError):
        raise probe.ProbeRefusal("transport_failure") from None
    finally:
        probe.persist(output / "requests.jsonl", {
            **record, "event": "response", "at": probe.utc_now(), "http_status": status}, append=True)
        entry["http_status"] = status
    if len(body) > probe.MAX_RESPONSE_BYTES:
        raise probe.ProbeRefusal("response_too_large")
    rows = probe.parse_response(body, secret)  # Detect secret echo before retaining anything.
    retrieved = probe.utc_now()
    filename = f"response-{number:02d}-{item['service']}-{item['bas_dd']}.json"
    probe.persist(output / filename, body, raw=True)
    entry.update(read_status="retained", file=filename, bytes=len(body), rows=len(rows),
                 sha256=saved.digest(body), retrieved_at=retrieved)
    extracted = extract_targets(rows, item, retrieved, entry["sha256"])
    targets.extend(extracted)
    saved.write_exclusive(output / f"anchors-{number:02d}.json", saved.encoded(extracted))


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path) -> dict:
    """Durable start precedes credential/network; failed attempts cannot be retried here."""
    root = root.resolve()
    saved.require(Path.cwd().resolve() == root, "run from repository root")
    sources, raw, spec = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
    source_path = Path(__file__).resolve().relative_to(root).as_posix()
    source_sha256 = saved.digest(Path(__file__).read_bytes())
    saved.require(any(row["path"] == source_path and row["sha256"] == source_sha256
        for row in sources["files"]), "producer must be committed frozen source before actual invocation")
    output, runs = saved.validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    manifest, targets = [], []

    def evaluate():
        created, started = False, time.monotonic()
        try:
            saved.private_directory(output.parent)
            output.mkdir(mode=0o700)
            created = True
            saved._sync_directory(output.parent)
            saved.write_exclusive(output / "specification.json", raw)
            saved.write_exclusive(output / "source-manifest.json", saved.encoded(sources))
            secret = probe.credential()
            opener = probe.make_opener(header_case_check=True)
            for number, item in enumerate(REQUEST_MATRIX, 1):
                if number > 1:
                    time.sleep(1.1)
                _fetch(opener, item, secret, output, manifest, targets, number)
            after, after_raw, _ = saved.freeze_sources(root, spec_path, spec_validator=validate_spec)
            identity = lambda value: {key: item for key, item in value.items() if key != "installed_locked_distributions"}
            saved.require(identity(after) == identity(sources) and after_raw == raw, "source/runtime changed")
            resources = saved.encoded(exit_scope._resources(started))
            saved.write_exclusive(output / "resource-manifest.json", resources)
            saved.write_exclusive(output / "input-manifest.json", saved.encoded(manifest))
            result = {"schema": SCHEMA, "status": "completed" if all(row["status"] == "observed" for row in targets)
                      else "completed_with_unresolved", "targets": targets,
                "requests_attempted": len(manifest), "requests_unattempted": 3 - len(manifest),
                "automatic_retries": 0, "D1_completed_studies": 0, "D2_completed_studies": 0,
                **dict.fromkeys(FALSE_FLAGS, False), "database_access": False,
                "api_access": True, "new_source_requests": len(manifest),
                "source_manifest_sha256": saved.digest(saved.encoded(sources)),
                "specification_sha256": saved.digest(raw),
                "resource_manifest_sha256": saved.digest(resources),
                "input_manifest_sha256": saved.digest(saved.encoded(manifest)),
                "request_ledger_sha256": saved.digest(saved.read_private(output / "requests.jsonl")),
                "independent_output_verification": "pending"}
            saved.write_exclusive(output / "result.json", saved.encoded(result))
            return {"status": result["status"], "output_dir": str(output),
                "result_sha256": saved.digest(saved.encoded(result)),
                "requests_attempted": len(manifest), "unresolved_targets": sum(row["status"] != "observed" for row in targets),
                "D1_completed_studies": 0, "D2_completed_studies": 0, "promotion_allowed": False}
        except BaseException as exc:
            if created:
                reason = str(exc) if isinstance(exc, probe.ProbeRefusal) else "validation_or_local_failure"
                if reason not in SAFE_FAILURE_REASONS:
                    reason = "validation_or_local_failure"
                if not (output / "resource-manifest.json").exists():
                    saved.write_exclusive(output / "resource-manifest.json", saved.encoded(exit_scope._resources(started)))
                if not (output / "input-manifest.json").exists():
                    saved.write_exclusive(output / "input-manifest.json", saved.encoded(manifest))
                saved.write_exclusive(output / "failure.json", saved.encoded({"status": "failed",
                    "error_type": type(exc).__name__, "failure": reason, "actual_inputs_attempted": manifest,
                    "requests_attempted": len(manifest), "requests_unattempted": 3 - len(manifest),
                    "partial_targets": targets, "partial_package_preserved": True,
                    "source_manifest_sha256": saved.digest(saved.encoded(sources)),
                    "specification_sha256": saved.digest(raw), "automatic_retries": 0,
                    "D1_completed_studies": 0, "D2_completed_studies": 0,
                    **dict.fromkeys(FALSE_FLAGS, False)}))
            # Neither experiment log nor CLI receives a network/library exception string.
            raise probe.ProbeRefusal("raw_anchor_attempt_failed") from None

    previous = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(study_id=spec["study_id"], specification_sha256=saved.digest(raw),
            window=spec["window"], runs_path=runs, parameters={**spec["parameters"], "source_manifest": sources,
                "request_contract": spec["request_contract"], "output_dir": str(output), "automatic_retries": 0}, evaluate=evaluate)
    finally:
        os.umask(previous)


def main(argv=None) -> int:
    parser = probe.SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception:
        print(saved.encoded({"status": "failed", "failure": "raw_anchor_attempt_failed", "automatic_retries": 0}).decode())
        return 1
    print(saved.encoded(record["result"]).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
