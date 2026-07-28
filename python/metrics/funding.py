"""Funding-rate row type for the metrics layer -- datetime-keyed,
`metrics/`'s internal format. Mirrors `backtest.kline.Kline`'s
relationship to `data.bingx_klines.KlineRow` (ms-timestamp-keyed wire/
storage format -> datetime-keyed internal format used by the
backtest/metrics layers); see `research/holdout.py`'s
`_kline_row_to_kline` for this codebase's established conversion-
function precedent, which a caller wiring real funding data into
`metrics.position.PositionTracker`/`metrics.metrics.compute_metrics`
should follow the same way for `data.bingx_funding.FundingRow` ->
`FundingRate`.

Deliberately its own module rather than folded into `backtest.kline`
(a different package -- `metrics/` doesn't otherwise depend on
`backtest/` for data types, only for `Fill`/`Kline` *values* passed in
by callers) or inlined into `metrics.position` (kept small and reusable
by both `metrics.position` and `metrics.metrics`, same reasoning as
`backtest/kline.py` being its own file).

Not a cross-language wire contract -- stays Python-only, same as
`backtest.kline.Kline`.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class FundingRate:
    """One funding settlement. `mark_price` is BingX's own historical
    mark price *at that exact settlement* (returned alongside
    `fundingRate` by the funding-rate history endpoint) -- using it,
    rather than a position's entry price or a kline's close, is what
    makes the funding P&L attribution in `metrics.position` match how a
    real exchange actually computes the payment (see that module's
    docstring and `.planning/sr-m-funding-rate-pipeline.md`).
    """

    funding_time: datetime
    funding_rate: Decimal
    mark_price: Decimal
