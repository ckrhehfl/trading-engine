"""Shared rolling realized-volatility estimator and volatility-target
position-size scalar -- Strategy Research Task H. See CLAUDE.md's
"Strategy Research Methodology" section and `.planning/sr-h-ensemble-
regime-voltargeting.md` for the deep-research finding this module
implements: real institutional volatility targeting (Robert Carver's
framework; also used by Concretum Group) scales the **overall position
size** inversely to a rolling realized-volatility estimate, so exposure
automatically shrinks in high-volatility/choppy periods and grows in
calm/trending periods -- a portfolio/exposure-level control, genuinely
different from `research.strategies.risk_management`'s ATR-based
stop-loss (which controls how much a *single trade* can lose, independent
of overall position size). This module implements the general,
commonly-cited institutional convention (targeting a fixed annualized
volatility, here 20%) -- **not a reproduction of any single firm's exact
formula**: Concretum's own precise methodology was not accessible during
this task's research pass (Cloudflare-blocked), so this is documented as
"the general convention," not "Concretum's specific implementation."

Two pieces:

- `RollingRealizedVolatility` -- an incremental, look-ahead-safe rolling
  realized-volatility estimator, fed one `Kline` per call via `update()`.
  Sample standard deviation (ddof=1, matching `metrics.metrics`'s own
  Sharpe-ratio convention) of simple per-bar returns over a trailing
  window, annualized via the same `sqrt(bars_per_day * 365)` factor
  `metrics.metrics._sharpe_ratio` uses (365, not 365.25 -- this project's
  fixed annualization convention, not open to per-call judgment).
- `compute_vol_scalar(realized_annualized_vol, *, target_annualized_vol,
  min_scalar, max_scalar) -> Decimal | None` -- a pure function computing
  `target / realized`, clamped to `[min_scalar, max_scalar]`. This is the
  multiplier a strategy applies to a base (ATR-risk-sized) position
  quantity to scale overall exposure -- kept as a **separate, composable**
  step from `risk_management.compute_position_size`, not folded into it,
  per this task's explicit brief: ATR sizing answers "how big given this
  trade's stop distance," vol targeting separately answers "how big given
  the market's current overall volatility regime." A strategy applies
  both: `final_quantity = atr_sized_quantity * regime_weight * vol_scalar`.
"""

from collections import deque
from decimal import Decimal

from backtest.kline import Kline

# 20 bars -- a common, simple rolling realized-volatility lookback
# convention (not searched/tuned to this asset). Expressed as "number of
# returns in the window" (period+1 closes are held to produce exactly
# `period` returns).
DEFAULT_VOL_LOOKBACK_PERIOD = 20

# 365, not 365.25 -- this project's fixed annualization convention (see
# `metrics/metrics.py`'s `_DAYS_PER_YEAR` and CLAUDE.md's "Strategy
# Research Operational Design"), not open to per-implementation judgment.
_DAYS_PER_YEAR = 365

# 20% annualized -- Concretum's own reported target, reused here as a
# reasonable, commonly-cited institutional convention (see module
# docstring for why this is "the general convention," not a literal
# reproduction of Concretum's exact, inaccessible-during-research formula).
DEFAULT_TARGET_ANNUALIZED_VOL = Decimal("0.20")

# Position-size scalar bounds. min_scalar=0 lets exposure shrink all the
# way to "skip this trade" in an extremely volatile/choppy market (never
# negative -- a negative scalar would silently flip a trade's direction,
# which is not what volatility targeting means). max_scalar=3 is a
# deliberate, documented safety cap on the OTHER end: in an anomalously
# calm market, target/realized could otherwise blow up toward an
# unboundedly large multiplier (approaching a divide-by-near-zero); 3x is
# a round, conservative ceiling, not searched/tuned to this asset --
# revisit if a real run shows this cap binding frequently enough to
# matter.
DEFAULT_MIN_VOL_SCALAR = Decimal("0")
DEFAULT_MAX_VOL_SCALAR = Decimal("3")


class RollingRealizedVolatility:
    """Incremental, look-ahead-safe rolling realized-volatility estimator
    over a bar-by-bar stream of closes, fed one `Kline` per call via
    `update()`.

    Maintains a `deque(maxlen=period + 1)` of recent closes (enough to
    derive exactly `period` trailing simple returns). Once the window is
    full, returns the **sample** standard deviation (ddof=1 -- divides by
    `period - 1`, matching `metrics.metrics._sharpe_ratio`'s own
    `statistics.stdev` convention) of those `period` returns, annualized
    via `sqrt(bars_per_day * _DAYS_PER_YEAR)`. Returns `None` before the
    window fills (warmup) -- same "no evidence yet" convention as every
    other rolling-window warmup in this codebase.

    **A zero-variance reading is a valid `Decimal(0)` result, never
    `None`** -- unlike `metrics.metrics._sharpe_ratio` (where zero-variance
    returns yield `None` because variance is used as *that* function's own
    division denominator), this estimator's own arithmetic never divides
    by the value it's computing. A perfectly flat price series has a
    genuine, meaningful volatility reading of exactly zero. The
    divide-by-zero risk this could create *downstream* (a zero realized
    vol as `compute_vol_scalar`'s denominator) is handled there, via
    capping -- see that function's docstring.

    All arithmetic is `Decimal` throughout, including the final
    `Decimal.sqrt()` annualization step -- no float round-trip, matching
    this codebase's `risk_management.py`/`data/store.py` precision
    convention.

    Look-ahead safety: `update(kline)` only ever reads `kline` and
    internal state accumulated from bars already fed -- same guarantee as
    `risk_management.AverageTrueRange`/`regime_weighting.
    AverageDirectionalIndex`.
    """

    __slots__ = ("_period", "_bars_per_day", "_closes")

    def __init__(self, period: int = DEFAULT_VOL_LOOKBACK_PERIOD, bars_per_day: int = 24) -> None:
        if period < 2:
            raise ValueError(f"period must be at least 2 (need >=2 returns for a sample stdev), got {period}")
        if bars_per_day <= 0:
            raise ValueError(f"bars_per_day must be positive, got {bars_per_day}")
        self._period = period
        self._bars_per_day = bars_per_day
        self._closes: deque[Decimal] = deque(maxlen=period + 1)

    @property
    def period(self) -> int:
        return self._period

    def update(self, kline: Kline) -> Decimal | None:
        self._closes.append(kline.close)
        if len(self._closes) < self._period + 1:
            return None

        closes = list(self._closes)
        returns: list[Decimal] = []
        # Deliberately NOT strict=True: `closes` and `closes[1:]` are
        # meant to have different lengths -- this is the standard
        # consecutive-pairs-via-offset-zip idiom (zip stops at the
        # shorter sequence, which is exactly `len(closes) - 1` pairs,
        # by construction), not an accidental length mismatch.
        for previous, current in zip(closes, closes[1:]):
            if previous == 0:
                # Degenerate (never expected for real BTC prices) -- skip
                # rather than divide by zero, same defensive posture as
                # metrics.metrics._sharpe_ratio's identical guard.
                continue
            returns.append((current - previous) / previous)

        if len(returns) < 2:
            return None

        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
        stdev_return = variance.sqrt()
        annualization_factor = Decimal(self._bars_per_day * _DAYS_PER_YEAR).sqrt()
        return stdev_return * annualization_factor


def compute_vol_scalar(
    realized_annualized_vol: Decimal | None,
    *,
    target_annualized_vol: Decimal = DEFAULT_TARGET_ANNUALIZED_VOL,
    min_scalar: Decimal = DEFAULT_MIN_VOL_SCALAR,
    max_scalar: Decimal = DEFAULT_MAX_VOL_SCALAR,
) -> Decimal | None:
    """`scalar = target_annualized_vol / realized_annualized_vol`, clamped
    to `[min_scalar, max_scalar]` -- the multiplier a strategy applies to
    a base (ATR-risk-sized) position quantity to scale overall exposure
    for the current volatility regime. See module docstring for how this
    composes with `risk_management.compute_position_size`.

    `realized_annualized_vol=None` (warmup -- `RollingRealizedVolatility.
    update`'s contract before its window fills) returns `None`, **not**
    a baked-in default scalar. This is a deliberate contrast with
    `regime_weighting.compute_regime_weight`'s `None`-in -> `Decimal(0)`-out
    convention: ADX has a natural "off" value at the bottom of its own
    scale (zero conviction is a sensible reading of "no trend-strength
    evidence yet"), but there is no equally natural single scalar value
    for "no volatility estimate yet" -- defaulting to `1` (unscaled) would
    silently claim "average volatility" with zero actual evidence, and
    defaulting to `0` would conflate "no reading yet" with "extremely
    volatile, skip entirely" (a very different, false claim). Forcing
    `None` through means the caller must decide explicitly; this
    project's strategies (`single_lookback_momentum.py`,
    `ensemble_momentum.py`) choose to skip trading entirely until this
    estimator's own warmup completes -- the same "no evidence = no trade"
    convention used throughout this codebase (e.g.
    `risk_management.AverageTrueRange`'s `None`-during-warmup gating an
    entry decision the same way).

    `realized_annualized_vol <= 0` (a valid, if degenerate, `Decimal(0)`
    reading from `RollingRealizedVolatility` for a perfectly flat price
    series -- see that class's docstring) returns `max_scalar` directly,
    not a `ZeroDivisionError` and not an unboundedly large value: an
    anomalously calm market is exactly the scenario `max_scalar` exists to
    cap, so a literal zero-vol reading is just the most extreme case of
    that same capping, not a special crash path.

    **Deliberately scales UP (`max_scalar`), never down (`min_scalar`),
    for a low/zero realized-vol reading -- this is the textbook-correct
    direction for volatility targeting, not a conservative-vs-aggressive
    judgment call to reconsider.** The entire premise of volatility
    targeting (see module docstring) is that a genuinely calm market
    should get *more* exposure, not less -- capping the resulting scalar
    at a safe multiple (`max_scalar`, not `min_scalar` or `None`) is what
    keeps that premise from producing unbounded exposure, without
    inverting it into "when calm, trade smaller" (which would defeat the
    entire point of the technique). This module trusts its `Kline` inputs
    the same way every other incremental calculator in this codebase does
    (`risk_management.AverageTrueRange`, `regime_weighting.
    AverageDirectionalIndex`) -- a stale/duplicated live data feed
    producing a false-flat reading is a live-market-data-quality concern
    entirely out of scope for a pure, backtest-oriented calculator (and
    orthogonal to volatility targeting specifically: the same class of
    concern would affect ATR/ADX identically). Python never places live
    orders (CLAUDE.md's Non-negotiable Rules) -- any live equivalent of
    this pipeline would need its own live-data-quality validation ahead
    of the Java Risk Gateway, nowhere near this research-plane code path.

    Raises `ValueError` for a non-positive `target_annualized_vol`, a
    negative `min_scalar`, or `max_scalar < min_scalar` -- each is a
    caller-configuration error with no sensible scalar to compute, fail
    loud at the entry point rather than propagate a nonsensical result.
    """
    if target_annualized_vol <= 0:
        raise ValueError(f"target_annualized_vol must be positive, got {target_annualized_vol}")
    if min_scalar < 0:
        raise ValueError(f"min_scalar must be non-negative, got {min_scalar}")
    if max_scalar < min_scalar:
        raise ValueError(f"max_scalar ({max_scalar}) must be >= min_scalar ({min_scalar})")

    if realized_annualized_vol is None:
        return None
    if realized_annualized_vol <= 0:
        return max_scalar

    scalar = target_annualized_vol / realized_annualized_vol
    if scalar < min_scalar:
        return min_scalar
    if scalar > max_scalar:
        return max_scalar
    return scalar
