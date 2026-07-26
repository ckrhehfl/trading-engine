"""Shared ATR-based risk-management primitives: Average True Range,
stop/target level calculation, fixed-fractional position sizing, and
same-bar stop/target trigger detection.

Introduced in Strategy Research Task F (see `.planning/sr-f-risk-
management-and-1h-variant.md`) to add real risk management to
`RegimeMomentumStrategy` (Part 1: `regime_momentum_risk_managed.py`) and
to give the native-1h momentum variant (Part 2: `hourly_momentum.py`) the
*identical* risk-management approach -- a shared module is how that
task's explicit "same risk-management approach... so the two are
reasonably comparable" requirement is actually guaranteed, rather than
reimplementing the same formulas twice with a chance of subtle drift.

None of the constants below were searched or tuned to this asset --
CLAUDE.md's Strategy Research Methodology "few tunable knobs" guidance
(given the thin real BingX data available to this project's walk-forward
folds) is why they're fixed module constants rather than another `fit()`
grid dimension.
"""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from backtest.kline import Kline
from schemas.order_intent import Side

# Wilder's original ATR period -- the conventional default, not searched.
DEFAULT_ATR_PERIOD = 14

# 1.5x ATR gives a stop room to survive ordinary in-trend noise. 1x ATR is
# a common alternative but empirically tends to be tight enough to get hit
# by routine pullbacks well before a real reversal; 1.5x-2x is a more
# commonly cited convention specifically for a trend-following stop (as
# opposed to a tighter mean-reversion one). Picked the lower end of that
# range, not searched/tuned to this asset.
DEFAULT_STOP_MULTIPLIER = Decimal("1.5")

# 2x the stop distance -- a 1:2 risk/reward ratio, a common starting
# convention (risk 1 unit to make 2), not searched/tuned to this asset.
DEFAULT_TARGET_MULTIPLIER = Decimal("3.0")

# Risk 1% of the reference equity per trade -- a common conservative
# starting convention for fixed-fractional sizing, not searched/tuned.
DEFAULT_RISK_FRACTION = Decimal("0.01")

# Fixed reference equity used ONLY to translate a risk fraction into a
# concrete backtest quantity -- see `compute_position_size`'s docstring
# for why this is deliberately not a live running account balance. Same
# value as this project's other research-code starting-equity constants
# (`research.walkforward._DEFAULT_STARTING_EQUITY`,
# `research.strategies.ma_crossover.DEFAULT_STARTING_EQUITY`,
# `research.strategies.regime_momentum.DEFAULT_STARTING_EQUITY`) for
# consistency across dollar-figure-bearing constants in this codebase,
# though this one plays a genuinely different role (a sizing input, not a
# P&L-tracking baseline).
DEFAULT_REFERENCE_EQUITY = Decimal("10000")


class AverageTrueRange:
    """Incremental, look-ahead-safe Average True Range over a bar-by-bar
    stream, fed one `Kline` per call via `update()`.

    True Range for the first-ever bar fed (no prior close observed yet) is
    `high - low`; every subsequent bar's True Range is
    `max(high - low, abs(high - prev_close), abs(low - prev_close))` --
    the standard definition. `update()` returns the simple mean of the
    trailing `period` True Range values once at least `period` bars have
    been fed, else `None` (warmup -- same "no evidence yet" convention as
    every other rolling-window warmup in this codebase, e.g.
    `regime_momentum.RegimeMomentumStrategy`'s `_hourly_closes`).

    Deliberately a simple rolling mean, not Wilder's original exponential
    smoothing: CLAUDE.md's design brief for this asked for "a standard,
    look-ahead-safe rolling volatility measure," not specifically Wilder
    smoothing. A plain rolling mean is simpler to reason about and to
    verify by hand in a test, and the choice between the two smoothing
    conventions is not itself a claim this project is making an edge on.

    Look-ahead safety: `update(kline)` only ever reads `kline` (the
    current bar) and internal state accumulated from bars already fed --
    the same "only sees up to and including now" guarantee `KlineWindow`
    gives the rest of this codebase's bar loop, just expressed as
    incremental state instead of a bounds-checked view.
    """

    __slots__ = ("_period", "_true_ranges", "_prev_close")

    def __init__(self, period: int = DEFAULT_ATR_PERIOD) -> None:
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self._period = period
        self._true_ranges: deque[Decimal] = deque(maxlen=period)
        self._prev_close: Decimal | None = None

    @property
    def period(self) -> int:
        return self._period

    def update(self, kline: Kline) -> Decimal | None:
        if self._prev_close is None:
            true_range = kline.high - kline.low
        else:
            true_range = max(
                kline.high - kline.low,
                abs(kline.high - self._prev_close),
                abs(kline.low - self._prev_close),
            )
        self._prev_close = kline.close
        self._true_ranges.append(true_range)
        if len(self._true_ranges) < self._period:
            return None
        return sum(self._true_ranges) / self._period


@dataclass
class OpenPosition:
    """A strategy's own internally-tracked open-position state -- side,
    quantity, and the ATR-derived stop/target levels set at entry.

    Not a wire schema (`schemas/order_intent.py` stays exchange/asset-
    class-agnostic and has no concept of any particular strategy's risk
    management) -- purely internal bookkeeping for a stateful `Strategy`
    (see `regime_momentum_risk_managed.py`/`hourly_momentum.py`) to know
    when to emit a flattening exit intent.

    `entry_price` is the strategy's own decision-time reference price (the
    signal bar's own close -- see `compute_stop_and_target`'s docstring
    for why), not necessarily the actual simulated fill price
    (`backtest/fill.py` fills a `GUARDED_MARKET` intent at the *next*
    bar's open, adjusted for slippage). This is a small, deliberate,
    documented approximation that affects only the strategy's own
    exit-timing decisions, never the backtest's reported P&L --
    `metrics/position.py::PositionTracker` reconstructs realized P&L
    independently from the actual `(OrderIntent, Fill)` pairs the engine
    produces, not from anything a strategy believes about its own
    position.
    """

    side: Side
    quantity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal


def compute_stop_and_target(
    *,
    entry_price: Decimal,
    atr: Decimal,
    side: Side,
    stop_multiplier: Decimal = DEFAULT_STOP_MULTIPLIER,
    target_multiplier: Decimal = DEFAULT_TARGET_MULTIPLIER,
) -> tuple[Decimal, Decimal]:
    """`stop_price, target_price = entry_price -/+ (atr * multiplier)`,
    direction depending on `side`. Returns `(stop_price, target_price)`.

    `entry_price` is a caller-supplied reference price, not necessarily
    the eventual real fill price -- see `OpenPosition`'s docstring. Using
    the signal bar's own close as this reference (as both strategies built
    on this module do) is a deliberate choice: a strategy has no
    legitimate, look-ahead-safe way to know the *next* bar's actual fill
    price at the moment it decides to enter -- that would mean reading
    data beyond the current bar before emitting this bar's intent.

    Raises `ValueError` for a non-positive `atr` -- a zero/negative ATR is
    a degenerate input (e.g. a perfectly flat price series) that cannot
    produce a meaningful stop distance; callers should check `atr is not
    None` (from `AverageTrueRange.update`'s warmup contract) before
    calling this, and this function still fails loud rather than silently
    producing a zero-width or inverted stop/target pair.
    """
    if atr <= 0:
        raise ValueError(f"atr must be positive, got {atr}")
    stop_distance = atr * stop_multiplier
    target_distance = atr * target_multiplier
    if side == Side.LONG:
        return entry_price - stop_distance, entry_price + target_distance
    return entry_price + stop_distance, entry_price - target_distance


def compute_position_size(
    *,
    entry_price: Decimal,
    stop_price: Decimal,
    reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
    risk_fraction: Decimal = DEFAULT_RISK_FRACTION,
) -> Decimal | None:
    """Fixed-fractional position sizing: quantity such that, *given*
    `entry_price` and `stop_price` exactly as supplied, a stop-loss hit
    loses exactly `reference_equity * risk_fraction` (before fees/
    slippage, which this backtest-only formula does not account for).

    That guarantee is only as precise as its two price inputs: a caller
    (e.g. `regime_momentum_risk_managed.py`/`hourly_momentum.py`) that
    supplies its own signal-time reference price rather than the actual
    realized fill price (see those modules' `OpenPosition` docstring) will
    see the *realized* loss on a real stop-out land somewhat above or
    below the 1% target, not exactly on it -- this function's own
    arithmetic is exact, but "exactly 1%" is a claim about its inputs, not
    a claim about what a caller's eventual real fill will realize.

    `reference_equity` is a **fixed constant**, not a live running account
    balance -- deliberately so, per this task's explicit brief:
    reconstructing a live equity figure inside a strategy would duplicate
    `metrics/position.py::PositionTracker`'s job (which already does this
    correctly from real fills, downstream of the strategy) and would make
    position sizing depend on a value the strategy has no legitimate way
    to observe (the `Strategy` protocol never feeds fill confirmations
    back to the strategy that emitted the intent -- see
    `backtest.engine.Strategy`'s plain `Callable` signature). This is a
    deliberate simplification for backtesting purposes only, not a
    live-accounting claim -- a live system would size from the Java Risk
    Gateway's actual account state, nowhere near this code path (Python
    never places live orders, per CLAUDE.md's Non-negotiable Rules).

    Returns `None` (never raises, never returns a zero/negative quantity)
    when `stop_distance <= 0` -- a degenerate input (identical
    entry/stop, which `compute_stop_and_target` never itself produces for
    a positive ATR, but a caller could still construct) that cannot be
    sized against. Callers must treat `None` as "skip this entry" -- the
    same "no signal" outcome as every other warmup/degenerate case in this
    codebase's strategies.
    """
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return None
    quantity = (reference_equity * risk_fraction) / stop_distance
    if quantity <= 0:
        return None
    return quantity


def check_exit_trigger(position: OpenPosition, bar: Kline) -> Literal["stop", "target"] | None:
    """Checks whether `bar`'s high/low crossed `position`'s stop or
    target level. Returns `"stop"`, `"target"`, or `None`.

    If both are technically crossed within the same bar's `[low, high]`
    range (possible for a wide-range bar, since only OHLC is known, not
    intra-bar tick order), `"stop"` wins -- a deliberate, conservative
    tie-break. This backtest has no intra-bar tick data to know which
    level was actually touched first, and assuming the worse outcome for
    the trader is the standard, conservative convention when that
    information genuinely isn't available, rather than assuming a
    favorable intra-bar ordering with no basis for it.
    """
    if position.side == Side.LONG:
        stop_hit = bar.low <= position.stop_price
        target_hit = bar.high >= position.target_price
    else:
        stop_hit = bar.high >= position.stop_price
        target_hit = bar.low <= position.target_price
    if stop_hit:
        return "stop"
    if target_hit:
        return "target"
    return None
