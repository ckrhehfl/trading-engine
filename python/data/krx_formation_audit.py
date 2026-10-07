"""Audit fixed KRX formation-date sources, without certifying a universe.

Only --output-dir is accepted. The existing probe supplies environment-only
authentication, bounded transport and private evidence helpers. No database,
liquidity history, strategy returns or order endpoint is accessed.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
import hashlib
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from data import krx_openapi_probe as probe


SPECIFICATION = "krx-large-liquid-formations-v1"
DATES = (
    "20190402", "20191004", "20200407", "20201013", "20210415", "20211020",
    "20220422", "20221027", "20230427", "20231103", "20240510", "20241115",
    "20250527", "20251201",
)
MAX_REQUESTS = 56
MARKET_CAP_MIN_KRW = Decimal("5000000000000")
CALENDAR_SHA256 = "fceaf0d8748f203001afbf11a66c44aa756f7f55b21e22aaee04152186506ce4"
CALENDAR_MANIFEST_SHA256 = "ccdcee1681ad9fa16aef837e4aceaf4d2f40847974046a1714d1352c3df01882"
MARKETS = ("KOSPI", "KOSDAQ")


def _uncertified_flags() -> dict:
    return {"universe_ready": False, "historical_coverage_certified": False,
            "historical_eligibility_certified": False}


def _number(row: dict, field: str) -> Decimal:
    # Called only after numeric_counts has validated the entire response.
    return Decimal(row[field].replace(",", ""))


def _summarize(rows: list[dict], service: str, day: str) -> dict:
    if (day not in DATES or service not in probe.SERVICES or not isinstance(rows, list)
            or any(not isinstance(row, dict) for row in rows)):
        raise probe.ProbeRefusal("schema_failure")
    observation = probe.summarize(rows, service, day)
    is_trade = service in probe.SERVICES[:2]
    market = MARKETS[probe.SERVICES.index(service) % 2]
    market_field = "MKT_NM" if is_trade else "MKT_TP_NM"
    errors = {
        "market_mismatches": sum(row.get(market_field) != market for row in rows),
        "identifier_format_errors": sum(
            probe.id_kind(row.get("ISU_CD")) != ("short6" if is_trade else "isin12")
            or (not is_trade and probe.id_kind(row.get("ISU_SRT_CD")) != "short6")
            for row in rows),
        "empty_required_labels": sum(
            not isinstance(row.get(field), str) or not row[field].strip()
            for row in rows
            for field in (("SECUGRP_NM", "KIND_STKCERT_TP_NM") if not is_trade else ())
        ),
        # KOSPI's section is legitimately empty in the saved source samples.
        "empty_kosdaq_sections": sum(
            not isinstance(row.get("SECT_TP_NM"), str) or not row["SECT_TP_NM"].strip()
            for row in rows) if market == "KOSDAQ" else 0,
    }
    numeric = observation["numeric"]
    if is_trade:
        numeric.update({field: probe.numeric_counts(rows, field, positive=field == "TDD_CLSPRC")
                        for field in ("TDD_CLSPRC", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC")})
    for field in (("LIST_SHRS", "ACC_TRDVOL") if is_trade else ("LIST_SHRS",)):
        errors[f"nonintegral_{field.lower()}"] = sum(
            _number(row, field) != _number(row, field).to_integral_value()
            for row in rows
        ) if not any(numeric[field].values()) else 0
    if not is_trade:
        errors["future_listing_dates"] = observation["listing_dates_after_requested_date"]
    observation["formation_errors"] = errors
    observation["schema_valid"] = bool(
        observation["schema_valid"] and rows and not any(errors.values())
        and not any(count for counts in numeric.values() for count in counts.values())
    )
    return observation


def _check_relations(rows_by_service: dict[str, list[dict]]) -> int:
    """Reject each detectable mismatch immediately, including on partial days."""
    for services, field in ((probe.SERVICES[:2], "ISU_CD"),
                            (probe.SERVICES[2:], "ISU_SRT_CD"),
                            (probe.SERVICES[2:], "ISU_CD")):
        sets = [{row[field] for row in rows_by_service.get(service, [])} for service in services]
        if sets[0] & sets[1]:
            raise probe.ProbeRefusal("cross_market_duplicate")
    joins = 0
    for trade_service, basic_service in zip(probe.SERVICES[:2], probe.SERVICES[2:], strict=True):
        trades = {row["ISU_CD"]: row for row in rows_by_service.get(trade_service, [])}
        for row in trades.values():
            close, shares = _number(row, "TDD_CLSPRC"), _number(row, "LIST_SHRS")
            # Decimal's default 28-digit context must not round the product.
            with localcontext() as context:
                context.prec = len(close.as_tuple().digits) + len(shares.as_tuple().digits)
                if _number(row, "MKTCAP") != close * shares:
                    raise probe.ProbeRefusal("capitalization_mismatch")
        if trade_service not in rows_by_service or basic_service not in rows_by_service:
            continue
        basics = {row["ISU_SRT_CD"]: row for row in rows_by_service[basic_service]}
        if trades.keys() != basics.keys():
            raise probe.ProbeRefusal("membership_mismatch")
        if any(_number(row, "LIST_SHRS") != _number(basics[code], "LIST_SHRS")
               for code, row in trades.items()):
            raise probe.ProbeRefusal("listed_shares_mismatch")
        joins += 1
    return joins


def audit_day(day: str, rows_by_service: dict[str, list[dict]]) -> dict:
    """Validate all source rows before counting the inclusive size boundary.

    A common-share label is counted separately; it cannot establish domestic
    operating-company status, historical type eligibility or liquidity.
    """
    if day not in DATES or set(rows_by_service) != set(probe.SERVICES):
        raise probe.ProbeRefusal("incomplete_day")
    for service in probe.SERVICES:
        if not _summarize(rows_by_service[service], service, day)["schema_valid"]:
            raise probe.ProbeRefusal("schema_failure")
    _check_relations(rows_by_service)
    markets = []
    for market, trade_service, basic_service in zip(MARKETS, probe.SERVICES[:2], probe.SERVICES[2:], strict=True):
        trades = rows_by_service[trade_service]
        basics = {row["ISU_SRT_CD"]: row for row in rows_by_service[basic_service]}
        passes = [row for row in trades if _number(row, "MKTCAP") >= MARKET_CAP_MIN_KRW]
        markets.append({
            "market": market, "trade_rows": len(trades), "basic_rows": len(basics),
            "joined_rows": len(trades), "cap_pass_rows": len(passes),
            "cap_below_rows": len(trades) - len(passes),
            "cap_pass_common_label_rows": sum(
                basics[row["ISU_CD"]]["KIND_STKCERT_TP_NM"] == "보통주" for row in passes),
        })
    return {
        "bas_dd": day, "markets": markets, **_uncertified_flags(),
        **{field: sum(item[field] for item in markets) for field in (
            "trade_rows", "basic_rows", "joined_rows", "cap_pass_rows", "cap_below_rows",
            "cap_pass_common_label_rows")},
    }


def _initial_report() -> dict:
    return {
        "specification": SPECIFICATION, "started_at": probe.utc_now(), "status": "started",
        "max_requests": MAX_REQUESTS, "requests_attempted": 0, "requests_unattempted": MAX_REQUESTS,
        "http_200_responses": 0, "schema_valid_responses": 0, "days_audited": 0,
        "market_joins_valid": 0, "cap_pass_rows": 0, "cap_below_rows": 0,
        "cap_pass_common_label_rows": 0, "observations": [], "days": [], **_uncertified_flags(),
    }


def acquire(output: Path) -> dict:
    secret = probe.credential()
    version = probe.source_version()
    output = probe.prepare_output(output)
    report = _initial_report()
    report["source_sha"] = version
    try:
        probe.persist(output / "started.json", {
            **report, "host": probe.HOST, "calendar_sha256": CALENDAR_SHA256,
            "calendar_manifest_sha256": CALENDAR_MANIFEST_SHA256,
            "formation_expression": "range(60, end_index - 126, 126)",
            "calendar_start": "20190102", "calendar_end": "20260918",
            "market_cap_min_krw": str(MARKET_CAP_MIN_KRW), "market_cap_boundary": "inclusive",
            "future_universe_contract": {
                "markets": list(MARKETS), "security_class": "domestic_operating_company_common_stock",
                "liquidity_measure": "preceding_60_trading_day_median_traded_value",
                "liquidity_min_krw": "10000000000", "decision_time": "next_session_08:30_Asia/Seoul",
                "liquidity_or_type_eligibility_checked": False,
            },
            "request_matrix": [{"service": service, "bas_dd": day} for day in DATES for service in probe.SERVICES],
            "timeout_seconds": probe.TIMEOUT_SECONDS, "interval_seconds": probe.INTERVAL_SECONDS,
            "max_response_bytes": probe.MAX_RESPONSE_BYTES,
        })
        opener = probe.make_opener()
        for day in DATES:
            rows_for_day = {}
            for service in probe.SERVICES:
                if report["requests_attempted"] >= MAX_REQUESTS:
                    raise probe.ProbeRefusal("request_limit")
                if report["requests_attempted"]:
                    time.sleep(probe.INTERVAL_SECONDS)
                number = report["requests_attempted"] + 1
                request_record = {"number": number, "service": service, "bas_dd": day}
                probe.persist(output / "requests.jsonl", {
                    **request_record, "event": "attempt", "at": probe.utc_now(),
                }, append=True)
                url = f"{probe.HOST}/svc/apis/sto/{service}?basDd={day}"
                request = Request(url, headers={"AUTH_KEY": secret}, method="GET")
                report["requests_attempted"] = number
                status = None
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
                    reason = "redirect_rejected" if 300 <= status < 400 else (
                        "access_failure" if status in (401, 403) else "http_failure")
                    raise probe.ProbeRefusal(reason) from None
                except (URLError, TimeoutError, OSError):
                    raise probe.ProbeRefusal("transport_failure") from None
                finally:
                    probe.persist(output / "requests.jsonl", {
                        **request_record, "event": "response", "at": probe.utc_now(), "http_status": status,
                    }, append=True)
                if len(body) > probe.MAX_RESPONSE_BYTES:
                    raise probe.ProbeRefusal("response_too_large")
                rows = probe.parse_response(body, secret)
                # Persist secret-free, valid-envelope raw evidence before field validation.
                probe.persist(output / f"response-{number:02d}-{service}-{day}.json", body, raw=True)
                observation = _summarize(rows, service, day)
                observation.update(at=probe.utc_now(), response_sha256=hashlib.sha256(body).hexdigest())
                report["observations"].append(observation)
                if not observation["schema_valid"]:
                    raise probe.ProbeRefusal("schema_failure")
                report["schema_valid_responses"] += 1
                rows_for_day[service] = rows
                report["market_joins_valid"] = report["days_audited"] * 2 + _check_relations(rows_for_day)
            result = audit_day(day, rows_for_day)
            report["days"].append(result)
            report["days_audited"] += 1
            for field in ("cap_pass_rows", "cap_below_rows", "cap_pass_common_label_rows"):
                report[field] += result[field]
        report["status"] = "formations_audited"
    except probe.ProbeRefusal as error:
        report.update(status="failed", failure=str(error))
    except OSError:
        report.update(status="failed", failure="local_io_failure")
    except Exception:
        report.update(status="failed", failure="unexpected_failure")
    report["finished_at"] = probe.utc_now()
    report["requests_unattempted"] = MAX_REQUESTS - report["requests_attempted"]
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
        if published:
            try:
                (output / "report.json").unlink()
                probe.sync_directory(output)
            except Exception:
                report["report_cleanup_unverified"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    try:
        parser = probe.SafeParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--output-dir", required=True, type=Path)
        args = parser.parse_args(argv)
        report = acquire(args.output_dir)
    except probe.ProbeRefusal as error:
        report = {**_initial_report(), "status": "failed", "failure": str(error), "report_persisted": False}
    except Exception:
        report = {**_initial_report(), "status": "failed", "failure": "unexpected_failure", "report_persisted": False}
    print(probe.canonical(report).decode())
    return 0 if report["status"] == "formations_audited" else 1


if __name__ == "__main__":
    raise SystemExit(main())
