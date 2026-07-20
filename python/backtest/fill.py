from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from backtest.kline import Kline
from schemas.order_intent import OrderIntent, OrderType, Side

_BPS_DIVISOR = Decimal("10000")


@dataclass(frozen=True)
class Fill:
    intent_id: UUID
    fill_time: datetime
    fill_price: Decimal
    quantity: Decimal
    fee: Decimal
    notional: Decimal


def simulate_fill(
    intent: OrderIntent,
    klines: list[Kline],
    signal_bar_index: int,
    fee_bps: Decimal,
    slippage_bps: Decimal,
) -> Fill | None:
    """Simulate the fill of `intent`, generated while looking at
    `klines[: signal_bar_index + 1]`, against the *next* bar only — never
    the signal bar itself (no same-candle execution).
    """
    next_index = signal_bar_index + 1
    if next_index >= len(klines):
        return None
    next_bar = klines[next_index]

    if intent.order_type == OrderType.GUARDED_MARKET:
        raw_price = next_bar.open
        # Slippage only applies here: a market order doesn't control its
        # execution price. A LIMIT order's whole point is a price
        # guarantee — it fills at the limit price or not at all, never
        # worse, so slippage must not be layered on top of it below.
        slippage_factor = slippage_bps / _BPS_DIVISOR
        if intent.side == Side.LONG:
            fill_price = raw_price * (Decimal("1") + slippage_factor)
        else:  # SHORT
            fill_price = raw_price * (Decimal("1") - slippage_factor)
    else:  # LIMIT
        if not (next_bar.low <= intent.limit_price <= next_bar.high):
            return None
        fill_price = intent.limit_price

    notional = intent.quantity * fill_price
    fee = notional * fee_bps / _BPS_DIVISOR

    return Fill(
        intent_id=intent.intent_id,
        fill_time=next_bar.open_time,
        fill_price=fill_price,
        quantity=intent.quantity,
        fee=fee,
        notional=notional,
    )
