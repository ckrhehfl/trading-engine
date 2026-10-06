"""Fixed, quotation-only diagnostic for Task AI's six raw/adjusted anchors.

No symbol/date/host override, wide probe, return calculation or database write.
Evidence is created before price access, and a failed run cannot look completed.
Credentials use the existing operator-entered environment and KisSession cache.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from data.bingx_klines import KlineRow
from data.kis_klines import (
    ADJUSTED, RAW, INTER_REQUEST_DELAY_S, PAPER_HOST, KisSession,
    fetch_daily_page, trading_date_to_ms,
)
from data.krx_scan import in_continuous_session


@dataclass(frozen=True)
class Anchor:
    code: str
    day: str


ANCHORS = (
    Anchor("033660", "20210805"), Anchor("316140", "20210827"),
    Anchor("367480", "20230817"), Anchor("146060", "20230908"),
    Anchor("476470", "20250619"), Anchor("462310", "20250709"),
)
NEGATIVE_CONTROLS = ("999999", "ZZZZZZ", "000000")
POSITIVE_CONTROL = "005930"
BASES = (ADJUSTED, RAW)
FIELDS = ("open", "high", "low", "close", "volume", "quote_volume")
ROOT = Path(__file__).resolve().parents[2]


class ProbeRefusal(RuntimeError):
    """Evidence is insufficient; no usable basis is produced."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def persist(path: Path, value: object, *, append: bool = False) -> None:
    """Flush every diagnostic boundary, including before the first price read."""
    with path.open("a" if append else "x", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def outside_session() -> None:
    if in_continuous_session():
        raise ProbeRefusal("protected_session")


def quotation(row: KlineRow, day: str) -> dict[str, str]:
    if row.open_time_ms != trading_date_to_ms(day):
        raise ProbeRefusal("wrong_date")
    values = {field: getattr(row, field) for field in FIELDS}
    if any(not isinstance(v, Decimal) or not v.is_finite() for v in values.values()):
        raise ProbeRefusal("invalid_number")
    if any(v <= 0 for v in values.values()):
        # These are observed, traded anchors, not stale marks during a halt.
        raise ProbeRefusal("nonpositive_or_unobservable_anchor")
    if not (row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high):
        raise ProbeRefusal("inconsistent_ohlc")
    # Numeric equality should not depend on a vendor changing decimal padding.
    def number(value: Decimal) -> str:
        fixed = format(value, "f")
        return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
    return {field: number(value) for field, value in values.items()}


def archived_anchors(scan_path: Path) -> dict[str, dict[str, str]]:
    """Read only the six exact anchors, after validating the spent panel."""
    uri = scan_path.resolve(strict=True).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        if conn.execute("SELECT start,end FROM scan_panel WHERE id=1").fetchone() != (
            "20190102", "20260918",
        ):
            raise ProbeRefusal("wrong_or_reserved_panel")
        result = {}
        for anchor in ANCHORS:
            if conn.execute("SELECT status FROM scan_progress WHERE code=?", (anchor.code,)).fetchone() != ("done",):
                raise ProbeRefusal("incomplete_scan")
            rows = conn.execute(
                "SELECT open,high,low,close,volume,turnover FROM scan_bars WHERE code=? AND bsop_date=?",
                (anchor.code, anchor.day),
            ).fetchall()
            if len(rows) != 1:
                raise ProbeRefusal("missing_or_duplicate_archived_anchor")
            row = KlineRow(open_time_ms=trading_date_to_ms(anchor.day),
                           **dict(zip(FIELDS, (Decimal(v) for v in rows[0]), strict=True)))
            result[anchor.code] = quotation(row, anchor.day)
        return result


def source_version() -> str:
    sources = ["python/data", "python/uv.lock", "python/pyproject.toml",
               ".planning/rd-ai-merger-identity-evidence.md", ".planning/rd-aj-basis-probe.md"]
    if subprocess.check_output([
        "git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all", "--", *sources,
    ]).strip():
        raise ProbeRefusal("uncommitted_sources")
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def acquire(scan_path: Path, output: Path, session_factory: Callable[[], KisSession]) -> dict:
    """Execute the fixed matrix; callers cannot enlarge the data window."""
    outside_session()
    version = source_version()
    if output.resolve().is_relative_to(ROOT):
        raise ProbeRefusal("evidence_must_be_outside_checkout")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    plan = {
        "purpose": "price-basis diagnostic only; no returns or strategy selection",
        "host": PAPER_HOST, "anchors": [vars(a) for a in ANCHORS],
        "negative_controls": NEGATIVE_CONTROLS, "positive_control": POSITIVE_CONTROL,
        "bases": BASES, "planned_logical_calls": 72,
        "scan_path": str(scan_path.resolve()), "code_version": version,
        "interpretation": "an anchor hash is not the full portfolio dataset hash",
    }
    persist(output / "started.json", {**plan, "started_at": utc_now(), "plan_sha256": digest(plan)})
    counts = {"logical_calls": 0, "quotation_http_attempts": 0}
    current = None
    observations: dict[tuple[str, str], dict[str, str]] = {}
    try:
        archive = archived_anchors(scan_path)
        persist(output / "archive.json", {"anchors": archive, "sha256": digest(archive)})
        outside_session()
        session = session_factory()
        if session.host != PAPER_HOST:
            raise ProbeRefusal("unexpected_host")

        def before_attempt() -> None:
            outside_session()
            time.sleep(INTER_REQUEST_DELAY_S)
            outside_session()
            counts["quotation_http_attempts"] += 1
            persist(output / "events.jsonl", {
                "event": "quotation_http_attempt", "at": utc_now(), "request": current, **counts,
            }, append=True)

        def ask(code: str, day: str, basis: str, role: str) -> dict[str, str] | None:
            nonlocal current
            outside_session()
            current = {"code": code, "start": day, "end": day,
                       "adjusted": basis, "role": role, "is_index": False}
            counts["logical_calls"] += 1
            persist(output / "events.jsonl", {"event": "request_started", "at": utc_now(),
                                              "request": current, **counts}, append=True)
            rows = fetch_daily_page(
                session, code, day, day, adjusted=basis,
                before_attempt=before_attempt, before_headers=outside_session,
            )
            if role == "negative":
                if rows:
                    raise ProbeRefusal("negative_control_has_rows")
                value = None
            else:
                if len(rows) != 1:
                    raise ProbeRefusal("missing_or_duplicate_response")
                value = quotation(rows[0], day)
            persist(output / "events.jsonl", {"event": "response_validated", "at": utc_now(),
                                              "request": current, "quotation": value}, append=True)
            return value

        for anchor in ANCHORS:
            # All control shapes for this date pass before either target basis.
            for basis in BASES:
                for code in NEGATIVE_CONTROLS:
                    ask(code, anchor.day, basis, "negative")
                ask(POSITIVE_CONTROL, anchor.day, basis, "positive")
            for basis in BASES:
                value = ask(anchor.code, anchor.day, basis, "target")
                observations[anchor.code, basis] = value
                if basis == ADJUSTED and value != archive[anchor.code]:
                    raise ProbeRefusal("archived_adjusted_mismatch")
        for anchor in ANCHORS:
            for basis in BASES:
                if ask(anchor.code, anchor.day, basis, "repeat") != observations[anchor.code, basis]:
                    raise ProbeRefusal("repeat_mismatch")

        evidence = [{"code": a.code, "day": a.day, "adjusted": observations[a.code, ADJUSTED],
                     "raw": observations[a.code, RAW]} for a in ANCHORS]
        result = {"status": "acquired_consistent_anchors", "completed_at": utc_now(),
                  "code_version": version, "plan_sha256": digest(plan), **counts,
                  "archive_anchor_sha256": digest(archive), "quotation_sha256": digest(evidence),
                  "anchors": evidence, "basis_certified": False,
                  "interpretation": "requires independent adjustment-convention and lot-dataset verification; no replay authorized",
                  "count_scope": "quotation GET attempts only; existing session authentication/cache is not instrumented"}
        # Publish success only after the complete record has been synced.
        # A disk-full/fsync failure must not leave a success-shaped result.
        pending = output / ".result.pending"
        persist(pending, result)
        pending.rename(output / "result.json")
        return result
    except BaseException as exc:
        # Exception strings/objects may carry credentials or vendor payloads.
        # Only fixed refusal reasons constructed here may reach the evidence.
        reasons = {"protected_session", "wrong_date", "invalid_number",
                   "nonpositive_or_unobservable_anchor", "inconsistent_ohlc",
                   "wrong_or_reserved_panel", "incomplete_scan", "missing_or_duplicate_archived_anchor",
                   "unexpected_host", "negative_control_has_rows", "missing_or_duplicate_response",
                   "archived_adjusted_mismatch", "repeat_mismatch"}
        reason = str(exc) if type(exc) is ProbeRefusal and str(exc) in reasons else "upstream_or_interrupted"
        persist(output / "failure.json", {"status": "rejected", "at": utc_now(),
                                          "reason": reason, "request": current, **counts})
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="new directory outside the checkout")
    args = parser.parse_args(argv)
    app_key, app_secret = os.environ.get("KIS_APP_KEY", ""), os.environ.get("KIS_APP_SECRET", "")
    if not app_key or not app_secret:
        parser.error("operator-entered KIS_APP_KEY and KIS_APP_SECRET are required")
    try:
        result = acquire(args.scan_db, args.output_dir, lambda: KisSession(app_key, app_secret, host=PAPER_HOST))
    except (Exception, KeyboardInterrupt):
        # Avoid a traceback chaining a vendor response, token or request object.
        print("Basis diagnostic refused; inspect sanitized evidence if its directory was created.", file=sys.stderr)
        return 1
    print(canonical({"status": result["status"], "logical_calls": result["logical_calls"],
                     "quotation_http_attempts": result["quotation_http_attempts"], "basis_certified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
