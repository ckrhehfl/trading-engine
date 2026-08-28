"""Selective mean reversion on 1m BTC-USDT -- Scalping Task S14.

The first scalping candidate built to S8's decomposed methodology rather
than as a single fused threshold rule, and the first in this project
structured as **Condition / Setup / Trigger / Invalidation** (CSTI), the
structure practitioner literature uses and which S4's and S6's candidates
both lacked.

Every constant below is either *measured elsewhere in this arc* or a
*cited external convention*. None is fitted here. Two are **selected**
from S13's 15-cell sweep and are declared as such, because a selected
constant has to be deflated against, not quietly presented as a
convention -- see "Selection disclosure".

## Condition -- when this strategy is allowed to look at all

Trailing percentile rank of **absolute** ATR-over-price (S10's
`VolatilityAxis.ABSOLUTE`) must sit at or above `activity_quantile`.

S10 measured this: an ATR *ratio* to its own trailing mean separates
forward movement 1.22x top-1%-vs-all, while the absolute fraction-of-
price separates **5.21x**. A ratio discards exactly the absolute level a
cost-versus-move decision needs -- a market that doubles its volatility
and stays there reads 1.0.

The rank is **trailing**, never a global percentile over the whole
dataset. That distinction cost ~80% of an apparent edge once already in
this arc (S12's activity filter, caught on review), and is the difference
between a filter a live system could have applied and one that reads the
future.

## Setup -- two orthogonal information sources, not one

S11 measured per-feature ICs and, more importantly, their correlations:
the ten features that cleared its bars are really about **three**
signals, and **order flow is uncorrelated with every price feature at
|r| <= 0.006** -- indistinguishable from zero, and the first genuinely
independent pair this project has held at once.

This strategy composes exactly those two:

- `z(htf_ret_4h)`  -- 240-bar price return, S11's strongest single
  feature (IC -0.051 at 60m), negative sign, i.e. mean-reverting
- `z(taker_buy_share)` -- order flow, IC -0.027 at 15m, also negative

Both z-scored against their own trailing 1,440-bar (one day) window,
**with the current value excluded from its own reference window** so a
bar cannot normalise against itself. Composite score is their unweighted
sum. Unweighted deliberately: S11 gives no basis for a weighting, and
fitting one here would add a free parameter for no measured reason.

Both ICs are negative, so the strategy **fades** the composite: a very
positive score is sold, a very negative score is bought. S13 confirmed
the sign directly -- trend-following the identical signals returns
exactly the negative outcome in every swept cell.

## Trigger -- `|score| >= entry_z`, evaluated only while flat

## Invalidation -- the stop comes from a measured distribution

`stop_atr_multiple = 2.65`, which is **winners' 80th-percentile MAE**
measured over 106,361 real positions in S12. At that distance 20% of
winning positions are stopped out and 72.7% of losing ones are.

S6 used 1.5 ATR by convention and never measured it; on the same sample
that cuts **40.9% of winners** to save 89.2% of losers. Practitioner
literature is explicit that a stop belongs at a level that *invalidates
the thesis*, not at a round distance, and S12 is what makes that level
knowable here.

## The R:R qualification gate -- the selectivity mechanism S4 and S6 had no equivalent of

Sequencing matters and is taken directly from the practitioner
literature: **place the stop first, then compute reward-to-risk against
a structural target, then take the trade only if the ratio qualifies, and
only then size it.** A trade whose structure offers 1:0.8 is skipped, not
resized.

The structural target is the reversion the setup actually hypothesises:
`htf_ret_4h` returning to its own trailing mean, implying

    target_price = close[t - htf_lag] * (1 + mean(htf_ret_4h))

The gate then requires `|target - entry| / |entry - stop| >= min_rr`,
with `min_rr = 2.0` -- the floor practitioner sources give (3:1
preferred, 2:1 minimum), not a value tuned here.

This is not cosmetic. It is a second, *independent* selectivity filter:
the setup can fire while the structure offers no room, and those trades
are declined. Nothing in S4 or S6 could decline a trade for that reason.

Asymmetry is the point, not win rate. At 3:1 a 40% win rate is
profitable; at 0.5:1 even 60% loses. S13 showed win rate rising
monotonically with holding period while expectancy fell and turned
negative, which is the same lesson from the other direction.

## Time exit

`max_hold_bars = 60`. S11's ICs are strongest at the 60-minute horizon
and S13's holding-period sweep found gross outcome peaks there and
inverts by two hours -- hold past the reversion window and the edge is
spent.

## Selection disclosure -- what is fitted, what is not

| Constant | Source | Selected here? |
|---|---|---|
| `activity_quantile = 0.99` | S13 sweep | **yes** |
| `entry_z = 5.0` | S13 sweep | **yes** |
| `stop_atr_multiple = 2.65` | S12 measured MAE p80 | no |
| `min_rr = 2.0` | practitioner floor | no |
| `max_hold_bars = 60` | S11 IC peak, S13 confirmed | no (measured) |
| `htf_lag = 240` | S11's strongest feature | no |
| `z_window = 1440` | one day, S12 convention | no |
| `atr_period = 14` | Wilder | no |
| `risk_fraction`, `reference_equity` | package defaults | no |

Two selected constants out of a 15-cell sweep. This is **not** a
zero-fitted-parameter strategy and must not be presented as one --
`daily-tsmom-ensemble`'s policy exception rests on that property and does
not extend here. DSR against the real project-level `N` is the gate.

## Known limitation, inherited and disclosed

`compute_position_size` sizes against a **fixed** `reference_equity`, not
real shrinking equity -- a project-wide characteristic S6 surfaced and S7
only half-closed (the insolvency floor is a circuit breaker, not
equity-aware sizing). This strategy inherits it.
"""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import deque
from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import uuid4

from backtest.kline import Kline
from research.strategies.risk_management import (
    DEFAULT_ATR_PERIOD,
    DEFAULT_REFERENCE_EQUITY,
    DEFAULT_RISK_FRACTION,
    AverageTrueRange,
    OpenPosition,
    check_exit_trigger,
    compute_position_size,
    compute_stop_and_target,
)
from schemas.order_intent import OrderIntent, OrderType, Side

DEFAULT_BARS_PER_DAY = 1440

DEFAULT_HTF_LAG = 240          # S11's htf_ret_4h
DEFAULT_Z_WINDOW = 1440        # one day of 1m bars
DEFAULT_ACTIVITY_HISTORY = 1440
DEFAULT_ACTIVITY_QUANTILE = Decimal("0.99")   # SELECTED, S13 sweep
DEFAULT_ENTRY_Z = Decimal("5.0")              # SELECTED, S13 sweep
DEFAULT_STOP_ATR_MULTIPLE = Decimal("2.65")   # S12 measured winners' MAE p80
DEFAULT_MIN_RR = Decimal("2.0")               # practitioner floor
DEFAULT_MAX_HOLD_BARS = 60                    # S11 IC peak / S13 confirmed

# Sizing modes (Scalping Task S15).
#   "fixed"       -- risk `risk_fraction` of a constant `reference_equity`,
#                    this package's long-standing behaviour and still the
#                    default, so nothing changes for an existing caller.
#   "compounding" -- risk `risk_fraction` of the CURRENT mark-to-market
#                    equity the engine reports via `on_equity`.
# `compounding` is what closes the half Task S7 left open: fixed sizing
# keeps risking a share of the ORIGINAL account after most of it is gone,
# which is the mechanism behind S6's -239,161% run.
SIZING_FIXED = "fixed"
SIZING_COMPOUNDING = "compounding"
SIZING_MODES = (SIZING_FIXED, SIZING_COMPOUNDING)

# Whether `stop_atr_multiple` is an EXIT TRIGGER or only a SIZING BASIS
# (Scalping Task S15(b)). Default `True` preserves S14's behaviour.
#
# S15(b) measured, at every candidate width from 1.5 to 12 ATR and in both
# operating cells, that a stop realises a LARGER loss than the position it
# catches would have taken on its own -- it manufactures losses rather
# than avoiding them, because this signal's edge lives inside the adverse
# excursion. "No stop" beat every stop width tested.
#
# With `use_stop=False` the same ATR distance still sets position size and
# still denominates the R:R gate; it simply stops being a price at which
# the position is closed. The position is then bounded by TIME
# (`max_hold_bars`) rather than by price, with equity-aware sizing and
# `run_backtest`'s zero-equity floor as the portfolio-level controls.
DEFAULT_USE_STOP = True


class TrailingZScore:
    """Rolling z-score where the current value is **excluded from its own
    reference window**, so a bar cannot normalise against itself.

    Returns `None` until `window` prior observations exist, and whenever
    the reference window's variance is zero (no scale to divide by --
    "no evidence", never a fabricated infinity).

    `None` inputs contribute no observation, exactly as `OfiBands` treats
    a bar with no real order-flow data: ordinary warmup, not a crash and
    not a fabricated zero.
    """

    __slots__ = ("_window", "_buf", "_sum", "_sumsq")

    def __init__(self, window: int = DEFAULT_Z_WINDOW) -> None:
        if window < 2:
            raise ValueError(f"window must be at least 2, got {window}")
        self._window = window
        self._buf: deque[Decimal] = deque()
        self._sum = Decimal(0)
        self._sumsq = Decimal(0)

    @property
    def window(self) -> int:
        return self._window

    @property
    def bars_seen(self) -> int:
        return len(self._buf)

    def update(self, value: Decimal | None) -> tuple[Decimal, Decimal] | None:
        """Returns `(z, reference_mean)` or `None`. The mean is returned
        because the reversion target needs it, and recomputing it outside
        would risk drifting from the window this z was actually measured
        against."""
        if value is None:
            return None
        out: tuple[Decimal, Decimal] | None = None
        if len(self._buf) == self._window:
            mean = self._sum / self._window
            var = self._sumsq / self._window - mean * mean
            if var > 0:
                out = ((value - mean) / var.sqrt(), mean)
            old = self._buf.popleft()
            self._sum -= old
            self._sumsq -= old * old
        self._buf.append(value)
        self._sum += value
        self._sumsq += value * value
        return out


class TrailingPercentileRank:
    """Exact rolling percentile rank against the trailing `history`
    observations, maintained incrementally.

    Exact, not a periodically-rebuilt approximation: an earlier version of
    this idea in `research/excursion.py` rebuilt its sorted reference on a
    schedule, leaving the reference up to `history - 1` observations
    stale. That was a real bug with a verified counterexample, and the
    incremental maintenance here is what replaced it.

    Returns `None` during warmup and for a `None` input.
    """

    __slots__ = ("_history", "_window", "_ordered")

    def __init__(self, history: int = DEFAULT_ACTIVITY_HISTORY) -> None:
        if history < 1:
            raise ValueError(f"history must be at least 1, got {history}")
        self._history = history
        self._window: deque[Decimal] = deque()
        self._ordered: list[Decimal] = []

    @property
    def history(self) -> int:
        return self._history

    def update(self, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        out: Decimal | None = None
        if len(self._window) == self._history:
            out = Decimal(bisect_left(self._ordered, value)) / self._history
            expiring = self._window.popleft()
            del self._ordered[bisect_left(self._ordered, expiring)]
        self._window.append(value)
        insort(self._ordered, value)
        return out


def _taker_share(kline: Kline) -> Decimal | None:
    """`taker_buy_base_volume / volume`, or `None` when this bar carries
    no real order-flow data (every BingX-sourced bar, and every Binance
    bar predating S5's capture) or has zero volume."""
    if kline.taker_buy_base_volume is None or kline.volume == 0:
        return None
    return kline.taker_buy_base_volume / kline.volume


class SelectiveReversionStrategy:
    """Bound, stateful `Strategy` implementing the CSTI structure in the
    module docstring. Entry is evaluated only while flat; exit is
    stop, target, or the `max_hold_bars` time limit, whichever comes
    first."""

    __slots__ = (
        "_symbol", "_htf_lag", "_activity_quantile", "_entry_z",
        "_stop_atr_multiple", "_min_rr", "_max_hold_bars",
        "_reference_equity", "_risk_fraction",
        "_atr", "_activity", "_z_price", "_z_flow",
        "_closes", "_position", "_bars_held", "_declined_on_rr", "_exits",
        "_sizing_mode", "_equity", "_declined_no_equity", "_use_stop",
    )

    def __init__(
        self,
        *,
        symbol: str,
        htf_lag: int = DEFAULT_HTF_LAG,
        z_window: int = DEFAULT_Z_WINDOW,
        activity_history: int = DEFAULT_ACTIVITY_HISTORY,
        activity_quantile: Decimal = DEFAULT_ACTIVITY_QUANTILE,
        entry_z: Decimal = DEFAULT_ENTRY_Z,
        atr_period: int = DEFAULT_ATR_PERIOD,
        stop_atr_multiple: Decimal = DEFAULT_STOP_ATR_MULTIPLE,
        min_rr: Decimal = DEFAULT_MIN_RR,
        max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
        use_stop: bool = DEFAULT_USE_STOP,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        risk_fraction: Decimal = DEFAULT_RISK_FRACTION,
        sizing_mode: str = SIZING_FIXED,
    ) -> None:
        if sizing_mode not in SIZING_MODES:
            raise ValueError(f"sizing_mode must be one of {SIZING_MODES}, got {sizing_mode!r}")
        if htf_lag < 1:
            raise ValueError(f"htf_lag must be at least 1, got {htf_lag}")
        if entry_z <= 0:
            raise ValueError(f"entry_z must be positive, got {entry_z}")
        if stop_atr_multiple <= 0:
            raise ValueError(f"stop_atr_multiple must be positive, got {stop_atr_multiple}")
        if min_rr <= 0:
            raise ValueError(f"min_rr must be positive, got {min_rr}")
        # Deliberately NOT floored at DEFAULT_MIN_RR. 2:1 is the policy
        # floor for a research configuration and DEFAULT_MIN_RR enforces
        # it for every real run; a hard constructor floor would also make
        # the gate impossible to switch off, and switching it off is
        # exactly how `s14_stop_diagnosis.py` isolates its contribution.
        # A value below 2.0 is a diagnostic setting and is never a valid
        # configuration to report a result from.
        if max_hold_bars < 1:
            raise ValueError(f"max_hold_bars must be at least 1, got {max_hold_bars}")
        if not (0 <= activity_quantile < 1):
            raise ValueError(f"activity_quantile must be in [0, 1), got {activity_quantile}")

        self._symbol = symbol
        self._htf_lag = htf_lag
        self._activity_quantile = activity_quantile
        self._entry_z = entry_z
        self._stop_atr_multiple = stop_atr_multiple
        self._min_rr = min_rr
        self._max_hold_bars = max_hold_bars
        self._use_stop = use_stop
        self._reference_equity = reference_equity
        self._risk_fraction = risk_fraction
        self._sizing_mode = sizing_mode
        self._equity: Decimal | None = None
        self._declined_no_equity = 0

        self._atr = AverageTrueRange(period=atr_period)
        self._activity = TrailingPercentileRank(history=activity_history)
        self._z_price = TrailingZScore(window=z_window)
        self._z_flow = TrailingZScore(window=z_window)
        # Only `htf_lag + 1` closes are ever needed, so the strategy keeps
        # its own bounded ring rather than indexing back into `window` --
        # which `run_backtest` is free to bound independently.
        self._closes: deque[Decimal] = deque(maxlen=htf_lag + 1)
        self._position: OpenPosition | None = None
        self._bars_held = 0
        self._declined_on_rr = 0
        # Exit-reason tally. Not diagnostics-for-their-own-sake: which of
        # the three ends a position is the single most load-bearing fact
        # about a stop/target design, and S6 shipped without it.
        self._exits: dict[str, int] = {"stop": 0, "target": 0, "time": 0}

    @property
    def open_position(self) -> OpenPosition | None:
        return self._position

    def on_equity(self, equity: Decimal, /) -> None:
        """`backtest.engine.EquityObserver`. Records the engine's
        mark-to-market equity for this bar, before an intent is asked for.

        Stored rather than acted on: sizing reads it at entry time, which
        is the only moment it is needed."""
        self._equity = equity

    @property
    def sizing_equity(self) -> Decimal | None:
        """The equity the next entry would size against, or `None` when
        `compounding` mode has not been given one yet."""
        if self._sizing_mode == SIZING_FIXED:
            return self._reference_equity
        return self._equity

    @property
    def declined_no_equity(self) -> int:
        """Entries refused because `compounding` sizing had no usable
        equity. Non-zero means the engine was run without
        `starting_equity`, i.e. the run is misconfigured -- reported
        rather than hidden, because the alternative failure is silent."""
        return self._declined_no_equity

    @property
    def exits(self) -> dict[str, int]:
        """How each closed position ended: `stop`, `target`, or `time`.
        A design whose target is never reached is a different strategy
        from the one its author believes they wrote, and this is what
        makes that visible without re-deriving it from fills."""
        return dict(self._exits)

    @property
    def declined_on_rr(self) -> int:
        """Trades the setup fired on but the R:R gate refused. Reported
        rather than discarded -- it is the only direct measure of how much
        work that gate is doing."""
        return self._declined_on_rr

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]

        atr = self._atr.update(current)
        self._closes.append(current.close)

        activity_rank = None
        if atr is not None and current.close > 0:
            activity_rank = self._activity.update(atr / current.close)

        htf_ret = None
        if len(self._closes) == self._closes.maxlen:
            past = self._closes[0]
            if past > 0:
                htf_ret = (current.close - past) / past
        price_z = self._z_price.update(htf_ret)
        flow_z = self._z_flow.update(_taker_share(current))

        if self._position is not None:
            self._bars_held += 1
            trigger = check_exit_trigger(self._position, current)
            if trigger is not None:
                self._exits[trigger] += 1
                return self._flatten(current)
            if self._bars_held >= self._max_hold_bars:
                self._exits["time"] += 1
                return self._flatten(current)
            return None

        if (
            activity_rank is None
            or activity_rank < self._activity_quantile
            or price_z is None
            or flow_z is None
            or atr is None
            or atr <= 0
        ):
            return None

        score = price_z[0] + flow_z[0]
        if abs(score) < self._entry_z:
            return None
        # Both ICs are negative -- fade, never follow. S13 confirmed the
        # sign by measuring the inverse and getting exactly the negative.
        side = Side.SHORT if score > 0 else Side.LONG
        return self._open(current, side, atr, price_z[1])

    @staticmethod
    def _unreachable_stop(side: Side) -> Decimal:
        """A stop level `check_exit_trigger` can never fire.

        Chosen over adding a branch to `check_exit_trigger` deliberately:
        that function's stop-wins-on-a-same-bar-tie rule is a tested,
        conservative contract shared by four strategies, and suppressing
        its `"stop"` return here would also suppress the tie case where a
        target was genuinely reached. Putting the level out of reach
        leaves the contract untouched and makes `"target"` the only
        price-based exit, which is exactly the intent.

        A long is never stopped below zero; a short is never stopped at a
        price no market reaches. Both are recorded on `OpenPosition` as
        the sentinel they are, not as a risk figure -- the real risk basis
        is `stop_atr_multiple`, which still sized the position.
        """
        return Decimal(0) if side == Side.LONG else Decimal("1e30")

    def _reversion_target(self, mean_htf_ret: Decimal) -> Decimal | None:
        """The price implied by `htf_ret_4h` reverting to the trailing
        mean this bar's own z-score was measured against."""
        if len(self._closes) != self._closes.maxlen:
            return None
        past = self._closes[0]
        return past * (1 + mean_htf_ret) if past > 0 else None

    def _open(self, current: Kline, side: Side, atr: Decimal, mean_htf_ret: Decimal) -> OrderIntent | None:
        entry_price = current.close
        target_price = self._reversion_target(mean_htf_ret)
        if target_price is None:
            return None

        # Sequencing is deliberate and taken from the practitioner
        # literature: stop first (from the measured invalidation
        # distance), then the reward the structure actually offers, then
        # the qualification test, and only then size. A trade that does
        # not qualify is declined outright, never resized into.
        risk = self._stop_atr_multiple * atr
        reward = target_price - entry_price if side == Side.LONG else entry_price - target_price
        if reward <= 0 or reward / risk < self._min_rr:
            self._declined_on_rr += 1
            return None

        stop_price, resolved_target = compute_stop_and_target(
            entry_price=entry_price,
            atr=atr,
            side=side,
            stop_multiplier=self._stop_atr_multiple,
            target_multiplier=reward / atr,
        )
        # `stop_price` above is the RISK BASIS -- it sized the position and
        # denominated the R:R gate, both of which stay true either way.
        # Whether it is also an exit level is a separate decision.
        exit_stop = stop_price if self._use_stop else self._unreachable_stop(side)
        # Fail closed rather than fall back. In `compounding` mode a
        # missing equity figure means the engine was run without
        # `starting_equity`, and silently sizing off the constant instead
        # would reproduce exactly the behaviour this mode exists to
        # replace -- while reporting itself as compounding. A non-positive
        # equity is a wiped-out account, which cannot be sized against at
        # all.
        equity = self.sizing_equity
        if equity is None or equity <= 0:
            self._declined_no_equity += 1
            return None
        quantity = compute_position_size(
            entry_price=entry_price,
            stop_price=stop_price,
            reference_equity=equity,
            risk_fraction=self._risk_fraction,
        )
        if quantity is None:
            return None

        self._position = OpenPosition(
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_price=exit_stop,
            target_price=resolved_target,
        )
        self._bars_held = 0
        return self._intent(current, side, quantity)

    def _flatten(self, current: Kline) -> OrderIntent:
        position = self._position
        assert position is not None
        self._position = None
        self._bars_held = 0
        closing = Side.SHORT if position.side == Side.LONG else Side.LONG
        return self._intent(current, closing, position.quantity)

    def _intent(self, current: Kline, side: Side, quantity: Decimal) -> OrderIntent:
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=quantity,
            limit_price=None,
            signal_timeframe="1m",
            created_at=current.open_time,
        )


class SelectiveReversionTrainable:
    """`TrainableStrategy` adapter. `fit` ignores the train window
    entirely and returns a fresh strategy bound to `params` -- there is
    nothing to estimate from training data, because every constant is
    measured elsewhere or selected in advance rather than fitted per
    fold.

    The train window is still consumed by the walk-forward geometry, and
    that is not wasted: it is what keeps each validate window's warmup
    (1,440 bars of z-score history plus 240 of price lag) from eating
    into the evaluated period.
    """

    def __init__(self, *, symbol: str) -> None:
        self._symbol = symbol

    def fit(self, train_klines, params, *, parent_run_id: str):  # noqa: ARG002
        del train_klines, parent_run_id
        return _build(self._symbol, params)


def _build(symbol: str, params) -> SelectiveReversionStrategy:
    def dec(key: str, default: Decimal) -> Decimal:
        v = params.get(key, default)
        return v if isinstance(v, Decimal) else Decimal(str(v))

    def ints(key: str, default: int) -> int:
        return int(params.get(key, default))

    return SelectiveReversionStrategy(
        symbol=symbol,
        htf_lag=ints("htf_lag", DEFAULT_HTF_LAG),
        z_window=ints("z_window", DEFAULT_Z_WINDOW),
        activity_history=ints("activity_history", DEFAULT_ACTIVITY_HISTORY),
        activity_quantile=dec("activity_quantile", DEFAULT_ACTIVITY_QUANTILE),
        entry_z=dec("entry_z", DEFAULT_ENTRY_Z),
        atr_period=ints("atr_period", DEFAULT_ATR_PERIOD),
        stop_atr_multiple=dec("stop_atr_multiple", DEFAULT_STOP_ATR_MULTIPLE),
        min_rr=dec("min_rr", DEFAULT_MIN_RR),
        max_hold_bars=ints("max_hold_bars", DEFAULT_MAX_HOLD_BARS),
        use_stop=bool(params.get("use_stop", DEFAULT_USE_STOP)),
        reference_equity=dec("reference_equity", DEFAULT_REFERENCE_EQUITY),
        risk_fraction=dec("risk_fraction", DEFAULT_RISK_FRACTION),
        sizing_mode=str(params.get("sizing_mode", SIZING_FIXED)),
    )


DEFAULT_PARAMS: dict[str, Any] = {
    "htf_lag": DEFAULT_HTF_LAG,
    "z_window": DEFAULT_Z_WINDOW,
    "activity_history": DEFAULT_ACTIVITY_HISTORY,
    "activity_quantile": DEFAULT_ACTIVITY_QUANTILE,
    "entry_z": DEFAULT_ENTRY_Z,
    "atr_period": DEFAULT_ATR_PERIOD,
    "stop_atr_multiple": DEFAULT_STOP_ATR_MULTIPLE,
    "min_rr": DEFAULT_MIN_RR,
    "max_hold_bars": DEFAULT_MAX_HOLD_BARS,
    "use_stop": DEFAULT_USE_STOP,
}
