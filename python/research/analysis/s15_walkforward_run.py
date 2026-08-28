"""Task S15: walk-forward the design S15(a)/(b)/(c) actually point at.

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s15_walkforward_run

S14 was REJECTED with mean fold Sharpe -1.471, and its diagnosis said the
2.65 ATR stop was doing the damage. S15 then measured all three proposed
remedies rather than assuming any of them:

**(a) Enter later -- NO.** Delaying entry does lower the adverse
excursion (mean MAE 4.65 -> 2.23 ATR at +60 bars) and destroys the
outcome at the same rate (+30.87 -> +3.93bps at `|z|>=6`). The excursion
is not a cost paid before the edge; it *is* the edge. A confirmation
entry (wait for a 25-50% retrace of the adverse move) is the best of the
delayed variants and still strictly worse than immediate on both net
outcome and t.

**(b) A better stop -- NO, and not for the reason first suspected.**
Re-running S12's `recommend_stop` on the correct population gives
2.71/3.36 ATR, essentially S14's 2.65, so "the stop came from the wrong
population" is false. The real finding is stronger: at **every** width
from 1.5 to 12 ATR, in both cells, the stop realises a larger loss than
the position it catches would have taken on its own. It manufactures
losses rather than avoiding them, and "no stop" beat every width tested.

**(c) Equity-aware sizing -- built.** `backtest.engine.EquityObserver`
hands the strategy the mark-to-market equity the engine already computes
for its S7 insolvency floor, closing the half S7 left open.

So the configuration tested here keeps the ATR distance as the **sizing
and R:R basis** while removing it as an **exit**, and lets the position be
bounded by time, equity-aware sizing, and the zero-equity floor instead.

**Two cells, declared before running**, so the second isolates whether
compounding sizing is what matters rather than being reached for after
seeing the first:

1. `use_stop=False, sizing_mode=compounding`
2. `use_stop=False, sizing_mode=fixed`

Both are logged and both count toward the project-level `N`.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from decimal import Decimal

from research import lineage, overfitting_check
from research.holdout import load_research_klines
from research.strategies.selective_reversion import (
    DEFAULT_BARS_PER_DAY,
    DEFAULT_PARAMS,
    SIZING_COMPOUNDING,
    SIZING_FIXED,
    SelectiveReversionTrainable,
)
from research.walkforward import run_walk_forward

CONFIG = "configs/research/research_binance_futures_1m.json"
SYMBOL = "BINANCE-FUTURES:BTCUSDT"
STRATEGY_ID = "selective-reversion-no-stop"
STRATEGY_VERSION = "2.0.0"
FAMILY = "btc-scalping"

FEE_BPS = Decimal("5")
SLIPPAGE_BPS = Decimal("1")
TRAIN_BARS = 43_200
VALIDATE_BARS = 43_200
STEP_BARS = 43_200

CELLS = [
    ("no stop + compounding sizing", {"use_stop": False, "sizing_mode": SIZING_COMPOUNDING}),
    ("no stop + fixed sizing", {"use_stop": False, "sizing_mode": SIZING_FIXED}),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    klines = load_research_klines(0, 4_102_444_800_000, holdout_config_path=args.config)
    if not klines:
        print(f"no research klines for {SYMBOL}", file=sys.stderr)
        return 1
    n_folds = max(0, (len(klines) - TRAIN_BARS - VALIDATE_BARS) // STEP_BARS + 1)
    print(f"{SYMBOL} 1m: {len(klines):,} bars, {n_folds} folds, "
          f"fee {FEE_BPS}bps + slippage {SLIPPAGE_BPS}bps per side\n")
    if args.dry_run:
        return 0

    for label, overrides in CELLS:
        params = {**DEFAULT_PARAMS, **overrides}
        result = run_walk_forward(
            klines,
            SelectiveReversionTrainable(symbol=SYMBOL),
            STRATEGY_ID,
            STRATEGY_VERSION,
            params,
            train_bars=TRAIN_BARS,
            validate_bars=VALIDATE_BARS,
            step_bars=STEP_BARS,
            fee_bps=FEE_BPS,
            slippage_bps=SLIPPAGE_BPS,
            bars_per_day=DEFAULT_BARS_PER_DAY,
            strategy_family=FAMILY,
        )
        sharpes = [float(f.metrics.sharpe_ratio) for f in result.folds
                   if f.metrics.sharpe_ratio is not None]
        trades = sum(f.metrics.num_trades for f in result.folds)
        positive = sum(1 for s in sharpes if s > 0)
        pf = [float(f.metrics.profit_factor) for f in result.folds
              if f.metrics.profit_factor is not None]
        dd = [float(f.metrics.max_drawdown) for f in result.folds
              if f.metrics.max_drawdown is not None]

        print(f"=== {label} ===")
        print(f"  run_id          {result.run_id}")
        print(f"  folds/trades    {len(result.folds)} / {trades:,}")
        if sharpes:
            print(f"  folds Sharpe>0  {positive}/{len(sharpes)} "
                  f"({positive/len(sharpes)*100:.1f}%)")
            print(f"  mean Sharpe     {statistics.mean(sharpes):+.4f}   "
                  f"(S14 with the stop: -1.4709)")
        if pf:
            print(f"  mean PF         {statistics.mean(pf):.4f}  (floor 1.3)")
        if dd:
            print(f"  worst drawdown  {max(dd)*100:.2f}%  (ceiling 25%)")
        print()

    resolution = lineage.resolve_family(STRATEGY_ID, {"strategy_family": FAMILY})
    counts = overfitting_check.check_project_combination_count()
    print(f"family {resolution.family} (source={resolution.source})   "
          f"project N now {counts.research_selection_trials}")
    print("Score with `python -m research.analysis.s14_eligibility "
          f"--strategy-id {STRATEGY_ID}`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
