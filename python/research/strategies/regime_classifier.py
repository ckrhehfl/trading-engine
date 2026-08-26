"""Two-axis market regime classification -- structure x volatility.

Built for Scalping Strategy Research Task S8 step 2 (see
`.planning/scalp-s8-research-methodology.md` §3.3). **Not a strategy.**
It emits no signal, takes no direction, and sizes nothing. It answers one
question -- *what kind of market is this right now* -- so that a strategy
built on top can avoid the failure S8 named explicitly: running a
mean-reversion rule into an emerging trend, which the regime literature
identifies as one of the most reliable ways to lose a lot quickly, and
which is exactly what `vwap_mid_reversion` did.

**Why two axes and not one.** "Trending vs ranging" alone is incomplete:
a quiet uptrend and a volatile news-driven rally are different
environments that call for different behaviour, and collapsing them
loses that. So the label is a pair:

    structure   TRENDING | RANGING          (from ADX)
    volatility  EXPANSION | COMPRESSION     (from an ATR ratio)

**Everything here is reused, not invented.** The ADX implementation and
its 14-period/20/25 thresholds come from `regime_weighting`; the ATR
implementation and its 14 period come from `risk_management`. Both were
already in this codebase, already tested, and already documented as
conventional values not searched or tuned to this asset. The one genuinely
new number is `DEFAULT_ATR_RATIO_WINDOW = 20`, the lookback the current
ATR is compared against -- a conventional round number, named as such by
the regime literature S8 cites ("current ATR to its 20-period average"),
and not fitted here to anything.

**Hysteresis without inventing thresholds.** S8 requires two thresholds
per boundary so the label cannot flicker at a single crossing point. That
falls out of the existing constants rather than needing new ones: ADX
above 25 means trending, below 20 means ranging, and **the 20-25 band
holds whatever the previous state was**. The volatility axis works
identically -- above 1.5 expansion, below 0.8 compression, and the band
between holds. This is the same shape `compute_regime_weight` already
uses those constants for, applied to a discrete label instead of a
continuous weight.

**Minimum dwell time, derived rather than picked.** S8 also requires a
minimum dwell so a label cannot change on consecutive bars. The default
is the ADX period itself: a label derived from a 14-bar indicator cannot
meaningfully re-decide faster than its own lookback, so 14 is a
principled floor rather than a tuned one. Dwell is applied per axis --
each axis has been in its own current state for its own number of bars.

**Look-ahead safety** is inherited: `update(kline)` reads only the
current bar and state accumulated from bars already fed, the same
guarantee `AverageTrueRange.update` and `AverageDirectionalIndex.update`
document for themselves. `classify` returns `None` throughout warmup --
the "no evidence yet" convention used everywhere else in this codebase --
and warmup is genuinely long here, because the ATR ratio needs `atr_period`
bars to produce a first ATR and then `atr_ratio_window` more ATR readings
to have something to compare against.
"""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from backtest.kline import Kline
from research.strategies.regime_weighting import (
    DEFAULT_ADX_HIGH_THRESHOLD,
    DEFAULT_ADX_LOW_THRESHOLD,
    DEFAULT_ADX_PERIOD,
    AverageDirectionalIndex,
)
from research.strategies.risk_management import DEFAULT_ATR_PERIOD, AverageTrueRange

# The ATR ratio's comparison lookback -- current ATR against the mean of
# the trailing this-many ATR readings. 20 is the conventional round
# number the regime literature names ("current ATR vs its 20-period
# average"); not searched or tuned here. See module docstring.
DEFAULT_ATR_RATIO_WINDOW = 20

# Conventional ATR-ratio interpretation, same source: at or above 1.5x its
# own recent average is a volatility expansion, at or below 0.8x is a
# compression. The band between them is the hysteresis zone -- exactly how
# DEFAULT_ADX_LOW/HIGH_THRESHOLD already behave for the structure axis.
DEFAULT_ATR_RATIO_HIGH = Decimal("1.5")
DEFAULT_ATR_RATIO_LOW = Decimal("0.8")


class Structure(Enum):
    TRENDING = "trending"
    RANGING = "ranging"


class Volatility(Enum):
    EXPANSION = "expansion"
    COMPRESSION = "compression"


@dataclass(frozen=True)
class Regime:
    """One classified bar. `structure_bars`/`volatility_bars` count how
    many bars each axis has held its current value, which is what the
    dwell rule is enforced against and what a caller needs to tell a
    freshly-flipped regime from a long-established one.
    """

    structure: Structure
    volatility: Volatility
    structure_bars: int
    volatility_bars: int


class AtrRatio:
    """Current ATR divided by the mean of the trailing `window` ATR
    readings -- a scale-free measure of whether volatility is expanding or
    compressing *relative to its own recent past*, which is what makes it
    comparable across price levels and across assets.

    Returns `None` until the underlying `AverageTrueRange` has warmed up
    **and** `window` ATR readings have accumulated after that. The current
    reading is deliberately excluded from its own denominator: comparing a
    value against a mean that includes it damps exactly the signal being
    measured.

    A zero mean (every ATR in the window exactly zero -- a perfectly flat
    stretch) returns `None` rather than dividing by zero. That is "no
    evidence", not "no expansion", and matches how
    `AverageDirectionalIndex` handles its own degenerate flat-window case.
    """

    __slots__ = ("_atr", "_history", "_window")

    def __init__(
        self,
        atr_period: int = DEFAULT_ATR_PERIOD,
        window: int = DEFAULT_ATR_RATIO_WINDOW,
    ) -> None:
        if window <= 0:
            raise ValueError(f"window must be positive, got {window}")
        self._atr = AverageTrueRange(period=atr_period)
        self._history: deque[Decimal] = deque(maxlen=window)
        self._window = window

    @property
    def window(self) -> int:
        return self._window

    def update(self, kline: Kline) -> Decimal | None:
        atr = self._atr.update(kline)
        if atr is None:
            return None
        # Compare against the trailing window as it stood BEFORE this
        # reading, then fold this reading in -- see class docstring.
        ratio: Decimal | None = None
        if len(self._history) == self._window:
            mean = sum(self._history) / self._window
            ratio = atr / mean if mean != 0 else None
        self._history.append(atr)
        return ratio


class RegimeClassifier:
    """Feeds each bar to both axes and applies hysteresis plus a minimum
    dwell time before allowing either label to change.

    `update(kline)` returns the `Regime` for that bar, or `None` while
    either axis is still warming up. Once both have warmed up, a `Regime`
    is always returned: the classifier has no "unknown" state after
    warmup, because an indicator sitting inside its hysteresis band is not
    undecided -- it is holding its previous decision, which is the whole
    point of the band.

    The initial label for each axis is taken from the first reading that
    resolves outside its hysteresis band. A reading that warms up *inside*
    the band has no previous state to hold, so the classifier keeps
    returning `None` until one axis-defining reading arrives. This is
    deliberate: seeding an arbitrary initial state would put a fabricated
    label on real bars.
    """

    __slots__ = (
        "_adx",
        "_adx_high",
        "_adx_low",
        "_atr_ratio",
        "_min_dwell_bars",
        "_ratio_high",
        "_ratio_low",
        "_structure",
        "_structure_bars",
        "_volatility",
        "_volatility_bars",
    )

    def __init__(
        self,
        adx_period: int = DEFAULT_ADX_PERIOD,
        atr_period: int = DEFAULT_ATR_PERIOD,
        atr_ratio_window: int = DEFAULT_ATR_RATIO_WINDOW,
        adx_low: Decimal = DEFAULT_ADX_LOW_THRESHOLD,
        adx_high: Decimal = DEFAULT_ADX_HIGH_THRESHOLD,
        ratio_low: Decimal = DEFAULT_ATR_RATIO_LOW,
        ratio_high: Decimal = DEFAULT_ATR_RATIO_HIGH,
        min_dwell_bars: int | None = None,
    ) -> None:
        if adx_low > adx_high:
            raise ValueError(f"adx_low ({adx_low}) must not exceed adx_high ({adx_high})")
        if ratio_low > ratio_high:
            raise ValueError(f"ratio_low ({ratio_low}) must not exceed ratio_high ({ratio_high})")
        if min_dwell_bars is not None and min_dwell_bars < 0:
            raise ValueError(f"min_dwell_bars must not be negative, got {min_dwell_bars}")

        self._adx = AverageDirectionalIndex(period=adx_period)
        self._atr_ratio = AtrRatio(atr_period=atr_period, window=atr_ratio_window)
        self._adx_low = adx_low
        self._adx_high = adx_high
        self._ratio_low = ratio_low
        self._ratio_high = ratio_high
        # Default derived from the ADX lookback, not picked -- see module
        # docstring.
        self._min_dwell_bars = adx_period if min_dwell_bars is None else min_dwell_bars

        self._structure: Structure | None = None
        self._volatility: Volatility | None = None
        self._structure_bars = 0
        self._volatility_bars = 0

    @property
    def min_dwell_bars(self) -> int:
        return self._min_dwell_bars

    def _resolve(self, reading, low, high, below, above, current, held_bars):
        """Hysteresis + dwell for one axis.

        Returns `(state, bars_held)`. A reading inside the band holds the
        current state. A reading outside it changes state only if the
        current state has been held for at least `min_dwell_bars`; while
        the dwell is unmet the outside-the-band reading is ignored, which
        is the point -- a rapid re-crossing must not be able to flip the
        label.
        """
        if reading is None:
            return current, held_bars
        proposed = below if reading <= low else (above if reading >= high else current)
        if proposed is None:
            # Warmed up inside the band with no prior state to hold.
            return None, 0
        if current is None:
            return proposed, 1
        if proposed is not current and held_bars < self._min_dwell_bars:
            return current, held_bars + 1
        if proposed is not current:
            return proposed, 1
        return current, held_bars + 1

    def update(self, kline: Kline) -> Regime | None:
        adx = self._adx.update(kline)
        ratio = self._atr_ratio.update(kline)

        self._structure, self._structure_bars = self._resolve(
            adx,
            self._adx_low,
            self._adx_high,
            Structure.RANGING,
            Structure.TRENDING,
            self._structure,
            self._structure_bars,
        )
        self._volatility, self._volatility_bars = self._resolve(
            ratio,
            self._ratio_low,
            self._ratio_high,
            Volatility.COMPRESSION,
            Volatility.EXPANSION,
            self._volatility,
            self._volatility_bars,
        )

        if self._structure is None or self._volatility is None:
            return None
        return Regime(
            structure=self._structure,
            volatility=self._volatility,
            structure_bars=self._structure_bars,
            volatility_bars=self._volatility_bars,
        )
