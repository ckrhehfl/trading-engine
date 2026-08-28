"""Task S17: where does `daily-tsmom-ensemble`'s drawdown actually come from?

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s17_tsmom_drawdown_anatomy

`sr-ab` (Binance spot 1d, 2017-2021) cleared three of five gates by wide
margins -- PSR 0.9945, Sharpe 1.305 against a 0.8503 floor, profit factor
7.68 -- and missed the other two by almost nothing: **max drawdown
20.135% against a 20% ceiling** and **64 trades against a 68 floor**.

The strategy holds until the ensemble's sign flips. It has no stop, no
target and no position management of any kind once a trade is on. So the
drawdown is not a mystery to be searched for: it is whatever the worst
hold-through was, and this measures it directly rather than guessing.

**Measurement only, and on already-spent windows.** Both `sr-v`'s and
`sr-ab`'s holdouts have been accessed and are research data now; this
project's standing rule keeps such a window "valid for reproducing a
previously logged result, for diagnosing a mechanism". This diagnoses a
mechanism. It runs no walk-forward, logs nothing to
`runs/experiments.jsonl`, and selects no configuration -- any rule
derived from what it reports has to be specified and evaluated
separately.

The output that matters is the **excursion distribution of the real
trades**, because that is what a stop would have to be set from. Setting
it from a search over drawdown outcomes would be exactly the
overfitting this strategy's zero-fitted-parameter property currently
protects it from.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import statistics
import sys
from decimal import Decimal

from backtest.engine import run_backtest
from backtest.kline import Kline
from metrics.metrics import compute_metrics
from research.strategies.daily_tsmom_ensemble import DailyTsmomEnsembleStrategy

DEFAULT_DB = "python/data/var/klines.sqlite3"
STARTING_EQUITY = Decimal("10000")

# The two windows daily-tsmom-ensemble was actually confirmed against.
WINDOWS = [
    ("sr-ab  Binance spot 2017-2021", "BINANCE:BTCUSDT"),
    # NOT sr-v's window. sr-v's holdout was 2021-05-14..2024-04-26 (1,079
    # bars, 26 trades); this is the FULL BingX 1d series through today, so
    # its figures are not comparable to sr-v's reported ones and are not
    # presented as a reproduction of them.
    ("BingX 1d, full series (NOT sr-v's window)", "BTC-USDT"),
]

# sr-ab's own figures, so a reproduction that drifts is visible rather
# than quietly accepted.
SR_AB_REPORTED = {"max_drawdown": 0.20135, "trades": 64, "profit_factor": 7.68}


def load(db: str, symbol: str) -> list[Kline]:
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT open_time_ms, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND interval='1d' ORDER BY open_time_ms",
        (symbol,),
    ).fetchall()
    conn.close()
    return [
        Kline(
            open_time=dt.datetime.fromtimestamp(r[0] // 1000, dt.timezone.utc),
            open=Decimal(str(r[1])), high=Decimal(str(r[2])),
            low=Decimal(str(r[3])), close=Decimal(str(r[4])),
            volume=Decimal(str(r[5])),
        )
        for r in rows
    ]


def equity_drawdowns(curve: list[Decimal]) -> list[tuple[int, int, float]]:
    """Every peak-to-trough episode as `(peak_index, trough_index, depth)`.

    Reported as episodes rather than a single maximum because "which
    trade caused it" is the question, and a lone max hides whether the
    ceiling was breached once or repeatedly.
    """
    episodes: list[tuple[int, int, float]] = []
    peak = curve[0]
    peak_i = trough_i = 0
    trough = curve[0]
    for i, v in enumerate(curve):
        if v > peak:
            if peak > 0 and trough < peak:
                episodes.append((peak_i, trough_i, float((peak - trough) / peak)))
            peak, peak_i, trough, trough_i = v, i, v, i
        elif v < trough:
            trough, trough_i = v, i
    if peak > 0 and trough < peak:
        episodes.append((peak_i, trough_i, float((peak - trough) / peak)))
    return sorted(episodes, key=lambda e: -e[2])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-path", default=DEFAULT_DB)
    args = ap.parse_args(argv)

    for label, symbol in WINDOWS:
        klines = load(args.db_path, symbol)
        if not klines:
            print(f"{label}: no 1d bars for {symbol}", file=sys.stderr)
            continue
        strategy = DailyTsmomEnsembleStrategy(symbol=symbol)
        result = run_backtest(
            klines, strategy, fee_bps=Decimal("5"), slippage_bps=Decimal("2"),
            starting_equity=STARTING_EQUITY,
        )
        metrics = compute_metrics(
            klines, result.filled_intents, result.fills, STARTING_EQUITY, bars_per_day=1
        )

        print(f"\n=== {label} ===")
        print(f"  bars {len(klines):,}  "
              f"({klines[0].open_time:%Y-%m-%d} .. {klines[-1].open_time:%Y-%m-%d})")
        print(f"  trades {metrics.num_trades}   "
              f"max drawdown {float(metrics.max_drawdown)*100:.3f}%   "
              f"profit factor "
              f"{'n/a' if metrics.profit_factor is None else f'{float(metrics.profit_factor):.2f}'}")

        episodes = equity_drawdowns(list(metrics.equity_curve))
        print(f"\n  worst drawdown episodes (peak -> trough):")
        for peak_i, trough_i, depth in episodes[:5]:
            over = "  <- OVER the 20% ceiling" if depth > 0.20 else ""
            print(f"    {klines[peak_i].open_time:%Y-%m-%d} -> "
                  f"{klines[trough_i].open_time:%Y-%m-%d}  "
                  f"{trough_i - peak_i:>4}d  {depth*100:>7.3f}%{over}")

        breaches = [e for e in episodes if e[2] > 0.20]
        print(f"\n  episodes over 20%: {len(breaches)}")
        if len(breaches) == 1:
            print("  -> a SINGLE episode is what fails the ceiling. A rule that")
            print("     truncates it does not have to be aggressive to work.")
        elif breaches:
            print("  -> more than one breach: a single-episode fix will not be enough.")

        closed = metrics.closed_trades
        if closed:
            pnls = [float(t.realized_pnl) for t in closed]
            losers = [p for p in pnls if p < 0]
            print(f"\n  closed trades {len(closed)}   "
                  f"winners {sum(1 for p in pnls if p > 0)}   losers {len(losers)}")
            if losers:
                print(f"  worst single trade  {min(pnls):+,.0f} "
                      f"({min(pnls)/float(STARTING_EQUITY)*100:+.2f}% of starting equity)")
                print(f"  median loser        {statistics.median(losers):+,.0f}")

    print("\nRead the episode count first. This strategy holds until the")
    print("ensemble's sign flips, so a drawdown is one uninterrupted")
    print("hold-through -- the question a stop answers is whether cutting")
    print("that specific hold would have cost more than it saved, which is")
    print("what S12's MAE/MFE machinery measures and what a search over")
    print("stop widths would only obscure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
