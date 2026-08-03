"""Look-ahead-safe alignment of FRED daily macro observations onto BTC's
own daily kline bars -- Strategy Research Task X (the first macro-
conditioned strategy; see `.planning/sr-x-macro-real-yield-strategy.md`
and CLAUDE.md's "Strategy Research Methodology").

## Why this needs its own module, not an inline lookup in the strategy

`sr-w` (`.planning/sr-w-macro-data-pipeline.md`) built the FRED data path
but deliberately stopped short of designing any macro-conditioned signal
-- so nothing in this codebase previously had to answer the question this
module exists to answer: given one BTC daily bar, what value does a
same-day-or-earlier FRED observation series actually show?

The naive answer -- "look up today's date in the macro table" -- is
wrong twice over, both confirmed empirically in `sr-w`:

1. **FRED has no row at all for a weekend**, and BTC trades every
   calendar day. A BTC Saturday bar has no same-day macro observation to
   look up, ever.
2. **A real US market holiday that falls on a weekday still gets a row
   from FRED, but with a `None` value** (`fred_client.py`'s own
   documented `"."` marker) -- also nothing directly usable.

The correct rule, and the one this module implements: **forward-fill**
the most recent REAL (non-`None`) macro observation dated on or before
the BTC bar's own calendar date. Never a value dated after the BTC bar
-- that would be feeding the strategy a macro reading it could not
actually have known at the time, the exact look-ahead-bias failure mode
CLAUDE.md's Strategy Research Methodology names explicitly for feature
engineering generally ("no feature may be computed using statistics ...
derived from data outside what would actually have been available at
that point in time"), applied here to a genuinely new cross-series
alignment surface that had no prior structural protection the way
`KlineWindow` (single-series look-ahead safety) and `research.holdout`
(train/holdout split safety) already give BTC's own OHLCV data.

## Two entry points, one algorithm

- `MacroSeriesCursor` -- an incremental, online forward-fill cursor, fed
  one `Kline` at a time via `.update()`. This is the shape a bar-by-bar
  `backtest.engine.Strategy.__call__` actually needs (mirrors
  `research.strategies.volatility_targeting.RollingRealizedVolatility
  .update`'s established "one bar per call" convention), and it is what
  `research.strategies.macro_real_yield_trend` actually uses.
- `forward_fill_macro_series` -- a batch convenience wrapper (literally
  `[cursor.update(k) for k in klines]`) for tests and any offline/
  exploratory use that wants the whole aligned series as a plain list in
  one call.

Both are look-ahead-safe by construction, not merely by convention: the
cursor's internal pointer only ever advances past an `observation_date`
that is `<=` the *current* kline's own calendar date, and it is
monotonic (it never rewinds, never re-reads an already-consumed
observation). A kline can therefore never see an observation dated after
itself, regardless of call order or how many observations the cursor was
constructed with -- even when (the realistic, intended usage) the cursor
is built from a macro series' FULL history, including dates far beyond
whatever kline sequence it ends up being fed.

## Not a generic date-alignment library

This module aligns one FRED `ObservationRow` series onto one `Kline`
sequence, using each side's own date field (`Kline.open_time`'s UTC
calendar date; `ObservationRow.observation_date`, already a plain
`YYYY-MM-DD` string -- see `fred_client.py`'s module docstring for why
FRED's own date unit is preserved as-is rather than translated to an
epoch timestamp). It does not attempt to be a generic time-series-
alignment utility: it assumes (does not defend against) `observations`
describing a real, non-adversarial FRED-shaped series, and it assumes --
matching `backtest.engine.run_backtest`'s own calling convention exactly
-- that a `MacroSeriesCursor` instance's `.update()` is called with
klines in non-decreasing date order, which is the order
`run_backtest`'s bar loop already guarantees structurally (it calls a
`Strategy` once per bar, `i` from `0` to `len(klines) - 1`, on
`KlineWindow(klines, i + 1)`).
"""

from decimal import Decimal
from typing import Sequence

from backtest.kline import Kline
from data.fred_client import ObservationRow


class MacroSeriesCursor:
    """Incremental, look-ahead-safe forward-fill cursor over a macro
    observation series. See module docstring.

    `observations` is sorted defensively by `observation_date` at
    construction time -- `data.store.fetch_macro_observations` already
    returns rows ascending, but this class does not assume every caller
    goes through that path (e.g. a synthetic test fixture, or a future
    caller assembling `ObservationRow`s from more than one source), and
    getting the order wrong here would silently corrupt every reading
    from that point on rather than raising anything.
    """

    __slots__ = ("_observations", "_index", "_last_value")

    def __init__(self, observations: Sequence[ObservationRow]) -> None:
        self._observations: list[ObservationRow] = sorted(observations, key=lambda row: row.observation_date)
        self._index = 0
        self._last_value: Decimal | None = None

    def update(self, kline: Kline) -> Decimal | None:
        """Advance the cursor through every observation dated on or
        before `kline`'s own UTC calendar date, and return the most
        recent REAL (non-`None`) value seen so far -- `None` if no real
        observation has ever been seen yet (e.g. `kline` predates the
        macro series' own real start, or every observation seen so far
        is a null/holiday marker).

        Look-ahead safety: the internal pointer only ever consumes an
        observation whose `observation_date <= kline_date`, and it never
        looks backward or re-reads an already-consumed observation -- so
        a kline can never see a macro observation dated after itself, no
        matter how many times `update()` has already been called or how
        far into the future the underlying `observations` series
        actually extends.
        """
        kline_date = kline.open_time.date().isoformat()
        observations = self._observations
        n = len(observations)
        while self._index < n and observations[self._index].observation_date <= kline_date:
            value = observations[self._index].value
            if value is not None:
                self._last_value = value
            self._index += 1
        return self._last_value


def forward_fill_macro_series(klines: Sequence[Kline], observations: Sequence[ObservationRow]) -> list[Decimal | None]:
    """Batch convenience wrapper around `MacroSeriesCursor`: the
    forward-filled macro value aligned to each of `klines`, in order,
    as a plain list the same length as `klines`.

    `klines` must be in non-decreasing date order -- matches every kline
    sequence this project ever produces (`data.store.fetch_klines`'s own
    `ORDER BY open_time_ms ASC`) and `backtest.engine.run_backtest`'s own
    calling convention (see module docstring).
    """
    cursor = MacroSeriesCursor(observations)
    return [cursor.update(kline) for kline in klines]
