"""Trend core with a conjunction-gated tactical hedge.

Trade Management Task C. **The specification was written and committed
before this file existed** --
`.planning/tm-c-confluence-hedge-specification.md` -- because `N` is 127
and a conjunction across four families has far more configurations than a
threshold on one. Every constant here traces to that document.

## What it is

A `daily-tsmom-ensemble` core, unchanged, plus a short leg opened only
when four independent signal families agree the up-move is crowded and
stretched, and closed when they stop agreeing.

The core keeps its own entry, its own size and its own life. The hedge is
a separate leg that can be right or wrong without touching it -- which is
the thing this project's single-signed-quantity model could not express
at all, and which the operator spent many exchanges describing before it
became clear the framework, not the idea, was the obstacle.

## The conjunction, and why it is not theatre

| Condition | Family | Says |
|---|---|---|
| funding >= trailing p80 | carry | leveraged longs are crowded |
| `z(taker_buy_share) > 1` | order flow | buyers are exhausting |
| `z(htf_return) > 1` | price | price is stretched |
| activity rank >= 0.5 | volatility | there is enough movement to pay costs |

Stacking *correlated* conditions reduces trade count while adding no
information -- S11 measured exactly that for price features, which
collapse to about three signals because they correlate 0.72-0.85. What
makes this a real conjunction is that order flow is measurably
independent of price (`|r| <= 0.006`), and funding and volatility are
different axes again.

The first three are the directional case and **all must hold**. The
fourth is a gate, not a vote.

## What this candidate actually tests

S15 and S17 both measured that reducing exposure during an adverse
excursion destroys trend-following returns -- a stop cuts winners harder
than losers, and delaying entry gives away the edge. A hedge **is** a
reduction in net exposure.

So this is a direct test of whether a *selective, conjunction-gated*
reduction behaves differently from an unconditional one. If it does not,
that is a third confirmation of the same finding, and the specification's
stopping rule closes the direction rather than adjusting a threshold.

## No stop on the core, deliberately

The core is held until the ensemble's sign flips, exactly as measured.
Adding a stop was tested (S17) and it cut winners' excursions -- winners'
MAE p80 12.5-15.3% against losers' 8.1-9.7% -- on a strategy where 19
winners carry 44 losers.
"""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import deque
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from backtest.kline import Kline
from metrics.book import LegPurpose
from research.strategies.daily_tsmom_ensemble import (
    DEFAULT_BARS_PER_DAY,
    DEFAULT_LOOKBACKS,
    DailyTsmomEnsembleStrategy,
)
from research.strategies.leg_manager import LegManager
from schemas.order_intent import OrderIntent, Side

# --- every constant traces to the pre-registration -------------------

DEFAULT_TRAILING_WINDOW = 90
"""One quarter of daily bars. This project's `z_window` convention (one
day of 1m bars) scaled to this timeframe. Convention, not fitted."""

DEFAULT_FUNDING_PERCENTILE = Decimal("0.80")
"""Top quintile. A standard extremity cut. **Arbitrary**, and declared so
in the specification -- changing it later is visibly a second trial."""

DEFAULT_FLOW_Z = Decimal("1.0")
DEFAULT_PRICE_Z = Decimal("1.0")
"""One sigma each, and deliberately the SAME value rather than two
separate choices -- two knobs would be two things to tune."""

DEFAULT_ACTIVITY_RANK = Decimal("0.5")
"""Median: "tradeable at all", not "extreme". The gate exists so a hedge
is not opened into a market too quiet to pay a round trip."""

DEFAULT_HEDGE_FRACTION = Decimal("0.5")
"""Half the core. A hedge, not a reversal -- the operator described
trading *around* a position. **Arbitrary**, declared so."""

DEFAULT_ATR_PERIOD = 14


class _Trailing:
    """Rolling window with rank and z-score, current value EXCLUDED from
    its own reference.

    Excluding the current value is not a nicety: a bar that normalises
    against a window containing itself is comparing a number to a
    distribution it already moved, which flatters every extreme reading.
    Returns `None` during warmup rather than a partial answer.

    Both statistics are maintained **incrementally** -- running sums for
    the z-score, a sorted mirror with `bisect` for the rank. A naive
    recompute is O(window) per bar, which is invisible on 2,000 daily
    bars and fatal on 60,000 hourly ones with a 2,160-bar window.
    """

    __slots__ = ("_window", "_values", "_ordered", "_sum", "_sumsq")

    def __init__(self, window: int) -> None:
        if window < 2:
            raise ValueError(f"window must be at least 2, got {window}")
        self._window = window
        self._values: deque[Decimal] = deque()
        self._ordered: list[Decimal] = []
        self._sum = Decimal(0)
        self._sumsq = Decimal(0)

    @property
    def ready(self) -> bool:
        return len(self._values) == self._window

    def push(self, value: Decimal) -> None:
        if len(self._values) == self._window:
            expiring = self._values.popleft()
            del self._ordered[bisect_left(self._ordered, expiring)]
            self._sum -= expiring
            self._sumsq -= expiring * expiring
        self._values.append(value)
        insort(self._ordered, value)
        self._sum += value
        self._sumsq += value * value

    def rank(self, value: Decimal) -> Decimal | None:
        """Fraction of the reference window strictly below `value`."""
        if not self.ready:
            return None
        return Decimal(bisect_left(self._ordered, value)) / self._window

    def zscore(self, value: Decimal) -> Decimal | None:
        if not self.ready:
            return None
        n = Decimal(self._window)
        mean = self._sum / n
        var = self._sumsq / n - mean * mean
        if var <= 0:
            return None
        return (value - mean) / var.sqrt()


def _true_range(current: Kline, previous: Kline | None) -> Decimal:
    if previous is None:
        return current.high - current.low
    return max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
    )


def _taker_share(kline: Kline) -> Decimal | None:
    """`None` when a bar carries no real order-flow data (every
    BingX-sourced bar) or has zero volume -- ordinary warmup, never a
    fabricated value."""
    if kline.taker_buy_base_volume is None or kline.volume == 0:
        return None
    return kline.taker_buy_base_volume / kline.volume


class ConfluenceHedgeStrategy:
    """`daily-tsmom` core plus a conjunction-gated tactical short.

    Returns a *sequence* of intents, because a bar can legitimately carry
    two actions -- the core flipping while the hedge is retired, say --
    and splitting them across bars would change the prices they act at.

    `funding_by_day` maps a UTC date to that day's funding rate. Optional:
    without it the funding condition can never hold, so **no hedge is
    ever opened**. That is fail-closed by design -- a missing input must
    disable the overlay, never silently reduce the conjunction to three
    conditions.
    """

    def __init__(
        self,
        *,
        symbol: str,
        funding_by_day: dict[Any, Decimal] | None = None,
        lookbacks: Sequence[int] = DEFAULT_LOOKBACKS,
        trailing_window: int = DEFAULT_TRAILING_WINDOW,
        funding_percentile: Decimal = DEFAULT_FUNDING_PERCENTILE,
        flow_z: Decimal = DEFAULT_FLOW_Z,
        price_z: Decimal = DEFAULT_PRICE_Z,
        activity_rank: Decimal = DEFAULT_ACTIVITY_RANK,
        hedge_fraction: Decimal = DEFAULT_HEDGE_FRACTION,
        atr_period: int = DEFAULT_ATR_PERIOD,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        **core_kwargs: Any,
    ) -> None:
        if not Decimal(0) < hedge_fraction <= Decimal(1):
            raise ValueError(
                f"hedge_fraction must be in (0, 1] -- above 1 is a reversal, not a "
                f"hedge; got {hedge_fraction}"
            )
        if not Decimal(0) <= funding_percentile < Decimal(1):
            raise ValueError(f"funding_percentile must be in [0, 1), got {funding_percentile}")
        if not Decimal(0) <= activity_rank < Decimal(1):
            raise ValueError(f"activity_rank must be in [0, 1), got {activity_rank}")

        self._symbol = symbol
        self._funding_by_day = funding_by_day or {}
        self._funding_percentile = funding_percentile
        self._flow_z = flow_z
        self._price_z = price_z
        self._activity_rank = activity_rank
        self._hedge_fraction = hedge_fraction
        self._atr_period = atr_period
        self._htf_lag = max(lookbacks) // 4 or 1

        self._core = DailyTsmomEnsembleStrategy(
            symbol=symbol, lookbacks=lookbacks, bars_per_day=bars_per_day, **core_kwargs
        )
        self._legs = LegManager(symbol=symbol, signal_timeframe="1d")

        self._funding_hist = _Trailing(trailing_window)
        self._flow_hist = _Trailing(trailing_window)
        self._price_hist = _Trailing(trailing_window)
        self._activity_hist = _Trailing(trailing_window)

        self._closes: deque[Decimal] = deque(maxlen=self._htf_lag + 1)
        self._tr: deque[Decimal] = deque(maxlen=atr_period)
        self._previous: Kline | None = None
        self._hedge_entry_high: Decimal | None = None

        # Counted, not inferred: how often the conjunction actually fired,
        # and how often each condition alone would have. Reported so a
        # result that turns out to be one condition wearing four hats is
        # visible rather than assumed away.
        # `_sync_core` treats every core intent as a full transition:
        # it closes the existing core leg and opens a new one. That is
        # correct for a sign flip, which is the only thing the core emits
        # by default. It is WRONG for a same-sign resize, which
        # `rebalance_on_conviction=True` produces as a pure delta intent
        # -- long 10 to long 12 arrives as "long 2", and this class would
        # book it as "close 10, open 12". The tactical hedge would also
        # be closed early by the same branch.
        #
        # Refused rather than handled, because handling it means teaching
        # the book to distinguish a delta from a transition, which is a
        # real design question and not one this candidate needs: the flag
        # defaults off and was measured as harmful. Fail closed instead
        # of silently mis-booking.
        if getattr(self._core, "_rebalance_on_conviction", False):
            raise ValueError(
                "ConfluenceHedgeStrategy cannot wrap a core with "
                "rebalance_on_conviction=True: that core emits same-sign "
                "delta intents, and this class books every core intent as a "
                "full close-and-reopen, which would misstate the core leg "
                "and close the tactical hedge early."
            )
        self.condition_hits = {"funding": 0, "flow": 0, "price": 0, "activity": 0}
        self.conjunction_hits = 0
        self.hedges_opened = 0
        self.hedges_invalidated = 0
        # Bars where a hedge was open but its conditions could not be
        # evaluated. Counted rather than silently folded into the exit
        # rule, so a future run on gappier data can see it happening.
        self.hedge_bars_unevaluable = 0

    @property
    def book(self):
        return self._legs.book

    @property
    def legs(self) -> LegManager:
        return self._legs

    # -- conditions --------------------------------------------------

    def _conditions(self, current: Kline) -> dict[str, bool] | None:
        """Each condition's truth, or `None` while any input is warming
        up. A partial conjunction is never evaluated."""
        share = _taker_share(current)
        funding = self._funding_by_day.get(current.open_time.date())

        htf = None
        if len(self._closes) == self._closes.maxlen and self._closes[0] > 0:
            htf = (current.close - self._closes[0]) / self._closes[0]

        activity = None
        if len(self._tr) == self._atr_period and current.close > 0:
            activity = (sum(self._tr, Decimal(0)) / self._atr_period) / current.close

        if share is None or funding is None or htf is None or activity is None:
            return None

        funding_rank = self._funding_hist.rank(funding)
        flow_z = self._flow_hist.zscore(share)
        price_z = self._price_hist.zscore(htf)
        activity_rank = self._activity_hist.rank(activity)
        if None in (funding_rank, flow_z, price_z, activity_rank):
            return None

        return {
            "funding": funding_rank >= self._funding_percentile,
            "flow": flow_z > self._flow_z,
            "price": price_z > self._price_z,
            "activity": activity_rank >= self._activity_rank,
        }

    # -- the bar -----------------------------------------------------

    def __call__(self, window: Sequence[Kline]) -> Sequence[OrderIntent] | None:
        current = window[-1]

        # The core decides first and owns the primary thesis. Its intent
        # is mirrored into the book so both legs share one accounting.
        core_intent = self._core([current])
        if core_intent is not None:
            self._sync_core(core_intent, current)

        conditions = self._conditions(current)
        if conditions is not None:
            for name, hit in conditions.items():
                if hit:
                    self.condition_hits[name] += 1
            if all(conditions.values()):
                self.conjunction_hits += 1

        self._manage_hedge(current, conditions)
        self._observe(current)
        return self._legs.drain()

    def _sync_core(self, intent: OrderIntent, current: Kline) -> None:
        """Mirror the core's own position changes into the book.

        The core strategy is reused byte-for-byte, so it still speaks in
        net quantities. This translates that into leg terms without
        touching it -- keeping its evaluated behaviour exactly as
        measured.
        """
        existing = self._legs.legs(LegPurpose.CORE)
        for leg in existing:
            self._legs.close_leg(leg.leg_id, price=current.close, time=current.open_time)
        # A core flip must retire the hedge too: the hedge's whole premise
        # is protecting a long that no longer exists.
        if existing:
            for leg in self._legs.legs(LegPurpose.TACTICAL):
                self._legs.close_leg(leg.leg_id, price=current.close, time=current.open_time)
            self._hedge_entry_high = None
        if intent.quantity > 0 and self._core.position_sign != 0:
            self._legs.open_leg(
                side=Side.LONG if self._core.position_sign > 0 else Side.SHORT,
                quantity=self._core.position_quantity,
                price=current.close,
                time=current.open_time,
                purpose=LegPurpose.CORE,
            )

    def _manage_hedge(self, current: Kline, conditions: dict[str, bool] | None) -> None:
        open_hedges = self._legs.legs(LegPurpose.TACTICAL)

        if open_hedges:
            # Invalidation first: a new high says the pullback thesis is
            # wrong, and that outranks the exit rule.
            if self._hedge_entry_high is not None and current.close > self._hedge_entry_high:
                for leg in open_hedges:
                    self._legs.close_leg(leg.leg_id, price=current.close, time=current.open_time)
                self._hedge_entry_high = None
                self.hedges_invalidated += 1
                return
            # A bar whose inputs cannot be evaluated is NOT the exit
            # rule. The specification's rule is "the setup conditions no
            # longer all hold" -- a missing taker-volume figure or a day
            # with no funding row says nothing about whether they hold.
            # Treating the two the same would shorten holds, change the
            # hedge count MIN_HEDGES is judged against, and move the
            # reported P&L, all without any of it being in the spec.
            #
            # Holding through it is bounded, not open-ended: the
            # invalidation check above needs no conditions and still
            # fires, so a hedge cannot survive a data outage indefinitely
            # while its thesis is being disproved by price.
            #
            # Measured on this run's data: zero hedges were closed this
            # way, so this correction changes none of Task C's reported
            # figures. It is fixed because it is wrong, not because it
            # mattered here.
            if conditions is None:
                self.hedge_bars_unevaluable += 1
                return
            if not all(conditions.values()):
                for leg in open_hedges:
                    self._legs.close_leg(leg.leg_id, price=current.close, time=current.open_time)
                self._hedge_entry_high = None
            return

        if conditions is None or not all(conditions.values()):
            return
        # A hedge protects a LONG core. A short core needs no short hedge,
        # and hedging it would be a reversal wearing a hedge's name.
        core_legs = [leg for leg in self._legs.legs(LegPurpose.CORE) if leg.side is Side.LONG]
        if not core_legs:
            return
        core = core_legs[0]
        # Only hedge a core that is actually in profit -- there must be
        # something to protect.
        if current.close <= core.entry_price:
            return

        self._legs.open_leg(
            side=Side.SHORT,
            quantity=core.quantity * self._hedge_fraction,
            price=current.close,
            time=current.open_time,
            purpose=LegPurpose.TACTICAL,
            invalidation=current.high,
        )
        self._hedge_entry_high = current.high
        self.hedges_opened += 1

    def _observe(self, current: Kline) -> None:
        """Update every trailing reference AFTER the bar's decisions, so
        no window ever contains the value being judged against it."""
        share = _taker_share(current)
        if share is not None:
            self._flow_hist.push(share)
        funding = self._funding_by_day.get(current.open_time.date())
        if funding is not None:
            self._funding_hist.push(funding)
        if len(self._closes) == self._closes.maxlen and self._closes[0] > 0:
            self._price_hist.push((current.close - self._closes[0]) / self._closes[0])
        self._tr.append(_true_range(current, self._previous))
        if len(self._tr) == self._atr_period and current.close > 0:
            self._activity_hist.push((sum(self._tr, Decimal(0)) / self._atr_period) / current.close)
        self._closes.append(current.close)
        self._previous = current
