"""Discovery-only activity portfolio; first measure dispersion, before a family.

The loader accepts only the completed, spent 2019+ scan panel. No exchange
client is imported. Prices are split-adjusted, not dividend total returns.
Arrays bound memory on the collector host; databases are opened read-only.
"""

from __future__ import annotations

import argparse
from array import array
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
import subprocess

from research import experiment_log
from research.krx_tax_schedule import KOSPI, total_bp


EXECUTION_SOURCES = (
    "python/research/__init__.py", "python/research/activity_portfolio.py",
    "python/research/experiment_log.py", "python/research/krx_tax_schedule.py",
    "python/data/__init__.py", "python/data/_paths.py",
)


@dataclass
class Series:
    """One name aligned to the index calendar; zero means an absent price."""

    opens: array
    closes: array
    turnover: array
    frozen: bytearray
    locked: bytearray


def readonly(path: Path) -> sqlite3.Connection:
    """Open an existing file without allowing SQLite to create or mutate it."""
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA query_only=ON")
    return con


def load_panel(scan_path: Path, calendar_path: Path) -> tuple[list[date], dict[str, Series], str, dict]:
    """Validate panel metadata before any price query; stream one code at a time."""
    with closing(readonly(scan_path)) as scan, closing(readonly(calendar_path)) as reference:
        panel = scan.execute("SELECT start,end FROM scan_panel WHERE id=1").fetchone()
        if panel != ("20190102", "20260918"):
            raise ValueError("only the registered spent 20190102..20260918 panel is allowed")
        lo = int(datetime(2019, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
        hi = int(datetime(2026, 9, 23, tzinfo=timezone.utc).timestamp() * 1000)
        dates = [datetime.fromtimestamp(r[0] / 1000, timezone.utc).date() for r in reference.execute(
            "SELECT open_time_ms FROM klines WHERE symbol=? AND interval='1d' "
            "AND open_time_ms BETWEEN ? AND ? ORDER BY open_time_ms",
            ("KRX-INDEX:0001", lo, hi),
        )]
        if not dates or dates[0] != date(2019, 1, 2) or date(2026, 9, 18) not in dates:
            raise ValueError("index calendar does not cover the registered panel")
        if len(set(dates)) != len(dates):
            raise ValueError("duplicate index sessions")
        positions = {d.strftime("%Y%m%d"): i for i, d in enumerate(dates)}
        expected = {d for d in positions if d <= panel[1]}
        observed = {r[0] for r in scan.execute("SELECT DISTINCT bsop_date FROM scan_bars")}
        if observed != expected:
            raise ValueError("scan and index calendar disagree in either direction")
        codes = [r[0] for r in scan.execute("SELECT code FROM scan_progress WHERE status='done' ORDER BY code")]
        if not codes:
            raise ValueError("no completed names")
        quality = dict(scan.execute("SELECT status,count(*) FROM scan_progress GROUP BY status"))
        digest = hashlib.sha256()
        digest.update(json.dumps([d.isoformat() for d in dates]).encode())
        out = {}
        for code in codes:
            n = len(dates)
            series = Series(array("d", [0]) * n, array("d", [0]) * n,
                            array("d", [0]) * n, bytearray(n), bytearray(n))
            for row in scan.execute(
                "SELECT bsop_date,open,high,low,close,turnover FROM scan_bars "
                "WHERE code=? ORDER BY bsop_date", (code,),
            ):
                digest.update(json.dumps([code, *row], separators=(",", ":")).encode())
                day, *raw = row
                o, h, l, c = map(float, raw[:4])
                t = 0.0 if raw[4] is None else float(raw[4])
                if not all(math.isfinite(v) for v in (o, h, l, c, t)) or min(o, h, l, c) <= 0 or t < 0:
                    raise ValueError("invalid price or turnover in completed panel")
                i = positions[day]
                series.opens[i], series.closes[i], series.turnover[i] = o, c, t
                equal = o == h == l == c
                series.frozen[i] = equal and t == 0
                series.locked[i] = equal and t > 0
            out[code] = series
        return dates, out, digest.hexdigest(), quality


def eligible(series: Series, day: int, lookback: int, threshold: float) -> bool:
    """Use the formation close and preceding non-frozen observations only."""
    if not series.closes[day] or series.frozen[day]:
        return False
    prior = []
    for i in range(day - 1, -1, -1):
        if series.closes[i] and not series.frozen[i]:
            prior.append(series.turnover[i])
            if len(prior) == lookback:
                break
    if len(prior) != lookback:
        return False
    baseline = statistics.median(prior)
    return baseline > 0 and series.turnover[day] / baseline >= threshold


def simulate(dates: list[date], panel: dict[str, Series], params: dict) -> dict:
    """Maintain one cash-funded book through halts; never invent a last-bar exit.

    Signals use yesterday's close. Due sales execute at today's open if the
    daily bar is present and not frozen; otherwise they remain pending.
    The daily-bar fill proxy cannot establish queue availability at a limit.
    Such fills are counted, not mislabeled as halted observations.
    """
    lookback, holding, slots = params["lookback"], params["holding_sessions"], params["slots"]
    if any(type(v) is not int or v < 1 for v in (lookback, holding, slots)):
        raise ValueError("lookback, holding and slots must be positive integers")
    for key in ("threshold", "commission_bps_per_side", "slippage_bps_per_side"):
        value = params[key]
        if isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{key} must be finite and nonnegative")
    end = dates.index(date.fromisoformat(params["end"]))
    if end + 2 >= len(dates):
        raise ValueError("calendar needs two settlement sessions after the end")
    cash, previous = 1.0, 1.0
    positions: dict[str, dict] = {}
    returns, navs = [], []
    diagnostics = Counter()
    trades = 0
    cost = params["commission_bps_per_side"] + params["slippage_bps_per_side"]
    formation_days = set(range(lookback, end - holding, holding))
    for i in range(lookback + 1, end + 1):
        for code, position in list(positions.items()):
            series = panel[code]
            if not series.closes[i] or series.frozen[i]:
                diagnostics["unpriced_or_frozen_position_sessions"] += 1
            if i >= position["due"]:
                if series.opens[i] and not series.frozen[i]:
                    value = position["shares"] * series.opens[i]
                    # A point-in-time market map is absent. KOSPI's total is
                    # equal to KOSDAQ and above KONEX over this panel: model
                    # that upper tax bound, not a claim all names are KOSPI.
                    cash += value * (1 - (cost + total_bp(KOSPI, dates[i], dates)) / 1e4)
                    diagnostics["delayed_exits"] += i > position["due"]
                    diagnostics["limit_locked_fills"] += bool(series.locked[i])
                    trades += 1
                    del positions[code]
                else:
                    diagnostics["pending_exit_sessions"] += 1
        if i - 1 in formation_days:
            known = i - 1
            pool = [code for code, series in panel.items() if code not in positions
                    and eligible(series, known, lookback, params["threshold"])]
            pool.sort(key=lambda code: hashlib.sha256(
                f"{params['seed']}:{dates[known]}:{code}".encode()).digest())
            # Frozen holdings retain their capital and occupy a slot.
            equity = cash + sum(p["shares"] * (panel[c].opens[i] if panel[c].opens[i]
                                and not panel[c].frozen[i] else p["mark"])
                                for c, p in positions.items())
            allocation = equity / slots
            for code in pool[:max(0, slots - len(positions))]:
                series = panel[code]
                if not series.opens[i] or series.frozen[i]:
                    diagnostics["unfilled_entries"] += 1
                    continue  # no hindsight replacement with another name
                budget = min(cash, allocation)
                if budget <= 0:
                    break
                shares = budget / (series.opens[i] * (1 + cost / 1e4))
                positions[code] = {"shares": shares, "due": i + holding,
                                   "mark": series.opens[i]}
                cash -= budget
                diagnostics["entries"] += 1
                diagnostics["limit_locked_fills"] += bool(series.locked[i])
        for code, position in positions.items():
            series = panel[code]
            if series.closes[i] and not series.frozen[i]:
                position["mark"] = series.closes[i]
        nav = cash + sum(p["shares"] * p["mark"] for p in positions.values())
        diagnostics["invested_sessions"] += bool(positions)
        returns.append(nav / previous - 1)
        navs.append(nav)
        previous = nav
    return {"returns": returns, "navs": navs, "diagnostics": dict(diagnostics),
            "closed_trades": trades, "unresolved_positions": len(positions),
            "unresolved_marked_value": sum(p["shares"] * p["mark"] for p in positions.values())}


def block_standard_error(values: list[float], block: int, seed: int, repetitions: int) -> float:
    """Circular session-block bootstrap of the mean, with a fixed seed."""
    n = len(values)
    if n < 2 or not 1 <= block <= n or repetitions < 2:
        raise ValueError("invalid bootstrap geometry")
    prefix = [0.0]
    for value in values + values:
        prefix.append(prefix[-1] + value)
    rng = random.Random(seed)
    samples = []
    for _ in range(repetitions):
        total = 0.0
        remaining = n
        while remaining:
            width = min(block, remaining)
            start = rng.randrange(n)
            total += prefix[start + width] - prefix[start]
            remaining -= width
        samples.append(total / n)
    return statistics.stdev(samples)


def calibration(book: dict, params: dict) -> dict:
    """Report dispersion and power planning only, never a performance verdict."""
    values = book["returns"]
    if len(values) < max(params["bootstrap_blocks"]) * 2:
        raise ValueError("too few sessions for the registered block sensitivity")
    naive = statistics.stdev(values) / math.sqrt(len(values))
    if naive == 0 or not math.isfinite(naive):
        raise ValueError("no finite nonzero event-arm dispersion; cannot size a family")
    errors = {str(b): block_standard_error(values, b, params["seed"], params["bootstrap_repetitions"])
              for b in params["bootstrap_blocks"]}
    se = max(errors.values())
    normal = statistics.NormalDist()
    detectable = (normal.inv_cdf(1 - params["alpha"]) + normal.inv_cdf(params["power"])) * se
    cost_floor = params["sizing_round_trip_bps"] / 1e4 / params["holding_sessions"]
    return {"sessions": len(values), "session_return_sd": statistics.stdev(values),
            "iid_session_se": naive, "block_se": errors,
            "corrected_to_naive_se": se / naive if naive else None,
            "detectable_daily_bps": detectable * 1e4,
            "cost_floor_daily_bps": cost_floor * 1e4,
            "cost_floor_resolvable": detectable <= cost_floor,
            "diagnostics": book["diagnostics"], "closed_trades": book["closed_trades"],
            "unresolved_positions": book["unresolved_positions"],
            "interpretation": "dispersion calibration only; no edge claim or family authorized"}


def committed_specification(root: Path, path: Path) -> bytes:
    """Refuse changed specifications and staged, unstaged or untracked source edits."""
    relative = path.resolve().relative_to(root).as_posix()
    committed = subprocess.check_output(["git", "-C", str(root), "show", f"HEAD:{relative}"])
    raw = path.read_bytes()
    if raw != committed:
        raise ValueError("specification must match its committed bytes")
    dirty = subprocess.check_output([
        "git", "-C", str(root), "status", "--porcelain", "--", *EXECUTION_SOURCES,
    ])
    if dirty.strip():
        raise ValueError("execution sources must be committed before a discovery run")
    return raw


def main(argv: list[str] | None = None) -> int:
    """Run only the committed sizing specification and log before loading data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--scan-db", type=Path, required=True)
    parser.add_argument("--calendar-db", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    if Path.cwd().resolve() != root:
        raise ValueError("run from the repository root so logged code_version names this checkout")
    raw = committed_specification(root, args.spec)
    spec = json.loads(raw)
    if spec["mode"] != "discovery_calibration" or spec["promotion_allowed"] is not False:
        raise ValueError("only non-promotable discovery calibration is supported")
    if spec["window"] != {"symbol": "KRX:", "interval": "1d", "start": "2019-01-02", "end": "2026-09-18"}:
        raise ValueError("unregistered data window")

    def evaluate():
        """Load only after the durable start, then return dispersion with provenance."""
        dates, panel, digest, quality = load_panel(args.scan_db, args.calendar_db)
        result = calibration(simulate(dates, panel, spec["parameters"]), spec["parameters"])
        return {**result, "dataset_sha256": digest, "scan_progress": quality}

    record = experiment_log.run_discovery_trial(
        study_id=spec["study_id"], specification_sha256=hashlib.sha256(raw).hexdigest(),
        parameters=spec["parameters"], window=spec["window"], evaluate=evaluate,
        runs_path=args.runs_path,
    )
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
