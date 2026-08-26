from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Kline:
    """One OHLCV bar. Internal simulation input, not a cross-language
    wire contract — stays Python-only until Java actually needs klines.

    `taker_buy_base_volume`/`taker_buy_quote_volume` (Scalping Strategy
    Research Task S6) are additive, optional order-flow fields mirroring
    `data.bingx_klines.KlineRow`'s own Task S5 fields exactly — `None`
    for every BingX-sourced bar and any bar loaded before this field
    existed. `research.holdout._kline_row_to_kline` is what actually
    carries them through from the stored `KlineRow`; a real, necessary
    gap Task S5 itself left open (it extended the storage layer only,
    never this in-memory type a `Strategy` actually observes), closed
    here rather than assumed already done.
    """

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    taker_buy_base_volume: Decimal | None = None
    taker_buy_quote_volume: Decimal | None = None
