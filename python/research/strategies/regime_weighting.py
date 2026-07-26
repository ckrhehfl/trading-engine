"""Shared ADX (Average Directional Index) calculator and continuous
regime-weight ramp -- Strategy Research Task H. See CLAUDE.md's "Strategy
Research Methodology" section and `.planning/sr-h-ensemble-regime-
voltargeting.md` for the deep-research finding this module implements:
regime detection should gate/weight a momentum strategy's position size
*continuously*, not as a hard binary on/off switch -- multiple credibility-
graded sources converge on continuous/probabilistic scaling reducing
whipsaw versus an abrupt cutoff. ADX is the concrete technique chosen
(over Hurst exponent or an HMM regime model) because it's a standard,
well-understood technical indicator, cheap to compute incrementally, and
because a peer-reviewed source found HMM regime models showing real
in-sample predictive power that degraded substantially out-of-sample -- a
caution against relying on heavier machinery at this project's current
stage.

Two pieces:

- `AverageDirectionalIndex` -- an incremental, look-ahead-safe ADX
  calculator, fed one `Kline` per call via `update()`. Same incremental-
  state shape as `research.strategies.risk_management.AverageTrueRange`
  (only ever reads the current bar plus state already accumulated from
  bars already fed).
- `compute_regime_weight(adx, low, high) -> Decimal` -- a pure function
  mapping an ADX reading to a `[0, 1]` conviction weight via a linear
  ramp between `low` and `high` (0 at/below `low`, 1 at/above `high`,
  linearly interpolated between) -- the continuous alternative to a hard
  `adx > 25` cutoff. Conventional ADX interpretation (`>25` trending,
  `<20` ranging/choppy) is used as the default `low`/`high`, not
  empirically tuned to this asset.

**Deliberate simplification, stated explicitly (same precedent as
`risk_management.AverageTrueRange`'s own docstring)**: `AverageDirectionalIndex`
uses a **plain rolling mean** for its True Range / directional-movement /
DX smoothing steps, not Wilder's original exponential (recursive)
smoothing formula that "ADX" conventionally refers to in most technical-
analysis references. CLAUDE.md's brief for this task asks for "a standard,
look-ahead-safe rolling technical indicator," not specifically Wilder's
exact recursive smoothing -- and `risk_management.py` already established
this exact precedent for `AverageTrueRange` in this codebase, for the same
reasons: a plain rolling mean is simpler to verify by hand in a test, and
the choice between the two smoothing conventions is not itself a claim of
edge. The resulting values will differ numerically from a "textbook"
Wilder-smoothed ADX reading of the same underlying data, but the
qualitative behavior (low during choppy/ranging periods, high during
strongly trending periods) is the same, which is what this module's
continuous-weighting use actually depends on.
"""

from collections import deque
from decimal import Decimal

from backtest.kline import Kline

# 14 -- the conventional ADX period (same convention Wilder used, and the
# same value `risk_management.DEFAULT_ATR_PERIOD` already uses in this
# codebase for the closely related ATR calculation), not searched/tuned
# to this asset.
DEFAULT_ADX_PERIOD = 14

# Conventional ADX interpretation: below 20 is "ranging/choppy", above 25
# is "trending" -- CLAUDE.md's explicit brief for this task names these
# exact numbers. Not searched/tuned to this asset.
DEFAULT_ADX_LOW_THRESHOLD = Decimal("20")
DEFAULT_ADX_HIGH_THRESHOLD = Decimal("25")


class AverageDirectionalIndex:
    """Incremental, look-ahead-safe Average Directional Index over a
    bar-by-bar stream, fed one `Kline` per call via `update()`.

    Standard directional-movement definitions (unambiguous, not a
    simplification -- only the *smoothing* step below deviates from
    Wilder's original convention, per this module's docstring):

        up_move = high[t] - high[t-1]
        down_move = low[t-1] - low[t]
        +DM[t] = up_move   if up_move > down_move and up_move > 0   else 0
        -DM[t] = down_move if down_move > up_move and down_move > 0 else 0
        TR[t] = max(high[t]-low[t], |high[t]-close[t-1]|, |low[t]-close[t-1]|)

    Smoothing (the deliberate simplification -- plain rolling mean, not
    Wilder's exponential recursion, see module docstring): `+DM`, `-DM`,
    and `TR` are each accumulated over a trailing `period`-length window
    (a `deque(maxlen=period)`). Once that window is full:

        +DI = 100 * sum(+DM window) / sum(TR window)
        -DI = 100 * sum(-DM window) / sum(TR window)
        DX  = 100 * |+DI - -DI| / (+DI + -DI)   (0 if +DI + -DI == 0)

    `DX` values are themselves accumulated over a second trailing
    `period`-length window; once *that* window is also full, `update()`
    returns `ADX = mean(DX window)` -- a plain rolling mean of DX, same
    simplification. Returns `None` before either window is full (warmup),
    and for the very first bar ever fed (no prior high/low/close exists
    to compute True Range / directional movement against) -- same "no
    evidence yet" convention as every other rolling-window warmup in this
    codebase.

    Degenerate case: if a window's `sum(TR)` is exactly 0 (every bar in
    the window was perfectly flat, `high == low == close` throughout),
    `+DI`/`-DI` would divide by zero -- handled by treating `DX` as `0`
    directly for that bar (a perfectly flat market has no directional
    movement to measure, which is exactly what `ADX == 0` means; not a
    crash, not a `None`).

    Look-ahead safety: `update(kline)` only ever reads `kline` (the
    current bar) and internal state accumulated from bars already fed --
    same guarantee as `risk_management.AverageTrueRange.update`.
    """

    __slots__ = (
        "_period",
        "_prev_high",
        "_prev_low",
        "_prev_close",
        "_true_ranges",
        "_plus_dms",
        "_minus_dms",
        "_dx_values",
    )

    def __init__(self, period: int = DEFAULT_ADX_PERIOD) -> None:
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self._period = period
        self._prev_high: Decimal | None = None
        self._prev_low: Decimal | None = None
        self._prev_close: Decimal | None = None
        self._true_ranges: deque[Decimal] = deque(maxlen=period)
        self._plus_dms: deque[Decimal] = deque(maxlen=period)
        self._minus_dms: deque[Decimal] = deque(maxlen=period)
        self._dx_values: deque[Decimal] = deque(maxlen=period)

    @property
    def period(self) -> int:
        return self._period

    def update(self, kline: Kline) -> Decimal | None:
        if self._prev_high is None:
            # First bar ever -- no prior high/low/close to compute
            # directional movement / True Range against.
            self._prev_high = kline.high
            self._prev_low = kline.low
            self._prev_close = kline.close
            return None

        up_move = kline.high - self._prev_high
        down_move = self._prev_low - kline.low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else Decimal(0)
        minus_dm = down_move if (down_move > up_move and down_move > 0) else Decimal(0)
        true_range = max(
            kline.high - kline.low,
            abs(kline.high - self._prev_close),
            abs(kline.low - self._prev_close),
        )

        self._prev_high = kline.high
        self._prev_low = kline.low
        self._prev_close = kline.close

        self._true_ranges.append(true_range)
        self._plus_dms.append(plus_dm)
        self._minus_dms.append(minus_dm)
        if len(self._true_ranges) < self._period:
            return None

        tr_sum = sum(self._true_ranges)
        if tr_sum == 0:
            dx = Decimal(0)
        else:
            plus_di = 100 * sum(self._plus_dms) / tr_sum
            minus_di = 100 * sum(self._minus_dms) / tr_sum
            di_sum = plus_di + minus_di
            dx = Decimal(0) if di_sum == 0 else 100 * abs(plus_di - minus_di) / di_sum

        self._dx_values.append(dx)
        if len(self._dx_values) < self._period:
            return None

        return sum(self._dx_values) / self._period


def compute_regime_weight(
    adx: Decimal | None,
    low: Decimal = DEFAULT_ADX_LOW_THRESHOLD,
    high: Decimal = DEFAULT_ADX_HIGH_THRESHOLD,
) -> Decimal:
    """Map an ADX reading to a `[0, 1]` conviction weight via a linear
    ramp: `0` at/below `low`, `1` at/above `high`, linearly interpolated
    for any value strictly between them. This is the continuous
    alternative to a hard `adx > threshold` on/off gate -- see this
    module's docstring for the research finding it implements.

    `adx=None` (warmup -- `AverageDirectionalIndex.update`'s contract
    before its window fills) maps to `Decimal(0)` directly: "no evidence
    of trend strength yet" is naturally, sensibly `0` conviction on this
    continuous scale, so it's baked into this function's contract rather
    than pushed onto every call site. Contrast with `volatility_targeting.
    compute_vol_scalar`'s `None`-in/`None`-out contract, which deliberately
    does NOT bake in a default -- see that function's docstring for why
    the two differ.

    Raises `ValueError` if `low >= high` -- a degenerate or inverted
    threshold pair has no sensible ramp to compute.
    """
    if low >= high:
        raise ValueError(f"low ({low}) must be strictly less than high ({high})")
    if adx is None:
        return Decimal(0)
    if adx <= low:
        return Decimal(0)
    if adx >= high:
        return Decimal(1)
    return (adx - low) / (high - low)
