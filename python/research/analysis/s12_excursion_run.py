"""Reproduces Task S12's MAE/MFE result end to end.

Committed because the result is load-bearing and the reviewer was right
that describing the inputs is not the same as being able to regenerate
them: the trailing z-scores, the activity filter and the entry selection
all have to be reproducible, not just the excursion functions.

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s12_excursion_run

Measurement only. Defines a PROVISIONAL entry so excursions have
something to measure -- no stop, no target, no sizing, no P&L curve.
Runs on already-spent windows; touches no holdout and writes nothing to
runs/experiments.jsonl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
from pathlib import Path

from research.excursion import (
    fragility_check,
    mae_by_outcome,
    measure_excursion,
    mfe_capture_rate,
    recommend_stop,
    trailing_percentile_rank,
)

DEFAULT_DB = "python/data/var/klines.sqlite3"
SYMBOL = "BINANCE-FUTURES:BTCUSDT"

# Every constant below is conventional or measured elsewhere, never fitted
# here -- see .planning/scalp-s12-mae-mfe.md.
ATR_PERIOD = 14         # Wilder's, already this project's convention
Z_WINDOW = 1440         # one day of 1m bars
ENTRY_Z = 2.0           # conventional 2-sigma
ACTIVITY_QUANTILE = 0.90  # S9's tradeable-moment threshold
ACTIVITY_HISTORY = 1440   # trailing reference for that threshold: one day
MAX_HOLD = 60           # the horizon S11's ICs were strongest at
COST_BPS = 12.0         # S9's measured round trip
HTF_LAG = 240           # htf_ret_4h, S11's strongest feature


def load(db: str):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT open, high, low, close, volume, taker_buy_base_volume FROM klines "
        "WHERE symbol=? AND interval='1m' ORDER BY open_time_ms",
        (SYMBOL,),
    ).fetchall()
    conn.close()
    return rows


def atr_series(highs, lows, closes, period=ATR_PERIOD):
    n = len(closes)
    tr = [0.0] * n
    for i in range(n):
        tr[i] = (
            highs[i] - lows[i]
            if i == 0
            else max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        )
    out, run = [None] * n, 0.0
    for i in range(n):
        run += tr[i]
        if i >= period:
            run -= tr[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def trailing_zscore(src, window=Z_WINDOW):
    """The current value is excluded from its own reference window, so a
    bar cannot normalise against itself."""
    n = len(src)
    out = [None] * n
    buf: list[float] = []
    s = s2 = 0.0
    for i in range(n):
        v = src[i]
        if v is None:
            continue
        if len(buf) == window:
            m = s / window
            var = max(s2 / window - m * m, 0.0)
            if var > 0:
                out[i] = (v - m) / (var**0.5)
            old = buf.pop(0)
            s -= old
            s2 -= old * old
        buf.append(v)
        s += v
        s2 += v * v
    return out


def build_entries(rows):
    n = len(rows)
    opens = [float(r[0]) for r in rows]
    highs = [float(r[1]) for r in rows]
    lows = [float(r[2]) for r in rows]
    closes = [float(r[3]) for r in rows]
    vols = [float(r[4]) for r in rows]
    takers = [float(r[5]) if r[5] is not None else None for r in rows]

    atr = atr_series(highs, lows, closes)
    activity = [
        None if atr[i] is None or closes[i] <= 0 else atr[i] / closes[i] * 1e4
        for i in range(n)
    ]
    # Trailing rank, NOT a global percentile. A global threshold would
    # filter early bars using the volatility distribution of bars that had
    # not happened yet -- look-ahead, and disqualifying for positions whose
    # statistics get reported as if a live system could have selected them.
    activity_rank = trailing_percentile_rank(activity, ACTIVITY_HISTORY)

    htf = [None] * n
    for i in range(HTF_LAG, n):
        if closes[i - HTF_LAG] > 0:
            htf[i] = (closes[i] - closes[i - HTF_LAG]) / closes[i - HTF_LAG]
    share = [
        takers[i] / vols[i] if takers[i] is not None and vols[i] > 0 else None
        for i in range(n)
    ]

    z_price = trailing_zscore(htf)
    z_flow = trailing_zscore(share)

    entries = []
    for i in range(n - MAX_HOLD - 2):
        if activity_rank[i] is None or activity_rank[i] < ACTIVITY_QUANTILE:
            continue
        zp, zf = z_price[i], z_flow[i]
        if zp is None or zf is None:
            continue
        score = zp + zf  # equal weight, deliberately unfitted
        if score >= ENTRY_Z:
            entries.append((i, "short"))   # fade strength
        elif score <= -ENTRY_Z:
            entries.append((i, "long"))    # fade weakness
    return entries, (opens, highs, lows, closes), atr


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-path", default=DEFAULT_DB)
    ap.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = ap.parse_args(argv)

    rows = load(args.db_path)
    if not rows:
        print(f"no {SYMBOL} 1m bars in {args.db_path}", file=sys.stderr)
        return 1
    entries, (opens, highs, lows, closes), atr = build_entries(rows)

    excursions = [
        e
        for e in (
            measure_excursion(i, side, opens, highs, lows, closes, MAX_HOLD, COST_BPS, atr[i])
            for i, side in entries
        )
        if e is not None
    ]
    real = [e for e in excursions if not e.censored]
    winners = [e for e in real if e.is_winner]

    rec = recommend_stop(excursions, unit="atr")
    avg_r, fragility_warning = (
        fragility_check(excursions, rec.winner_mae_p80)
        if rec.winner_mae_p80
        else (None, None)
    )
    capture, capture_note = mfe_capture_rate(excursions)

    result = {
        "symbol": SYMBOL,
        "bars": len(rows),
        "entries": len(entries),
        "positions": len(excursions),
        "censored": len(excursions) - len(real),
        "win_rate": len(winners) / len(real) if real else None,
        "mean_net_bps": statistics.mean(e.outcome_bps for e in real) if real else None,
        "mean_gross_bps": statistics.mean(e.outcome_gross_bps for e in real) if real else None,
        "cost_bps": COST_BPS,
        "stop": {
            "winner_mae_p80_atr": rec.winner_mae_p80,
            "winner_mae_p90_atr": rec.winner_mae_p90,
            "loser_mae_median_atr": rec.loser_mae_median,
            "losers_cut_at_p80": rec.losers_cut_at_p80,
            "warning": rec.warning,
        },
        "fragility_mean_winner_mae_r": avg_r,
        "fragility_warning": fragility_warning,
        "mfe_capture_median": capture,
        "mfe_capture_note": capture_note,
        "stop_tradeoff": mae_by_outcome(excursions, [1.0, 1.5, 2.0, rec.winner_mae_p80 or 2.8, 3.9]),
    }

    if args.json:
        payload = json.dumps(result, indent=2, sort_keys=True, default=float)
        print(payload)
        print(f"\n# sha256: {hashlib.sha256(payload.encode()).hexdigest()}", file=sys.stderr)
        return 0

    print(f"{SYMBOL}: {result['bars']:,} bars")
    print(f"entries {result['entries']:,}  positions {result['positions']:,}  "
          f"censored {result['censored']:,}\n")
    print(f"win rate            : {result['win_rate']*100:.1f}%")
    print(f"mean GROSS          : {result['mean_gross_bps']:+.2f} bps")
    print(f"round-trip cost     : {COST_BPS:.2f} bps")
    print(f"mean NET            : {result['mean_net_bps']:+.2f} bps\n")
    def fmt(v, suffix=""):
        return "n/a" if v is None else f"{v:.3f}{suffix}"

    print(f"stop, winners' p80  : {fmt(rec.winner_mae_p80, ' ATR')}")
    print(f"stop, winners' p90  : {fmt(rec.winner_mae_p90, ' ATR')}")
    print(f"loser MAE median    : {fmt(rec.loser_mae_median, ' ATR')}")
    print("losers cut at p80   : "
          + ("n/a" if rec.losers_cut_at_p80 is None else f"{rec.losers_cut_at_p80*100:.1f}%"))
    if rec.warning:
        print(f"  ! {rec.warning}")
    print(f"\nmean winner MAE     : {fmt(avg_r, ' R')}  (against the p80 stop)")
    print(f"  ! {fragility_warning}" if fragility_warning else "  within Sweeney's 0.7R threshold")
    print(f"\nMFE capture median  : {fmt(capture)}")
    if capture_note:
        print(f"  ! {capture_note}")
    print("\nstop trade-off (winners cut / losers cut):")
    for stop, cut in result["stop_tradeoff"].items():
        print(f"  {float(stop):>5.2f} ATR -> {cut['winners_cut']*100:>5.1f}% / {cut['losers_cut']*100:>5.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
