"""Task S13, second half: does selectivity move the gross outcome enough
to clear the fee?

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s13_selectivity_sweep

Committed because this result *reversed* S13's original conclusion and a
reversal has to be reproducible, not just asserted.

S12 fixed the entry at `|score| >= 2.0` inside the top 10% of activity,
which fires 57 times a day. The human operator pointed out that this is
still "dozens of trades a day", not the "once or twice, only on a
genuinely big signal" they had been describing -- roughly 29x more
selective, and an operating point nothing had tested. This sweeps it.

Reuses S12's entry construction unchanged (same trailing z-scores, same
trailing -- never global -- activity rank, same 60m horizon); only the
two selectivity knobs vary. Measurement only: no stop, no target, no
sizing. Already-spent window, no holdout access, writes nothing to
runs/experiments.jsonl.

The per-year table is not decoration. It is what showed the aggregate
result is concentrated in 2021, which is the finding that keeps this a
candidate rather than a conclusion.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sqlite3
import statistics
import sys

from research.analysis.s12_excursion_run import (
    ACTIVITY_HISTORY,
    ATR_PERIOD,
    COST_BPS,
    DEFAULT_DB,
    HTF_LAG,
    MAX_HOLD,
    SYMBOL,
    atr_series,
    trailing_zscore,
)
from research.excursion import measure_excursion, trailing_percentile_rank

# The grid. Deliberately coarse and pre-declared: this is a search, it is
# counted as one, and every cell is reported whether it flatters the
# result or not.
Z_GRID = [2.0, 3.0, 4.0, 5.0, 6.0]
ACTIVITY_GRID = [0.90, 0.99, 0.999]

# Below this many INDEPENDENT observations a cell is reported as
# undersampled rather than scored -- never silently dropped.
MIN_REPORTABLE = 30


def load(db: str):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT open_time_ms, open, high, low, close, volume, taker_buy_base_volume "
        "FROM klines WHERE symbol=? AND interval='1m' ORDER BY open_time_ms",
        (SYMBOL,),
    ).fetchall()
    conn.close()
    return rows


def build_series(rows):
    """Everything that does not depend on the two swept thresholds, so the
    grid costs one pass over the data rather than fifteen."""
    n = len(rows)
    times = [int(r[0]) for r in rows]
    opens = [float(r[1]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    closes = [float(r[4]) for r in rows]
    vols = [float(r[5]) for r in rows]
    takers = [float(r[6]) if r[6] is not None else None for r in rows]

    atr = atr_series(highs, lows, closes, ATR_PERIOD)
    activity = [
        None if atr[i] is None or closes[i] <= 0 else atr[i] / closes[i] * 1e4
        for i in range(n)
    ]
    activity_rank = trailing_percentile_rank(activity, ACTIVITY_HISTORY)

    htf = [None] * n
    for i in range(HTF_LAG, n):
        if closes[i - HTF_LAG] > 0:
            htf[i] = (closes[i] - closes[i - HTF_LAG]) / closes[i - HTF_LAG]
    share = [
        takers[i] / vols[i] if takers[i] is not None and vols[i] > 0 else None
        for i in range(n)
    ]
    score = []
    zp_all, zf_all = trailing_zscore(htf), trailing_zscore(share)
    for i in range(n):
        zp, zf = zp_all[i], zf_all[i]
        score.append(None if zp is None or zf is None else zp + zf)

    return times, (opens, highs, lows, closes), atr, activity_rank, score


def run_cell(z, quantile, times, ohlc, atr, activity_rank, score, max_hold):
    opens, highs, lows, closes = ohlc
    n = len(closes)
    out = []
    # `measure_excursion` fills at bar i+1 and needs `max_hold` bars after
    # it, so the last fully observable signal sits at n - max_hold - 2
    # inclusive. `range` is half-open, hence the +1: the previous bound
    # silently dropped that final entry.
    for i in range(n - max_hold - 1):
        rank, s = activity_rank[i], score[i]
        if rank is None or rank < quantile or s is None:
            continue
        if s >= z:
            side = "short"      # fade strength
        elif s <= -z:
            side = "long"       # fade weakness
        else:
            continue
        e = measure_excursion(i, side, opens, highs, lows, closes, max_hold, COST_BPS, atr[i])
        if e is not None and not e.censored:
            out.append((times[i], e))
    return out


def non_overlapping(cell, hold):
    """Keep only positions whose holding windows do not overlap.

    This is not a refinement, it is a correctness requirement. Extreme
    readings cluster, so consecutive qualifying bars produce many
    `hold`-bar windows covering nearly the same price path. Counting
    those as independent observations inflates any standard error, t
    statistic or p-value computed from them -- by roughly threefold on
    this data (Task S14). `research/ic.py` already enforces exactly this
    discipline for IC sampling; the excursion sweep did not inherit it,
    and that is the defect this function closes.

    Greedy earliest-first, which is deterministic and never looks ahead.
    """
    out, busy_until = [], -1
    for ts, e in cell:
        if e.index > busy_until:
            out.append((ts, e))
            busy_until = e.index + hold
    return out


def summarise(cell):
    """Mean, standard error and t, computed on whatever sample is passed.

    The caller is responsible for passing an independent sample: this
    function assumes i.i.d. observations and says so rather than
    silently applying an i.i.d. formula to overlapping windows. See
    `non_overlapping`.
    """
    g = [e.outcome_gross_bps for _, e in cell]
    if not g:
        return float("nan"), float("nan"), float("nan")
    mean = statistics.mean(g)
    stderr = statistics.stdev(g) / math.sqrt(len(g)) if len(g) > 1 else float("nan")
    return mean, stderr, (mean / stderr if stderr and not math.isnan(stderr) else float("nan"))


def by_year(cell):
    buckets: dict[int, list[float]] = {}
    for ts, e in cell:
        y = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).year
        buckets.setdefault(y, []).append(e.outcome_gross_bps)
    return {y: (len(v), statistics.mean(v)) for y, v in sorted(buckets.items())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-path", default=DEFAULT_DB)
    ap.add_argument("--max-hold", type=int, default=MAX_HOLD)
    args = ap.parse_args(argv)

    rows = load(args.db_path)
    if not rows:
        print(f"no {SYMBOL} 1m bars in {args.db_path}", file=sys.stderr)
        return 1

    times, ohlc, atr, activity_rank, score = build_series(rows)
    days = (times[-1] - times[0]) / 86_400_000
    print(f"{SYMBOL}: {len(rows):,} bars over {days:,.0f} days, "
          f"{args.max_hold}m hold, {COST_BPS:.0f}bps round trip\n")

    hdr = (f"{'|z|>=':>6} {'activity':>9} | {'all':>8} {'gross':>9} {'t':>6} | "
           f"{'indep':>7} {'per day':>8} {'gross':>9} {'net':>9} {'t':>6}")
    print(hdr)
    print("-" * len(hdr))
    print("  'all' counts every qualifying bar and its t is NOT corrected for")
    print("  overlapping holding windows; 'indep' is the non-overlapping")
    print("  subset and is the only column whose t means anything.\n")
    best = []
    for z in Z_GRID:
        for q in ACTIVITY_GRID:
            cell = run_cell(z, q, times, ohlc, atr, activity_rank, score, args.max_hold)
            indep = non_overlapping(cell, args.max_hold)
            label = f"{z:>6.1f} {f'top {(1-q)*100:g}%':>9}"
            # Every cell is reported, including undersampled ones. Silently
            # dropping them would hide exactly the cells whose apparent
            # strength comes from having almost no observations.
            if len(indep) < MIN_REPORTABLE:
                print(f"{label} | {len(cell):>8,} {'':>9} {'':>6} | "
                      f"{len(indep):>7,} {'':>8} {'':>9} {'':>9} "
                      f"insufficient sample (<{MIN_REPORTABLE} independent)")
                continue
            mean_all, _, t_all = summarise(cell)
            mean, _, t = summarise(indep)
            print(f"{label} | {len(cell):>8,} {mean_all:>6.2f}bps {t_all:>6.2f} | "
                  f"{len(indep):>7,} {len(indep)/days:>8.2f} {mean:>6.2f}bps "
                  f"{mean-COST_BPS:>6.2f}bps {t:>6.2f}")
            if mean > COST_BPS:
                best.append((z, q, indep))

    print(f"\n{len(Z_GRID)*len(ACTIVITY_GRID)} cells searched -- this is a real "
          f"selection count and must be deflated against, not ignored.")
    print("A Bonferroni-corrected two-sided threshold at this search width is "
          "|t| ~= 2.94.")

    for z, q, cell in best:
        print(f"\n=== |z|>={z:g}, top {(1-q)*100:g}% -- per year ===")
        years = by_year(cell)
        total = statistics.mean([e.outcome_gross_bps for _, e in cell]) * len(cell)
        for y, (cnt, m) in years.items():
            share_trades = cnt / len(cell) * 100
            share_edge = (m * cnt) / total * 100 if total else float("nan")
            print(f"  {y}  {cnt:>6,} trades ({share_trades:>4.1f}%)  "
                  f"{m:>+8.2f}bps  contributes {share_edge:>5.1f}% of the edge")
        ex21 = [e.outcome_gross_bps for ts, e in cell
                if dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).year != 2021]
        if ex21:
            m = statistics.mean(ex21)
            verdict = "clears" if m > COST_BPS else "FAILS"
            print(f"  excluding 2021: {m:+.2f}bps -> {verdict} the {COST_BPS:.0f}bps cost")
    return 0


if __name__ == "__main__":
    sys.exit(main())
