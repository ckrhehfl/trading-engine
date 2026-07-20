from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from backtest.fill import Fill, simulate_fill
from backtest.kline import Kline
from schemas.order_intent import OrderIntent

Strategy = Callable[[list[Kline]], OrderIntent | None]


@dataclass(frozen=True)
class BacktestResult:
    fills: list[Fill] = field(default_factory=list)


def run_backtest(
    klines: list[Kline],
    strategy: Strategy,
    fee_bps: Decimal,
    slippage_bps: Decimal,
) -> BacktestResult:
    """Deterministic fill simulator — not a P&L/portfolio backtester (see
    schemas/README.md-adjacent design notes in the PR that added this).

    `strategy` is called at each bar with only `klines[: i + 1]` — bars
    after the current one are never passed, so lookahead bias is
    structurally impossible rather than merely avoided by convention.
    """
    fills: list[Fill] = []
    for i in range(len(klines)):
        visible = klines[: i + 1]
        intent = strategy(visible)
        if intent is None:
            continue
        fill = simulate_fill(intent, klines, i, fee_bps, slippage_bps)
        if fill is not None:
            fills.append(fill)
    return BacktestResult(fills=fills)
