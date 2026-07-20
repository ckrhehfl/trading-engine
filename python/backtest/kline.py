from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Kline:
    """One OHLCV bar. Internal simulation input, not a cross-language
    wire contract — stays Python-only until Java actually needs klines.
    """

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
