from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Sequence

from backtest.fill import Fill, simulate_fill
from backtest.kline import Kline
from backtest.kline_window import KlineWindow
from schemas.order_intent import OrderIntent

Strategy = Callable[[Sequence[Kline]], OrderIntent | None]


@dataclass(frozen=True)
class BacktestResult:
    fills: list[Fill] = field(default_factory=list)
    # Index-aligned with `fills`: filled_intents[i] is the OrderIntent that
    # produced fills[i]. Additive — `Fill` itself has no `side` field, so a
    # portfolio/metrics layer consuming this result needs the originating
    # intent to know direction (see python/metrics/).
    filled_intents: list[OrderIntent] = field(default_factory=list)


def run_backtest(
    klines: list[Kline],
    strategy: Strategy,
    fee_bps: Decimal,
    slippage_bps: Decimal,
) -> BacktestResult:
    """Deterministic fill simulator — not a P&L/portfolio backtester (see
    schemas/README.md-adjacent design notes in the PR that added this).

    `strategy` is called at each bar with only a `KlineWindow(klines, i + 1)`
    — bars after the current one are never observable through it, so
    lookahead bias is structurally impossible rather than merely avoided by
    convention. `KlineWindow` is an O(1) bounds-checked view (see
    `backtest/kline_window.py`), not a copy — `klines[: i + 1]` was an O(n)
    copy every iteration (O(n^2) over a full run), which mattered once real
    multi-year datasets and walk-forward runs started exercising this loop.
    """
    fills: list[Fill] = []
    filled_intents: list[OrderIntent] = []
    for i in range(len(klines)):
        visible = KlineWindow(klines, i + 1)
        intent = strategy(visible)
        if intent is None:
            continue
        fill = simulate_fill(intent, klines, i, fee_bps, slippage_bps)
        if fill is not None:
            fills.append(fill)
            filled_intents.append(intent)
    return BacktestResult(fills=fills, filled_intents=filled_intents)
