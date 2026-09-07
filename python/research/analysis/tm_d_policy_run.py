"""Trade Management Task D: run the six management policies for real.

    PYTHONPATH=python python/.venv/bin/python -m research.analysis.tm_d_policy_run

Executes `.planning/tm-d-breakout-management-preregistration.md` exactly.
That document merged before the implementation existed and is a
contract: this runner may not introduce a policy, a threshold or a
reporting choice it does not name.

## What this run can and cannot produce

**It cannot produce a pass** — procedurally, not mathematically. The
Binance futures 1m window is spent (S6 confirmed a holdout on it; S8
reclassified it as research data), so a result from it is not admissible
as evidence for promotion whatever it shows. DSR is still computed and
reported against both `N`s, because a high `N` raises the requirement
rather than forbidding it, and pretending otherwise was an overclaim the
registration already retracted.

**What it can produce** is the answer to a question this project has
never asked: given an entry fixed in advance, how much does management
change the outcome, and in which direction? That decides whether a
Phase 2 holdout access is worth requesting — a separate document and a
separate human approval, not something this run grants.

## Reporting order is fixed in advance

Gate A (drawdown, profit factor, net expectancy, episode count) is
evaluated **first**, and a policy failing it gets no statistical result
quoted in its favour. **`total R` is the registered headline comparison,
not win rate**: sources are explicit that scale-out raises win rate while
capping the right tail, so choosing the statistic after seeing which
policy it favours would rank P3 above P0 for the wrong reason.

## The fill-contract guard runs before any number is reported

`verify_fill_contract` checks every fill of every policy against
`klines[signal_bar_index + 1].open * (1 + side * SLIPPAGE_BPS / 10000)`.
A single breach **voids the run**. That check runs first, and a breach
aborts before any metric is printed — a voided run whose numbers were
already on screen is a voided run somebody will quote.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from backtest.engine import run_backtest
from metrics.metrics import compute_metrics
from research.holdout import load_research_klines
from research.strategies.breakout_management import (
    BreakoutManagementStrategy,
    Policy,
)
from research.strategies.fill_contract import verify_fill_contract
from schemas.order_intent import Side

SYMBOL = "BINANCE-FUTURES:BTCUSDT"
CONFIG = "configs/research/research_binance_futures_1m.json"

# The project's own measured constants, unchanged by this task.
FEE_BPS = Decimal("5")
SLIPPAGE_BPS = Decimal("1")
STARTING_EQUITY = Decimal("100000")

BARS_PER_DAY = 1440

# Gate A, pinned in the registration against CLAUDE.md as of 2026-09-07.
MAX_DRAWDOWN = Decimal("0.20")
MIN_PROFIT_FACTOR = Decimal("1.3")
MIN_EPISODES = 100


def _fmt(value, spec="+.4f"):
    return "n/a" if value is None else format(float(value), spec)


def run_policy(policy: Policy, klines) -> dict:
    strategy = BreakoutManagementStrategy(
        policy, SLIPPAGE_BPS, STARTING_EQUITY, symbol=SYMBOL
    )
    result = run_backtest(
        klines, strategy, FEE_BPS, SLIPPAGE_BPS, starting_equity=STARTING_EQUITY
    )

    breaches = verify_fill_contract(
        klines, result.fills, result.filled_intents, SLIPPAGE_BPS
    )
    if breaches:
        # Void, and the numbers are deliberately not computed: a voided
        # run whose metrics were printed is a voided run somebody quotes.
        return {"policy": policy.value, "void": [str(b) for b in breaches[:5]],
                "breach_count": len(breaches)}

    metrics = compute_metrics(
        klines,
        result.filled_intents,
        result.fills,
        starting_equity=STARTING_EQUITY,
        bars_per_day=BARS_PER_DAY,
    )

    episodes = strategy.episodes
    fills_by_intent = {f.intent_id: f for f in result.fills}
    sides_by_intent = {i.intent_id: i.side for i in result.filled_intents}

    # Episode P&L from real fills, never from the prices the strategy
    # saw when deciding. `R` is normalised to each episode's own
    # initial-layer planned risk, so P4's half-size layers land in the
    # same unit as P0's full one.
    r_values: list[float] = []
    unmatched = 0
    for ep in episodes:
        cash = Decimal(0)
        fees = Decimal(0)
        matched = 0
        for intent_id in ep.intent_ids:
            fill = fills_by_intent.get(intent_id)
            if fill is None:
                continue
            matched += 1
            side = sides_by_intent[intent_id]
            # A buy pays out cash, a sell brings it in.
            direction = Decimal(-1) if side is Side.LONG else Decimal(1)
            cash += direction * fill.fill_price * fill.quantity
            fees += fill.fee
        if matched != len(ep.intent_ids):
            unmatched += 1
            continue
        if ep.planned_risk > 0:
            r_values.append(float((cash - fees) / ep.planned_risk))

    return {
        "policy": policy.value,
        "void": None,
        "episodes": len(episodes),
        "ambiguous_days": strategy.ambiguous_days,
        "peak_qty_max": max((float(e.peak_qty) for e in episodes), default=0.0),
        "stopped": sum(1 for e in episodes if e.stopped),
        "fills": len(result.fills),
        "total_return": metrics.total_return,
        "max_drawdown": metrics.max_drawdown,
        "profit_factor": metrics.profit_factor,
        "sharpe_ratio": metrics.sharpe_ratio,
        "num_trades": metrics.num_trades,
        "win_rate": metrics.win_rate,
        "final_equity": metrics.final_equity,
        "total_r": sum(r_values),
        "mean_r": (sum(r_values) / len(r_values)) if r_values else None,
        "episodes_scored": len(r_values),
        "episodes_unmatched": unmatched,
        # `total_return` force-closes a still-open position at the last
        # bar (`build_equity_curve` does), while `total R` covers closed
        # episodes only — it has no closing fill to rebuild from. The
        # two therefore cover different sets, and the difference is
        # material exactly when a carrying policy ends mid-trade. Both
        # are reported with their scope rather than silently averaged.
        "episodes_open_at_end": 1 if strategy._episode is not None else 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=CONFIG)
    parser.add_argument("--limit-bars", type=int, default=None,
                        help="smoke-test on a prefix; never used for a reported run")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    klines = load_research_klines(0, 4_102_444_800_000, holdout_config_path=args.config)
    if not klines:
        print(f"no research klines for {SYMBOL}", file=sys.stderr)
        return 1
    if args.limit_bars:
        klines = klines[: args.limit_bars]

    span_days = (klines[-1].open_time - klines[0].open_time).total_seconds() / 86400
    print(f"{SYMBOL} 1m: {len(klines):,} bars, {span_days:,.0f} days "
          f"({klines[0].open_time:%Y-%m-%d} .. {klines[-1].open_time:%Y-%m-%d})")
    print(f"fee={FEE_BPS}bps slippage={SLIPPAGE_BPS}bps equity={STARTING_EQUITY}\n")

    rows = []
    for policy in Policy:
        print(f"  running {policy.value} ...", flush=True)
        rows.append(run_policy(policy, klines))

    voided = [r for r in rows if r.get("void")]
    if voided:
        print("\nRUN VOID — the fill contract was breached:", file=sys.stderr)
        for r in voided:
            print(f"  {r['policy']}: {r['breach_count']} breach(es)", file=sys.stderr)
            for b in r["void"]:
                print(f"    {b}", file=sys.stderr)
        return 2

    header = (f"\n{'policy':<8}{'episodes':>9}{'return':>12}{'maxDD':>9}"
              f"{'PF':>8}{'Sharpe':>9}{'win%':>7}{'totalR':>10}{'meanR':>9}"
              f"{'stopped':>9}{'peakQty':>10}")
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['policy']:<8}{r['episodes']:>9,}"
              f"{_fmt(r['total_return'], '+.4f'):>12}"
              f"{_fmt(r['max_drawdown'], '.4f'):>9}"
              f"{_fmt(r['profit_factor'], '.3f'):>8}"
              f"{_fmt(r['sharpe_ratio'], '+.3f'):>9}"
              f"{_fmt(r['win_rate'], '.3f'):>7}"
              f"{_fmt(r['total_r'], '+.1f'):>10}"
              f"{_fmt(r['mean_r'], '+.4f'):>9}"
              f"{r['stopped']:>9,}{r['peak_qty_max']:>10.4f}")

    print("\nGate A (evaluated first; a failing policy gets no statistic quoted for it)")
    for r in rows:
        checks = {
            f"maxDD<={MAX_DRAWDOWN}": r["max_drawdown"] is not None
                and Decimal(str(r["max_drawdown"])) <= MAX_DRAWDOWN,
            f"PF>={MIN_PROFIT_FACTOR}": r["profit_factor"] is not None
                and Decimal(str(r["profit_factor"])) >= MIN_PROFIT_FACTOR,
            f"episodes>={MIN_EPISODES}": r["episodes"] >= MIN_EPISODES,
        }
        verdict = "PASS" if all(checks.values()) else "FAIL"
        detail = " ".join(f"{k}={'y' if v else 'n'}" for k, v in checks.items())
        print(f"  {r['policy']}: {verdict}   {detail}")

    still_open = [r["policy"] for r in rows if r["episodes_open_at_end"]]
    if still_open:
        print(f"\nstill open at the last bar (excluded from total R, INCLUDED in "
              f"total_return via the equity curve's force-close): {', '.join(still_open)}")

    unmatched_total = sum(r["episodes_unmatched"] for r in rows)
    if unmatched_total:
        print(f"\nWARNING: {unmatched_total} episode(s) had an intent with no fill "
              f"and were excluded from R — investigate before quoting total R")

    print(f"\nambiguous days skipped (both triggers in one bar): "
          f"{rows[0]['ambiguous_days']:,}")

    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
