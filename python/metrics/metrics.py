"""Portfolio/metrics layer: turns a `BacktestResult`'s `fills`/
`filled_intents` plus the `klines` a backtest ran against into a
mark-to-market equity curve and a summary `Metrics` record (total return,
Sharpe ratio, max drawdown, win rate, trade count, profit factor).

Not inside `backtest/` — that package stays scoped to fill simulation only
(see `backtest/engine.py`'s `run_backtest` docstring). This module is a
separate, downstream concern that consumes a backtest's output; it does
not simulate fills itself.

**Known gap, not solved here**: perpetual funding-rate P&L is not modeled
anywhere in this pipeline — flagged so it isn't silently forgotten, not
addressed by this module.
"""

import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import StatisticsError, fmean, stdev

from backtest.fill import Fill
from backtest.kline import Kline
from metrics.position import ClosedTrade, PositionTracker
from schemas.order_intent import OrderIntent

# 15m bars: 96 bars/day. 365, not 365.25 — this project's fixed
# annualization convention (see CLAUDE.md's "Strategy Research Operational
# Design"), not open to per-implementation judgment. This remains the
# *default* `bars_per_day` for both `compute_metrics` and `_sharpe_ratio`
# below (every 15m caller in this codebase omits the parameter and gets
# byte-for-byte the same behavior as before Strategy Research Task F) --
# Task F's native-1h strategy variant (`research/strategies/
# hourly_momentum.py`, 24 bars/day) is what actually needed this to become
# a parameter instead of a hardcoded module constant: using the 15m factor
# for 1h data would inflate the reported Sharpe by exactly sqrt(96/24) =
# 2x, since the same per-bar return series would be (incorrectly) treated
# as happening 4x more often per day than it really did. See
# `.planning/sr-f-risk-management-and-1h-variant.md`.
_BARS_PER_DAY = 96
_DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class Metrics:
    """Summary statistics for one backtest run.

    `sharpe_ratio`, `win_rate`, and `profit_factor` are `None` — never
    `0.0`, never `inf`, never a raised exception — for the degenerate
    inputs named in CLAUDE.md's "Strategy Research Operational Design":
    zero closed trades, zero-variance per-bar returns, and zero losing
    trades respectively. `None` means "no evidence", which is a different
    claim than "bad evidence" (`0.0`) or "unboundedly good" (`inf`).
    """

    starting_equity: Decimal
    final_equity: Decimal
    equity_curve: list[Decimal]
    closed_trades: list[ClosedTrade]
    total_return: Decimal
    sharpe_ratio: float | None
    max_drawdown: Decimal
    win_rate: float | None
    num_trades: int
    profit_factor: float | None


def build_equity_curve(
    klines: list[Kline],
    filled_intents: list[OrderIntent],
    fills: list[Fill],
    starting_equity: Decimal,
) -> tuple[list[Decimal], list[ClosedTrade]]:
    """Walks `klines` bar by bar, applying every `(intent, fill)` pair
    whose `fill.fill_time` has been reached (fills always land exactly on
    a kline's `open_time` — see `backtest/fill.py`), and marks any open
    position to that bar's close:

        equity[i] = starting_equity + realized_pnl_so_far
                    - cumulative_fees_so_far
                    + position_qty * (klines[i].close - avg_entry_price)

    Any position still open after the final bar is force-closed at that
    bar's close price (CLAUDE.md's degenerate-input rule) so it isn't
    silently dropped from trade-count/win-rate/profit-factor metrics. This
    only appends to the returned `closed_trades` list — it does not change
    `equity_curve[-1]`'s value, since force-closing at the same price
    already used to mark the final bar to market just converts an
    unrealized amount already counted into a realized one.

    All arithmetic here is `Decimal`, matching `simulate_fill`'s exact
    precision — kept that way through this function; `Decimal` -> `float`
    conversion happens only in the statistical aggregation steps below
    (`_sharpe_ratio`), which stdlib `statistics`/`math` require.
    """
    tracker = PositionTracker()
    closed_trades: list[ClosedTrade] = []
    equity_curve: list[Decimal] = []

    fill_index = 0
    n_fills = len(fills)

    for kline in klines:
        while fill_index < n_fills and fills[fill_index].fill_time <= kline.open_time:
            closed = tracker.apply(filled_intents[fill_index], fills[fill_index])
            if closed is not None:
                closed_trades.append(closed)
            fill_index += 1

        if tracker.position_qty == 0:
            unrealized = Decimal(0)
        else:
            unrealized = tracker.position_qty * (kline.close - tracker.avg_entry_price)

        equity = starting_equity + tracker.realized_pnl - tracker.cumulative_fees + unrealized
        equity_curve.append(equity)

    assert fill_index == n_fills, (
        f"{n_fills - fill_index} fill(s) have fill_time after the last kline's "
        "open_time — this violates backtest/fill.py's contract that a fill's "
        "fill_time is always some kline's open_time in the same klines list "
        "(fills would otherwise silently vanish from equity/trade metrics)."
    )

    if klines and tracker.position_qty != 0:
        final_kline = klines[-1]
        closed = tracker.force_close(final_kline.close, final_kline.open_time)
        if closed is not None:
            closed_trades.append(closed)

    return equity_curve, closed_trades


def compute_metrics(
    klines: list[Kline],
    filled_intents: list[OrderIntent],
    fills: list[Fill],
    starting_equity: Decimal,
    bars_per_day: int = _BARS_PER_DAY,
) -> Metrics:
    """Builds the equity curve/closed-trade list and reduces them to the
    summary `Metrics` record. See `Metrics`' docstring for the degenerate-
    input contract (`None`, not `0.0`/`inf`/an exception).

    `bars_per_day` (default 96, the pre-existing 15m assumption) is passed
    straight through to `_sharpe_ratio`'s annualization -- see that
    function's docstring and this module's `_BARS_PER_DAY` comment for why
    it exists as a parameter rather than a hardcoded constant.
    """
    if starting_equity <= 0:
        raise ValueError(f"starting_equity must be positive, got {starting_equity}")

    equity_curve, closed_trades = build_equity_curve(klines, filled_intents, fills, starting_equity)
    final_equity = equity_curve[-1] if equity_curve else starting_equity

    return Metrics(
        starting_equity=starting_equity,
        final_equity=final_equity,
        equity_curve=equity_curve,
        closed_trades=closed_trades,
        total_return=(final_equity / starting_equity) - 1,
        sharpe_ratio=_sharpe_ratio(equity_curve, bars_per_day=bars_per_day),
        max_drawdown=_max_drawdown(equity_curve),
        win_rate=_win_rate(closed_trades),
        num_trades=len(closed_trades),
        profit_factor=_profit_factor(closed_trades),
    )


def _sharpe_ratio(equity_curve: list[Decimal], bars_per_day: int = _BARS_PER_DAY) -> float | None:
    """Per-bar simple returns -> `fmean(r) / stdev(r)` (sample stdev,
    ddof=1) -> annualized via `sqrt(bars_per_day * 365)`. Risk-free rate
    assumed 0. `bars_per_day` defaults to 96 (15m bars, this project's
    original and still most common timeframe) -- pass 24 for 1h bars, or
    whatever else a future timeframe needs. 365, not 365.25, regardless of
    `bars_per_day` -- this project's fixed annualization convention (see
    CLAUDE.md's "Strategy Research Operational Design"), not open to
    per-implementation judgment.

    `None` (never a raised `ZeroDivisionError`/`StatisticsError`, never an
    inflated value) when there are fewer than two per-bar returns to take
    a sample stdev over, or when every equity level is unusable as a
    return's denominator (0), or when the returns have zero variance —
    all "no evidence of risk-adjusted edge one way or the other", not "an
    edge of exactly zero".
    """
    if len(equity_curve) < 2:
        return None

    returns: list[float] = []
    for previous, current in zip(equity_curve, equity_curve[1:]):
        if previous == 0:
            # Can't express a return relative to a zero-equity base.
            # Not a documented CLAUDE.md scenario (equity is not expected
            # to reach exactly zero), but skipping rather than dividing by
            # zero is the only safe option if it ever does.
            continue
        returns.append(float((current - previous) / previous))

    if len(returns) < 2:
        return None

    mean_return = fmean(returns)
    try:
        stdev_return = stdev(returns)
    except StatisticsError:
        return None
    if stdev_return == 0:
        return None

    annualization_factor = math.sqrt(bars_per_day * _DAYS_PER_YEAR)
    return (mean_return / stdev_return) * annualization_factor


def _max_drawdown(equity_curve: list[Decimal]) -> Decimal:
    """Running peak `peak[t] = max(equity[0..t])`, `drawdown[t] = (peak[t]
    - equity[t]) / peak[t]`, reports `max(drawdown)`. `Decimal(0)` for an
    empty curve or a curve that never drops below its running peak.
    """
    if not equity_curve:
        return Decimal(0)

    peak = equity_curve[0]
    worst = Decimal(0)
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = (peak - equity) / peak
            if drawdown > worst:
                worst = drawdown
    return worst


def _win_rate(closed_trades: list[ClosedTrade]) -> float | None:
    """`count(trades where realized_pnl > 0) / count(all trades)`,
    trade-level (not fill-level). `None` for zero closed trades — a flat,
    trade-less run is "no evidence", not a 0% win rate.
    """
    if not closed_trades:
        return None
    winners = sum(1 for trade in closed_trades if trade.realized_pnl > 0)
    return winners / len(closed_trades)


def _profit_factor(closed_trades: list[ClosedTrade]) -> float | None:
    """`sum(winning trade pnl) / abs(sum(losing trade pnl))`. `None`
    (never `float('inf')`) both when there are zero closed trades and when
    there are zero losing trades — the latter means there's no evidence of
    a poor risk/reward ratio to report, not an unboundedly good one.
    """
    if not closed_trades:
        return None
    gross_profit = sum((trade.realized_pnl for trade in closed_trades if trade.realized_pnl > 0), Decimal(0))
    gross_loss = sum((trade.realized_pnl for trade in closed_trades if trade.realized_pnl < 0), Decimal(0))
    if gross_loss == 0:
        return None
    return float(gross_profit / abs(gross_loss))
