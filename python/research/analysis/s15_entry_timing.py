"""Task S15(a): is the mean-reversion entry too early, and does waiting fix it?

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s15_entry_timing

S12 measured that winning positions dig **1.86 ATR** against themselves
before working, and S14's diagnosis turned that into a concrete problem:
a 2.65 ATR stop moved the full-window result from -0.20% to -134%,
because the edge lives inside exactly the excursion the stop cuts off.

Two readings of that, and they imply opposite fixes:

1. The entry is **early** -- the move is still going when it is faded.
   Then waiting should reduce the adverse excursion and a stop becomes
   affordable.
2. The adverse excursion **is** the edge -- the position is paid for
   holding through it. Then waiting gives the move away and nothing is
   gained.

This measures which. Same signals, same 60-bar horizon, entry delayed by
k bars; plus a confirmation variant that waits for the move to actually
turn rather than for a fixed number of bars.

Every figure is over **non-overlapping** positions. S13 reported t = 7-8
on overlapping windows and the real figure was 1.5-2.6; that correction
is the reason this file computes nothing on overlapping samples.

Measurement only -- `run_backtest` is never called, nothing is written to
`runs/experiments.jsonl`, and no configuration is selected here. Adopting
any row below would be a selection and needs its own logged walk-forward.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys

from research.analysis.s12_excursion_run import COST_BPS, DEFAULT_DB, MAX_HOLD, SYMBOL
from research.analysis.s13_selectivity_sweep import build_series, load, non_overlapping
from research.excursion import measure_excursion

# The two cells S13's corrected table left standing at all, plus the
# operating point S14 actually traded.
CELLS = [(5.0, 0.99), (6.0, 0.99)]
DELAYS = [0, 5, 10, 15, 30, 60]

# Confirmation: enter only once price has retraced this fraction of its
# post-signal adverse excursion, i.e. once the move has visibly turned.
# 0.25 and 0.50 are coarse and pre-declared, not searched.
RETRACEMENTS = [0.25, 0.50]
CONFIRM_WINDOW = 60     # bars allowed for the turn to appear


def collect(z, quantile, times, ohlc, atr, rank, score, *, delay, max_hold):
    """Signals as in S13, but filled `delay` bars later."""
    opens, highs, lows, closes = ohlc
    n = len(closes)
    out = []
    for i in range(n - max_hold - delay - 1):
        r, s = rank[i], score[i]
        if r is None or r < quantile or s is None:
            continue
        if s >= z:
            side = "short"
        elif s <= -z:
            side = "long"
        else:
            continue
        e = measure_excursion(i + delay, side, opens, highs, lows, closes,
                              max_hold, COST_BPS, atr[i])
        if e is not None and not e.censored:
            out.append((times[i], e))
    return out


def collect_confirmed(z, quantile, times, ohlc, atr, rank, score, *,
                      retracement, max_hold, confirm_window):
    """Enter only once the move has turned.

    After the signal, track the worst excursion against the intended
    direction. Enter on the first bar that has given back `retracement` of
    that worst excursion -- a turn that actually happened, rather than a
    fixed number of bars in which one might have.

    Uses only bars at or before the entry decision, so it is
    look-ahead-safe by the same standard as the strategy itself.
    """
    opens, highs, lows, closes = ohlc
    n = len(closes)
    out = []
    for i in range(n - max_hold - confirm_window - 1):
        r, s = rank[i], score[i]
        if r is None or r < quantile or s is None:
            continue
        if s >= z:
            side = "short"
        elif s <= -z:
            side = "long"
        else:
            continue

        anchor = closes[i]
        worst = anchor          # most adverse price seen so far
        entry_index = None
        for j in range(i + 1, i + 1 + confirm_window):
            if side == "short":
                worst = max(worst, highs[j])
                excursion = worst - anchor
                if excursion > 0 and (worst - closes[j]) >= retracement * excursion:
                    entry_index = j
                    break
            else:
                worst = min(worst, lows[j])
                excursion = anchor - worst
                if excursion > 0 and (closes[j] - worst) >= retracement * excursion:
                    entry_index = j
                    break
        if entry_index is None:
            continue
        e = measure_excursion(entry_index, side, opens, highs, lows, closes,
                              max_hold, COST_BPS, atr[i])
        if e is not None and not e.censored:
            out.append((times[i], e))
    return out


def report(label, cell, hold, days):
    indep = non_overlapping(cell, hold)
    if len(indep) < 30:
        return f"{label:>22} {len(cell):>8,} {len(indep):>8,}   insufficient independent sample"
    g = [e.outcome_gross_bps for _, e in indep]
    mae = [e.mae_atr for _, e in indep if e.mae_atr is not None]
    mean = statistics.mean(g)
    t = mean / (statistics.stdev(g) / math.sqrt(len(g))) if len(g) > 1 else float("nan")
    win = sum(1 for _, e in indep if e.is_winner) / len(indep) * 100
    mae_txt = f"{statistics.mean(mae):>6.2f}" if mae else "   n/a"
    return (f"{label:>22} {len(cell):>8,} {len(indep):>8,} {len(indep)/days:>7.2f} "
            f"{win:>6.1f}% {mean:>8.2f}bps {mean-COST_BPS:>8.2f}bps {t:>6.2f} {mae_txt}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-path", default=DEFAULT_DB)
    args = ap.parse_args(argv)

    rows = load(args.db_path)
    if not rows:
        print(f"no {SYMBOL} 1m bars in {args.db_path}", file=sys.stderr)
        return 1
    times, ohlc, atr, rank, score = build_series(rows)
    days = (times[-1] - times[0]) / 86_400_000
    print(f"{SYMBOL}: {len(rows):,} bars over {days:,.0f} days, {MAX_HOLD}m hold, "
          f"{COST_BPS:.0f}bps round trip\n"
          f"All statistics over NON-OVERLAPPING positions.\n")

    hdr = (f"{'entry':>22} {'all':>8} {'indep':>8} {'/day':>7} {'win%':>7} "
           f"{'gross':>11} {'net':>11} {'t':>6} {'meanMAE':>7}")
    for z, q in CELLS:
        print(f"=== |z|>={z:g}, top {(1-q)*100:g}% ===")
        print(hdr)
        print("-" * len(hdr))
        for d in DELAYS:
            cell = collect(z, q, times, ohlc, atr, rank, score, delay=d, max_hold=MAX_HOLD)
            print(report(f"+{d} bars" if d else "immediate (S14)", cell, MAX_HOLD, days))
        for frac in RETRACEMENTS:
            cell = collect_confirmed(z, q, times, ohlc, atr, rank, score,
                                     retracement=frac, max_hold=MAX_HOLD,
                                     confirm_window=CONFIRM_WINDOW)
            print(report(f"turn {frac:.0%} retrace", cell, MAX_HOLD, days))
        print()

    print("meanMAE is in ATR: it is what a stop has to survive. S12 measured")
    print("1.86 ATR for the immediate entry, and S14 showed a 2.65 ATR stop")
    print("destroys the result. A later entry is only useful if it lowers")
    print("this WITHOUT giving up the gross outcome.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
