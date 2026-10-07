"""Audit the fixed preceding-session KRX histories without selecting a universe.

Only private evidence paths are accepted. Explicit recovery reuses one pinned
failed run with a fixed transport retry allowance; ordinary runs never retry.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal, localcontext
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
from socket import EAI_AGAIN, gaierror
from ssl import SSLError
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from data import krx_formation_audit as formation
from data import krx_openapi_probe as probe


SPECIFICATION = "krx-large-liquid-liquidity-v1"
RECOVERY_SPECIFICATION = "krx-large-liquid-liquidity-recovery-v1"
RECOVERY_SOURCE_SHA = "e7c13b33b2b0754b031796e88881baf742d2049f"
RECOVERY_REPORT_SHA256 = "53effb672a849084148511be55cda1e92e90d181d0d6ee47291545ecdf12b612"
RECOVERY_STARTED_SHA256 = "34b0abbd41bd360a9309114b6cdb34dd30516bff747586d7190cc6d8acb0a79f"
RECOVERY_LEDGER_SHA256 = "8b2c2367f1ac6e0d3ca5d8f07dd2cf9380bb99a593b0fd769d5e8495fb8da622"
RECOVERY_PREFIX = 549
RECOVERY_MAX_REQUESTS = 1133
RECOVERY_MAX_RETRIES = 2
RECOVERY_COOLDOWN_SECONDS = 30
CALENDAR_SHA256 = formation.CALENDAR_SHA256
CALENDAR_MANIFEST_SHA256 = formation.CALENDAR_MANIFEST_SHA256
FORMATION_REPORT_SHA256 = "17ae2a87663ed592c2968b9ce051f4fa248574a6d70561e704e7b5a27c8c593b"
FORMATION_STARTED_SHA256 = "2bce0c4b916a1f67415e91dd1b4372ae8425e518d98624661725c58407ee48e9"
FORMATION_LEDGER_SHA256 = "f85c8a4403eaacde5d6fd0195054568262cbbb4cdab98ffdc846ab45933f2487"
MAX_REQUESTS = 1680
MIN_FREE_BYTES = 2 * 1024**3
LIQUIDITY_MIN_KRW = Decimal("10000000000")
WINDOW_SESSIONS = 60


def _private_path(path: Path, *, directory: bool = False) -> Path:
    path = Path(os.path.abspath(path))
    if path.is_relative_to(probe.ROOT):
        raise probe.ProbeRefusal("evidence_must_be_outside_checkout")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise probe.ProbeRefusal("unsafe_input_path")
    if directory:
        try:
            info = path.stat()
        except OSError:
            raise probe.ProbeRefusal("input_unavailable") from None
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
            raise probe.ProbeRefusal("unsafe_input_permissions")
    return path


def _read_private(path: Path, expected_hash: str | None = None) -> bytes:
    path = _private_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1):
                raise probe.ProbeRefusal("unsafe_input_permissions")
            if info.st_size > probe.MAX_RESPONSE_BYTES:
                raise probe.ProbeRefusal("input_too_large")
            data = stream.read(probe.MAX_RESPONSE_BYTES + 1)
            if len(data) > probe.MAX_RESPONSE_BYTES:
                raise probe.ProbeRefusal("input_too_large")
    except OSError:
        raise probe.ProbeRefusal("input_unavailable") from None
    if expected_hash is not None and hashlib.sha256(data).hexdigest() != expected_hash:
        raise probe.ProbeRefusal("input_hash_mismatch")
    return data


def _json(data: bytes) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError):
        raise probe.ProbeRefusal("invalid_input_json") from None


def _real_date(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 8 or not value.isascii() or not value.isdigit():
        return False
    try:
        datetime.strptime(value, "%Y%m%d")
        return True
    except ValueError:
        return False


def load_calendar(path: Path) -> tuple[dict[str, list[str]], list[dict]]:
    """Verify the pinned inventory and derive only the registered fixed matrix."""
    _private_path(Path(path).parent, directory=True)
    manifest = _json(_read_private(path, CALENDAR_MANIFEST_SHA256))
    if (not isinstance(manifest, dict) or manifest.get("not_execution_authorization") is not True
            or manifest.get("type_history_coverage_complete") is not False):
        raise probe.ProbeRefusal("invalid_calendar_manifest")
    calendar = manifest.get("calendar_dates")
    if (not isinstance(calendar, list) or len(calendar) != 1898
            or not all(_real_date(day) for day in calendar)
            or calendar != sorted(set(calendar)) or calendar[0] != "20190102"
            or calendar[-1] != "20260923" or "20260918" not in calendar
            or hashlib.sha256(probe.canonical(calendar)).hexdigest() != CALENDAR_SHA256):
        raise probe.ProbeRefusal("invalid_calendar_manifest")
    indices = range(60, calendar.index("20260918") - 126, 126)
    if ([calendar[index] for index in indices] != list(formation.DATES)
            or manifest.get("formation_dates") != list(formation.DATES)):
        raise probe.ProbeRefusal("formation_calendar_mismatch")
    windows = {calendar[index]: calendar[index-60:index] for index in indices}
    dates = sorted({day for window in windows.values() for day in window})
    if (len(dates) != 840 or set(dates) & set(formation.DATES)
            or not all("20190102" <= day <= "20260918" for day in dates)):
        raise probe.ProbeRefusal("liquidity_calendar_mismatch")
    proposed = manifest.get("proposed_minimum_request_matrix")
    if (not isinstance(proposed, list) or len(proposed) != 1736
            or any(not isinstance(item, dict) or set(item) != {"bas_dd", "service"}
                   or not isinstance(item["bas_dd"], str) or not isinstance(item["service"], str)
                   for item in proposed)):
        raise probe.ProbeRefusal("invalid_request_matrix")
    expected = {(day, service) for day in (*dates, *formation.DATES) for service in probe.SERVICES[:2]}
    expected.update((day, service) for day in formation.DATES for service in probe.SERVICES[2:])
    if {(item["bas_dd"], item["service"]) for item in proposed} != expected:
        raise probe.ProbeRefusal("invalid_request_matrix")
    return windows, [{"bas_dd": day, "service": service} for day in dates for service in probe.SERVICES[:2]]


def _bar_metrics(row: dict) -> dict:
    prices = [formation._number(row, field) for field in ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC")]
    turnover = formation._number(row, "ACC_TRDVAL")
    return {"turnover": turnover, "equal_ohlc_zero_turnover": len(set(prices)) == 1 and turnover == 0,
            "zero_ohl": any(price == 0 for price in prices[:3])}


def load_formation_cache(path: Path, secret: str) -> list[dict]:
    """Re-audit fixed AQ filenames and retain every capitalization-pass target."""
    path = _private_path(path, directory=True)
    report = _json(_read_private(path / "report.json", FORMATION_REPORT_SHA256))
    started = _json(_read_private(path / "started.json", FORMATION_STARTED_SHA256))
    ledger = [_json(line) for line in _read_private(path / "requests.jsonl", FORMATION_LEDGER_SHA256).splitlines()]
    expected_matrix = [{"service": service, "bas_dd": day} for day in formation.DATES for service in probe.SERVICES]
    required = {"specification": formation.SPECIFICATION, "status": "formations_audited", "max_requests": 56,
                "requests_attempted": 56, "requests_unattempted": 0, "http_200_responses": 56,
                "schema_valid_responses": 56, "days_audited": 14, "market_joins_valid": 28,
                "report_persisted": True, **formation._uncertified_flags()}
    if (not isinstance(report, dict) or any(report.get(key) != value for key, value in required.items())
            or any(report.get(flag) is not False for flag in formation._uncertified_flags())
            or not isinstance(started, dict) or started.get("specification") != formation.SPECIFICATION
            or started.get("request_matrix") != expected_matrix or len(ledger) != 112):
        raise probe.ProbeRefusal("invalid_formation_evidence")
    observations = report.get("observations")
    if not isinstance(observations, list) or len(observations) != 56:
        raise probe.ProbeRefusal("invalid_formation_evidence")
    targets, days = [], []
    for index, item in enumerate(expected_matrix):
        observation = observations[index]
        if (not isinstance(observation, dict) or observation.get("service") != item["service"]
                or observation.get("requested_bas_dd") != item["bas_dd"]
                or observation.get("schema_valid") is not True
                or not isinstance(observation.get("response_sha256"), str)
                or len(observation["response_sha256"]) != 64):
            raise probe.ProbeRefusal("invalid_formation_evidence")
        for offset, event in enumerate(("attempt", "response")):
            entry = ledger[index * 2 + offset]
            if (not isinstance(entry, dict) or any(entry.get(key) != value for key, value in item.items())
                    or entry.get("event") != event or entry.get("number") != index + 1
                    or (event == "response" and entry.get("http_status") != 200)):
                raise probe.ProbeRefusal("invalid_formation_evidence")
        if index % 4 == 0:
            rows_by_service = {}
        body = _read_private(path / f"response-{index+1:02d}-{item['service']}-{item['bas_dd']}.json", observation["response_sha256"])
        rows_by_service[item["service"]] = probe.parse_response(body, secret)
        if index % 4 != 3:
            continue
        day = item["bas_dd"]
        result = formation.audit_day(day, rows_by_service)
        days.append(result)
        for trade_service, basic_service in zip(probe.SERVICES[:2], probe.SERVICES[2:], strict=True):
            basics = {row["ISU_SRT_CD"]: row for row in rows_by_service[basic_service]}
            for row in rows_by_service[trade_service]:
                if formation._number(row, "MKTCAP") < formation.MARKET_CAP_MIN_KRW:
                    continue
                basic = basics[row["ISU_CD"]]
                metrics = _bar_metrics(row)
                targets.append({"formation": day, "code": row["ISU_CD"],
                                "source_common_label": basic["KIND_STKCERT_TP_NM"] == "보통주",
                                "reported_listing_date": basic["LIST_DD"],
                                "formation_tradability_proxy": not metrics["zero_ohl"] and not metrics["equal_ohlc_zero_turnover"]})
    if report.get("days") != days or any(report.get(field) != sum(day[field] for day in days) for field in (
            "cap_pass_rows", "cap_below_rows", "cap_pass_common_label_rows")):
        raise probe.ProbeRefusal("formation_reaudit_mismatch")
    return targets


def summarize_history(target: dict, window: list[str], observations: dict[str, dict], audited_dates: set[str]) -> dict:
    """Keep missing sessions unresolved; compute an exact median only for 60/60."""
    if (len(window) != WINDOW_SESSIONS or window != sorted(set(window))
            or any(not _real_date(day) or not "20190102" <= day < target["formation"] for day in window)
            or not observations.keys() <= set(window)):
        raise probe.ProbeRefusal("invalid_history_window")
    missing = [day for day in window if day not in observations]
    values = [item["turnover"] for item in observations.values()]
    if any(not isinstance(value, Decimal) or not value.is_finite() or value < 0 for value in values):
        raise probe.ProbeRefusal("invalid_history_observation")
    values.sort()
    median = None
    if not missing:
        left, right = values[29:31]
        # Align coefficients at their finer exponent, with carry and division room.
        exponent = min(left.as_tuple().exponent, right.as_tuple().exponent)
        digits = max(len(value.as_tuple().digits) + value.as_tuple().exponent - exponent for value in (left, right))
        with localcontext() as context:
            context.prec = digits + 2
            median = (left + right) / 2
    reasons = ["historical_eligibility_unverified", "activity_baseline_unverified"]
    if not target["source_common_label"]:
        reasons.append("source_not_common_label")
    if missing:
        reasons.append("unresolved_history")
    elif median < LIQUIDITY_MIN_KRW:
        reasons.append("liquidity_below_threshold")
    if not target["formation_tradability_proxy"]:
        reasons.append("formation_not_tradable_proxy")
    before = sum(day < target["reported_listing_date"] for day in missing)
    return {**target, "observed_dates": [day for day in window if day in observations],
            "observed_count": len(observations), "missing_dates": missing, "missing_count": len(missing),
            "uncollected_dates": [day for day in missing if day not in audited_dates],
            "missing_before_reported_listing_count": before,
            "missing_on_or_after_reported_listing_count": len(missing) - before,
            "zero_turnover_count": sum(value == 0 for value in values),
            "equal_ohlc_zero_turnover_count": sum(item["equal_ohlc_zero_turnover"] for item in observations.values()),
            "zero_ohl_count": sum(item["zero_ohl"] for item in observations.values()),
            "median_traded_value_krw": str(median) if median is not None else None,
            "liquidity_pass": median >= LIQUIDITY_MIN_KRW if median is not None else None,
            "reasons": reasons}


def _counts(candidates: list[dict]) -> dict:
    return {"cap_pass_rows": len(candidates),
            "complete_history_rows": sum(item["missing_count"] == 0 for item in candidates),
            "incomplete_history_rows": sum(item["missing_count"] != 0 for item in candidates),
            "liquidity_pass_rows": sum(item["liquidity_pass"] is True for item in candidates),
            "liquidity_below_rows": sum(item["liquidity_pass"] is False for item in candidates),
            "formation_tradability_proxy_pass_rows": sum(item["formation_tradability_proxy"] for item in candidates),
            **{field: sum(item[field] for item in candidates) for field in (
                "observed_count", "missing_count", "missing_before_reported_listing_count",
                "missing_on_or_after_reported_listing_count", "zero_turnover_count",
                "equal_ohlc_zero_turnover_count", "zero_ohl_count")}}


def _initial_report() -> dict:
    return {"specification": SPECIFICATION, "started_at": probe.utc_now(), "status": "started",
            "max_requests": MAX_REQUESTS, "requests_attempted": 0, "requests_unattempted": MAX_REQUESTS,
            "http_200_responses": 0, "schema_valid_responses": 0, "days_audited": 0,
            "coverage_complete": False, "candidates_persisted": False,
            "observations": [], "days": [], **formation._uncertified_flags()}


def _recovery_report() -> dict:
    return {**_initial_report(), "specification": RECOVERY_SPECIFICATION,
            "max_requests": RECOVERY_MAX_REQUESTS, "required_new_responses": MAX_REQUESTS - RECOVERY_PREFIX,
            "requests_unattempted": MAX_REQUESTS - RECOVERY_PREFIX, "new_logical_items_attempted": 0,
            "response_counts_scope": "new_invocation", "reused_prefix_validated": False,
            "reused_valid_responses": 0, "combined_valid_responses": 0, "logical_items_remaining": MAX_REQUESTS,
            "transport_retries_used": 0, "unused_wire_attempt_budget": RECOVERY_MAX_REQUESTS,
            "max_transport_retries": RECOVERY_MAX_RETRIES, "transport_retry_cooldown_seconds": RECOVERY_COOLDOWN_SECONDS,
            "recovery_provenance": {"source_sha": RECOVERY_SOURCE_SHA,
                                    "report_sha256": RECOVERY_REPORT_SHA256,
                                    "started_sha256": RECOVERY_STARTED_SHA256,
                                    "ledger_sha256": RECOVERY_LEDGER_SHA256,
                                    "reused_logical_first": 1, "reused_logical_last": RECOVERY_PREFIX}}


def _recovery_rows(path: Path, matrix: list[dict], secret: str) -> Iterator[tuple[dict, list[dict]]]:
    """Stream and revalidate only the immutable first run, never another recovery."""
    path = _private_path(path, directory=True)
    report = _json(_read_private(path / "report.json", RECOVERY_REPORT_SHA256))
    started = _json(_read_private(path / "started.json", RECOVERY_STARTED_SHA256))
    ledger = [_json(line) for line in _read_private(path / "requests.jsonl", RECOVERY_LEDGER_SHA256).splitlines()]
    required = {"specification": SPECIFICATION, "source_sha": RECOVERY_SOURCE_SHA, "max_requests": MAX_REQUESTS,
                "status": "failed", "failure": "transport_failure", "requests_attempted": 550,
                "requests_unattempted": 1130, "http_200_responses": RECOVERY_PREFIX,
                "schema_valid_responses": RECOVERY_PREFIX, "days_audited": 274, "report_persisted": True}
    started_required = {"specification": SPECIFICATION, "source_sha": RECOVERY_SOURCE_SHA, "status": "started",
                        "max_requests": MAX_REQUESTS, "requests_attempted": 0, "requests_unattempted": MAX_REQUESTS,
                        "http_200_responses": 0, "schema_valid_responses": 0, "days_audited": 0,
                        "request_matrix": matrix, "observations": [], "days": [], "host": probe.HOST,
                        "calendar_sha256": CALENDAR_SHA256, "calendar_manifest_sha256": CALENDAR_MANIFEST_SHA256,
                        "formation_report_sha256": FORMATION_REPORT_SHA256,
                        "formation_started_sha256": FORMATION_STARTED_SHA256, "formation_ledger_sha256": FORMATION_LEDGER_SHA256,
                        "timeout_seconds": probe.TIMEOUT_SECONDS, "interval_seconds": probe.INTERVAL_SECONDS,
                        "max_response_bytes": probe.MAX_RESPONSE_BYTES, "min_free_bytes": MIN_FREE_BYTES,
                        "lookback_sessions": WINDOW_SESSIONS, "window": "C[f-60:f]",
                        "liquidity_min_krw": str(LIQUIDITY_MIN_KRW), "market_cap_min_krw": str(formation.MARKET_CAP_MIN_KRW),
                        "threshold_boundaries": "inclusive", "required_invocation_address_space_limit_bytes": 384 * 1024**2}
    if (not isinstance(report, dict) or any(report.get(key) != value for key, value in required.items())
            or not isinstance(started, dict) or any(started.get(key) != value for key, value in started_required.items())
            or any(value.get(flag) is not False for value in (report, started)
                   for flag in (*formation._uncertified_flags(), "coverage_complete", "candidates_persisted"))
            or any(flag in report for flag in ("evidence_write_failure", "artifact_cleanup_unverified", "candidates_sha256"))
            or len(ledger) != 1100 or not isinstance(report.get("observations"), list)
            or len(report["observations"]) != RECOVERY_PREFIX
            or len(matrix) != MAX_REQUESTS or matrix[549] != {"bas_dd": "20210310", "service": "ksq_bydd_trd"}):
        raise probe.ProbeRefusal("invalid_recovery_evidence")
    expected_files = {f"response-{number:04d}-{item['service']}-{item['bas_dd']}.json"
                      for number, item in enumerate(matrix[:RECOVERY_PREFIX], 1)}
    if ({entry.name for entry in path.glob("response-*.json")} != expected_files
            or any(os.path.lexists(path / name) for name in ("candidates.json", "candidates.pending.json", "report.pending.json"))):
        raise probe.ProbeRefusal("invalid_recovery_evidence")
    for index, item in enumerate(matrix[:550]):
        for offset, event in enumerate(("attempt", "response")):
            entry = ledger[index * 2 + offset]
            if (not isinstance(entry, dict) or any(entry.get(key) != value for key, value in item.items())
                    or entry.get("event") != event or entry.get("number") != index + 1
                    or (event == "response" and entry.get("http_status") != (200 if index < RECOVERY_PREFIX else None))):
                raise probe.ProbeRefusal("invalid_recovery_evidence")
    for index, item in enumerate(matrix[:RECOVERY_PREFIX]):
        observation = report["observations"][index]
        if (not isinstance(observation, dict) or observation.get("schema_valid") is not True
                or not isinstance(observation.get("response_sha256"), str) or len(observation["response_sha256"]) != 64):
            raise probe.ProbeRefusal("invalid_recovery_evidence")
        body = _read_private(path / f"response-{index+1:04d}-{item['service']}-{item['bas_dd']}.json", observation["response_sha256"])
        rows = probe.parse_response(body, secret)
        summary = formation.summarize_trading_day(rows, item["service"], item["bas_dd"])
        if (not summary["schema_valid"]
                or summary != {key: value for key, value in observation.items() if key not in ("at", "response_sha256")}):
            raise probe.ProbeRefusal("recovery_reaudit_mismatch")
        yield item, rows


def _retryable_transport(error: BaseException) -> bool:
    # Local resource and filesystem errors must not inherit OSError's broad
    # legacy transport classification. Unknown OS errors stop conservatively.
    cause = error.reason if isinstance(error, URLError) else error
    if isinstance(cause, SSLError):
        return False
    if isinstance(cause, gaierror):
        return cause.errno == EAI_AGAIN
    if not isinstance(cause, OSError):
        return False
    if cause.errno is not None:
        return cause.errno in {
            errno.ENETDOWN, errno.ENETRESET, errno.ENETUNREACH, errno.EHOSTDOWN, errno.EHOSTUNREACH,
            errno.ECONNABORTED, errno.ECONNREFUSED, errno.ECONNRESET, errno.ETIMEDOUT, errno.EPIPE,
        }
    return isinstance(cause, (TimeoutError, ConnectionError))


def _publish_report(output: Path, report: dict) -> None:
    report["report_persisted"] = True
    published = False
    try:
        probe.persist(output / "report.pending.json", report)
        os.replace(output / "report.pending.json", output / "report.json")
        published = True
        probe.sync_directory(output)
    except Exception:
        report.update(status="failed", evidence_write_failure=True, report_persisted=False)
        report.setdefault("failure", "evidence_write_failure")
        for name in (["report.json"] if published else []) + (["candidates.json"] if report["candidates_persisted"] else []):
            try:
                (output / name).unlink()
                probe.sync_directory(output)
            except Exception:
                report["artifact_cleanup_unverified"] = True
        report["candidates_persisted"] = False


def acquire(calendar_manifest: Path, formation_dir: Path, output: Path, *, recovery_dir: Path | None = None) -> dict:
    """Audit the fixed matrix, optionally recovering only the pinned failed run."""
    secret = probe.credential()
    version = probe.source_version()
    windows, matrix = load_calendar(calendar_manifest)
    targets = load_formation_cache(formation_dir, secret)
    recovering = recovery_dir is not None
    report = {**(_recovery_report() if recovering else _initial_report()), "source_sha": version}
    histories = {(target["formation"], target["code"]): {} for target in targets}
    targets_by_formation = {day: [target for target in targets if target["formation"] == day] for day in formation.DATES}
    formation_by_date = {day: formation_day for formation_day, window in windows.items() for day in window}
    audited_dates = set()
    rows_for_day = {}

    def accept_rows(item: dict, rows: list[dict]) -> None:
        day, service = item["bas_dd"], item["service"]
        rows_for_day[service] = rows
        formation._check_relations(rows_for_day)
        if service == probe.SERVICES[1]:
            formation_day = formation_by_date[day]
            target_codes = {target["code"] for target in targets_by_formation[formation_day]}
            for market_rows in rows_for_day.values():
                for row in market_rows:
                    if row["ISU_CD"] in target_codes:
                        histories[formation_day, row["ISU_CD"]][day] = _bar_metrics(row)
            audited_dates.add(day)
            report["days_audited"] += 1
            rows_for_day.clear()
        if recovering:
            report["combined_valid_responses"] += 1

    prefix = RECOVERY_PREFIX if recovering else 0
    if recovering:
        recovery_dir = _private_path(recovery_dir, directory=True)
        if Path(os.path.abspath(output)).is_relative_to(recovery_dir):
            raise probe.ProbeRefusal("output_overlaps_recovery_evidence")
        # Exhaust the stream before creating output or making any new request.
        # The last KOSPI half remains in rows_for_day for logical item 550.
        for item, rows in _recovery_rows(recovery_dir, matrix, secret):
            accept_rows(item, rows)
            report["reused_valid_responses"] += 1
        if report["reused_valid_responses"] != RECOVERY_PREFIX or report["days_audited"] != 274:
            raise probe.ProbeRefusal("incomplete_recovery_prefix")
        report["reused_prefix_validated"] = True
        report["logical_items_remaining"] = MAX_REQUESTS - report["combined_valid_responses"]
    output = probe.prepare_output(output)
    try:
        probe.persist(output / "started.json", {
            **report, "host": probe.HOST, "calendar_sha256": CALENDAR_SHA256,
            "calendar_manifest_sha256": CALENDAR_MANIFEST_SHA256,
            "formation_report_sha256": FORMATION_REPORT_SHA256,
            "formation_started_sha256": FORMATION_STARTED_SHA256, "formation_ledger_sha256": FORMATION_LEDGER_SHA256,
            "formation_cap_pass_rows": len(targets),
            "formation_cap_pass_common_label_rows": sum(target["source_common_label"] for target in targets),
            "request_matrix": matrix, **({"new_request_matrix": matrix[prefix:]} if recovering else {}),
            "lookback_sessions": WINDOW_SESSIONS,
            "liquidity_min_krw": str(LIQUIDITY_MIN_KRW), "market_cap_min_krw": str(formation.MARKET_CAP_MIN_KRW),
            "threshold_boundaries": "inclusive", "window": "C[f-60:f]",
            "min_free_bytes": MIN_FREE_BYTES, "required_invocation_address_space_limit_bytes": 384 * 1024**2,
            "timeout_seconds": probe.TIMEOUT_SECONDS, "interval_seconds": probe.INTERVAL_SECONDS,
            "max_response_bytes": probe.MAX_RESPONSE_BYTES,
        })
        opener = probe.make_opener()
        for logical_number, item in enumerate(matrix[prefix:], prefix + 1):
            day, service = item["bas_dd"], item["service"]
            retrying = False
            while True:
                if report["requests_attempted"] >= report["max_requests"]:
                    raise probe.ProbeRefusal("request_limit")
                if report["requests_attempted"]:
                    time.sleep(RECOVERY_COOLDOWN_SECONDS if retrying else probe.INTERVAL_SECONDS)
                if shutil.disk_usage(output)[2] < MIN_FREE_BYTES:
                    raise probe.ProbeRefusal("low_disk_space")
                number = report["requests_attempted"] + 1
                request_record = {"number": number, **item, **({"logical_number": logical_number} if recovering else {})}
                probe.persist(output / "requests.jsonl", {**request_record, "event": "attempt", "at": probe.utc_now()}, append=True)
                url = f"{probe.HOST}/svc/apis/sto/{service}?basDd={day}"
                request = Request(url, headers={"AUTH_KEY": secret}, method="GET")
                report["requests_attempted"] = number
                if recovering:
                    report["new_logical_items_attempted"] += not retrying
                    report["transport_retries_used"] += retrying
                status, transport_failure = None, False
                try:
                    with opener.open(request, timeout=probe.TIMEOUT_SECONDS) as response:
                        status = response.status
                        if status != 200 or response.geturl() != url:
                            raise probe.ProbeRefusal("unexpected_response")
                        report["http_200_responses"] += 1
                        body = response.read(probe.MAX_RESPONSE_BYTES + 1)
                except HTTPError as error:
                    status = error.code
                    error.close()
                    reason = "redirect_rejected" if 300 <= status < 400 else ("access_failure" if status in (401, 403) else "http_failure")
                    raise probe.ProbeRefusal(reason) from None
                except (URLError, TimeoutError, OSError) as error:
                    if recovering and not _retryable_transport(error):
                        raise probe.ProbeRefusal("local_io_failure") from None
                    transport_failure = True
                except MemoryError:
                    if recovering:
                        raise probe.ProbeRefusal("local_io_failure") from None
                    raise
                finally:
                    probe.persist(output / "requests.jsonl", {
                        **request_record, "event": "response", "at": probe.utc_now(), "http_status": status,
                        **({"failure": "transport_failure"} if recovering and transport_failure else {}),
                    }, append=True)
                if not transport_failure:
                    break
                if not recovering or report["transport_retries_used"] >= RECOVERY_MAX_RETRIES:
                    raise probe.ProbeRefusal("transport_failure")
                retrying = True
            if len(body) > probe.MAX_RESPONSE_BYTES:
                raise probe.ProbeRefusal("response_too_large")
            rows = probe.parse_response(body, secret)
            probe.persist(output / f"response-{logical_number:04d}-{service}-{day}.json", body, raw=True)
            observation = formation.summarize_trading_day(rows, service, day)
            observation.update(at=probe.utc_now(), response_sha256=hashlib.sha256(body).hexdigest())
            if recovering:
                observation.update(logical_number=logical_number, number=number)
            report["observations"].append(observation)
            if not observation["schema_valid"]:
                raise probe.ProbeRefusal("schema_failure")
            report["schema_valid_responses"] += 1
            accept_rows(item, rows)
        if recovering and report["combined_valid_responses"] != MAX_REQUESTS:
            raise probe.ProbeRefusal("incomplete_recovery")
        report["status"] = "liquidity_audited"
    except probe.ProbeRefusal as error:
        report.update(status="failed", failure=str(error))
    except OSError:
        report.update(status="failed", failure="local_io_failure")
    except Exception:
        report.update(status="failed", failure="unexpected_failure")
    try:
        candidates = [summarize_history(target, windows[target["formation"]], histories[target["formation"], target["code"]], audited_dates) for target in targets]
        report["totals"] = _counts(candidates)
        report["common_label_totals"] = _counts([item for item in candidates if item["source_common_label"]])
        report["days"] = [{"formation": day,
                           "all_cap_pass": _counts([item for item in candidates if item["formation"] == day]),
                           "common_label": _counts([item for item in candidates if item["formation"] == day and item["source_common_label"]])}
                          for day in formation.DATES]
        report["coverage_complete"] = report["status"] == "liquidity_audited" and not report["totals"]["incomplete_history_rows"]
        if report["status"] == "liquidity_audited":
            artifact = {"specification": report["specification"], "candidates": candidates, **formation._uncertified_flags()}
            probe.persist(output / "candidates.pending.json", artifact)
            os.replace(output / "candidates.pending.json", output / "candidates.json")
            report["candidates_persisted"] = True
            probe.sync_directory(output)
            report["candidates_sha256"] = hashlib.sha256(probe.canonical(artifact) + b"\n").hexdigest()
    except Exception:
        report.update(status="failed", evidence_write_failure=True)
        report.setdefault("failure", "evidence_write_failure")
        if report["candidates_persisted"]:
            try:
                (output / "candidates.json").unlink()
                probe.sync_directory(output)
            except Exception:
                report["artifact_cleanup_unverified"] = True
        report["candidates_persisted"] = False
    report["finished_at"] = probe.utc_now()
    if recovering:
        report["logical_items_remaining"] = MAX_REQUESTS - report["combined_valid_responses"]
        report["unused_wire_attempt_budget"] = RECOVERY_MAX_REQUESTS - report["requests_attempted"]
        report["requests_unattempted"] = MAX_REQUESTS - RECOVERY_PREFIX - report["new_logical_items_attempted"]
    else:
        report["requests_unattempted"] = MAX_REQUESTS - report["requests_attempted"]
    _publish_report(output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    recovering = False
    try:
        parser = probe.SafeParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--calendar-manifest", required=True, type=Path)
        parser.add_argument("--formation-dir", required=True, type=Path)
        parser.add_argument("--output-dir", required=True, type=Path)
        parser.add_argument("--recovery-dir", type=Path)
        args = parser.parse_args(argv)
        recovering = args.recovery_dir is not None
        report = acquire(args.calendar_manifest, args.formation_dir, args.output_dir, recovery_dir=args.recovery_dir)
    except probe.ProbeRefusal as error:
        report = {**(_recovery_report() if recovering else _initial_report()), "status": "failed", "failure": str(error), "report_persisted": False}
    except Exception:
        report = {**(_recovery_report() if recovering else _initial_report()), "status": "failed", "failure": "unexpected_failure", "report_persisted": False}
    print(probe.canonical(report).decode())
    return 0 if report["status"] == "liquidity_audited" else 1


if __name__ == "__main__":
    raise SystemExit(main())
