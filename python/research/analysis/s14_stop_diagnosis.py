"""Task S14 diagnosis: why did a positive gross edge become a negative
strategy?

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.s14_stop_diagnosis

S13's sweep measured **+25.10bps gross per position** at `|z|>=5` in the
top 1% of activity, 60-bar horizon. `selective-reversion` trades those
same entries and its 83-fold walk-forward came back at **mean fold
Sharpe -1.47, 36.1% of folds positive, -73.3% compounded**. Both numbers
are real, so something between the two is doing the damage.

Only three things stand between them: the stop, the reversion target, and
the R:R gate. This isolates each by turning it off, using `run_backtest`
directly so nothing is written to `runs/experiments.jsonl`.

**This is diagnosis, not selection**, in exactly the sense CLAUDE.md's
standing rule distinguishes them (a window stays open for "diagnosing a
mechanism" while closed to "selecting a configuration"). Nothing here
picks a configuration to carry forward. If any variant below were ever
adopted, that adoption would need its own logged walk-forward run and
would count toward `N` -- reading a number off this script is not a
substitute for that.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from decimal import Decimal

from backtest.engine import run_backtest
from metrics.metrics import compute_metrics
from research.holdout import load_research_klines
from research.strategies.selective_reversion import (
    DEFAULT_BARS_PER_DAY,
    SelectiveReversionStrategy,
)

CONFIG = "configs/research/research_binance_futures_1m.json"
SYMBOL = "BINANCE-FUTURES:BTCUSDT"
FEE_BPS = Decimal("5")
SLIPPAGE_BPS = Decimal("1")
STARTING_EQUITY = Decimal("10000")

# A stop this wide cannot be reached inside a 60-bar hold in practice, so
# it is how "no stop" is expressed without a separate code path -- the
# same shape S13's excursion measurement had.
EFFECTIVELY_NO_STOP = Decimal("1000")

VARIANTS: list[tuple[str, dict]] = [
    ("as shipped (2.65 ATR stop, R:R>=2)", {}),
    ("no stop", {"stop_atr_multiple": EFFECTIVELY_NO_STOP}),
    ("no R:R gate", {"min_rr": Decimal("0.0001")}),
    ("neither", {"stop_atr_multiple": EFFECTIVELY_NO_STOP, "min_rr": Decimal("0.0001")}),
    ("stop 5 ATR", {"stop_atr_multiple": Decimal("5")}),
    ("stop 8 ATR", {"stop_atr_multiple": Decimal("8")}),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=CONFIG)
    args = ap.parse_args(argv)

    klines = load_research_klines(0, 4_102_444_800_000, holdout_config_path=args.config)
    print(f"{SYMBOL} 1m: {len(klines):,} bars, one pass per variant, "
          f"{FEE_BPS + SLIPPAGE_BPS}bps per side\n")

    hdr = (f"{'variant':>36} {'trades':>7} {'win%':>6} {'PF':>7} "
           f"{'return':>9} {'Sharpe':>8} | {'stop':>6} {'target':>7} {'time':>6} {'declined':>9}")
    print(hdr)
    print("-" * len(hdr))

    for label, overrides in VARIANTS:
        s = SelectiveReversionStrategy(symbol=SYMBOL, **overrides)
        result = run_backtest(klines, s, fee_bps=FEE_BPS, slippage_bps=SLIPPAGE_BPS)
        m = compute_metrics(
            klines,
            result.filled_intents,
            result.fills,
            STARTING_EQUITY,
            bars_per_day=DEFAULT_BARS_PER_DAY,
        )
        e = s.exits
        pf = "n/a" if m.profit_factor is None else f"{float(m.profit_factor):.3f}"
        wr = "n/a" if m.win_rate is None else f"{float(m.win_rate)*100:.1f}"
        sh = "n/a" if m.sharpe_ratio is None else f"{float(m.sharpe_ratio):+.3f}"
        print(f"{label:>36} {m.num_trades:>7,} {wr:>6} {pf:>7} "
              f"{float(m.total_return)*100:>8.2f}% {sh:>8} | "
              f"{e['stop']:>6,} {e['target']:>7,} {e['time']:>6,} {s.declined_on_rr:>9,}")

    print("\nRead the exit columns first. A target that never fills means the")
    print("design in the docstring is not the design that ran.")
    return 0


def _mean(xs):  # kept for callers importing this module
    return statistics.mean(xs) if xs else None


if __name__ == "__main__":
    sys.exit(main())
