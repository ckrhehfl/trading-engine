"""The registration's fill-contract guard, made runnable.

`.planning/tm-d-breakout-management-preregistration.md` requires that
**every** fill any policy produces satisfies

    fill.fill_price == klines[signal_bar_index + 1].open
                       * (1 + side * SLIPPAGE_BPS / 10000)

and that a run containing a deviation is **void**, not a result.

Two things this exists to prevent, both of which this project has done:

- **A guard written in fields that do not exist.** An earlier draft of
  the registration wrote `f.price` and `f.signal_bar`. `backtest.fill.
  Fill` has `intent_id`, `fill_time`, `fill_price`, `quantity`, `fee`,
  `notional`, and `signal_bar_index` is an *input* to `simulate_fill`,
  never an output. That guard could not have run at all.
- **A guard covering a fraction of what it claims.** A later draft
  checked stop-triggered exits only — one of seven event types.

`BacktestResult` gives parallel `fills` and `filled_intents` lists but
no signal-bar index. **Recovering it from `Fill.fill_time` alone is not
enough**, and a first version did exactly that: it derived the fill bar
from `fill_time` and then compared the price against *that same bar's*
open. Same-candle execution would have passed unchallenged, because the
expected price would have been computed from whichever bar the engine
chose to fill on.

So the signal bar is taken from `OrderIntent.created_at` — which
`BreakoutManagementStrategy` sets to the signal bar's `open_time` — and
the timing is checked explicitly: **the fill bar must be exactly the
signal bar plus one.** The price check then hangs off a signal index the
engine did not get to choose.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backtest.fill import Fill
from backtest.kline import Kline
from schemas.order_intent import OrderIntent, Side

_BPS = Decimal("10000")


@dataclass(frozen=True)
class ContractBreach:
    """One fill that did not match the contract. Any breach voids a run."""

    intent_id: str
    fill_time: str
    expected: Decimal
    actual: Decimal
    reason: str
    signal_index: int | None = None
    fill_index: int | None = None

    def __str__(self) -> str:
        return (
            f"{self.reason}: intent {self.intent_id} at {self.fill_time} "
            f"expected {self.expected} got {self.actual}"
        )


def verify_fill_contract(
    klines: list[Kline],
    fills: list[Fill],
    filled_intents: list[OrderIntent],
    slippage_bps: Decimal,
) -> list[ContractBreach]:
    """Check every fill against the registration's contract.

    Returns every breach rather than the first, because "which events
    deviate" is the diagnostic and a single-breach abort would hide
    whether the problem is one event type or all of them.
    """
    by_open = {bar.open_time: i for i, bar in enumerate(klines)}
    slip = Decimal(slippage_bps) / _BPS
    breaches: list[ContractBreach] = []

    for fill, intent in zip(fills, filled_intents, strict=True):
        signal_index = by_open.get(intent.created_at)
        fill_index = by_open.get(fill.fill_time)

        if signal_index is None:
            breaches.append(
                ContractBreach(
                    str(fill.intent_id),
                    str(fill.fill_time),
                    Decimal(0),
                    fill.fill_price,
                    "intent.created_at matches no bar open, so the signal bar "
                    "cannot be identified",
                )
            )
            continue
        if fill_index is None:
            breaches.append(
                ContractBreach(
                    str(fill.intent_id),
                    str(fill.fill_time),
                    Decimal(0),
                    fill.fill_price,
                    "fill_time matches no bar open",
                )
            )
            continue
        if fill_index != signal_index + 1:
            breaches.append(
                ContractBreach(
                    str(fill.intent_id),
                    str(fill.fill_time),
                    Decimal(0),
                    fill.fill_price,
                    f"filled on bar {fill_index}, expected {signal_index + 1} "
                    f"(the bar after the signal)",
                    signal_index,
                    fill_index,
                )
            )
            continue

        sign = 1 if intent.side is Side.LONG else -1
        expected = klines[signal_index + 1].open * (Decimal(1) + Decimal(sign) * slip)
        if fill.fill_price != expected:
            breaches.append(
                ContractBreach(
                    str(fill.intent_id),
                    str(fill.fill_time),
                    expected,
                    fill.fill_price,
                    "fill price is not next-bar open adjusted by slippage",
                    signal_index,
                    fill_index,
                )
            )

    return breaches
