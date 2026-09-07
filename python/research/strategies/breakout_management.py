"""Larry Williams volatility breakout, with six management policies.

Implements `.planning/tm-d-breakout-management-preregistration.md`
exactly. That document is a **contract**: it was committed before any
data was loaded and merged before this file existed, so nothing here may
introduce a parameter, a threshold or a rule it does not name.

## What varies, and what does not

The entry is fixed and comes from outside this project: `k = 0.5`, the
previous session's range, a UTC-midnight day boundary. The six policies
differ **only** in what happens after the position is open.

## The one ambiguity in the registration, and how it is read

The registration defines `1R` from "Entry", and separately pins that a
signal on bar `t` fills on bar `t+1`'s open. Those two are one bar
apart, so "Entry" could mean the trigger price or the realised fill.

**This implementation reads it as the trigger price**, for both the
stop level and the `R` denominator, because:

- a real order and its stop are placed together, at signal time, from
  the intended entry — a stop that moved with realised slippage is not
  something any trader places;
- sizing has to happen at signal time regardless, since the intent
  carries a quantity, so the trigger is already the only price available
  for it;
- it makes `R` a single fixed quantity per episode rather than one that
  depends on the fill, which is what "normalised to the initial layer's
  planned risk" requires.

Recorded here rather than chosen silently. The realised fill still lands
one bar later at whatever price the market gave, and that difference is
the lag the registration already accounts for.

## The other ambiguity: a bar that triggers both directions

The registration defines a long trigger above the open and a short
trigger below it, and says entry happens when price "first touches"
one. A single 1m bar can span both, and 1m OHLC cannot say which came
first.

**Such a day is skipped**, and skipped days are counted and reported.
That is the same conservative reading the registration applies to every
other unknown intrabar path (no stop on the entry bar; one pyramid layer
per bar).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Sequence
from uuid import uuid4

from backtest.kline import Kline
from schemas.order_intent import OrderIntent, OrderType, Side

# --------------------------------------------------------------------
# Constants. Every one is named in the registration; none is ours.
# --------------------------------------------------------------------

K = Decimal("0.5")
"""The breakout multiple. Literal in `sharebook-kr/larry_simple`'s own
code (`target = last + (high - low) * 0.5`), not a swept parameter."""

ATR_PERIOD = 14
"""Wilder's, on daily bars built from 1m on the UTC-midnight grid."""

TRAIL_ATR_MULT = Decimal("3")
"""The practitioner convention for a daily-swing trail."""

MAX_LAYERS = 3
"""P4: 1 + 1/2 + 1/4 = 1.75x the initial layer."""

RISK_FRACTION = Decimal("0.005")
"""0.5% of equity risked against the `1R` stop distance, identical for
every policy's **initial layer**."""

_BPS = Decimal("10000")


class Policy(str, Enum):
    BASELINE = "P0"
    STOP = "P1"
    TRAIL = "P2"
    SCALE_OUT = "P3"
    PYRAMID = "P4"
    HEDGE = "P5"


@dataclass(frozen=True)
class Leg:
    """One filled tranche. `qty` is always positive; `side` carries
    direction, matching `OrderIntent`'s own convention."""

    side: Side
    entry: Decimal
    qty: Decimal
    opened_at: int
    """Bar index of the fill."""


@dataclass
class Episode:
    """Initial entry to the exit of the final leg.

    The registration's unit of observation — every scale-out tranche,
    pyramid layer and hedge leg belongs to the episode that spawned it,
    so P4 does not appear to have three times P0's sample size for doing
    the same thing once.
    """

    side: Side
    trigger: Decimal
    r_distance: Decimal
    """`1R` as a positive price distance."""
    planned_risk: Decimal
    """`qty * r_distance` for the initial layer. Every `R` figure this
    task reports is normalised to this, including P4's half-size
    layers."""
    signal_index: int
    fill_index: int
    legs: list[Leg] = field(default_factory=list)
    hedge: Leg | None = None
    stop: Decimal | None = None
    levels_taken: int = 1
    """Highest `R` multiple already acted on: 1 means the entry only."""
    scaled_out: bool = False
    peak_qty: Decimal = Decimal("0")
    """Largest gross quantity held, for the exposure figure P4 must
    report separately from its management effect."""
    trail_extreme: Decimal | None = None
    stopped: bool = False
    day_key: int | None = None
    """UTC day the entry filled on, so a position can be closed on the
    first bar of the next day when the 23:59 bar is missing."""

    @property
    def sign(self) -> int:
        return 1 if self.side is Side.LONG else -1

    @property
    def open_qty(self) -> Decimal:
        return sum((leg.qty for leg in self.legs), Decimal(0))


def _day_key(bar: Kline) -> int:
    """UTC-midnight day index. `Kline.open_time` is an aware datetime,
    so the grid is derived from it rather than assumed."""
    return int(bar.open_time.timestamp()) // 86_400


def _wilder_atr(days: list[tuple[Decimal, Decimal, Decimal]], period: int) -> Decimal | None:
    """Wilder's ATR over completed daily (high, low, close) tuples.

    Returns `None` until `period + 1` days exist, so a trail is simply
    unavailable rather than being seeded from a partial history.
    """
    if len(days) < period + 1:
        return None
    trs: list[Decimal] = []
    for i in range(1, len(days)):
        high, low, _ = days[i]
        prev_close = days[i - 1][2]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = sum(trs[:period], Decimal(0)) / Decimal(period)
    for tr in trs[period:]:
        atr = (atr * Decimal(period - 1) + tr) / Decimal(period)
    return atr


class BreakoutManagementStrategy:
    """A `backtest.engine.Strategy` and an `EquityObserver`.

    Stateful across bars by design: it models its own position, because
    the engine's `Strategy` contract passes only the visible klines and
    never reports fills back. Every fill price it assumes is the
    registration's contract — `klines[t + 1].open * (1 + side * slip)` —
    and `verify_fill_contract` checks that assumption against what the
    engine actually produced rather than trusting it.
    """

    def __init__(
        self,
        policy: Policy,
        slippage_bps: Decimal,
        starting_equity: Decimal,
        symbol: str = "BTCUSDT",
    ):
        self.policy = policy
        self._symbol = symbol
        self._slip = Decimal(slippage_bps) / _BPS
        self._equity = Decimal(starting_equity)

        self._day_key: int | None = None
        self._day_open: Decimal | None = None
        self._prev_high: Decimal | None = None
        self._prev_low: Decimal | None = None
        self._cur_high: Decimal | None = None
        self._cur_low: Decimal | None = None
        self._cur_close: Decimal | None = None
        self._completed_days: list[tuple[Decimal, Decimal, Decimal]] = []
        self._atr: Decimal | None = None

        self._episode: Episode | None = None
        self._pending: tuple[Side, Decimal, Decimal, int] | None = None
        """(side, trigger, r_distance, signal_index) awaiting its fill bar."""
        self._entered_today = False

        # Reported, not tuned: the registration requires ambiguous days
        # be skipped, and a count is the only honest way to show how
        # much of the sample that removed.
        self.ambiguous_days = 0
        self.episodes: list[Episode] = []

    # -- EquityObserver ------------------------------------------------

    def on_equity(self, equity: Decimal) -> None:
        self._equity = equity

    # -- Strategy ------------------------------------------------------

    def __call__(self, window: Sequence[Kline]) -> Sequence[OrderIntent] | None:
        i = len(window) - 1
        bar = window[i]
        intents: list[OrderIntent] = []

        self._roll_day(bar)

        # A pending entry fills on this bar's open. Establish the
        # position before anything else looks at it; management is not
        # eligible until the *next* bar (t+2), which `_manage` enforces.
        if self._pending is not None and i == self._pending[3] + 1:
            self._open_episode(bar, i)

        if self._episode is not None:
            intents.extend(self._manage(window, i, bar))
        elif not self._entered_today:
            intents.extend(self._maybe_enter(bar, i))

        return intents or None

    # -- day bookkeeping ----------------------------------------------

    def _roll_day(self, bar: Kline) -> None:
        key = _day_key(bar)
        if key == self._day_key:
            self._cur_high = max(self._cur_high, bar.high)  # type: ignore[type-var]
            self._cur_low = min(self._cur_low, bar.low)  # type: ignore[type-var]
            self._cur_close = bar.close
            return

        if self._day_key is not None:
            self._completed_days.append((self._cur_high, self._cur_low, self._cur_close))  # type: ignore[arg-type]
            self._prev_high, self._prev_low = self._cur_high, self._cur_low
            # Recomputed once per completed day, from completed days
            # only — day d never sees itself.
            self._atr = _wilder_atr(self._completed_days, ATR_PERIOD)

        self._day_key = key
        self._day_open = bar.open
        self._cur_high, self._cur_low, self._cur_close = bar.high, bar.low, bar.close
        self._entered_today = False

    @staticmethod
    def _is_last_bar_of_day(bar: Kline) -> bool:
        """From the clock, never from the next bar.

        A first version peeked at `window[i + 1]`. `run_backtest` passes
        `KlineWindow(klines, i + 1)`, whose length is exactly `i + 1`,
        so that index was always out of range and the guard always
        returned `False` — **the time exit could never fire**. The
        strategy genuinely cannot see the next bar, which is the whole
        point of the window, so the day's end has to be read off the
        clock instead: the last minute of a UTC day opens at 23:59.
        """
        return bar.open_time.hour == 23 and bar.open_time.minute == 59

    # -- entry ---------------------------------------------------------

    def _maybe_enter(self, bar: Kline, i: int) -> list[OrderIntent]:
        if self._pending is not None or self._prev_high is None or self._day_open is None:
            return []
        rng = self._prev_high - self._prev_low  # type: ignore[operator]
        if rng <= 0:
            return []

        long_trigger = self._day_open + K * rng
        short_trigger = self._day_open - K * rng
        hit_long = bar.high >= long_trigger
        hit_short = bar.low <= short_trigger

        if hit_long and hit_short:
            # Both triggers inside one bar: the intrabar path is
            # unobservable, so the day is skipped rather than guessed.
            self.ambiguous_days += 1
            self._entered_today = True
            return []
        if not hit_long and not hit_short:
            return []

        side = Side.LONG if hit_long else Side.SHORT
        trigger = long_trigger if hit_long else short_trigger
        extreme = self._prev_low if hit_long else self._prev_high
        r = abs(trigger - (extreme + trigger) / Decimal(2))  # type: ignore[operator]
        if r <= 0:
            return []

        qty = (self._equity * RISK_FRACTION) / r
        if qty <= 0:
            return []

        self._pending = (side, trigger, r, i)
        self._entered_today = True
        return [self._intent(side, qty, bar)]

    def _open_episode(self, bar: Kline, i: int) -> None:
        side, trigger, r, signal_index = self._pending  # type: ignore[misc]
        self._pending = None
        sign = 1 if side is Side.LONG else -1
        entry = bar.open * (Decimal(1) + Decimal(sign) * self._slip)
        qty = (self._equity * RISK_FRACTION) / r

        ep = Episode(
            side=side,
            trigger=trigger,
            r_distance=r,
            planned_risk=qty * r,
            signal_index=signal_index,
            fill_index=i,
            day_key=_day_key(bar),
        )
        ep.legs.append(Leg(side=side, entry=entry, qty=qty, opened_at=i))
        ep.peak_qty = qty
        if self.policy is not Policy.BASELINE:
            # Stop from the trigger, not the fill: the order and its
            # stop are placed together at signal time. See the module
            # docstring.
            ep.stop = trigger - Decimal(sign) * r
        ep.trail_extreme = entry
        self._episode = ep

    # -- management ----------------------------------------------------

    def _manage(self, window: Sequence[Kline], i: int, bar: Kline) -> list[OrderIntent]:
        ep = self._episode
        assert ep is not None
        # t+2: a position is not managed on the bar it filled on.
        if i <= ep.fill_index:
            return []

        sign = ep.sign
        opposite = Side.SHORT if ep.side is Side.LONG else Side.LONG

        if ep.trail_extreme is not None:
            ep.trail_extreme = (
                max(ep.trail_extreme, bar.high) if sign > 0 else min(ep.trail_extreme, bar.low)
            )

        # 1. Stop / trailing stop — always first (S8 §3.7's stop-wins tie).
        stop = self._effective_stop(ep)
        if stop is not None:
            touched = bar.low <= stop if sign > 0 else bar.high >= stop
            if touched:
                return self._close_all(ep, opposite, bar, stopped=True)

        # 2. Scale-out target.
        if self.policy in (Policy.SCALE_OUT, Policy.HEDGE) and not ep.scaled_out:
            target = ep.trigger + Decimal(sign) * ep.r_distance
            if (bar.high >= target) if sign > 0 else (bar.low <= target):
                ep.scaled_out = True
                half = ep.legs[0].qty / Decimal(2)
                ep.stop = ep.legs[0].entry  # remainder's stop to entry
                if self.policy is Policy.SCALE_OUT:
                    ep.legs[0] = Leg(
                        side=ep.side,
                        entry=ep.legs[0].entry,
                        qty=ep.legs[0].qty - half,
                        opened_at=ep.legs[0].opened_at,
                    )
                    return [self._intent(opposite, half, bar)]
                # P5: offset instead of closing. Same signal bar, same
                # fill bar, same size — the control's whole point.
                ep.hedge = Leg(side=opposite, entry=Decimal(0), qty=half, opened_at=i)
                ep.peak_qty = max(ep.peak_qty, ep.open_qty + half)
                return [self._intent(opposite, half, bar)]

        # 3. Pyramid add — at most one layer per bar.
        if self.policy is Policy.PYRAMID and ep.levels_taken < MAX_LAYERS:
            nxt = ep.levels_taken + 1
            level = ep.trigger + Decimal(sign * nxt) * ep.r_distance
            if (bar.high >= level) if sign > 0 else (bar.low <= level):
                add = ep.legs[-1].qty / Decimal(2)
                ep.levels_taken = nxt
                ep.legs.append(Leg(side=ep.side, entry=level, qty=add, opened_at=i))
                ep.peak_qty = max(ep.peak_qty, ep.open_qty)
                ep.stop = level - Decimal(sign) * ep.r_distance
                return [self._intent(ep.side, add, bar)]

        # 4. Time exit — every policy except the trailing ones.
        if self.policy in (Policy.BASELINE, Policy.STOP, Policy.PYRAMID):
            if self._is_last_bar_of_day(bar):
                return self._close_all(ep, opposite, bar, stopped=False)
            # Safety net for a missing 23:59 bar: the window has one
            # known timestamp gap, and a position silently carrying into
            # the next day would change the policy rather than report a
            # data problem. Exits one minute later than the clock rule,
            # and can only fire when the clock rule could not.
            if ep.day_key is not None and _day_key(bar) != ep.day_key:
                return self._close_all(ep, opposite, bar, stopped=False)
        return []

    def _effective_stop(self, ep: Episode) -> Decimal | None:
        """The trail replaces the fixed stop only once it is better."""
        if self.policy is Policy.BASELINE:
            return None
        trailing = self.policy is Policy.TRAIL or (
            self.policy in (Policy.SCALE_OUT, Policy.HEDGE) and ep.scaled_out
        )
        if not trailing or self._atr is None or ep.trail_extreme is None:
            return ep.stop
        trail = ep.trail_extreme - Decimal(ep.sign) * TRAIL_ATR_MULT * self._atr
        if ep.stop is None:
            return trail
        return max(ep.stop, trail) if ep.sign > 0 else min(ep.stop, trail)

    def _close_all(self, ep: Episode, opposite: Side, bar: Kline, *, stopped: bool) -> list[OrderIntent]:
        qty = ep.open_qty
        ep.stopped = stopped
        out: list[OrderIntent] = []
        if qty > 0:
            out.append(self._intent(opposite, qty, bar))
        if ep.hedge is not None:
            # Both legs signal on the same bar and fill on the same next
            # bar, so P5 and P3 stay comparable.
            out.append(self._intent(ep.side, ep.hedge.qty, bar))
        self.episodes.append(ep)
        self._episode = None
        return out

    def _intent(self, side: Side, qty: Decimal, at: Kline) -> OrderIntent:
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=qty,
            limit_price=None,
            signal_timeframe="1m",
            created_at=at.open_time,
        )
