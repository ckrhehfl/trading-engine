"""Task S16: the two walk-forward cells S14/S15 never ran, and the two
checks that showed why their verdict's reasoning was wrong.

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s16_audit_run --check
    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s16_audit_run --run

Committed because this audit *reversed* part of a shipped conclusion, and
a reversal has to be reproducible.

S13's corrected sweep left two cells standing, `|z|>=5` and `|z|>=6`.
**Every walk-forward S14 and S15 ran used `entry_z=5.0`** -- the `|z|>=6`
cell, measured at 2.2x the outcome, was never tested, and "the signal is
not there" was declared anyway. That is the fifth instance of the error
pattern CLAUDE.md already records as a rule, committed at the most
expensive stage to commit it.

`--check` is read-only and needs no data: it recomputes the two
arithmetic facts that undercut S15's stated reasoning (the
fold-consistency bar is unreachable at this trade frequency, and the
Sharpe required to clear DSR grows past any plausible edge at this `N`).

`--run` executes the two missing walk-forward cells. They are logged and
count toward `N`, which is the point -- an audit that quietly avoided
raising `N` would be measuring something other than what the project
actually did.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from decimal import Decimal

REQUIRED_FOLDS = 67      # 80% of 83
TOTAL_FOLDS = 83
BARS_PER_DAY = 1440
NUM_OBSERVATIONS = 2490  # 83 folds x 43,200 validate bars / 1440

CELLS = [
    ("|z|>=6, top 1%", Decimal("6"), Decimal("0.99")),
    ("|z|>=6, top 0.1%", Decimal("6"), Decimal("0.999")),
]


def fold_bar_attainability() -> None:
    """Is 80% fold consistency reachable at ~2-6 trades per fold?

    S15 reported "45.8% of folds positive against an 80% floor" as
    evidence. With a median of 2-6 trades in a fold, a fold's sign is
    close to a coin flip, and the floor stops measuring the strategy.
    CLAUDE.md already makes this argument for fold *counts* (sr-j:
    demanding a literal 19/19 sweep "mostly measures luck, not edge");
    it had not been made for trades *per* fold.
    """
    print(f"Reachability of the {REQUIRED_FOLDS}/{TOTAL_FOLDS} (80%) fold-consistency bar:\n")
    print(f"  {'P(a fold is positive)':>22} {'P(>= 67 of 83)':>16}   reading")
    for p, label in [
        (0.50, "no edge at all"),
        (0.55, "a weak real edge"),
        (0.60, "a good edge"),
        (0.70, "a strong edge"),
        (0.80, "an implausibly strong edge"),
    ]:
        prob = sum(
            math.comb(TOTAL_FOLDS, k) * p**k * (1 - p) ** (TOTAL_FOLDS - k)
            for k in range(REQUIRED_FOLDS, TOTAL_FOLDS + 1)
        )
        print(f"  {p:>22.2f} {prob:>16.2e}   {label}")
    print("\n  A criterion a genuinely good strategy clears 0.005% of the time is")
    print("  not measuring the strategy. Below roughly 20-30 trades per fold,")
    print("  fold consistency and the sign test are uninformative in BOTH")
    print("  directions and must not be reported as evidence either way.\n")


def measured_trial_variance(runs_path: str) -> float:
    """The across-trial variance of daily Sharpes, read from the real log.

    Measured rather than assumed: this quantity sets how steeply the DSR
    benchmark rises with `N`, so a hardcoded guess would make the whole
    table decorative. Pools the research-purpose trials exactly as
    `retrospective.py` does -- infrastructure runs prove the harness
    works and were never candidates, so counting them would overstate
    the dispersion of genuine strategy search.
    """
    import json

    from research import lineage, retrospective
    from research.eligibility import deannualize_sharpe, sharpe_variance_across_trials

    records = [json.loads(line) for line in open(runs_path) if line.strip()]
    pooled: list[float | None] = []
    for family, values in retrospective.trial_sharpe_ratios(records).items():
        entry = lineage.FAMILY_BY_STRATEGY_ID
        purpose = next(
            (e.purpose for e in entry.values() if e.family == family),
            lineage.RESEARCH_PURPOSE,
        )
        if purpose != lineage.RESEARCH_PURPOSE:
            continue
        pooled.extend(values)
    daily = [
        None if v is None else deannualize_sharpe(v, bars_per_day=BARS_PER_DAY)
        for v in pooled
    ]
    variance = sharpe_variance_across_trials(daily)
    if variance is None:
        raise SystemExit(f"cannot measure trial variance from {runs_path}")
    return variance


def required_sharpe_by_n(trial_variance: float) -> None:
    """What annualized Sharpe clears DSR 0.95 at each `N`?

    `trial_variance` is the observed across-trial variance of daily
    Sharpes; it is a property of this project's own logged history, so it
    is taken as an argument rather than invented here.
    """
    from research.eligibility import deannualize_sharpe, evaluate_deflated_sharpe

    def dsr(annual: float, n: int) -> float:
        return evaluate_deflated_sharpe(
            sharpe_ratio=deannualize_sharpe(annual, bars_per_day=BARS_PER_DAY),
            num_observations=NUM_OBSERVATIONS,
            num_trials=n,
            trial_sharpe_variance=trial_variance,
        ).dsr

    print("Annualized Sharpe required to clear DSR 0.95, by trial count:\n")
    print(f"  {'N':>6} {'required Sharpe':>17}   reading")
    for n, note in [
        (1, "a pre-registered holdout -- no search to deflate"),
        (5, "this strategy family"),
        (20, ""),
        (50, ""),
        (127, "THIS PROJECT TODAY"),
        (200, ""),
    ]:
        lo, hi = 0.01, 60.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if dsr(mid, n) < 0.95:
                lo = mid
            else:
                hi = mid
        print(f"  {n:>6} {(lo + hi) / 2:>17.2f}   {note}")
    print("\n  Credible institutional trend-following reports 0.4-0.8 annualized.")
    print("  At N = 127 no realistic edge can clear this bar on this data,")
    print("  whatever it is -- which is the same arithmetic that closed the 1h")
    print("  window to selection, now true of the 1m window too.\n")


def run_missing_cells(config: str) -> None:
    from research.holdout import load_research_klines
    from research.strategies.selective_reversion import (
        DEFAULT_BARS_PER_DAY,
        DEFAULT_PARAMS,
        SIZING_COMPOUNDING,
        SelectiveReversionTrainable,
    )
    from research.walkforward import run_walk_forward

    symbol = "BINANCE-FUTURES:BTCUSDT"
    klines = load_research_klines(0, 4_102_444_800_000, holdout_config_path=config)
    for label, entry_z, activity_quantile in CELLS:
        params = {
            **DEFAULT_PARAMS,
            "entry_z": entry_z,
            "activity_quantile": activity_quantile,
            "use_stop": False,
            "sizing_mode": SIZING_COMPOUNDING,
        }
        result = run_walk_forward(
            klines,
            SelectiveReversionTrainable(symbol=symbol),
            "selective-reversion-no-stop",
            "2.1.0",
            params,
            train_bars=43_200,
            validate_bars=43_200,
            step_bars=43_200,
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("1"),
            bars_per_day=DEFAULT_BARS_PER_DAY,
            strategy_family="btc-scalping",
        )
        sharpes = [float(f.metrics.sharpe_ratio) for f in result.folds
                   if f.metrics.sharpe_ratio is not None]
        returns = [float(f.metrics.total_return) for f in result.folds]
        trades = sum(f.metrics.num_trades for f in result.folds)
        print(f"\n=== {label} ===  run_id {result.run_id}")
        print(f"  trades {trades} ({trades / len(result.folds):.1f}/fold)")
        print(f"  mean fold Sharpe {statistics.mean(sharpes):+.4f}   "
              f"positive {sum(1 for s in sharpes if s > 0)}/{len(sharpes)}")
        print(f"  compounded {(math.prod(1 + r for r in returns) - 1) * 100:+.2f}%")
    print("\nScore each with `python -m research.analysis.s14_eligibility "
          "--strategy-id selective-reversion-no-stop --run-id <id>`.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="read-only arithmetic; needs no data and logs nothing")
    ap.add_argument("--run", action="store_true",
                    help="execute the two missing walk-forward cells (logs, raises N)")
    ap.add_argument("--trial-variance", type=float, default=None,
                    help="across-trial variance of daily Sharpes; measured from "
                         "the log by default, never assumed")
    ap.add_argument("--config", default="configs/research/research_binance_futures_1m.json")
    ap.add_argument("--runs-path", default="runs/experiments.jsonl")
    args = ap.parse_args(argv)

    if not args.check and not args.run:
        ap.error("pass --check (read-only) or --run (logs a trial)")
    if args.check:
        fold_bar_attainability()
        variance = args.trial_variance
        if variance is None:
            variance = measured_trial_variance(args.runs_path)
            print(f"Across-trial variance of daily Sharpes, measured from "
                  f"{args.runs_path}: {variance:.6g}\n")
        required_sharpe_by_n(variance)
    if args.run:
        run_missing_cells(args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
