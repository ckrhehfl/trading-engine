"""Trade Management Task C: run the pre-registered confluence hedge.

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.tmc_confluence_hedge_run

Executes `.planning/tm-c-confluence-hedge-specification.md` exactly as
written. **No constant in that specification is read from the command
line**, deliberately: the four values it declares arbitrary (funding
percentile 0.80, flow z 1.0, price z 1.0, activity rank 0.5) plus the
0.5 hedge fraction are the surface a defeated researcher reaches for,
and its pre-committed stopping rule forecloses moving them. Exposing
them as flags would make that a matter of restraint rather than of
construction.

## Two timeframes, and why that is not a threshold search

The specification was registered against **daily** bars and returned 2
conjunction events against its own floor of 20 -- INCONCLUSIVE-DATA-
LIMITED, which it had named in advance as the most likely outcome. The
legitimate response to "too few samples" is more samples, so this runs
the identical specification on **hourly** bars as well.

What changes between the two runs is only how many bars express a given
calendar span. Everything measured in *calendar* terms is held fixed by
scaling its bar count: the lookback set `{21, 63, 126, 252}` days
becomes `{504, 1512, 3024, 6048}` hours, and the 90-day trailing window
becomes 2,160 bars. The ATR period stays 14 bars -- it is Wilder's own
convention, denominated in bars rather than days, and rescaling it would
be a change to the specification rather than a translation of it.

**This is still trial two of the same hypothesis and is reported as
such.** A second timeframe is not free: it is a second look, and if the
two disagreed the honest reading would be that neither is established.
They do not disagree, which is the only reason a conclusion is drawn at
all.

## Order flow is why the hourly run uses Binance

The conjunction needs `taker_buy_base_volume`, and BingX's wire carries
no buyer/seller breakdown at all -- every BingX row in this project's
cache has it `NULL`. The daily run against BingX bars therefore fired
the flow condition zero times and, by the strategy's own fail-closed
rule, opened no hedge whatsoever. That is the guard working, not a
result. Both runs here aggregate Binance USDT-M futures 1m bars, the one
series in this cache with real order flow.

## What is compared

The core alone against the core plus the tactical overlay, on the same
bars, same costs, same seed data. `Book.realized_pnl_by_purpose` splits
the outcome by leg purpose, which is the figure no previous candidate in
this project could produce -- every earlier strategy held a single
net position and so had no way to say which part of it earned what.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sqlite3
import statistics
import sys
from decimal import Decimal

from backtest.engine import run_backtest
from backtest.kline import Kline
from metrics.book import LegPurpose
from metrics.metrics import compute_metrics
from research.conclusion_check import (
    check_claim_universal,
    check_disjoint_intervals,
    check_parameter_swept,
    check_same_population,
    format_findings,
    require_no_blockers,
)
from research.strategies.confluence_hedge import ConfluenceHedgeStrategy
from research.strategies.daily_tsmom_ensemble import DailyTsmomEnsembleStrategy

DEFAULT_DB = "python/data/var/klines.sqlite3"
STARTING_EQUITY = Decimal("10000")

# The one series in this cache carrying real taker-buy volume.
FLOW_SYMBOL = "BINANCE-FUTURES:BTCUSDT"
# Funding is a BingX series; the rate is the instrument's, not a venue's
# private figure, and both venues quote the same BTC perpetual funding
# regime. Mapped by calendar day, so it resolves at either timeframe.
FUNDING_SYMBOL = "BTC-USDT"

FEE_BPS = Decimal("5")
SLIPPAGE_BPS = Decimal("2")

# The specification's own floor. Below this the run is INCONCLUSIVE-DATA-
# LIMITED and must not be written up as evidence in either direction.
MIN_HEDGES = 20

# Calendar-denominated constants from the specification, in days. Scaled
# by bars-per-day at each timeframe so the *span* is what is held fixed.
LOOKBACK_DAYS = (21, 63, 126, 252)
TRAILING_WINDOW_DAYS = 90

TIMEFRAMES = (("daily", 1, 86_400), ("hourly", 24, 3_600))


def aggregate(db: str, symbol: str, bucket_seconds: int) -> list[Kline]:
    """Aggregate cached 1m bars into `bucket_seconds` bars.

    Taker-buy volume sums like volume does. A bar missing it entirely
    would silently contribute zero to a sum that other bars populate, so
    a bucket is only given a flow figure when **every** minute in it has
    one -- a partially-populated bucket reports `None` and the strategy's
    fail-closed rule declines to use it, rather than reading a
    short-counted sum as genuine selling pressure.
    """
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT open_time_ms, open, high, low, close, volume, taker_buy_base_volume "
        "FROM klines WHERE symbol=? AND interval='1m' ORDER BY open_time_ms",
        (symbol,),
    )
    buckets: dict[int, list] = {}
    for open_ms, o, h, low, c, v, taker in rows:
        key = open_ms // (bucket_seconds * 1000)
        row = buckets.get(key)
        if row is None:
            buckets[key] = [
                Decimal(str(o)), Decimal(str(h)), Decimal(str(low)), Decimal(str(c)),
                Decimal(str(v)), Decimal(str(taker)) if taker is not None else None,
            ]
            continue
        row[1] = max(row[1], Decimal(str(h)))
        row[2] = min(row[2], Decimal(str(low)))
        row[3] = Decimal(str(c))
        row[4] += Decimal(str(v))
        if row[5] is not None:
            row[5] = None if taker is None else row[5] + Decimal(str(taker))
    conn.close()
    return [
        Kline(
            open_time=dt.datetime.fromtimestamp(key * bucket_seconds, dt.timezone.utc),
            open=r[0], high=r[1], low=r[2], close=r[3], volume=r[4],
            taker_buy_base_volume=r[5],
        )
        for key, r in sorted(buckets.items())
    ]


def load_funding(db: str) -> dict[dt.date, Decimal]:
    """Mean funding rate per calendar day.

    Averaged rather than taken last: funding settles every 8 hours, so a
    day holds up to three readings and "the rate in force that day" has
    no single answer. The mean is the neutral choice and is applied
    identically at both timeframes.
    """
    conn = sqlite3.connect(db)
    by_day: dict[dt.date, list[Decimal]] = {}
    for ms, rate in conn.execute(
        "SELECT funding_time_ms, funding_rate FROM funding_rates WHERE symbol=?",
        (FUNDING_SYMBOL,),
    ):
        day = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()
        by_day.setdefault(day, []).append(Decimal(str(rate)))
    conn.close()
    return {day: sum(v) / len(v) for day, v in by_day.items()}


def welch_t(sample: list[float]) -> tuple[float, float]:
    """One-sample t against zero, with a two-sided normal p-value.

    The normal approximation is used rather than an exact t-distribution
    because every sample this is called on here has well over 100
    observations, where the two agree to more decimal places than are
    reported. Returns `(0.0, 1.0)` for a degenerate sample rather than
    raising -- a zero-variance outcome sequence is a real possible result
    and is not distinguishable from zero.
    """
    if len(sample) < 2:
        return 0.0, 1.0
    stdev = statistics.stdev(sample)
    if stdev == 0:
        return 0.0, 1.0
    t = statistics.mean(sample) / (stdev / math.sqrt(len(sample)))
    p = math.erfc(abs(t) / math.sqrt(2))
    return t, p


def run_timeframe(
    label: str, bars_per_day: int, bucket_seconds: int, db: str, funding: dict
) -> dict:
    klines = aggregate(db, FLOW_SYMBOL, bucket_seconds)
    lookbacks = tuple(days * bars_per_day for days in LOOKBACK_DAYS)
    trailing = TRAILING_WINDOW_DAYS * bars_per_day

    core = DailyTsmomEnsembleStrategy(
        symbol=FLOW_SYMBOL, lookbacks=lookbacks, bars_per_day=bars_per_day
    )
    core_result = run_backtest(
        klines, core, fee_bps=FEE_BPS, slippage_bps=SLIPPAGE_BPS,
        starting_equity=STARTING_EQUITY,
    )
    core_metrics = compute_metrics(
        klines, core_result.filled_intents, core_result.fills,
        STARTING_EQUITY, bars_per_day=bars_per_day,
    )

    overlay = ConfluenceHedgeStrategy(
        symbol=FLOW_SYMBOL, funding_by_day=funding, lookbacks=lookbacks,
        trailing_window=trailing, bars_per_day=bars_per_day,
    )
    overlay_result = run_backtest(
        klines, overlay, fee_bps=FEE_BPS, slippage_bps=SLIPPAGE_BPS,
        starting_equity=STARTING_EQUITY,
    )
    overlay_metrics = compute_metrics(
        klines, overlay_result.filled_intents, overlay_result.fills,
        STARTING_EQUITY, bars_per_day=bars_per_day,
    )

    hedges = [c for c in overlay.book.closes if c.purpose is LegPurpose.TACTICAL]
    return {
        "label": label,
        "bars_per_day": bars_per_day,
        "klines": klines,
        "core": core_metrics,
        "overlay": overlay_metrics,
        "strategy": overlay,
        "hedges": hedges,
        "core_fees": sum(f.fee for f in core_result.fills),
        "overlay_fees": sum(f.fee for f in overlay_result.fills),
    }


def report(run: dict) -> list:
    """Print one timeframe's result and return its conclusion findings."""
    klines, strategy = run["klines"], run["strategy"]
    core, overlay, hedges = run["core"], run["overlay"], run["hedges"]

    print(f"\n{'=' * 66}")
    print(f"  {run['label']}  --  {len(klines):,} bars  "
          f"({klines[0].open_time:%Y-%m-%d} .. {klines[-1].open_time:%Y-%m-%d})")
    print("=" * 66)

    print("\n  condition firings")
    for name, hits in strategy.condition_hits.items():
        print(f"    {name:<10} {hits:>7,}")
    print(f"    {'ALL FOUR':<10} {strategy.conjunction_hits:>7,}"
          f"   -> {strategy.hedges_opened} hedges opened, "
          f"{strategy.hedges_invalidated} invalidated")

    print(f"\n  {'':<14}{'trades':>8}{'return':>11}{'maxDD':>9}"
          f"{'PF':>7}{'Sharpe':>9}")
    for name, m in (("core alone", core), ("core + hedge", overlay)):
        pf = "n/a" if m.profit_factor is None else f"{float(m.profit_factor):.2f}"
        sharpe = "n/a" if m.sharpe_ratio is None else f"{float(m.sharpe_ratio):+.3f}"
        print(f"    {name:<14}{m.num_trades:>8,}"
              f"{float(m.total_return) * 100:>+10.1f}%"
              f"{float(m.max_drawdown) * 100:>8.2f}%{pf:>7}{sharpe:>9}")

    delta = float(overlay.total_return - core.total_return) * 100
    print(f"\n    overlay contribution: {delta:+.2f} percentage points of return")

    # `Book.realized_pnl` is (exit - entry) x quantity: net of slippage,
    # which is baked into the fill price, but **gross of fees**, which
    # `Fill` carries separately. Printing it without the fee bill beside
    # it would show the overlay's raw edge as its outcome -- and the two
    # differ by an order of magnitude here, which is the whole finding.
    by_purpose = strategy.book.realized_pnl_by_purpose(FLOW_SYMBOL)
    for purpose, pnl in by_purpose.items():
        if pnl:
            print(f"    gross edge, {purpose.value:<9} {float(pnl):>+11,.0f}"
                  f"   (before fees)")
    fee_delta = float(run["overlay_fees"] - run["core_fees"])
    print(f"    fees the overlay added {fee_delta:>+16,.0f}")

    findings = []
    if len(hedges) < MIN_HEDGES:
        print(f"\n  VERDICT: INCONCLUSIVE-DATA-LIMITED "
              f"({len(hedges)} hedges < the specification's floor of {MIN_HEDGES})")
        print("  Not evidence in either direction. The specification named")
        print("  'too rare to measure' as the most likely outcome in advance.")
        return findings

    outcomes = [float(h.realized_pnl) for h in hedges]
    t, p = welch_t(outcomes)
    wins = sum(1 for o in outcomes if o > 0)
    print(f"\n  hedge legs: {len(hedges)}   won {wins} ({wins / len(hedges):.1%})")
    print(f"    mean {statistics.mean(outcomes):+,.1f}   "
          f"median {statistics.median(outcomes):+,.1f}   "
          f"t {t:+.2f}   p {p:.3f}")

    # Hedges are opened one at a time by construction; verify it rather
    # than trust it. Holds vary from bar to bar, so this needs the
    # interval form -- a uniform `hold_bars` set to the longest hold would
    # flag genuinely disjoint short holds as overlapping.
    #
    # `clustering_gap` is one full day at whichever timeframe is running.
    # Two hedges inside the same day are near-certainly the same
    # volatility episode seen twice, and the warning that produces is the
    # honest disclosure, not a nuisance to silence.
    index_of = {k.open_time: i for i, k in enumerate(klines)}
    intervals = [(index_of[h.entry_time], index_of[h.exit_time]) for h in hedges]
    holds = [end - start for start, end in intervals]
    findings.append(check_disjoint_intervals(
        intervals, clustering_gap=run["bars_per_day"],
        check=f"{run['label']}:hedge_independence",
    ))
    findings.append(check_same_population(
        {"core alone": [k.open_time for k in klines],
         "core + hedge": [k.open_time for k in klines]},
        check=f"{run['label']}:same_bars",
    ))
    print(f"    longest hold {max(holds)} bars, median {statistics.median(holds):.0f}")
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", default=DEFAULT_DB)
    args = parser.parse_args(argv)

    funding = load_funding(args.db_path)
    print(f"funding: {len(funding):,} days "
          f"({min(funding):%Y-%m-%d} .. {max(funding):%Y-%m-%d})")

    runs = [
        run_timeframe(label, bpd, secs, args.db_path, funding)
        for label, bpd, secs in TIMEFRAMES
    ]
    findings = [f for run in runs for f in report(run)]

    # Two timeframes is a sweep of one parameter, and a small one. It is
    # recorded here so the conclusion is scoped to what was actually
    # varied rather than generalised to "hedging".
    findings.append(check_parameter_swept(
        [{"params": {"bars_per_day": r["bars_per_day"]}} for r in runs],
        parameter="bars_per_day",
    ))
    conclusive = [r for r in runs if len(r["hedges"]) >= MIN_HEDGES]
    if conclusive:
        findings.append(check_claim_universal(
            conclusive,
            lambda r: r["overlay"].total_return <= r["core"].total_return,
            claim="the overlay did not improve total return at any timeframe measured",
        ))

    print(f"\n{'=' * 66}\n  conclusion checks\n{'=' * 66}")
    print(format_findings(findings))
    require_no_blockers(findings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
