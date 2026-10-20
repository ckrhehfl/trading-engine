"""Bounded provisional intraday frequency and own price-label dispersion.

No fills, profits, comparison family, eligibility certification or promotion.
Minute-open labels, the session schedule and two-minute availability lag are
explicit assumptions. Actual execution requires a separately committed spec.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import sqlite3
from statistics import NormalDist, stdev
import sys
import time

from research import experiment_log
from research import intraday_pattern_events as core


SPECIFICATION_ID = "intraday-pattern-calibration-v1"
MODE = "price-label-calibration"
ROOT = Path(__file__).resolve().parents[2]
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-pattern-calibration-v1.json"
WINDOW = {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}
COLUMNS = ("symbol", "interval", "open_time_ms", "open", "high", "low", "close", "fetched_at")
PARAMETERS = {"range_minutes": 30, "lookback_minutes": 30, "drop_bps": "200",
              "recovery_bps": "50", "availability_lag_minutes": 2,
              "decision_minutes": 240, "label_minutes": 120, "block_sessions": 5,
              "bootstrap_draws": 2000, "bootstrap_seed": 0, "cost_floor_bps": "33"}
REGISTERED_PATHS = {
    "source_database": "/home/minjun4897/trading-engine/python/data/var/klines.sqlite3",
    "upstream_path": "/home/minjun4897/research-evidence/intraday-cb-universe-20261020-v1/report.json",
    "output_directory": "/home/minjun4897/research-evidence/intraday-cc-calibration-20261020-v1",
    "canonical_runs_path": "/home/minjun4897/trading-engine/runs/experiments.jsonl",
}
RESOURCES = {"maximum_unique_symbols": 260, "maximum_target_days": 6000,
             "maximum_rows": 3_600_000, "maximum_session_rows": 600,
             "maximum_query_seconds": 10, "maximum_query_vm_steps": 5_000_000,
             "maximum_upstream_bytes": 33_554_432, "maximum_compressed_bytes": 536_870_912,
             "minimum_free_bytes": 1_073_741_824, "address_space_mib": 512,
             "cpu_seconds": 300, "wall_seconds": 1200, "nice": 10, "automatic_retries": 0}
FLAGS = {key: False for key in ("historical_eligibility_certified", "liquidity_certified",
         "historical_revision_status_certified", "calendar_completeness_certified",
         "historical_api_availability_proven", "price_basis_certified",
         "performance_certified", "promotion_allowed", "full_family_certified")}
FAMILIES = ("opening_range_breakout", "post_drop_recovery")
KST = timezone(timedelta(hours=9))
MINUTE, FIVE = timedelta(minutes=1), timedelta(minutes=5)


class CalibrationRefusal(RuntimeError):
    """Fixed reason codes; no raw data or credentials in errors."""


def _canonical(value: object) -> bytes:
    def encode(item: object) -> object:
        if isinstance(item, Fraction):
            return {"numerator": str(item.numerator), "denominator": str(item.denominator)}
        return experiment_log._json_default(item)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=encode).encode()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_spec(spec: Mapping) -> None:
    """Exact schema, excluding only the next registration's not-yet-known hash."""
    digest = spec.get("upstream_sha256")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise CalibrationRefusal("invalid_upstream_sha256")
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
                "parameters": PARAMETERS, "allowed_columns": list(COLUMNS),
                "resources": RESOURCES, "upstream_sha256": digest, **REGISTERED_PATHS}
    if _canonical(spec) != _canonical(expected):
        raise CalibrationRefusal("specification_scope_mismatch")


def session_open(day: str) -> datetime:
    """Assumed regular clock, with two explicitly assumed exceptional opens."""
    hour = 10 if day in {"2025-11-13", "2026-01-02"} else 9
    return datetime.combine(date.fromisoformat(day), datetime.min.time(), KST).replace(hour=hour)


def session_close(day: str) -> datetime:
    """Assumed close: Nov 13 is shifted to 16:30; Jan 2 remains 15:30."""
    return session_open(day).replace(hour=16 if day == "2025-11-13" else 15, minute=30)


@dataclass(frozen=True)
class Session:
    opening: datetime
    minutes: dict[datetime, tuple[Decimal, ...] | None]
    prefix: tuple[core.Bar, ...]
    gaps: tuple[dict, ...]
    source_offsets_ms: tuple[int, ...]


def aggregate_session(rawrows: Iterable[Mapping], session_open: datetime,
                      availability_lag_minutes: int = 2) -> Session:
    """Floor source stamps to assumed open-minute labels, never fill gaps.

    Duplicate minute buckets, including distinct seconds, are conflicts even
    when their prices agree. Invalid future minutes remain diagnostics rather
    than raising an error that could erase a previously observed signal.
    """
    opening = session_open
    if opening.utcoffset() is None or type(availability_lag_minutes) is not int or availability_lag_minutes < 0:
        raise CalibrationRefusal("invalid_session_assumptions")
    buckets: dict[datetime, list[Mapping]] = {}
    offsets: list[int] = []
    closing = session_close(opening.astimezone(KST).date().isoformat())
    symbol = None
    for row in rawrows:
        stamp = row.get("open_time_ms")
        if type(stamp) is not int:
            raise CalibrationRefusal("invalid_raw_timestamp")
        minute = datetime.fromtimestamp(stamp // 1000, KST).replace(second=0, microsecond=0)
        if not opening <= minute < closing or row.get("interval") != "1m":
            raise CalibrationRefusal("raw_row_outside_session")
        if not isinstance(row.get("symbol"), str) or not re.fullmatch(r"KRX:[0-9A-Z]{6}", row["symbol"]):
            raise CalibrationRefusal("invalid_raw_symbol")
        if symbol is not None and row["symbol"] != symbol:
            raise CalibrationRefusal("mixed_session_symbols")
        symbol = row["symbol"]
        buckets.setdefault(minute, []).append(row)
        offsets.append(stamp % 60_000)
    minutes, gaps = {}, []
    minute = opening
    while minute < closing:
        rows, prices, reason = buckets.get(minute, []), None, "missing"
        if len(rows) > 1:
            reason = "conflict"
        elif rows:
            try:
                values = [rows[0][key] for key in COLUMNS[3:7]]
                if not all(isinstance(value, str) for value in values):
                    raise ValueError
                parsed = tuple(Decimal(value) for value in values)
                op, hi, lo, cl = parsed
                if not all(value.is_finite() and value > 0 for value in parsed) or not lo <= min(op, cl) <= max(op, cl) <= hi:
                    raise ValueError
                prices = parsed
            except (KeyError, ValueError, InvalidOperation):
                reason = "invalid_price"
        minutes[minute] = prices
        if prices is None:
            if gaps and gaps[-1]["end"] == minute and gaps[-1]["state"] == reason:
                gaps[-1]["end"] = minute + MINUTE
            else:
                gaps.append({"start": minute, "end": minute + MINUTE, "state": reason})
        minute += MINUTE
    prefix, start = [], opening
    while start + FIVE <= closing:
        values = [minutes[start + index * MINUTE] for index in range(5)]
        if any(value is None for value in values):
            break
        end = start + FIVE
        prefix.append(core.Bar(start, end, end + availability_lag_minutes * MINUTE,
                               values[0][0], max(value[1] for value in values),
                               min(value[2] for value in values), values[-1][3]))
        start = end
    return Session(opening, minutes, tuple(prefix), tuple(gaps), tuple(offsets))


def price_label(session: Session, decision_at: datetime) -> dict:
    """Observed 120-minute same-session price change, never an executable fill."""
    elapsed = (decision_at - session.opening) // FIVE
    entry = session.opening + (elapsed + 1) * FIVE
    end = entry + timedelta(minutes=PARAMETERS["label_minutes"])
    labels = [session.minutes.get(entry + index * MINUTE) for index in range(120)]
    result = {"entry_label_start": entry, "end_label": end, "status": "unknown", "price_change": None}
    if any(value is None for value in labels):
        return result
    result.update(status="known", entry_open=labels[0][0], terminal_close=labels[-1][3],
                  price_change=Fraction(labels[-1][3]) / Fraction(labels[0][0]) - 1)
    return result


def calibrate_session(session: Session) -> dict:
    cutoff = session.opening + timedelta(minutes=PARAMETERS["decision_minutes"])
    bars = tuple(bar for bar in session.prefix if bar.available_at <= cutoff)
    required = int((cutoff - session.opening - 2 * MINUTE) // FIVE)
    complete = len(bars) == required
    events = (
        core.detect_opening_range_breakout(bars, session_open=session.opening, entry_cutoff=cutoff, range_minutes=30),
        core.detect_post_drop_recovery(bars, session_open=session.opening, entry_cutoff=cutoff,
                                      lookback_minutes=30, drop_bps=Decimal("200"), recovery_bps=Decimal("50")),
    )
    return {family: {"status": "signal" if event else "known_no_signal" if complete else "unknown",
                     "event": asdict(event) if event else None,
                     "label": price_label(session, event.decision_at) if event else None,
                     "required_bars": required, "valid_prefix_bars": len(bars)}
            for family, event in zip(FAMILIES, events, strict=True)}


def dispersion(records: list[dict], reference_sessions: list[str]) -> dict:
    """Conditional own-label dispersion; whole reference dates include zero events.

    Prices and ratios stay exact until this statistical boundary. Cluster SE
    uses G/(G-1) * sum_g(sum_i(r_i - mean))**2 / N**2.
    """
    if len(set(reference_sessions)) != len(reference_sessions) or reference_sessions != sorted(reference_sessions):
        raise CalibrationRefusal("invalid_reference_sessions")
    totals = {day: [0.0, 0] for day in reference_sessions}
    values = []
    for row in records:
        if row["session"] not in totals:
            raise CalibrationRefusal("label_outside_reference_sessions")
        if row["label"] is not None:
            if not isinstance(row["label"], Fraction):
                raise CalibrationRefusal("label_must_be_exact_ratio")
            value = float(row["label"])
            values.append(value)
            totals[row["session"]][0] += value
            totals[row["session"]][1] += 1
    n, groups = len(values), len(totals)
    known_groups = sum(count > 0 for _, count in totals.values())
    result = {"events": len(records), "known_labels": n, "unknown_labels": len(records) - n,
              "reference_sessions": groups, "known_label_sessions": known_groups,
              "event_sd": None, "naive_se": None,
              "session_cluster_se": None, "bootstrap_se": None, "design_se": None,
              "corrected_to_naive_se_ratio": None, "mde_bps": None,
              "bootstrap_zero_label_draws": 0, "cost_floor_bps_assumed": "33",
              "would_require_operator_underpower_choice": False,
              "planning_constraint": "insufficient_known_labels_or_reference_sessions",
              "planning_constraint_scope": "conditional own labels; not final family readiness or an adopted waiver",
              "family_adopted": False, "full_family_certified": False}
    if n < 2 or groups < 2:
        return result
    sd, mean = stdev(values), math.fsum(values) / n
    naive = sd / math.sqrt(n)
    result.update(event_sd=sd, naive_se=naive)
    if known_groups < 2:
        result["planning_constraint"] = "insufficient_known_label_sessions"
        return result  # Cross-stock labels on one date provide no date replication.
    cluster = math.sqrt(groups / (groups - 1) * math.fsum(
        (total - count * mean) ** 2 for total, count in totals.values())) / n
    daily = list(totals.values())
    blocks = {size: [(math.fsum(daily[(index + offset) % groups][0] for offset in range(size)),
                      sum(daily[(index + offset) % groups][1] for offset in range(size)))
                     for index in range(groups)] for size in {5, groups % 5} if size}
    randomizer, draws = random.Random(0), []
    for _ in range(2000):
        sampled = [blocks[5][randomizer.randrange(groups)] for _ in range(groups // 5)]
        if groups % 5:
            sampled.append(blocks[groups % 5][randomizer.randrange(groups)])
        count = sum(item[1] for item in sampled)
        if count:
            draws.append(math.fsum(item[0] for item in sampled) / count)
    bootstrap = stdev(draws) if len(draws) >= 2 else None
    design = max(naive, cluster, bootstrap) if bootstrap is not None else None
    result.update(event_sd=sd, naive_se=naive, session_cluster_se=cluster,
                  bootstrap_se=bootstrap, design_se=design,
                  corrected_to_naive_se_ratio=design / naive if naive and design is not None else None,
                  mde_bps=(NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) * design * 10000
                  if design is not None else None, bootstrap_zero_label_draws=2000 - len(draws))
    result["would_require_operator_underpower_choice"] = result["mde_bps"] is not None and result["mde_bps"] > 33
    result["planning_constraint"] = ("insufficient_valid_bootstrap_draws" if design is None else
        "own_label_mde_exceeds_assumed_cost_floor" if result["would_require_operator_underpower_choice"]
        else "no_underpower_choice_inferred")
    return result


def _target_matrix(upstream: dict) -> tuple[dict, dict[str, set[str]]]:
    if upstream.get("study_id") != "intraday-universe-sources-v1":
        raise CalibrationRefusal("wrong_upstream_study")
    upstream_flags = ("historical_eligibility_certified", "liquidity_certified",
                      "historical_revision_status_certified", "calendar_completeness_certified",
                      "historical_api_availability_proven", "performance_certified", "promotion_allowed")
    if any(upstream.get(key) is not False for key in upstream_flags):
        raise CalibrationRefusal("upstream_certification_changed")
    calendar = [item["date"] for item in upstream["calendar"]["rows"] if WINDOW["start"] <= item["date"] <= WINDOW["end"]]
    if not calendar or calendar != sorted(set(calendar)):
        raise CalibrationRefusal("invalid_upstream_calendar")
    variants, union = upstream["variants"], {}
    if set(variants) != {"D+1", "D+2"}:
        raise CalibrationRefusal("invalid_timing_variants")
    for variant in variants.values():
        if [day["session"] for day in variant["days"]] != calendar:
            raise CalibrationRefusal("target_matrix_lost_sessions")
        for day in variant["days"]:
            opening = session_open(day["session"])
            if datetime.fromisoformat(day["available_at"]) > datetime.fromisoformat(day["selected_at"]) or datetime.fromisoformat(day["selected_at"]) >= opening:
                raise CalibrationRefusal("noncausal_membership")
            targets = day["targets"]
            symbols = [item["symbol"] for item in targets]
            if len(symbols) != 20 or len(set(symbols)) != 20 or {item["rank"] for item in targets} != set(range(1, 21)):
                raise CalibrationRefusal("target_population_changed")
            if any(not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol) for symbol in symbols):
                raise CalibrationRefusal("invalid_target_symbol")
            union.setdefault(day["session"], set()).update(symbols)
    if sum(map(len, union.values())) > 6000 or len(set().union(*union.values())) > 260:
        raise CalibrationRefusal("target_budget_exceeded")
    return variants, union


def _authorize(action: int, table: str | None, column: str | None,
               database: str | None, source: str | None) -> int:
    allowed = action == sqlite3.SQLITE_SELECT or (action == sqlite3.SQLITE_READ
        and database == "main" and source is None and table == "klines" and column in COLUMNS)
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def _read_session(connection: sqlite3.Connection, symbol: str, day: str) -> tuple[list[dict], str]:
    opening = session_open(day)
    start, end = (int(value.timestamp()) * 1000 for value in (opening, session_close(day)))
    deadline, steps = time.monotonic() + 10, 0
    def budget() -> int:
        nonlocal steps
        steps += 1000
        return int(steps >= 5_000_000 or time.monotonic() >= deadline)
    connection.set_progress_handler(budget, 1000)
    cursor = None
    try:
        cursor = connection.execute(
            "SELECT symbol, interval, open_time_ms, open, high, low, close, fetched_at FROM main.klines "
            "WHERE symbol=? AND interval='1m' AND open_time_ms>=? AND open_time_ms<? ORDER BY open_time_ms LIMIT 601",
            (symbol, start, end))
        rows = [dict(zip(COLUMNS, row, strict=True)) for row in cursor]
        if len(rows) > 600:
            raise CalibrationRefusal("session_row_budget_exceeded")
        return rows, datetime.now(timezone.utc).isoformat()
    finally:
        if cursor is not None:
            cursor.close()  # End this single SELECT's snapshot before the next target.
        connection.set_progress_handler(None, 0)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_file(path: Path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb")


def run_calibration(*, db_path: str | Path, specification_path: str | Path,
                    upstream_path: str | Path, output_directory: str | Path,
                    runs_path: str | Path, before_completion: Callable[[], None] | None = None) -> dict:
    """No actual invocation until registration, reviews, CI and normal merge.

    Parent runner enforces OS memory/CPU/wall/nice limits. This loader enforces
    query, row, archive and cooperative elapsed-time budgets without retries.
    """
    database, spec_path, upstream_path, output, log = map(Path, (db_path, specification_path, upstream_path, output_directory, runs_path))
    if spec_path.resolve() != SPECIFICATION_PATH.resolve():
        raise CalibrationRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (database, spec_path, upstream_path, output, log)
    for path, key in zip((database, upstream_path, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path.resolve() != Path(spec[key]).resolve():
            raise CalibrationRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise CalibrationRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise CalibrationRefusal("aliased_paths")
    sources = (Path(__file__), Path(core.__file__), Path(experiment_log.__file__),
               ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock", spec_path)
    pins = {str(path): _sha(path) for path in sources}
    executable = Path(sys.executable).resolve(strict=True)
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
               "executable": str(executable), "executable_sha256": _sha(executable), "sqlite": sqlite3.sqlite_version}
    if pins[str(spec_path)] != spec_sha:
        raise CalibrationRefusal("specification_changed")

    def evaluate() -> dict:
        if not database.is_file() or not output.parent.is_dir() or os.path.lexists(output):
            raise CalibrationRefusal("missing_database_or_unsafe_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise CalibrationRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        if output.stat().st_mode & 0o777 != 0o700:
            raise CalibrationRefusal("unsafe_output_permissions")
        _sync_directory(output.parent)
        with upstream_path.open("rb") as stream:
            upstream_bytes = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        if len(upstream_bytes) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(upstream_bytes).hexdigest() != spec["upstream_sha256"]:
            raise CalibrationRefusal("upstream_size_or_hash_mismatch")
        upstream = json.loads(upstream_bytes)
        variants, union = _target_matrix(upstream)
        started_wall, started_cpu = time.monotonic(), time.process_time()
        report = {"study_id": SPECIFICATION_ID, "mode": MODE, "specification_sha256": spec_sha,
                  "upstream_sha256": spec["upstream_sha256"], "parameters": PARAMETERS,
                  "source_sha256": pins, "runtime": runtime, **FLAGS,
                  "assumptions": {"minute_label": "source timestamp floored to open minute",
                    "bar_available_at": "bar end plus two minutes",
                    "session_schedule": "09:00-15:30; 2025-11-13 10:00-16:30; 2026-01-02 10:00-15:30; assumed, not historical calendar certification",
                    "source_fetched_at": "collector retrieval stamp, not proof of historical availability; retrieved_at is this query's receipt",
                    "cost_floor": "33bp planning only: 1.5bp fees and 5bp slippage per side plus 20bp sell tax; no historical net PnL"},
                  "variants": {key: {"days": [], "families": {}} for key in variants}}
        events = {key: {family: [] for family in FAMILIES} for key in variants}
        archive_path, stream_digest, row_count = output / "inputs.jsonl.gz", hashlib.sha256(), 0
        connection = sqlite3.connect(database.resolve(strict=True).as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16384)
            connection.set_authorizer(_authorize)
            with _private_file(archive_path) as rawstream:
                try:
                    with gzip.GzipFile(filename="", mode="wb", fileobj=rawstream, compresslevel=1, mtime=0) as archive:
                        for index, (day, symbols) in enumerate(union.items()):
                            day_results = {}
                            for symbol in sorted(symbols):
                                if time.monotonic() - started_wall >= 1200 or time.process_time() - started_cpu >= 300:
                                    raise CalibrationRefusal("execution_budget_exceeded")
                                rows, received = _read_session(connection, symbol, day)
                                row_count += len(rows)
                                if row_count > 3_600_000:
                                    raise CalibrationRefusal("total_row_budget_exceeded")
                                chunk = _canonical({"symbol": symbol, "session": day, "observation_date": day,
                                    "available_at": None, "retrieved_at": received, "source": "collector klines",
                                    "is_final": None, "evidence_level": "assumed", "rows": rows}) + b"\n"
                                chunk_sha = hashlib.sha256(chunk).hexdigest()
                                archive.write(chunk)
                                stream_digest.update(chunk)
                                if rawstream.tell() > RESOURCES["maximum_compressed_bytes"]:
                                    raise CalibrationRefusal("compressed_output_budget_exceeded")
                                session = aggregate_session(rows, session_open(day))
                                day_results[symbol] = {"symbol": symbol, "session": day, "retrieved_at": received,
                                    "source": "collector klines; provisional source-labelled targets", "is_final": None,
                                    "data_vintage": chunk_sha, "evidence_level": "assumed", "input_rows": len(rows),
                                    "source_fetched_at": sorted({row["fetched_at"] for row in rows if row["fetched_at"] is not None}),
                                    "source_offset_counts_ms": dict(Counter(session.source_offsets_ms)), "gaps": session.gaps,
                                    "families": calibrate_session(session)}
                                for finding in day_results[symbol]["families"].values():
                                    if finding["event"] is not None:
                                        finding["event"].update(observation_date=day,
                                            available_at=finding["event"]["decision_at"], retrieved_at=received,
                                            source="collector klines; assumed completed five-minute bars", is_final=None,
                                            data_vintage=chunk_sha, evidence_level="assumed",
                                            parameters_sha256=hashlib.sha256(_canonical(PARAMETERS)).hexdigest())
                            for key, variant in variants.items():
                                membership = variant["days"][index]
                                for target in membership["targets"]:
                                    result = {**day_results[target["symbol"]], "observation_date": day, "available_at": None,
                                              "membership_observation_date": membership["observation_date"],
                                              "membership_available_at": membership["available_at"],
                                              "membership_source": {name: value for name, value in membership.items() if name != "targets"}}
                                    report["variants"][key]["days"].append(result)
                                    for family in FAMILIES:
                                        finding = result["families"][family]
                                        if finding["status"] == "signal":
                                            events[key][family].append({"session": day, "symbol": target["symbol"],
                                                "label": finding["label"]["price_change"]})
                finally:
                    rawstream.flush()
                    os.fsync(rawstream.fileno())  # Partial input remains durable on failure.
        finally:
            connection.close()
            _sync_directory(output)
        archive_sha = _sha(archive_path)
        if archive_path.stat().st_size > RESOURCES["maximum_compressed_bytes"]:
            raise CalibrationRefusal("compressed_output_budget_exceeded")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                monthly, weekly, counts = {}, {}, Counter()
                for row in variant["days"]:
                    state = row["families"][family]["status"]
                    counts[state] += 1
                    week = date.fromisoformat(row["session"]).isocalendar()
                    for groups, group in ((monthly, row["session"][:7]), (weekly, f"{week.year}-W{week.week:02d}")):
                        groups.setdefault(group, Counter())[state] += 1
                normalized = lambda mapping: {key: {state: value[state] for state in ("signal", "known_no_signal", "unknown")} for key, value in mapping.items()}
                variant["families"][family] = {"counts": normalized({"all": counts})["all"],
                    "by_month": normalized(monthly), "by_week": normalized(weekly),
                    "frequency_interpretation": "candidate signal lower bound when inputs unknown; not portfolio entries or independent draws",
                    "dispersion": dispersion(events[key][family], list(union))}
        report["input_evidence"] = {"file": archive_path.name, "compressed_sha256": archive_sha,
            "stream_sha256": stream_digest.hexdigest(), "queried_rows": row_count,
            "unique_target_days": sum(map(len, union.values()))}
        report_path = output / "report.json"
        with _private_file(report_path) as stream:
            stream.write(_canonical(report) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(output)
        report_sha = _sha(report_path)
        if before_completion is not None:
            before_completion()
        if any(_sha(path) != pins[str(path)] for path in sources) or _sha(upstream_path) != spec["upstream_sha256"]:
            raise CalibrationRefusal("source_specification_or_upstream_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(archive_path) != archive_sha or _sha(report_path) != report_sha:
            raise CalibrationRefusal("runtime_or_output_changed")
        return {"output_path": str(report_path), "output_sha256": report_sha,
                "input_evidence": report["input_evidence"], **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
                    "upstream_sha256": spec["upstream_sha256"], "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
