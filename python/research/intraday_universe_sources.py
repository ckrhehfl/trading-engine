"""Fixed monthly KRX source snapshots joined to saved intraday row metadata.

This is a provisional source-labelled top-20 coverage study, never a return,
liquidity, historical eligibility, calendar-completeness or promotion claim.
Actual use requires the registered paths and a durable discovery start.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any, Iterator
from urllib.error import HTTPError
from urllib.request import Request

from data import krx_formation_audit as formation
from data import krx_openapi_probe as probe
from research import experiment_log


SPECIFICATION_ID = "intraday-universe-sources-v1"
MODE = "discovery-universe-source-and-coverage-only"
SPECIFICATION_PATH = Path(__file__).resolve().parents[2] / "configs/research/discovery/intraday-universe-sources-v1.json"
DATES = ("20250829", "20250930", "20251031", "20251128", "20251230", "20260130",
         "20260227", "20260331", "20260430", "20260529", "20260630", "20260731", "20260831")
SERVICES = ("stk_bydd_trd", "ksq_bydd_trd", "stk_isu_base_info", "ksq_isu_base_info")
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
CALENDAR = {"symbol": "KRX-INDEX:0001", "interval": "1d", "start": "2025-08-01",
            "end": "2026-09-23", "columns": ["symbol", "interval", "open_time_ms", "fetched_at"]}
INVENTORY_SHA256 = "4c32bbb8eee4dad7177d1f3fe2c61b95ce003f998ffcf8e93df88c6eae82ccad"
REGISTERED_PATHS = {
    "source_database": "/home/minjun4897/trading-engine/python/data/var/klines.sqlite3",
    "canonical_runs_path": "/home/minjun4897/trading-engine/runs/experiments.jsonl",
    "inventory_path": "/home/minjun4897/research-evidence/intraday-ca-inventory-20261020-v1/inventory.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cb-universe-20261020-v1",
}
REQUESTS = {"maximum": 52, "timeout_seconds": 20, "pause_seconds": 1,
            "maximum_response_bytes": 8_388_608, "automatic_retries": 0,
            "maximum_total_response_bytes": 268_435_456}
_KST = timezone(timedelta(hours=9))
_PROGRESS_STEPS, _MAX_QUERY_STEPS, _MAX_QUERY_SECONDS = 1_000, 10_000_000, 30.0
_MAX_CALENDAR_ROWS, _MAX_INVENTORY_BYTES = 400, 32 * 1024 * 1024
_FLAGS = {"historical_eligibility_certified": False, "liquidity_certified": False,
          "historical_revision_status_certified": False, "calendar_completeness_certified": False,
          "event_or_return_access": False, "performance_certified": False, "promotion_allowed": False}


class SourcesRefusal(RuntimeError):
    """Fixed non-sensitive reason codes only."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_specification(path: Path) -> tuple[dict, str]:
    if path.resolve() != SPECIFICATION_PATH.resolve():
        raise SourcesRefusal("unregistered_specification_path")
    content = path.read_bytes()
    spec = json.loads(content)
    expected = {
        "id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "calendar": CALENDAR,
        "snapshot_dates": list(DATES), "services": list(SERVICES), "requests": REQUESTS,
        "selection": {"count": 20, "sort": ["market_cap_krw_descending", "short_code_ascending"],
                      "source_labels": {"SECUGRP_NM": "주권", "KIND_STKCERT_TP_NM": "보통주",
                                        "ISIN_prefix": "KR7", "ISIN_issue_position_9": "0"},
                      "liquidity_certified": False, "historical_classification_certified": False,
                      "historical_revision_status_certified": False, "no_replacement_for_missing_minutes": True},
        "timing": {"variants": ["D+1", "D+2"], "available_at_kst": "08:30:00",
                   "selected_at_kst": "08:30:00", "evidence_level": "assumed",
                   "historical_api_availability_proven": False},
        "inventory_file_sha256": INVENTORY_SHA256, **REGISTERED_PATHS,
        "execution": {"cpu_seconds": 120, "wall_seconds": 1500, "address_space_mib": 384,
                      "nice": 10, "oom_score_adj": 1000,
                      "source_checkout": "/home/minjun4897/research-checkouts/activity-discovery-20261005",
                      "collector_changes": False},
        "minute_price_access": False, "event_or_return_access": False,
        "eligibility_or_performance_certification": False,
    }
    # Canonical bytes also distinguish true from 1 and 52 from 52.0.
    if probe.canonical(spec) != probe.canonical(expected):
        raise SourcesRefusal("specification_scope_mismatch")
    return spec, hashlib.sha256(content).hexdigest()


def _authorize_calendar(action: int, table: str | None, column: str | None,
                        database: str | None, source: str | None) -> int:
    allowed = (action == sqlite3.SQLITE_SELECT or
               action == sqlite3.SQLITE_READ and database == "main" and source is None
               and table == "klines" and column in CALENDAR["columns"] or
               action == sqlite3.SQLITE_TRANSACTION and table in {"BEGIN", "ROLLBACK"})
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


@contextmanager
def _calendar_connection(path: Path) -> Iterator[sqlite3.Connection]:
    database = path.resolve(strict=True)
    if not database.is_file():
        raise SourcesRefusal("calendar_database_not_regular")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.set_authorizer(_authorize_calendar)
        deadline, operations = time.monotonic() + _MAX_QUERY_SECONDS, 0

        def budget() -> int:
            nonlocal operations
            operations += _PROGRESS_STEPS
            return int(operations >= _MAX_QUERY_STEPS or time.monotonic() >= deadline)

        connection.set_progress_handler(budget, _PROGRESS_STEPS)
        connection.execute("BEGIN")
        try:
            yield connection
        finally:
            connection.set_progress_handler(None, 0)
            connection.execute("ROLLBACK")
    finally:
        connection.close()


def read_calendar(path: Path) -> list[dict]:
    """Read only the fixed reference's four metadata columns in one snapshot."""
    start = int(datetime(2025, 8, 1, tzinfo=_KST).timestamp() * 1000)
    end = int(datetime(2026, 9, 24, tzinfo=_KST).timestamp() * 1000)
    result, seen = [], set()
    with _calendar_connection(path) as connection:
        for symbol, interval, stamp, fetched in connection.execute(
            "SELECT symbol, interval, open_time_ms, fetched_at FROM klines "
            "WHERE symbol='KRX-INDEX:0001' AND interval='1d' "
            "AND open_time_ms>=? AND open_time_ms<? ORDER BY open_time_ms", (start, end),
        ):
            if (type(stamp) is not int or stamp % 86_400_000 or
                    fetched is not None and not isinstance(fetched, str)):
                raise SourcesRefusal("invalid_calendar_metadata")
            day = datetime.fromtimestamp(stamp / 1000, _KST).date().isoformat()
            if day in seen or len(result) >= _MAX_CALENDAR_ROWS:
                raise SourcesRefusal("calendar_duplicate_or_limit")
            seen.add(day)
            result.append({"symbol": symbol, "interval": interval, "open_time_ms": stamp,
                           "fetched_at": fetched, "date": day})
    if not result:
        raise SourcesRefusal("empty_reference_calendar")
    for snapshot in DATES:
        day = datetime.strptime(snapshot, "%Y%m%d").date().isoformat()
        observed = [item["date"] for item in result if item["date"][:7] == day[:7]]
        if not observed or max(observed) != day:
            raise SourcesRefusal("snapshot_not_last_observed_month_session")
    return result


def read_inventory(path: Path, sessions: set[str]) -> dict[tuple[str, str], int]:
    if path.stat().st_size > _MAX_INVENTORY_BYTES:
        raise SourcesRefusal("inventory_size_limit")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != INVENTORY_SHA256:
        raise SourcesRefusal("inventory_file_hash_mismatch")
    envelope = json.loads(content)
    inventory = envelope["inventory"]
    summary, rows = inventory["summary"], inventory["rows"]
    digest = hashlib.sha256(probe.canonical({"summary": summary, "rows": rows})).hexdigest()
    if (envelope.get("study_id") != "intraday-scope-inventory-v1" or
            inventory["inventory_sha256"] != digest or summary.get("window") != WINDOW or
            summary.get("mode") != "discovery-metadata-only" or not isinstance(rows, list) or
            not rows or len(rows) > 200_000):
        raise SourcesRefusal("invalid_inventory_envelope")
    saved = {}
    for row in rows:
        symbol, day, count = row["symbol"], row["kst_date"], row["row_count"]
        offsets = row["timestamp_second_offset_counts"]
        if (not isinstance(symbol, str) or not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) or
                row.get("interval") != "1m" or not WINDOW["start"] <= day <= WINDOW["end"] or
                day not in sessions or type(count) is not int or count <= 0 or
                not isinstance(offsets, dict) or not offsets or
                any(not re.fullmatch(r"[0-9]|[1-5][0-9]", offset) or type(value) is not int or value <= 0
                    for offset, value in offsets.items()) or sum(offsets.values()) != count or
                (symbol, day) in saved):
            raise SourcesRefusal("invalid_inventory_row")
        saved[symbol, day] = count
    if any(summary.get(key) != value for key, value in {
        "row_count": sum(saved.values()), "aggregate_row_count": len(saved),
        "symbol_count": len({key[0] for key in saved}), "kst_date_count": len({key[1] for key in saved}),
    }.items()):
        raise SourcesRefusal("inventory_summary_mismatch")
    return saved


def _integer(row: dict, field: str) -> int:
    value = row.get(field)
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", value):
        raise SourcesRefusal("invalid_positive_integer")
    result = int(value.replace(",", ""))
    if result <= 0:
        raise SourcesRefusal("invalid_positive_integer")
    return result


def _validate_service(rows: list[dict], service: str, day: str) -> None:
    validator = formation.summarize_trading_day if service in SERVICES[:2] else formation._summarize_rows
    if not validator(rows, service, day)["schema_valid"]:
        raise SourcesRefusal("source_schema_failure")
    for row in rows:
        _integer(row, "LIST_SHRS")
        if service in SERVICES[:2]:
            if _integer(row, "MKTCAP") != _integer(row, "TDD_CLSPRC") * _integer(row, "LIST_SHRS"):
                raise SourcesRefusal("capitalization_mismatch")


def rank_snapshot(day: str, rows_by_service: dict[str, list[dict]], evidence: dict[str, dict]) -> dict:
    """Exact integer caps, complete one-to-one joins, fixed top 20, no liquidity filter."""
    if day not in DATES or set(rows_by_service) != set(SERVICES) or set(evidence) != set(SERVICES):
        raise SourcesRefusal("incomplete_snapshot")
    for service in SERVICES:
        _validate_service(rows_by_service[service], service, day)
    formation._check_relations(rows_by_service)
    candidates = []
    for market, trade_service, basic_service in zip(formation.MARKETS, SERVICES[:2], SERVICES[2:], strict=True):
        basics = {row["ISU_SRT_CD"]: row for row in rows_by_service[basic_service]}
        for row in rows_by_service[trade_service]:
            code = row["ISU_CD"]
            basic = basics[code]
            if (basic["SECUGRP_NM"] == "주권" and basic["KIND_STKCERT_TP_NM"] == "보통주"
                    and basic["ISU_CD"].startswith("KR7") and basic["ISU_CD"][8] == "0"):
                candidates.append({"symbol": "KRX:" + code, "short_code": code, "market": market,
                                   "isin": basic["ISU_CD"], "name": basic["ISU_NM"],
                                   "market_cap_krw": str(_integer(row, "MKTCAP")),
                                   "listed_shares": str(_integer(row, "LIST_SHRS")),
                                   "source_labels": {key: basic[key] for key in ("SECUGRP_NM", "KIND_STKCERT_TP_NM")},
                                   "source_services": [trade_service, basic_service]})
    candidates.sort(key=lambda row: (-int(row["market_cap_krw"]), row["short_code"]))
    if len(candidates) < 20:
        raise SourcesRefusal("fewer_than_twenty_source_label_candidates")
    members = [{**row, "rank": index + 1} for index, row in enumerate(candidates[:20])]
    return {"observation_date": datetime.strptime(day, "%Y%m%d").date().isoformat(),
            "members": members, "candidate_count": len(candidates), "sources": evidence,
            "rank_21": candidates[20] if len(candidates) > 20 else None,
            "cutoff_tied_candidate_count": sum(row["market_cap_krw"] == members[-1]["market_cap_krw"] for row in candidates),
            "classification_caveat": "Labels do not prove operating-company, SPAC or REIT history.",
            "basic_returned_observation_date_available": False, **_FLAGS}


def join_coverage(calendar: list[dict], snapshots: list[dict], saved: dict[tuple[str, str], int]) -> dict:
    """Latest snapshot actually available at 08:30; D+2 retains the prior month."""
    sessions = [item["date"] for item in calendar]
    variants = {}
    for lag in (1, 2):
        available = []
        for snapshot in snapshots:
            later = [day for day in sessions if day > snapshot["observation_date"]]
            if len(later) < lag:
                raise SourcesRefusal("insufficient_reference_sessions_for_availability")
            available.append((later[lag - 1] + "T08:30:00+09:00", snapshot))
        days, counts, months, targets, stored_symbols, missing_symbols = [], Counter(), {}, set(), set(), set()
        for day in sessions:
            if not WINDOW["start"] <= day <= WINDOW["end"]:
                continue
            selected_at = day + "T08:30:00+09:00"
            eligible = [item for item in available if item[0] <= selected_at]
            if not eligible:
                raise SourcesRefusal("no_available_snapshot")
            available_at, snapshot = max(eligible, key=lambda item: item[1]["observation_date"])
            month = months.setdefault(day[:7], Counter())
            presence = []
            for member in snapshot["members"]:
                symbol = member["symbol"]
                count = saved.get((symbol, day))
                state = "stored_rows_completeness_unverified" if count is not None else "no_stored_rows_unknown_reason"
                counts["target_symbol_sessions"] += 1
                month["target_symbol_sessions"] += 1
                key = "stored_symbol_sessions" if count is not None else "missing_symbol_sessions"
                counts[key] += 1
                month[key] += 1
                targets.add(symbol)
                (stored_symbols if count is not None else missing_symbols).add(symbol)
                presence.append({"symbol": symbol, "rank": member["rank"], "stored_row_count": count, "state": state})
            sources = snapshot["sources"]
            days.append({"session": day, "selected_at": selected_at, "observation_date": snapshot["observation_date"],
                         "available_at": available_at, "retrieved_at": {key: value["retrieved_at"] for key, value in sources.items()},
                         "source": "KRX OpenAPI source-labelled monthly snapshot", "is_final": None,
                         "data_vintage": {key: value["sha256"] for key, value in sources.items()},
                         "evidence_level": "assumed", "targets": presence})
        variants[f"D+{lag}"] = {"days": days, "summary": {
            **{key: counts[key] for key in ("target_symbol_sessions", "stored_symbol_sessions", "missing_symbol_sessions")},
            "unique_target_symbols": len(targets), "unique_stored_symbols": len(stored_symbols),
            "unique_missing_symbols": len(missing_symbols), "reference_sessions": len(days),
            "by_month": {month: {key: values[key] for key in ("target_symbol_sessions", "stored_symbol_sessions", "missing_symbol_sessions")}
                         for month, values in sorted(months.items())}}}
    return variants


def _acquire(output: Path) -> tuple[list[dict], dict]:
    secret, opener = probe.credential(), probe.make_opener()
    snapshots, total, number = [], 0, 0
    for day in DATES:
        rows_by_service, evidence = {}, {}
        for service in SERVICES:
            if number:
                time.sleep(REQUESTS["pause_seconds"])
            number += 1
            attempt = {"number": number, "service": service, "requested_bas_dd": day}
            probe.persist(output / "requests.jsonl", {**attempt, "event": "attempt", "at": probe.utc_now()}, append=True)
            url = f"{probe.HOST}/svc/apis/sto/{service}?basDd={day}"
            try:
                with opener.open(Request(url, headers={"AUTH_KEY": secret}, method="GET"),
                                 timeout=REQUESTS["timeout_seconds"]) as response:
                    if response.status != 200 or response.geturl() != url:
                        raise SourcesRefusal("unexpected_http_response")
                    body = response.read(min(REQUESTS["maximum_response_bytes"],
                                             REQUESTS["maximum_total_response_bytes"] - total) + 1)
                retrieved_at = probe.utc_now()
                total += len(body)
                if len(body) > REQUESTS["maximum_response_bytes"] or total > REQUESTS["maximum_total_response_bytes"]:
                    raise SourcesRefusal("response_byte_limit")
                rows = probe.parse_response(body, secret)
                name = f"response-{number:02d}-{service}-{day}.json"
                probe.persist(output / name, body, raw=True)
                evidence[service] = {"service": service, "requested_bas_dd": day, "raw_response": name,
                                     "sha256": hashlib.sha256(body).hexdigest(), "retrieved_at": retrieved_at,
                                     "bytes": len(body)}
                rows_by_service[service] = rows
                _validate_service(rows, service, day)
                formation._check_relations(rows_by_service)
            except BaseException as exc:
                if isinstance(exc, HTTPError):
                    exc.close()
                reason = (str(exc) if isinstance(exc, (SourcesRefusal, probe.ProbeRefusal))
                          else "request_failed")
                probe.persist(output / "requests.jsonl", {**attempt, "event": "failed", "at": probe.utc_now(),
                                                           **evidence.get(service, {}), "reason": reason}, append=True)
                raise SourcesRefusal(reason) from None
            probe.persist(output / "requests.jsonl", {**attempt, "event": "received", **evidence[service]}, append=True)
        snapshots.append(rank_snapshot(day, rows_by_service, evidence))
    return snapshots, {"requests_attempted": number, "total_response_bytes": total}


def run_sources(*, db_path: str | Path, specification_path: str | Path, inventory_path: str | Path,
                output_directory: str | Path, runs_path: str | Path,
                before_completion: Callable[[], None] | None = None) -> dict[str, Any]:
    """Durable start precedes DB/inventory/credential/HTTP reads; failure cannot complete."""
    database, spec_path, inventory, output, log = map(Path, (db_path, specification_path, inventory_path, output_directory, runs_path))
    spec, spec_sha = _load_specification(spec_path)
    for path, key in ((database, "source_database"), (inventory, "inventory_path"),
                      (output, "output_directory"), (log, "canonical_runs_path")):
        if not path.is_absolute() or path.resolve() != Path(spec[key]).resolve():
            raise SourcesRefusal("unregistered_input_output_path")
    paths = (database, spec_path, inventory, output, log)
    for index, path in enumerate(paths):
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise SourcesRefusal("symlinked_path")
        for other in paths[:index]:
            if path.resolve() == other.resolve() or path.exists() and other.exists() and path.samefile(other):
                raise SourcesRefusal("aliased_paths")
    python_root = Path(__file__).resolve().parents[1]
    sources = (Path(__file__), Path(probe.__file__), Path(formation.__file__), Path(experiment_log.__file__),
               python_root / "data/__init__.py", python_root / "research/__init__.py",
               python_root / "pyproject.toml", python_root / "uv.lock", spec_path)
    pins = {str(path.resolve()): _sha(path) for path in sources}
    executable = Path(sys.executable).resolve(strict=True)
    runtime = {"python_version": sys.version, "python_implementation": sys.implementation.name,
               "executable": str(executable), "executable_sha256": _sha(executable),
               "sqlite_version": sqlite3.sqlite_version}
    if pins[str(spec_path.resolve())] != spec_sha:
        raise SourcesRefusal("specification_changed")

    def evaluate() -> dict:
        destination = probe.prepare_output(output)
        calendar = read_calendar(database)
        saved = read_inventory(inventory, {row["date"] for row in calendar})
        snapshots, acquisition = _acquire(destination)
        report = {"study_id": SPECIFICATION_ID, "mode": MODE, "specification_sha256": spec_sha,
                  "inventory_file_sha256": INVENTORY_SHA256, "source_sha256": pins, "runtime": runtime,
                  "calendar": {"reference": CALENDAR, "rows": calendar,
                               "description": "Observed index sessions; not a complete-calendar certificate."},
                  "inventory_observed_union_dates": len({day for _, day in saved}),
                  "responses": [{"filename": snapshot["sources"][service]["raw_response"],
                                 "number": index * 4 + offset + 1,
                                 **{key: value for key, value in snapshot["sources"][service].items() if key != "raw_response"}}
                                for index, snapshot in enumerate(snapshots) for offset, service in enumerate(SERVICES)],
                  "snapshots": snapshots, "variants": join_coverage(calendar, snapshots, saved),
                  "acquisition": acquisition, "historical_api_availability_proven": False, **_FLAGS}
        probe.persist(destination / "report.json", report)
        report_path = destination / "report.json"
        report_sha = _sha(report_path)
        if before_completion is not None:
            before_completion()
        if any(_sha(path) != pins[str(path.resolve())] for path in sources) or _sha(inventory) != INVENTORY_SHA256:
            raise SourcesRefusal("source_specification_or_inventory_changed")
        if _sha(executable) != runtime["executable_sha256"]:
            raise SourcesRefusal("runtime_changed_before_completion")
        if _sha(report_path) != report_sha:
            raise SourcesRefusal("report_changed_before_completion")
        return {"output_path": str(report_path), "output_sha256": report_sha,
                "summaries": {key: value["summary"] for key, value in report["variants"].items()},
                **acquisition, **_FLAGS}

    return experiment_log.run_discovery_trial(
        study_id=SPECIFICATION_ID, specification_sha256=spec_sha, parameters={
            "mode": MODE, "paths": {key: spec[key] for key in REGISTERED_PATHS},
            "source_sha256": pins, "runtime": runtime, "inventory_file_sha256": INVENTORY_SHA256,
            "maximum_api_requests": 52, "minute_price_access": False, "event_or_return_access": False},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("db", "specification", "inventory", "output-directory", "runs-path"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        record = run_sources(db_path=args.db, specification_path=args.specification, inventory_path=args.inventory,
                             output_directory=args.output_directory, runs_path=args.runs_path)
    except Exception as exc:
        parser.exit(1, f"sources failed ({type(exc).__name__}); no automatic retry\n")
    print(json.dumps({"run_id": record["run_id"], "status": record["status"],
                      "output_sha256": record["result"]["output_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
