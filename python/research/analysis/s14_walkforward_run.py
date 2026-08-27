"""Task S14: the real walk-forward validation of `selective-reversion`.

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s14_walkforward_run

The first time this project's walk-forward + Eligibility Bar machinery
has been pointed at a scalping candidate at all. S4's and S6's candidates
were single pre-registered holdout confirmations, which the bar's
single-window variant governs instead; every other 1m task so far
measured quantities rather than evaluating a strategy.

Data is loaded through `research.holdout.load_research_klines` against
`configs/research/research_binance_futures_1m.json`, so the holdout
clamp is structural rather than a promise. That config's own rationale
records why this window is research data (S6 spent it as a holdout;
S8's methodology rebuild reclassified it) and confirms the genuinely
reserved window -- Binance **spot** 1m -- is untouched and not even
present locally.

Fold geometry, derived from this window's real measured span rather than
inherited: 30 days train / 30 days validate / 30 days step at
`bars_per_day = 1440`. Non-overlapping, rolling, fixed-size -- the same
shape as every other timeframe's geometry in this project, expressed in
this timeframe's own bars.

`fit()` estimates nothing (every constant is measured elsewhere or
selected in advance), so the train window's real job here is to keep
each validate window's 1,680-bar warmup from being charged against a
shorter evaluated period.

This run is logged and **counts toward the project-level selection-trial
`N`**. That is deliberate: S8's rule is "search freely, count every
trial, deflate with DSR."
"""

from __future__ import annotations

import argparse
import statistics
import sys
from decimal import Decimal

from research import eligibility, lineage, overfitting_check
from research.holdout import load_research_klines
from research.strategies.selective_reversion import (
    DEFAULT_BARS_PER_DAY,
    DEFAULT_PARAMS,
    SelectiveReversionTrainable,
)
from research.walkforward import run_walk_forward

CONFIG = "configs/research/research_binance_futures_1m.json"
SYMBOL = "BINANCE-FUTURES:BTCUSDT"
STRATEGY_ID = "selective-reversion"
STRATEGY_VERSION = "1.0.0"
FAMILY = "btc-scalping"

# S9's measured figures, unchanged. Never re-tuned per candidate.
FEE_BPS = Decimal("5")
SLIPPAGE_BPS = Decimal("1")

TRAIN_BARS = 43_200      # 30 days
VALIDATE_BARS = 43_200   # 30 days
STEP_BARS = 43_200       # non-overlapping


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--dry-run", action="store_true",
                    help="report the fold geometry and exit without running or logging")
    args = ap.parse_args(argv)

    # Both bounds must sit on the 60,000ms grid `data/_grid.py` enforces.
    # 2100-01-01T00:00:00Z is an exact multiple and is unreachable, so the
    # range is "everything", expressed the way the loader validates.
    klines = load_research_klines(0, 4_102_444_800_000, holdout_config_path=args.config)
    if not klines:
        print(f"no research klines for {SYMBOL}", file=sys.stderr)
        return 1
    span_days = (klines[-1].open_time - klines[0].open_time).total_seconds() / 86400
    n_folds = max(0, (len(klines) - TRAIN_BARS - VALIDATE_BARS) // STEP_BARS + 1)
    print(f"{SYMBOL} 1m: {len(klines):,} bars, {span_days:,.0f} days "
          f"({klines[0].open_time:%Y-%m-%d} .. {klines[-1].open_time:%Y-%m-%d})")
    print(f"folds: {n_folds}  train={TRAIN_BARS:,} validate={VALIDATE_BARS:,} step={STEP_BARS:,}")
    print(f"costs: fee {FEE_BPS}bps + slippage {SLIPPAGE_BPS}bps per side\n")
    if args.dry_run:
        return 0

    result = run_walk_forward(
        klines,
        SelectiveReversionTrainable(symbol=SYMBOL),
        STRATEGY_ID,
        STRATEGY_VERSION,
        DEFAULT_PARAMS,
        train_bars=TRAIN_BARS,
        validate_bars=VALIDATE_BARS,
        step_bars=STEP_BARS,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        bars_per_day=DEFAULT_BARS_PER_DAY,
        strategy_family=FAMILY,
    )

    sharpes = [float(f.metrics.annualized_sharpe) for f in result.folds
               if f.metrics.annualized_sharpe is not None]
    trades = sum(f.metrics.num_trades for f in result.folds)
    positive = sum(1 for s in sharpes if s > 0)
    drawdowns = [float(f.metrics.max_drawdown) for f in result.folds
                 if f.metrics.max_drawdown is not None]

    print(f"run_id {result.run_id}")
    print(f"folds evaluated   : {len(result.folds)}")
    print(f"total trades      : {trades:,}")
    print(f"folds Sharpe > 0  : {positive}/{len(sharpes)} "
          f"({positive/len(sharpes)*100:.1f}%)" if sharpes else "no Sharpe available")
    if sharpes:
        print(f"mean fold Sharpe  : {statistics.mean(sharpes):+.4f}")
        print(f"median            : {statistics.median(sharpes):+.4f}")
    if drawdowns:
        print(f"max drawdown      : {max(drawdowns)*100:.2f}% (worst fold)")

    resolution = lineage.resolve_family(STRATEGY_ID, strategy_family=FAMILY)
    print(f"\nfamily resolution : {resolution.family} (source={resolution.source})")
    if resolution.source == "unmapped":
        print("  ! UNMAPPED -- a DSR against this is inadmissible per the Eligibility Bar")
    counts = overfitting_check.check_project_combination_count()
    print(f"project N         : {counts.research_selection_trials}")

    print("\nPer-fold Sharpe:")
    for f in result.folds:
        s = f.metrics.annualized_sharpe
        print(f"  fold {f.fold_index:>3}  trades {f.metrics.num_trades:>4}  "
              f"Sharpe {'n/a' if s is None else f'{float(s):+8.4f}'}")
    del eligibility  # imported for the caller's convenience; scoring lives in s14_eligibility.py
    return 0


if __name__ == "__main__":
    sys.exit(main())
