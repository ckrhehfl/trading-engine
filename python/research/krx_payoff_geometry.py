"""Can an asymmetric payoff manufacture positive expectancy on KRX? -- and what the universe's own drift does to the question.

The operator asked about the mathematician-trader framing: **you do not
need to be right often if you are paid enough when you are.** At 3:1 the
breakeven win rate is 25%, so a 30% hit rate is profitable. That
arithmetic is correct and it is already this project's rule -- S8 says
*"asymmetry substitutes for win rate ... floor 2:1, prefer 3:1."*

This module measures the two things that decide whether the frame can
actually be used here, and the first one turned out to invalidate a
measurement that looked like a success.

## 1. The universe's own drift, which bounds everything measured on it

`rd-r` picked ten names on **2026Q1 futures liquidity**. Over the panel
they returned **+694% equal-weight, +54%/yr**, against KOSPI's +131%
(+19%/yr) -- **3.4x the index**. Names became liquid enough to carry a
single-stock future *because* they had gone up 10-25x, so the selection
and the return are the same fact.

**Any long-only rule on this basket profits, and that is not evidence
about the rule.** The +15%/-5% barrier rule the operator described posts
a 30.0% win rate against its 25% breakeven and +0.87%/trade net -- and
the identical rule run **short** posts -0.83%, which is what says the
result is the basket rather than the rule.

## 2. Where an asymmetric payoff can exist at all: the horizon

At 3:1, expectancy is `p*3 - (1-p)*1 - cost_R`, so breakeven is
`p = (1 + cost_R)/4`. **With no costs that is 25% at every horizon -- the
payoff ratio is scale-free.** Costs are what break the scale, because the
round trip is a fixed number of basis points while the move available
shrinks with the horizon.

That is why this module reports `cost_in_R` and the breakeven win rate
per horizon rather than a single "is scalping viable" verdict. The whole
appeal of an asymmetric payoff is a *low* breakeven win rate, and a short
horizon attacks precisely that.

Run:

    python -m research.krx_payoff_geometry
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import math
import sqlite3
import sys
from dataclasses import dataclass

import numpy as np

from data._paths import DEFAULT_DB_PATH
from research.conclusion_check import (
    check_clustered_observations,
    require_no_blockers,
)
from research.krx_conjunction import COST_FLOOR_BP
from research.krx_signal_ic import UNIVERSE
from research.strategies.scenario_playbook import DailyPanel, load_daily_panel

#: KOSPI's own daily series, the reference a selection premium is measured
#: against. Same symbol `krx_tax_schedule` derives the trading calendar
#: from -- an index prints exactly when the market is open.
INDEX_SYMBOL = "KRX-INDEX:0001"

#: The rule as the operator described it: sell at +15%, sell at -5%.
DESCRIBED_TARGET = 0.15
DESCRIBED_STOP = -0.05

#: Payoff ratios swept for the long/short control. 1.0 is the diagnostic
#: anchor: a driftless process must return exactly minus two round trips
#: there, so a sum near that is what identifies the rest as drift.
PAYOFF_RATIOS = (1.0, 2.0, 3.0, 5.0)

#: Intraday horizons, in minutes, for the cost-in-R table.
HORIZONS_MIN = (5, 15, 30, 60, 120, 240)

_BPS = 1e4


@dataclass(frozen=True)
class Drift:
    """One name's buy-and-hold over the panel."""

    code: str
    first_open: float
    last_close: float

    @property
    def ratio(self) -> float:
        return self.last_close / self.first_open


def panel_years(panel: DailyPanel) -> float:
    return (panel.dates[-1] - panel.dates[0]) / 1000 / 86400 / 365.25


def per_name_drift(panel: DailyPanel) -> list[Drift]:
    """Buy at the second open, hold to the last close.

    The *second* open, not the first, because the first bar is where the
    panel's own `prev_close` is undefined and every other module here
    starts at index 1.
    """
    return [
        Drift(code, float(panel.open_px[1, j]), float(panel.close_px[-1, j]))
        for j, code in enumerate(panel.codes)
    ]


def index_drift(conn: sqlite3.Connection, panel: DailyPanel) -> Drift | None:
    """KOSPI over exactly the panel's window, or `None` if unavailable.

    Returns `None` rather than substituting a guess: a selection premium
    quoted against an assumed market return would be the wrong number
    presented with the same confidence as the right one.
    """
    rows = conn.execute(
        "SELECT CAST(open AS REAL), CAST(close AS REAL) FROM klines "
        "WHERE symbol=? AND interval='1d' AND open_time_ms BETWEEN ? AND ? "
        "ORDER BY open_time_ms",
        (INDEX_SYMBOL, panel.dates[0], panel.dates[-1]),
    ).fetchall()
    if not rows:
        return None
    return Drift(INDEX_SYMBOL, float(rows[0][0]), float(rows[-1][1]))


def annualised(ratio: float, years: float) -> float:
    return ratio ** (1.0 / years) - 1.0 if years > 0 and ratio > 0 else float("nan")


@dataclass(frozen=True)
class BarrierResult:
    direction: int
    trades: int
    win_rate: float
    mean_net: float
    median_hold: float
    per_date: dict[int, list[float]]
    #: `(name index, entry session, exit session)` for every trade, so a
    #: caller can verify disjointness with `check_disjoint_intervals`
    #: rather than take the docstring's word for it. `per_date` alone
    #: cannot show it — it is keyed by entry index, so duplicate entries
    #: are impossible there by construction and an overlap is invisible.
    intervals: list[tuple[int, int, int]]


def barrier_trades(
    panel: DailyPanel,
    direction: int,
    target: float = DESCRIBED_TARGET,
    stop: float = DESCRIBED_STOP,
    cost_bp: float = COST_FLOOR_BP,
) -> BarrierResult:
    """Hold until `target` or `stop` is touched, one position per name.

    **Non-overlapping by construction**: a name carries at most one
    position and the next entry is the session after the previous exit.
    That is what the rule describes, and it is also what makes the
    date-clustered p-value admissible -- an earlier sweep here entered
    every name every day with a 10-session hold, so positions from
    different entry dates overlapped heavily and its p-values were not
    usable. S13's error, in a new place.

    **The stop wins a same-bar tie**, S8 §3.7's pinned convention: the
    pessimistic resolution is the honest one, and fixing it means the
    same bars cannot yield two different answers.
    """
    cost = cost_bp / _BPS
    n, m = panel.shape
    per_date: dict[int, list[float]] = collections.defaultdict(list)
    holds: list[int] = []
    intervals: list[tuple[int, int, int]] = []
    for j in range(m):
        t = 1
        while t < n - 1:
            entry = panel.open_px[t, j]
            if not (math.isfinite(entry) and entry > 0):
                t += 1
                continue
            outcome, end = None, None
            for s in range(t, n):
                high, low = panel.high_px[s, j], panel.low_px[s, j]
                if not (math.isfinite(high) and math.isfinite(low)):
                    break
                up = (high - entry) / entry if direction > 0 else (entry - low) / entry
                down = (low - entry) / entry if direction > 0 else (entry - high) / entry
                if down <= stop:
                    outcome, end = stop, s
                    break
                if up >= target:
                    outcome, end = target, s
                    break
            if outcome is None or end is None:
                break                       # ran out of window; do not guess
            per_date[t].append(outcome - cost)
            holds.append(end - t + 1)
            intervals.append((j, t, end + 1))       # half-open
            t = end + 1
    flat = [r for v in per_date.values() for r in v]
    return BarrierResult(
        direction=direction,
        trades=len(flat),
        win_rate=float(np.mean([r > 0 for r in flat])) if flat else float("nan"),
        mean_net=float(np.mean(flat)) if flat else float("nan"),
        median_hold=float(np.median(holds)) if holds else float("nan"),
        per_date=dict(per_date),
        intervals=intervals,
    )


def clustered_p(per_date: dict[int, list[float]]) -> tuple[int, float, float]:
    """`(dates, mean of per-date means, two-sided p)`.

    The date is the unit, per CLAUDE.md's session-clustering rule: several
    names can enter on one session and share that session's market move.
    """
    from statistics import NormalDist

    if len(per_date) < 3:
        return len(per_date), float("nan"), float("nan")
    a = np.array([float(np.mean(v)) for v in per_date.values()])
    sd = float(a.std(ddof=1))
    if sd <= 0:
        return a.size, float(a.mean()), float("nan")
    t = a.mean() / (sd / math.sqrt(a.size))
    return a.size, float(a.mean()), 2 * (1 - NormalDist().cdf(abs(t)))


def breakeven_win_rate(payoff: float, cost_in_r: float) -> float:
    """`p` solving `p*payoff - (1-p) - cost_in_r = 0`.

    **With `cost_in_r = 0` this is `1/(payoff+1)` at every horizon** --
    25% at 3:1, and it does not depend on the timeframe at all. That
    scale-freeness is the whole appeal of the asymmetric-payoff frame, and
    it is exactly what a fixed round trip destroys as the horizon shortens.
    """
    if payoff <= 0:
        raise ValueError(f"payoff must be positive, got {payoff}")
    return (1.0 + cost_in_r) / (payoff + 1.0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default=",".join(UNIVERSE))
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = ap.parse_args(argv)

    codes = [c.strip() for c in args.symbols.split(",") if c.strip()]
    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    try:
        panel = load_daily_panel(conn, codes)
        index = index_drift(conn, panel)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    years = panel_years(panel)
    first = dt.datetime.fromtimestamp(panel.dates[0] / 1000, dt.UTC).date()
    last = dt.datetime.fromtimestamp(panel.dates[-1] / 1000, dt.UTC).date()
    print(f"panel {first} -> {last}  ({years:.2f}y, {len(panel.dates):,} sessions)\n")

    # ---- 1. what the universe itself did
    print("=== 1. the universe's own drift, which bounds everything below ===")
    drifts = sorted(per_name_drift(panel), key=lambda d: -d.ratio)
    print(f"  {'code':<8} {'first open':>12} {'last close':>12} {'total':>9} {'per yr':>8}")
    for d in drifts:
        print(
            f"  {d.code:<8} {d.first_open:>12,.0f} {d.last_close:>12,.0f} "
            f"{d.ratio - 1:>+8.0%} {annualised(d.ratio, years):>+8.0%}"
        )
    eq = float(np.mean([d.ratio for d in drifts]))
    med = float(np.median([d.ratio for d in drifts]))
    print(f"\n  equal-weight  {eq:.2f}x = {eq - 1:+.0%} total, "
          f"{annualised(eq, years):+.0%}/yr;  median name {med - 1:+.0%}")
    if index is None:
        print("  KOSPI unavailable for this window — no selection premium quoted.")
    else:
        print(
            f"  KOSPI         {index.ratio:.2f}x = {index.ratio - 1:+.0%} total, "
            f"{annualised(index.ratio, years):+.0%}/yr"
        )
        print(
            f"  ** selection premium: {eq / index.ratio:.1f}x the index, "
            f"{annualised(eq, years) - annualised(index.ratio, years):+.0%}/yr excess **"
        )
    print(
        "\n  rd-r picked these on 2026Q1 futures liquidity, and a name became\n"
        "  liquid enough to carry a single-stock future BECAUSE it had gone up.\n"
        "  Any long-only rule here profits; that is not evidence about the rule."
    )

    # ---- 2. the described rule, and the control that reads it correctly
    print(f"\n=== 2. the +{DESCRIBED_TARGET:.0%}/{DESCRIBED_STOP:.0%} rule, "
          f"and its short control ===")
    results = {d: barrier_trades(panel, d) for d in (1, -1)}
    for d, label in ((1, "LONG"), (-1, "SHORT")):
        r = results[d]
        dates, mean, p = clustered_p(r.per_date)
        require_no_blockers([
            check_clustered_observations(
                [t for t, v in r.per_date.items() for _ in v], reported_n=dates
            )
        ])
        print(
            f"  {label:<6} {r.trades:>6,} trades  win {r.win_rate:>5.1%}  "
            f"E[trade] {r.mean_net:>+8.4%}  median hold {r.median_hold:>4.0f}  "
            f"({dates:,} dates, p={p:.4f})"
        )
    both = results[1].mean_net + results[-1].mean_net
    drift_only = -2 * COST_FLOOR_BP / _BPS
    print(
        f"\n  sum of the two directions {both:+.4%};  a driftless process gives "
        f"{drift_only:+.4%}\n  (just the two round trips), so the residual beyond "
        f"drift and cost is {both - drift_only:+.4%}."
    )
    be = 1.0 / (DESCRIBED_TARGET / -DESCRIBED_STOP + 1.0)
    print(
        f"  breakeven win rate at this {DESCRIBED_TARGET / -DESCRIBED_STOP:.0f}:1 is "
        f"{be:.1%}; LONG achieves {results[1].win_rate:.1%} — the arithmetic works,\n"
        f"  and the short leg says most of it is the basket."
    )

    # ---- 3. where an asymmetric payoff can exist: the horizon
    print("\n=== 3. the horizon decides the breakeven, because costs are fixed ===")
    print(
        f"  {'horizon':>12} {'median move':>12} {'stop (1R)':>10} "
        f"{'cost in R':>10} {'breakeven @3:1':>15}"
    )
    print("  " + "-" * 64)
    for label, move_bp in _intraday_moves(panel):
        stop_bp = move_bp / 3.0
        cost_r = COST_FLOOR_BP / stop_bp
        p = breakeven_win_rate(3.0, cost_r)
        note = "  impossible" if p >= 0.60 else ("  hard" if p >= 0.40 else "")
        print(
            f"  {label:>12} {move_bp:>11.1f}bp {stop_bp:>9.1f}bp "
            f"{cost_r:>10.2f}R {p:>14.1%}{note}"
        )
    print(
        f"\n  With no costs the breakeven at 3:1 is {breakeven_win_rate(3.0, 0.0):.1%} "
        f"at EVERY horizon.\n  The payoff ratio is scale-free; a fixed round trip is "
        f"what breaks the scale."
    )
    return 0


def _intraday_moves(panel: DailyPanel) -> list[tuple[str, float]]:
    """Median absolute move by horizon, intraday plus the daily reference.

    Imported lazily so the daily-only sections still run if the intraday
    tape is absent — this module's first two findings do not need it, and
    refusing everything because one section lacks data would be the wrong
    failure.
    """
    out: list[tuple[str, float]] = []
    try:
        from research.krx_signal_ic import load as load_intraday

        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
        try:
            universe = [load_intraday(conn, c) for c in panel.codes]
        finally:
            conn.close()
    except Exception as exc:                      # noqa: BLE001 - reported, not hidden
        print(f"  (intraday tape unavailable: {exc})")
        universe = []

    for mins in HORIZONS_MIN:
        vals = []
        for b in universe:
            k = b.open.size - mins
            if k <= 0:
                continue
            # Only within one contiguous block, so no move spans the
            # overnight break or the closing auction -- rd-p's rule.
            same = b.blocks[:k] == b.blocks[mins : mins + k]
            a, z = b.open[:k][same], b.open[mins : mins + k][same]
            ok = (a > 0) & np.isfinite(a) & np.isfinite(z)
            if ok.any():
                vals.append(np.abs(z[ok] / a[ok] - 1.0) * _BPS)
        if vals:
            out.append((f"{mins} min", float(np.median(np.concatenate(vals)))))

    from research.strategies.scenario_playbook import ATR_PERIOD, wilder_atr

    atr = wilder_atr(panel)
    rel = (atr / panel.close_px)[ATR_PERIOD:]
    rel = rel[np.isfinite(rel)]
    if rel.size:
        # The daily row uses the ATR itself as 1R rather than move/3, which
        # is what every other daily module here does; the column header
        # says "stop (1R)" and this keeps that honest.
        out.append(("1 day (ATR)", float(np.median(rel)) * _BPS * 3.0))
    return out


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
