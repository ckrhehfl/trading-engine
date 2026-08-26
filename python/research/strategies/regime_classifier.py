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
    volatility  EXPANSION | COMPRESSION     (from absolute ATR)

**The volatility axis was measured, and the first version of it was
wrong.** It originally used an ATR *ratio* -- current ATR over its own
trailing mean -- which Task S10 measured as separating forward movement
only 1.22x (top 1% vs all bars) on 3.6M real bars. Swapping to
**absolute** ATR as a fraction of price, changing nothing else, gives
**5.21x**, matching the independent rolling-sum-of-|returns| measure's
5.25x. A ratio to a recent mean throws away exactly the absolute level a
cost-versus-move decision depends on: a market that doubles its
volatility and stays there reads 1.0. `VolatilityAxis.ABSOLUTE` is the
default; `RATIO` is kept only so the negative result stays reproducible.
See `.planning/scalp-s10-regime-classifier.md`.

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
identically -- above the 90th percentile of its own trailing history is
expansion, below the 25th is compression, and the band between holds.
This is the same shape `compute_regime_weight` already uses the ADX
constants for, applied to a discrete label instead of a continuous
weight.

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
and warmup is genuinely long here, because the volatility axis needs
`atr_period` bars to produce a first ATR and then a full trailing
history of ATR readings to rank against.
"""

from bisect import bisect_left
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

# AbsoluteAtr's trailing reference window, and how often it is re-sorted.
# 1440 = one day of 1-minute bars: long enough that a rank means something
# beyond the last few minutes, short enough to track a genuinely shifting
# volatility level. Refreshing the sorted snapshot every 60 bars keeps the
# cost negligible over millions of bars; see AbsoluteAtr on why a stale
# snapshot is safe in the only direction that matters.
DEFAULT_ABSOLUTE_HISTORY = 1440
DEFAULT_ABSOLUTE_REFRESH = 60

# Percentile-rank thresholds for the ABSOLUTE axis. Above the 90th
# percentile of its own trailing history is high volatility, below the
# 25th is low, and the band between holds the previous label -- the same
# hysteresis shape the ADX 20/25 band already provides for structure.
DEFAULT_ABSOLUTE_HIGH = Decimal("0.90")
DEFAULT_ABSOLUTE_LOW = Decimal("0.25")


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

    **Measured and found not to work for regime conditioning. Retained
    only because it is what `VolatilityAxis.RATIO` selects, and that
    option exists so the negative result stays reproducible.** Task S10
    compared three conditioners on 3.6M real bars, holding everything
    else identical: a rolling sum of absolute returns separated forward
    15-minute movement 5.25x (top 1% vs all bars), absolute ATR as a
    fraction of price 5.21x, and *this* ratio only **1.22x**. Same ATR
    input as the absolute measure; the sole difference is dividing by the
    trailing mean instead of by price, and that one operation destroys
    the signal -- because a market that doubles its volatility and stays
    there returns a ratio of 1.0. "Compression" here means *quieter than
    it recently was*, not *quiet*, and a cost-versus-move decision needs
    the latter. Prefer `VolatilityAxis.ABSOLUTE`; see
    `.planning/scalp-s10-regime-classifier.md`.

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


class AbsoluteAtr:
    """ATR as a fraction of price, in bps, ranked against its own trailing
    history -- the volatility measure Task S10 measured as actually
    working (5.21x separation, versus 1.22x for `AtrRatio`).

    The difference from `AtrRatio` is the denominator: dividing ATR by
    *price* keeps the absolute volatility level, where dividing by its own
    trailing mean throws that level away. Dividing by price rather than
    using raw ATR is what makes the number comparable across a market that
    went from $16k to $100k over the window.

    `update()` returns the current reading's **percentile rank within the
    trailing `history` readings**, in `[0, 1]`, or `None` during warmup.
    A rank is used rather than a raw bps figure so a caller can apply
    fixed thresholds (`>= 0.9` = high volatility) that stay meaningful as
    the market's own volatility level drifts over years.

    **This forgets too -- the difference from `AtrRatio` is timescale, not
    kind.** A trailing percentile also decays once its window fills with
    the new level: a volatility step that persists for `history` bars
    eventually reads mid-rank again. The default history of 1440 bars is
    72x the ratio's 20-reading window, so a burst lasting minutes to hours
    stays visible where the ratio has already normalised it away. A
    genuinely permanent regime shift will fade from both, which is correct
    -- "high volatility" is only meaningful relative to some reference.

    **Look-ahead safety, and why the thresholds are recomputed on a
    schedule.** The rank is computed against a snapshot of the trailing
    window, refreshed every `refresh_every` bars. Refreshing on a schedule
    rather than every bar is a cost decision -- sorting a long window on
    every one of millions of bars is prohibitive -- and it is safe in the
    only direction that matters: the snapshot is always built from bars
    strictly older than the one being ranked, so a bar can never influence
    its own rank, and a stale snapshot makes the rank slightly
    out-of-date rather than clairvoyant.
    """

    __slots__ = ("_atr", "_history", "_refresh_every", "_since_refresh", "_sorted")

    def __init__(
        self,
        atr_period: int = DEFAULT_ATR_PERIOD,
        history: int = DEFAULT_ABSOLUTE_HISTORY,
        refresh_every: int = DEFAULT_ABSOLUTE_REFRESH,
    ) -> None:
        if history <= 0:
            raise ValueError(f"history must be positive, got {history}")
        if refresh_every <= 0:
            raise ValueError(f"refresh_every must be positive, got {refresh_every}")
        self._atr = AverageTrueRange(period=atr_period)
        self._history: deque[Decimal] = deque(maxlen=history)
        self._refresh_every = refresh_every
        self._since_refresh = 0
        self._sorted: list[Decimal] = []

    def update(self, kline: Kline) -> Decimal | None:
        atr = self._atr.update(kline)
        if atr is None or kline.close <= 0:
            return None
        reading = atr / kline.close * 10_000

        rank: Decimal | None = None
        if len(self._history) == self._history.maxlen:
            if not self._sorted or self._since_refresh >= self._refresh_every:
                self._sorted = sorted(self._history)
                self._since_refresh = 0
            self._since_refresh += 1
            below = bisect_left(self._sorted, reading)
            rank = Decimal(below) / Decimal(len(self._sorted))

        self._history.append(reading)
        return rank


class VolatilityAxis(Enum):
    """Which volatility measure `RegimeClassifier` uses.

    `ABSOLUTE` is the default and the only one measured to work.
    `RATIO` reproduces the S10 negative result and exists for that
    purpose; it is not a supported way to condition a strategy.
    """

    ABSOLUTE = "absolute"
    RATIO = "ratio"


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
        "_min_dwell_bars",
        "_structure",
        "_structure_bars",
        "_vol",
        "_vol_high",
        "_vol_low",
        "_volatility",
        "_volatility_bars",
    )

    def __init__(
        self,
        adx_period: int = DEFAULT_ADX_PERIOD,
        atr_period: int = DEFAULT_ATR_PERIOD,
        volatility_axis: VolatilityAxis = VolatilityAxis.ABSOLUTE,
        atr_ratio_window: int = DEFAULT_ATR_RATIO_WINDOW,
        absolute_history: int = DEFAULT_ABSOLUTE_HISTORY,
        absolute_refresh: int = DEFAULT_ABSOLUTE_REFRESH,
        adx_low: Decimal = DEFAULT_ADX_LOW_THRESHOLD,
        adx_high: Decimal = DEFAULT_ADX_HIGH_THRESHOLD,
        vol_low: Decimal | None = None,
        vol_high: Decimal | None = None,
        min_dwell_bars: int | None = None,
    ) -> None:
        if adx_low > adx_high:
            raise ValueError(f"adx_low ({adx_low}) must not exceed adx_high ({adx_high})")
        if min_dwell_bars is not None and min_dwell_bars < 0:
            raise ValueError(f"min_dwell_bars must not be negative, got {min_dwell_bars}")

        if volatility_axis is VolatilityAxis.ABSOLUTE:
            self._vol = AbsoluteAtr(
                atr_period=atr_period,
                history=absolute_history,
                refresh_every=absolute_refresh,
            )
            default_low, default_high = DEFAULT_ABSOLUTE_LOW, DEFAULT_ABSOLUTE_HIGH
        else:
            self._vol = AtrRatio(atr_period=atr_period, window=atr_ratio_window)
            default_low, default_high = DEFAULT_ATR_RATIO_LOW, DEFAULT_ATR_RATIO_HIGH
        vol_low = default_low if vol_low is None else vol_low
        vol_high = default_high if vol_high is None else vol_high
        if vol_low > vol_high:
            raise ValueError(f"vol_low ({vol_low}) must not exceed vol_high ({vol_high})")

        self._adx = AverageDirectionalIndex(period=adx_period)
        self._adx_low = adx_low
        self._adx_high = adx_high
        self._vol_low = vol_low
        self._vol_high = vol_high
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
        vol = self._vol.update(kline)

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
            vol,
            self._vol_low,
            self._vol_high,
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
