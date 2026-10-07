"""Bounded KRX OpenAPI access/field evidence, never a universe or strategy run.

Supply a new, private evidence directory; --header-case-check selects a fixed
single-request diagnostic. AUTH_KEY comes from KRX_API_KEY in the process
environment. No research database/log is read or written. Responses are local
evidence, never printed in operator reports.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from http.client import HTTPSConnection
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


HOST = "https://data-dbg.krx.co.kr"
SERVICES = ("stk_bydd_trd", "ksq_bydd_trd", "stk_isu_base_info", "ksq_isu_base_info")
DATES = ("20190102", "20220630", "20260918", "20260919")
SPECIFICATION = "krx-openapi-access-fields-v1"
HEADER_CASE_SPECIFICATION = "krx-openapi-auth-header-case-v1"
MAX_REQUESTS = 16
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 20
INTERVAL_SECONDS = 1.0
ROOT = Path(__file__).resolve().parents[2]
TRADE_FIELDS = (
    "BAS_DD", "ISU_CD", "ISU_NM", "MKT_NM", "SECT_TP_NM", "TDD_CLSPRC",
    "CMPPREVDD_PRC", "FLUC_RT", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC",
    "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP", "LIST_SHRS",
)
BASIC_FIELDS = (
    "ISU_CD", "ISU_SRT_CD", "ISU_NM", "ISU_ABBRV", "ISU_ENG_NM", "LIST_DD",
    "MKT_TP_NM", "SECUGRP_NM", "SECT_TP_NM", "KIND_STKCERT_TP_NM", "PARVAL", "LIST_SHRS",
)
NUMBER = re.compile(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?\Z")


class ProbeRefusal(RuntimeError):
    """Only a fixed, non-sensitive reason code may cross the CLI boundary."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ExactAuthKeyHTTPSConnection(HTTPSConnection):
    """Change only the authentication header's spelling at serialization."""

    def putheader(self, header, *values):
        if header.lower() == "auth_key":
            header = "AUTH_KEY"
        super().putheader(header, *values)


class ExactAuthKeyHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(ExactAuthKeyHTTPSConnection, req, context=self._context)


def make_opener(*, header_case_check: bool = False):
    # A fresh opener has neither ambient proxies nor cookies nor redirect auth forwarding.
    handlers = [ProxyHandler({}), NoRedirect()]
    if header_case_check:
        handlers.append(ExactAuthKeyHTTPSHandler())
    return build_opener(*handlers)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def persist(path: Path, value: object, *, append: bool = False, raw: bool = False) -> None:
    data = value if raw else canonical(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_APPEND if append else os.O_EXCL)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "ab" if append else "wb") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise ProbeRefusal("unsafe_file_permissions")
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def source_version() -> str:
    # Inherited Git repository/index overrides must not certify another checkout.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(ROOT), *args], env=env, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    try:
        if git("status", "--porcelain", "--untracked-files=all"):
            raise ProbeRefusal("uncommitted_sources")
        version = git("rev-parse", "HEAD")
        if not re.fullmatch(r"[0-9a-f]{40}", version):
            raise ProbeRefusal("source_version_unavailable")
        return version
    except (OSError, subprocess.SubprocessError):
        raise ProbeRefusal("source_version_unavailable") from None


def prepare_output(output: Path) -> Path:
    output = Path(os.path.abspath(output))
    if output.is_relative_to(ROOT):
        raise ProbeRefusal("evidence_must_be_outside_checkout")
    # Refuse symlinked ancestors and existing/dangling-symlink destinations.
    if any(parent.is_symlink() for parent in output.parents):
        raise ProbeRefusal("symlinked_output_parent")
    if not output.parent.is_dir() or os.path.lexists(output):
        raise ProbeRefusal("output_must_be_new_with_existing_parent")
    output.mkdir(mode=0o700)
    if stat.S_IMODE(output.stat().st_mode) != 0o700:
        raise ProbeRefusal("unsafe_directory_permissions")
    sync_directory(output.parent)
    return output


def credential() -> str:
    value = os.environ.get("KRX_API_KEY", "")
    if not value or any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise ProbeRefusal("missing_or_malformed_credential")
    return value


def id_kind(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "missing"
    if re.fullmatch(r"[0-9A-Z]{6}", value):
        return "short6"
    if re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", value):
        return "isin12"
    return "unknown"


def identifier_counts(rows: list[dict], field: str) -> dict:
    values = [row.get(field) for row in rows]
    usable = [value for value in values if isinstance(value, str) and value]
    kinds = Counter(id_kind(value) for value in values)
    return {
        "unique": len(set(usable)), "duplicate_rows": len(usable) - len(set(usable)),
        "missing": kinds["missing"], "unknown": kinds["unknown"],
        "short6": kinds["short6"], "isin12": kinds["isin12"],
    }


def numeric_counts(rows: list[dict], field: str, *, positive: bool) -> dict:
    missing = invalid = 0
    for row in rows:
        value = row.get(field)
        if value is None or value in ("", "-"):
            missing += 1
        elif not isinstance(value, str) or not NUMBER.fullmatch(value):
            invalid += 1
        else:
            number = Decimal(value.replace(",", ""))
            if not number.is_finite() or number < 0 or (positive and number == 0):
                invalid += 1
    return {"missing": missing, "invalid": invalid}


def summarize(rows: list[dict], service: str, day: str) -> dict:
    is_trade = service in SERVICES[:2]
    fields = TRADE_FIELDS if is_trade else BASIC_FIELDS
    ids = ("ISU_CD",) if is_trade else ("ISU_CD", "ISU_SRT_CD")
    missing = {field: sum(field not in row for row in rows) for field in fields}
    non_string = sum(not isinstance(value, str) for row in rows for value in row.values())
    identity = {field: identifier_counts(rows, field) for field in ids}
    numeric = {"LIST_SHRS": numeric_counts(rows, "LIST_SHRS", positive=True)}
    observation = {
        "service": service, "requested_bas_dd": day, "rows": len(rows),
        "required_missing_fields": missing, "non_string_fields": non_string,
        "identifiers": identity, "numeric": numeric,
        "schema_valid": False,
    }
    identity_valid = all(not any(counts[field] for field in ("missing", "unknown", "duplicate_rows"))
                         for counts in identity.values())
    if is_trade:
        numeric.update({field: numeric_counts(rows, field, positive=field == "MKTCAP")
                        for field in ("MKTCAP", "ACC_TRDVOL", "ACC_TRDVAL")})
        observation["date_mismatches"] = sum(row.get("BAS_DD") != day for row in rows)
        observation["negative_control"] = (
            "unexpected_nonempty" if rows else "empty_observed"
        ) if day == DATES[-1] else "not_applicable"
        date_valid = not observation["date_mismatches"] and (not rows if day == DATES[-1] else bool(rows))
    else:
        identity_valid = identity_valid and not identity["ISU_CD"]["short6"] and not identity["ISU_SRT_CD"]["isin12"]
        invalid_dates = future_dates = 0
        for row in rows:
            value = row.get("LIST_DD")
            try:
                if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
                    raise ValueError
                datetime.strptime(value, "%Y%m%d")
                future_dates += value > day
            except ValueError:
                invalid_dates += 1
        observation.update({
            "invalid_listing_dates": invalid_dates,
            "listing_dates_after_requested_date": future_dates,
            "observation_date_field_available": False,
            "weekend_rows_observed": len(rows) if day == DATES[-1] else None,
            # Only cardinalities: no security names, classification labels, or rankings.
            "type_field_distinct_counts": {
                field: len({row[field] for row in rows if isinstance(row.get(field), str)})
                for field in ("MKT_TP_NM", "SECUGRP_NM", "SECT_TP_NM", "KIND_STKCERT_TP_NM")
            },
        })
        date_valid = not invalid_dates and (bool(rows) or day == DATES[-1])
    observation["schema_valid"] = bool(
        not any(missing.values()) and not non_string and identity_valid and date_valid
        and not any(count for counts in numeric.values() for count in counts.values())
    )
    return observation


def has_secret(value: object, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, list):
        return any(has_secret(item, secret) for item in value)
    if isinstance(value, dict):
        return any(secret in key or has_secret(item, secret) for key, item in value.items())
    return False


def parse_response(body: bytes, secret: str) -> list[dict]:
    if secret.encode() in body:
        raise ProbeRefusal("credential_echo")
    def unique_object(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        payload = json.loads(body, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise ProbeRefusal("invalid_json") from None
    if has_secret(payload, secret):
        raise ProbeRefusal("credential_echo")
    if (not isinstance(payload, dict) or set(payload) != {"OutBlock_1"}
            or not isinstance(payload["OutBlock_1"], list)
            or any(not isinstance(row, dict) for row in payload["OutBlock_1"])):
        raise ProbeRefusal("invalid_envelope")
    return payload["OutBlock_1"]


def join_counts(trades: list[dict], basics: list[dict], service: str, day: str) -> dict:
    result = {"service": service, "requested_bas_dd": day, "matched_rows": 0,
              "unmatched_rows": 0, "ambiguous_rows": 0, "trade_id_kind": "empty"}
    kinds = {id_kind(row["ISU_CD"]) for row in trades}
    result["trade_id_kind"] = next(iter(kinds)) if len(kinds) == 1 else "mixed" if kinds else "empty"
    for kind, field in (("short6", "ISU_SRT_CD"), ("isin12", "ISU_CD")):
        counts = Counter(row[field] for row in basics)
        trade_counts = Counter(row["ISU_CD"] for row in trades if id_kind(row["ISU_CD"]) == kind)
        for value, number in trade_counts.items():
            if number != 1 or counts[value] > 1:
                result["ambiguous_rows"] += number
            elif counts[value] == 1:
                result["matched_rows"] += 1
            else:
                result["unmatched_rows"] += 1
    return result


def acquire(output: Path, *, header_case_check: bool = False) -> dict:
    secret = credential()
    version = source_version()
    output = prepare_output(output)
    dates = DATES[:1] if header_case_check else DATES
    services = SERVICES[:1] if header_case_check else SERVICES
    max_requests = 1 if header_case_check else MAX_REQUESTS
    report = {
        "specification": HEADER_CASE_SPECIFICATION if header_case_check else SPECIFICATION,
        "source_sha": version, "started_at": utc_now(), "max_requests": max_requests,
        "status": "started", "requests_attempted": 0, "observations": [], "joins": [],
        "http_200_responses": 0, "schema_valid_responses": 0,
        "basic_date_comparisons": [], "universe_ready": False,
        "historical_coverage_certified": False,
        "pending_verification": ["units", "publication_timing", "historical_completeness",
                                 "point_in_time_classification", "revisions"],
    }
    persist(output / "started.json", {
        **report, "host": HOST,
        "request_matrix": [{"service": service, "bas_dd": day} for day in dates for service in services],
        "timeout_seconds": TIMEOUT_SECONDS,
        "interval_seconds": INTERVAL_SECONDS, "max_response_bytes": MAX_RESPONSE_BYTES,
    })
    basic_hashes = {service: [] for service in services[2:]}
    try:
        opener = make_opener(header_case_check=header_case_check)
        for day in dates:
            rows_for_day = {}
            for service in services:
                if report["requests_attempted"] >= max_requests:
                    raise ProbeRefusal("request_limit")
                if report["requests_attempted"]:
                    time.sleep(INTERVAL_SECONDS)
                number = report["requests_attempted"] + 1
                request_record = {"number": number, "service": service, "bas_dd": day}
                persist(output / "requests.jsonl", {
                    **request_record, "event": "attempt", "at": utc_now(),
                }, append=True)
                url = f"{HOST}/svc/apis/sto/{service}?basDd={day}"
                request = Request(url, headers={"AUTH_KEY": secret}, method="GET")
                report["requests_attempted"] = number
                status = None
                try:
                    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                        status = response.status
                        if status != 200 or response.geturl() != url:
                            raise ProbeRefusal("unexpected_response")
                        report["http_200_responses"] += 1
                        body = response.read(MAX_RESPONSE_BYTES + 1)
                except HTTPError as error:
                    status = error.code
                    error.close()  # Do not read, save, or print an error body/header.
                    reason = "redirect_rejected" if 300 <= status < 400 else (
                        "access_failure" if status in (401, 403) else "http_failure")
                    raise ProbeRefusal(reason) from None
                except (URLError, TimeoutError, OSError):
                    raise ProbeRefusal("transport_failure") from None
                finally:
                    persist(output / "requests.jsonl", {
                        **request_record, "event": "response", "at": utc_now(), "http_status": status,
                    }, append=True)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ProbeRefusal("response_too_large")
                rows = parse_response(body, secret)
                observation = summarize(rows, service, day)
                observation.update({"at": utc_now(), "response_sha256": hashlib.sha256(body).hexdigest()})
                report["observations"].append(observation)
                # Valid envelope/secret-free response is retained even when field checks fail.
                persist(output / f"response-{number:02d}-{service}-{day}.json", body, raw=True)
                if not observation["schema_valid"]:
                    raise ProbeRefusal("schema_failure")
                report["schema_valid_responses"] += 1
                rows_for_day[service] = rows
                if service in basic_hashes:
                    fingerprint = hashlib.sha256(canonical(sorted(rows, key=canonical))).hexdigest()
                    basic_hashes[service].append({"bas_dd": day, "rows_sha256": fingerprint})
            if not header_case_check:
                for trade_service, basic_service in zip(SERVICES[:2], SERVICES[2:], strict=True):
                    report["joins"].append(join_counts(rows_for_day[trade_service], rows_for_day[basic_service],
                                                       trade_service, day))
        report["status"] = "single_request_checked" if header_case_check else "access_checked"
    except ProbeRefusal as error:
        report.update(status="failed", failure=str(error))
    except OSError:
        report.update(status="failed", failure="local_io_failure")
    except Exception:
        # A parser/library exception may include request headers or response text.
        report.update(status="failed", failure="unexpected_failure")
    report["basic_date_comparisons"] = [
        {"service": service, "observations": hashes,
         "all_observed_payloads_equal": len(hashes) > 1 and len({item["rows_sha256"] for item in hashes}) == 1,
         "historicity_certified": False}
        for service, hashes in basic_hashes.items()
    ]
    report["finished_at"] = utc_now()
    report["requests_unattempted"] = max_requests - report["requests_attempted"]
    # A partial write must never appear under the published report filename.
    # Preserve HTTP counts/original failure even if local evidence storage fails.
    report["report_persisted"] = True
    published = False
    try:
        persist(output / "report.pending.json", report)
        os.replace(output / "report.pending.json", output / "report.json")
        published = True
        sync_directory(output)
    except (OSError, ProbeRefusal):
        report.update(status="failed", evidence_write_failure=True, report_persisted=False)
        report.setdefault("failure", "evidence_write_failure")
        if published:
            try:
                (output / "report.json").unlink()
                sync_directory(output)
            except OSError:
                report["report_cleanup_unverified"] = True
    return report


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ProbeRefusal("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    try:
        parser = SafeParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--output-dir", required=True, type=Path)
        parser.add_argument("--header-case-check", action="store_true",
                            help="send only stk_bydd_trd/20190102 with exact AUTH_KEY spelling")
        args = parser.parse_args(argv)
        report = acquire(args.output_dir, header_case_check=args.header_case_check)
    except ProbeRefusal as error:
        report = {"status": "failed", "failure": str(error), "universe_ready": False}
    except Exception:
        report = {"status": "failed", "failure": "unexpected_failure", "universe_ready": False}
    print(canonical(report).decode())
    return 0 if report["status"] in ("access_checked", "single_request_checked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
