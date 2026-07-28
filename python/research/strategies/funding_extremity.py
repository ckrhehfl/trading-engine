"""Funding-rate-extremity contrarian strategy (Strategy Research Task N)
-- native-1h, BTC-USDT-perpetual-funding-rate-based signal, structurally
independent of every price/volume-based strategy in this package. See
CLAUDE.md's "Strategy Research Methodology" section and
`.planning/sr-n-funding-rate-strategy.md` for the full design context: six
price/volume-based strategy families have now been tried (Tasks E-L)
without clearing the Eligibility Bar; this is the first strategy in this
project built on a genuinely different signal class (perpetual funding
rate, a positioning/crowding proxy, not a price-trend or volume proxy).

## The signal: rolling funding-rate z-score, contrarian direction

Credible research linked in this project's earlier deep research pass
(see `.planning/sr-g-overfitting-safeguards.md` and CLAUDE.md's "Queued
next" note) associates BTC perpetual funding-rate extremes with crowded-
positioning unwinds: when funding is unusually high (longs paying shorts
heavily -- BingX's own documented convention, verified in
`metrics.position`'s docstring: `fundingRate > 0` means longs pay
shorts), the market reads as crowded LONG, and a contrarian SHORT is the
play; when funding is unusually low/negative (shorts paying heavily,
crowded SHORT), a contrarian LONG is the play.

**"Unusually high/low" is defined via a rolling, self-relative z-score,
not a fixed absolute threshold or a threshold derived from the full
multi-year history** -- a real empirical check (see
`.planning/sr-n-funding-rate-strategy.md`) across this project's full
cached BTC-USDT funding-rate history (6,199 real rows, 2020-11-29 through
present) found the annual standard deviation of funding rate itself
shifted by roughly **9x** across years (2021: ~4.05e-4; 2025: ~4.6e-5) --
a fixed absolute cutoff calibrated on one era would be badly miscalibrated
in another (either firing on routine noise or almost never firing). A
z-score, recomputed over only a trailing rolling window
(`RollingFundingZScore`, `DEFAULT_FUNDING_ZSCORE_LOOKBACK=90` real
settlements, ~30 days at funding's ~3x/day typical cadence -- not 90
hourly bars), adapts to whichever regime is currently in force, computed
look-ahead-safe from only settlements already known as of "now" -- the
same cursor-based look-ahead-safety design `metrics.position.
PositionTracker.apply_funding_through` already uses, reused here
deliberately rather than reinvented (see `RollingFundingZScore`'s
docstring).

A rolling **percentile-rank** threshold was also considered (and would
have been an equally defensible choice -- flagged explicitly as a
judgment call, not a claim of objective superiority): rejected in favor
of a z-score because (a) it keeps this module consistent with every
other indicator in this codebase, all of which use plain algebraic
rolling-mean/rolling-stdev formulas (`risk_management.AverageTrueRange`,
`regime_weighting.AverageDirectionalIndex`, `mean_reversion.
BollingerBands`, `volatility_targeting.RollingRealizedVolatility`), none
of which use order statistics; (b) a z-score is directly interpretable in
"how many standard deviations from its own recent normal" terms, matching
this project's existing threshold conventions (e.g. Bollinger Bands' own
`k * stdev` band width); (c) an empirical check of trade-opportunity
frequency across several candidate lookback windows (30/60/90/180/270
settlements) and thresholds (1.5/2.0/2.5 stdev) at this project's own 1h
research-window data found no meaningful difference in signal frequency
between them (roughly 150-160 raw extreme-reading settlements out of
2,464 total at a 2.0 threshold, regardless of lookback) -- so there was no
empirical reason found to prefer the more complex, order-statistic-based
alternative. See `.planning/sr-n-funding-rate-strategy.md` for the full
empirical table.

## Why NOT ADX regime weighting (a deliberate exclusion, not an oversight)

Every price-based strategy in this package (`ensemble_momentum.py`,
`single_lookback_momentum.py`, `mean_reversion.py`) uses `regime_weighting.
compute_regime_weight` (ADX-based) to scale conviction by trend strength
-- momentum strategies weight UP when ADX is high (trending), mean-
reversion weights UP when ADX is low (ranging). This strategy
deliberately does **not** use ADX at all. Two reasons, stated explicitly
per this task's own brief ("your call whether to include it, document
reasoning either way; don't force it in if it isn't clearly motivated"):

1. ADX measures price-trend strength -- a fundamentally different
   dimension from what this strategy's own signal measures (funding-rate/
   positioning extremity). There is no tested, evidenced basis in this
   project for which direction an ADX gate should even point for this
   signal: gating UP on high ADX (momentum's convention) would suppress
   this signal during calm periods and favor it during strong trends, but
   a strongly trending market is arguably where fading crowded positioning
   is *riskiest* (trend continuation risk working against the contrarian
   bet); gating UP on low ADX (mean-reversion's convention, since this is
   also structurally a reversion-style bet) would import mean-reversion's
   own untested-for-this-signal assumption wholesale. Neither has any
   evidence behind it specific to a funding-based signal.
2. Isolating this strategy to funding-only + risk management + volatility
   targeting (no price-trend filter at all) is what makes an honest
   evaluation of the funding-extremity signal *itself* possible -- adding
   an ADX gate on top would make it impossible to tell whether any
   observed result comes from the funding signal or from the price-trend
   filter riding along with it, the same "isolate one variable at a time"
   discipline this project's diagnosis tasks (sr-h, sr-i) already follow.

## Reused, unmodified, from sibling modules

- `research.strategies.risk_management`: `AverageTrueRange`, `OpenPosition`,
  `compute_stop_and_target`, `compute_position_size`, `check_exit_trigger`
  -- the identical ATR-based stop/target/sizing every strategy in this
  package uses, composed the same way.
- `research.strategies.volatility_targeting`: `RollingRealizedVolatility`,
  `compute_vol_scalar` -- the identical real volatility-targeting position
  scalar every momentum/mean-reversion strategy in this package uses.

`final_quantity = atr_sized_base_quantity * vol_scalar` -- one factor
lighter than `MeanReversionStrategy`'s composition (no regime-weight
factor, per the ADX exclusion above).

## Funding P&L: signal input AND P&L component, not just the latter

This strategy is the first in this project where the same underlying data
(the funding-rate series) plays two genuinely separate roles: (1) it
drives the entry *signal* (`RollingFundingZScore`, bound into
`FundingExtremityStrategy` at construction), and (2) once a position is
open, real funding payments/receipts accrue against it exactly like any
other strategy's position now can (`metrics.metrics.compute_metrics`'s
opt-in `funding_rates` parameter, Strategy Research Task M). These
compound in the SAME direction when the contrarian bet is right: a
correct contrarian short against high-positive funding both profits from
the (hoped-for) price unwind AND collects the funding payment itself
while held (short receives when funding is positive -- see
`metrics.position`'s sign-convention docstring) -- and compound against
each other when the bet is wrong. `FundingExtremityTrainable.fit()`
threads the SAME `funding_rates` series through to `compute_metrics` for
its own in-sample logging (not just the final walk-forward evaluation),
so even this strategy's in-sample figures reflect this compounding
honestly, not just the headline walk-forward numbers.

## Edge-triggering

Same "fire only when the signal STATE changes" convention as every other
strategy in this package (`_crossover_sign` in the momentum strategies,
`_signal_state` in `mean_reversion.py`) -- a new entry attempt requires
this bar's contrarian direction to differ from the *last established
nonzero direction*, not merely "z-score is beyond the threshold right
now" (which, since funding only actually changes ~3x/day while this
strategy is evaluated hourly, would otherwise refire on essentially every
bar during a sustained extreme reading). Includes the same
`entry_rejected_by_filters` fix `mean_reversion.py` already established
(CodeRabbit review finding on that task): the signal-state tracker is NOT
consumed when an entry was attempted but rejected purely by a downstream
filter (vol-scalar warmup, or `final_quantity <= 0`) -- doing so would
silently miss the entry once conditions later become favorable, unless
the raw z-score reading happens to change again first.

## Fold-boundary state seeding (Strategy Research Task O fix)

Strategy Research Task N's real 19-fold walk-forward run found only 7
trades fired despite 71 real crossings into an extreme funding reading --
diagnosed there as an edge-trigger design bottleneck, but not fixed at the
time (see `.planning/sr-n-funding-rate-strategy.md`'s "Diagnosing the
7-trade result"). Task O's own re-derivation from real cached data
(`.planning/sr-o-funding-fold-boundary-fix.md`) confirmed the PRECISE
mechanism -- and it is narrower than "the z-score is cold every fold":
`research.walkforward.run_walk_forward` builds a brand-new
`FundingExtremityStrategy` for every fold via
`FundingExtremityTrainable.fit()` (`_build_strategy()`), and that fresh
instance is evaluated ONLY against that fold's own `validate_klines` --
never `train_klines`. `RollingFundingZScore`'s own rolling window is
*not* actually cold at any fold boundary: because `funding_rates` is
always the full historical series (see that class's own docstring), its
internal cursor fast-forwards through real pre-fold history on the very
first `update()` call regardless of how "fresh" the instance is --
confirmed empirically across all 19 real folds, every one gets a real,
non-`None` z-score reading immediately. What IS genuinely cold every fold
is `_signal_state` (the edge-trigger tracker of "the last established
nonzero extremity direction"): a fresh `FundingExtremityStrategy` always
starts it at `None`, discarding real knowledge of whichever direction
funding was most recently extreme in before this fold began. This means
the FIRST crossing within every fold's 30-day validate window is always
wasted just re-establishing a baseline, and only a flip to the OPPOSITE
extreme within the REMAINING span of that SAME fold can ever fire a trade
-- and funding's demonstrated regime-persistence (sr-n) makes that rare.
Confirmed directly: at every one of the 19 real fold boundaries, a real,
well-defined, non-`None` "would-be" `_signal_state` exists from genuine
prior history -- today's code discards all 19 of them.

**Fix**: `initial_signal_state` (new constructor parameter, optional,
default `None` -- preserves byte-for-byte identical behavior for any
caller that doesn't pass it, e.g. every existing test in this module's
test suite) lets a caller seed `_signal_state` at construction time.
`compute_seed_signal_state` (module-level function, below) computes the
CORRECT value from real history: it replays `RollingFundingZScore`/
`_funding_signal` over `funding_rates` up to (and including) a
`known_through` cutoff, returning the last nonzero direction observed --
exactly what a strategy that had been running continuously since real
funding history began would have accumulated by that point, using
nothing but genuinely earlier real settlements (never fabricated, never
the fold's own future data). `FundingExtremityTrainable.fit()` calls this
once per fold: `train_klines[-1].open_time` (the last bar of THIS fold's
own train window) as `known_through` for the strategy it returns for real
`validate_klines` evaluation, and `train_klines[0].open_time` for the
separate in-sample `scoring_strategy` (so even that diagnostic-only
in-sample pass reflects genuine warmup, not a cold start, consistent with
this module's existing "honesty" precedent for in-sample logging -- see
"Funding P&L" section above). Threaded through via
`_build_strategy(initial_signal_state=...)`.

Look-ahead-safe by construction: `fit()` never receives `validate_klines`
at all (`TrainableStrategy.fit`'s own docstring already requires this --
"must not read `validate_klines` in any way"), so the seed can only ever
reflect data already known by the time evaluation begins. Whether the
`known_through` boundary treats an exactly-coincident settlement as
included or excluded does not matter in practice: any settlement at or
after a strategy's own first evaluated bar gets independently (and
identically) reprocessed by that strategy's real per-bar
`RollingFundingZScore.update()` call anyway, so the seed and the real
evaluation loop always converge on the same `_signal_state` for that
settlement regardless of which side of the boundary it's counted on --
verified directly by a dedicated look-ahead-safety test proving a
settlement timed after `train_klines[-1].open_time` never changes the
seed `fit()` computes for the strategy it returns.

## No grid search (brand-new, not-yet-evaluated signal)

Same judgment call as `mean_reversion.MeanReversionTrainable` (Task K):
every constant (`funding_zscore_lookback`, `entry_z_threshold`, ATR/vol
periods, stop/target multipliers, vol-target level/bounds, risk fraction)
is fixed at `FundingExtremityTrainable` construction time -- no opt-in
grid search like `ensemble_momentum.py`'s Task I risk:reward search.
Adding a second tunable dimension before this genuinely new signal family
has even been evaluated once would add search surface (and MinBTL-style
overfitting risk, `research.overfitting_check`) ahead of any evidence a
search is warranted.
"""

from collections import deque
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any
from uuid import uuid4

from backtest.engine import Strategy, run_backtest
from backtest.kline import Kline
from metrics.funding import FundingRate
from metrics.metrics import Metrics, compute_metrics
from research import experiment_log
from research.strategies.risk_management import (
    DEFAULT_ATR_PERIOD,
    DEFAULT_REFERENCE_EQUITY,
    DEFAULT_RISK_FRACTION,
    DEFAULT_STOP_MULTIPLIER,
    DEFAULT_TARGET_MULTIPLIER,
    AverageTrueRange,
    OpenPosition,
    check_exit_trigger,
    compute_position_size,
    compute_stop_and_target,
)
from research.strategies.volatility_targeting import (
    DEFAULT_MAX_VOL_SCALAR,
    DEFAULT_MIN_VOL_SCALAR,
    DEFAULT_TARGET_ANNUALIZED_VOL,
    DEFAULT_VOL_LOOKBACK_PERIOD,
    RollingRealizedVolatility,
    compute_vol_scalar,
)
from schemas.order_intent import OrderIntent, OrderType, Side

# Single-symbol/native-1h conventions matching every sibling strategy
# module in this package.
DEFAULT_SYMBOL = "BTC-USDT"
DEFAULT_STARTING_EQUITY = Decimal("10000")
DEFAULT_BARS_PER_DAY = 24

# ~30 days at funding's typical ~3x/day (8h) cadence -- see module
# docstring for the empirical basis (a fixed lookback in REAL SETTLEMENTS,
# not hourly bars, since most hourly bars fall between settlements and
# carry no new funding information at all).
DEFAULT_FUNDING_ZSCORE_LOOKBACK = 90

# 2 standard deviations -- a common, simple "unusual" cutoff (not
# searched/tuned to this asset), consistent with this project's other
# stdev-based thresholds (`mean_reversion.DEFAULT_BOLLINGER_K=2`). See
# module docstring's empirical frequency check for why this specific value
# was not found to matter much relative to nearby alternatives.
DEFAULT_ENTRY_Z_THRESHOLD = Decimal("2")


class RollingFundingZScore:
    """Incremental, look-ahead-safe rolling z-score of the most recently
    settled BTC-USDT funding rate, computed over the trailing
    `lookback_settlements` REAL SETTLEMENTS (not hourly bars -- funding
    settles roughly 3x/day; see `data/bingx_funding.py`'s module
    docstring), fed one kline's `open_time` per call via `update()`.

    `funding_rates` is expected to be the FULL known series (ascending by
    `funding_time`), not pre-sliced to whatever kline window this
    instance will actually be evaluated against -- passing extra rows
    beyond what `update()` will ever consume (settlements at/after the
    latest `current_open_time` ever passed in) is harmless by
    construction: the internal cursor only ever advances past rows with
    `funding_time <= current_open_time`, and never reads ahead of it. This
    mirrors `metrics.position.PositionTracker`'s own cursor-based
    `apply_funding_through` design exactly (see that module's docstring)
    -- an already-established, already-tested look-ahead-safety pattern
    in this codebase, deliberately reused here rather than reinvented.
    Because real funding-rate history on BingX goes back materially
    further than any kline granularity this project has backfilled
    (~5.7 years vs. ~2.2 years for 1h klines, per CLAUDE.md's "Exchange
    API Facts"), a caller can and should pass a `funding_rates` series
    that starts well before the kline data this will actually be
    evaluated against, so this indicator's own `lookback_settlements`
    warmup is already satisfied by real historical data before the first
    kline bar of interest, rather than needing its own multi-week warmup
    period the way every price-based indicator in this codebase does.

    `update()` returns `None` (warmup -- same "no evidence yet" convention
    as every other rolling-window indicator in this codebase) until at
    least `lookback_settlements` real settlements have been consumed.
    Between settlements (`update()` called once per hourly bar, but
    funding only actually changes on a real settlement), the most
    recently computed z-score is cached and returned unchanged --
    recomputing mean/stdev only when the cursor actually advances (the
    window's contents, and therefore the z-score, cannot have changed
    otherwise).
    """

    __slots__ = ("_funding_rates", "_lookback", "_window", "_cursor", "_cached_zscore")

    def __init__(
        self,
        funding_rates: Sequence[FundingRate],
        lookback_settlements: int = DEFAULT_FUNDING_ZSCORE_LOOKBACK,
    ) -> None:
        if lookback_settlements < 2:
            raise ValueError(
                f"lookback_settlements must be at least 2 (need >=2 samples for a sample "
                f"stdev), got {lookback_settlements}"
            )
        for previous, current in pairwise(funding_rates):
            if current.funding_time < previous.funding_time:
                raise ValueError(
                    "funding_rates must be sorted ascending by funding_time "
                    f"(found {current.funding_time} after {previous.funding_time})"
                )
        self._funding_rates = funding_rates
        self._lookback = lookback_settlements
        self._window: deque[Decimal] = deque(maxlen=lookback_settlements)
        self._cursor = 0
        self._cached_zscore: Decimal | None = None

    @property
    def lookback_settlements(self) -> int:
        return self._lookback

    def update(self, current_open_time: datetime) -> Decimal | None:
        advanced = False
        n = len(self._funding_rates)
        while self._cursor < n and self._funding_rates[self._cursor].funding_time <= current_open_time:
            self._window.append(self._funding_rates[self._cursor].funding_rate)
            self._cursor += 1
            advanced = True

        if len(self._window) < self._lookback:
            return None

        if advanced:
            values = list(self._window)
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
            stdev = variance.sqrt()
            self._cached_zscore = Decimal(0) if stdev == 0 else (values[-1] - mean) / stdev

        return self._cached_zscore


def _funding_signal(zscore: Decimal, entry_threshold: Decimal) -> int:
    """Contrarian direction from a funding-rate z-score -- the easy-to-
    get-backwards sign logic this module's own tests verify explicitly
    (see module docstring's "The signal" section for the full reasoning).

    `zscore >= entry_threshold`: funding is UNUSUALLY HIGH relative to its
    own recent history (longs paying heavily -- crowded LONG) -> the
    contrarian play is SHORT (`-1`).
    `zscore <= -entry_threshold`: funding is unusually LOW/negative
    relative to its own recent history (shorts paying -- crowded SHORT)
    -> the contrarian play is LONG (`+1`).
    Otherwise: `0` (no signal). Both boundaries are inclusive (`>=`/`<=`),
    consistent with this project's other inclusive threshold conventions
    (e.g. `research.eligibility.evaluate_fold_consistency`'s `>=`).
    """
    if entry_threshold <= 0:
        raise ValueError(f"entry_threshold must be positive, got {entry_threshold}")
    if zscore >= entry_threshold:
        return -1
    if zscore <= -entry_threshold:
        return 1
    return 0


def compute_seed_signal_state(
    funding_rates: Sequence[FundingRate],
    *,
    lookback_settlements: int,
    entry_z_threshold: Decimal,
    known_through: datetime,
) -> int | None:
    """Reconstruct the `_signal_state` a continuously-running
    `FundingExtremityStrategy` would have accumulated by `known_through`,
    from real history alone -- see module docstring's "Fold-boundary state
    seeding" section for why this exists (Strategy Research Task O, fixing
    the mechanical bug Task N diagnosed but did not fix).

    Replays `RollingFundingZScore`/`_funding_signal` over `funding_rates`
    (which may extend arbitrarily far beyond `known_through` -- e.g. the
    full historical series, per this module's established convention),
    consuming only settlements with `funding_time <= known_through`, and
    returns the LAST nonzero `_funding_signal` reading observed -- `None`
    if the rolling window never warms up (fewer than `lookback_settlements`
    real settlements at/before `known_through`) or no settlement ever
    crossed `entry_z_threshold` in that span.

    Look-ahead-safe by construction: a settlement with `funding_time >
    known_through` is never consumed, regardless of how far `funding_rates`
    extends beyond it (mirrors `RollingFundingZScore.update`'s own cursor
    design -- see that class's docstring).
    """
    zscore_calc = RollingFundingZScore(funding_rates, lookback_settlements=lookback_settlements)
    last_nonzero: int | None = None
    for rate in funding_rates:
        if rate.funding_time > known_through:
            break
        zscore = zscore_calc.update(rate.funding_time)
        if zscore is not None:
            signal = _funding_signal(zscore, entry_z_threshold)
            if signal != 0:
                last_nonzero = signal
    return last_nonzero


class FundingExtremityStrategy:
    """Bound, stateful `Strategy`: rolling funding-rate-z-score contrarian
    signal, edge-triggered, plus ATR-based stop/target exits and real
    volatility targeting. No ADX regime weighting -- see module docstring.
    """

    __slots__ = (
        "_funding_zscore",
        "_entry_z_threshold",
        "_symbol",
        "_target_annualized_vol",
        "_min_vol_scalar",
        "_max_vol_scalar",
        "_stop_multiplier",
        "_target_multiplier",
        "_reference_equity",
        "_risk_fraction",
        "_signal_state",
        "_atr",
        "_vol",
        "_position",
    )

    def __init__(
        self,
        *,
        symbol: str,
        funding_rates: Sequence[FundingRate],
        funding_zscore_lookback: int = DEFAULT_FUNDING_ZSCORE_LOOKBACK,
        entry_z_threshold: Decimal = DEFAULT_ENTRY_Z_THRESHOLD,
        atr_period: int = DEFAULT_ATR_PERIOD,
        stop_multiplier: Decimal = DEFAULT_STOP_MULTIPLIER,
        target_multiplier: Decimal = DEFAULT_TARGET_MULTIPLIER,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        risk_fraction: Decimal = DEFAULT_RISK_FRACTION,
        vol_period: int = DEFAULT_VOL_LOOKBACK_PERIOD,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        target_annualized_vol: Decimal = DEFAULT_TARGET_ANNUALIZED_VOL,
        min_vol_scalar: Decimal = DEFAULT_MIN_VOL_SCALAR,
        max_vol_scalar: Decimal = DEFAULT_MAX_VOL_SCALAR,
        initial_signal_state: int | None = None,
    ) -> None:
        if entry_z_threshold <= 0:
            raise ValueError(f"entry_z_threshold must be positive, got {entry_z_threshold}")

        self._funding_zscore = RollingFundingZScore(funding_rates, lookback_settlements=funding_zscore_lookback)
        self._entry_z_threshold = entry_z_threshold
        self._symbol = symbol
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar
        self._stop_multiplier = stop_multiplier
        self._target_multiplier = target_multiplier
        self._reference_equity = reference_equity
        self._risk_fraction = risk_fraction

        # Defaults to None (a fresh, un-established baseline) -- the
        # ORIGINAL, still-correct behavior for a strategy with no known
        # prior history. A caller with real pre-fold funding history (see
        # `compute_seed_signal_state` and module docstring's "Fold-boundary
        # state seeding" section) may seed this instead, so this instance
        # doesn't have to rediscover a direction the market was already in
        # before this instance was constructed.
        self._signal_state: int | None = initial_signal_state
        self._atr = AverageTrueRange(period=atr_period)
        self._vol = RollingRealizedVolatility(period=vol_period, bars_per_day=bars_per_day)
        self._position: OpenPosition | None = None

    @property
    def funding_zscore_lookback(self) -> int:
        return self._funding_zscore.lookback_settlements

    @property
    def entry_z_threshold(self) -> Decimal:
        return self._entry_z_threshold

    @property
    def stop_multiplier(self) -> Decimal:
        return self._stop_multiplier

    @property
    def target_multiplier(self) -> Decimal:
        return self._target_multiplier

    @property
    def open_position(self) -> OpenPosition | None:
        return self._position

    @property
    def signal_state(self) -> int | None:
        """The last established nonzero extremity direction (`+1`/`-1`), or
        `None` if none has been observed yet -- exposed read-only, same
        introspection convention as `open_position`, primarily for testing
        `initial_signal_state` seeding (see module docstring's "Fold-
        boundary state seeding" section).
        """
        return self._signal_state

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]

        atr = self._atr.update(current)
        realized_vol = self._vol.update(current)
        zscore = self._funding_zscore.update(current.open_time)

        current_signal = 0
        if zscore is not None:
            current_signal = _funding_signal(zscore, self._entry_z_threshold)

        intent: OrderIntent | None = None
        entry_rejected_by_filters = False

        if self._position is not None:
            trigger = check_exit_trigger(self._position, current)
            if trigger is not None:
                intent = self._flatten(current)
        elif zscore is not None:
            previous_signal = self._signal_state
            if previous_signal is not None and current_signal != 0 and current_signal != previous_signal:
                if atr is None or atr <= 0:
                    # ATR warmup (or a degenerate atr<=0 reading) is a
                    # downstream filter too -- same treatment as the
                    # vol-scalar/final_quantity<=0 filters below, and for
                    # the same reason: this transition must not be
                    # silently consumed just because ATR happened not to
                    # be ready yet (a real CodeRabbit review finding on
                    # this task's PR -- see module docstring's "Edge-
                    # triggering" section).
                    entry_rejected_by_filters = True
                else:
                    side = Side.LONG if current_signal > 0 else Side.SHORT
                    intent = self._open(current, side, atr, realized_vol)
                    entry_rejected_by_filters = intent is None

        # See module docstring's "Edge-triggering" section: do not consume
        # the edge-trigger state when an entry was attempted but rejected
        # purely by a downstream filter (vol-scalar warmup or
        # final_quantity <= 0) -- otherwise a signal that fires but gets
        # filtered out would be silently lost until the raw z-score
        # changes again. Every other case (still in a position, or no
        # prior state existed yet) keeps the unconditional update.
        if current_signal != 0 and not entry_rejected_by_filters:
            self._signal_state = current_signal

        return intent

    def _flatten(self, current: Kline) -> OrderIntent:
        position = self._position
        assert position is not None
        self._position = None
        closing_side = Side.SHORT if position.side == Side.LONG else Side.LONG
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=closing_side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=position.quantity,
            limit_price=None,
            signal_timeframe="1h",
            created_at=current.open_time,
        )

    def _open(
        self,
        current: Kline,
        side: Side,
        atr: Decimal,
        realized_vol: Decimal | None,
    ) -> OrderIntent | None:
        entry_price = current.close
        stop_price, target_price = compute_stop_and_target(
            entry_price=entry_price,
            atr=atr,
            side=side,
            stop_multiplier=self._stop_multiplier,
            target_multiplier=self._target_multiplier,
        )
        base_quantity = compute_position_size(
            entry_price=entry_price,
            stop_price=stop_price,
            reference_equity=self._reference_equity,
            risk_fraction=self._risk_fraction,
        )
        if base_quantity is None:
            return None

        vol_scalar = compute_vol_scalar(
            realized_vol,
            target_annualized_vol=self._target_annualized_vol,
            min_scalar=self._min_vol_scalar,
            max_scalar=self._max_vol_scalar,
        )
        if vol_scalar is None:
            return None

        final_quantity = base_quantity * vol_scalar
        if final_quantity <= 0:
            return None

        self._position = OpenPosition(
            side=side,
            quantity=final_quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
        )
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=final_quantity,
            limit_price=None,
            signal_timeframe="1h",
            created_at=current.open_time,
        )


def _data_range(klines: Sequence[Kline]) -> dict:
    if not klines:
        return {"start_ms": None, "end_ms": None, "num_bars": 0}
    return {
        "start_ms": int(klines[0].open_time.timestamp() * 1000),
        "end_ms": int(klines[-1].open_time.timestamp() * 1000),
        "num_bars": len(klines),
    }


def _metrics_summary(metrics: Metrics) -> dict:
    return {
        "starting_equity": metrics.starting_equity,
        "final_equity": metrics.final_equity,
        "total_return": metrics.total_return,
        "sharpe_ratio": metrics.sharpe_ratio,
        "max_drawdown": metrics.max_drawdown,
        "win_rate": metrics.win_rate,
        "num_trades": metrics.num_trades,
        "profit_factor": metrics.profit_factor,
    }


class FundingExtremityTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `FundingExtremityStrategy`.

    `fit()` grid-searches nothing -- every constant is fixed at
    `FundingExtremityTrainable` construction time, matching
    `mean_reversion.MeanReversionTrainable`'s precedent for a brand-new,
    not-yet-evaluated signal family (see module docstring's "No grid
    search" section).

    `funding_rates` is REQUIRED (no default -- unlike every other
    constructor parameter here, there is no sensible default for a
    strategy whose entire signal depends on it) and is threaded through
    to both (a) every `FundingExtremityStrategy` this builds (as the
    signal input) and (b) every `compute_metrics` call this makes for its
    own in-sample logging (as the P&L input) -- see module docstring's
    "Funding P&L: signal input AND P&L component" section for why both
    matter. The SAME series object is reused for every fold's `fit()`
    call and for the strategy ultimately evaluated against each fold's
    validate window -- safe by construction (see `RollingFundingZScore`'s
    docstring): passing a series that extends beyond any particular
    fold's own klines is harmless, since both the signal cursor and
    `metrics.position.PositionTracker`'s funding cursor only ever consume
    rows up to the current kline's own `open_time`.
    """

    def __init__(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        fee_bps: Decimal,
        slippage_bps: Decimal,
        funding_rates: Sequence[FundingRate],
        starting_equity: Decimal = DEFAULT_STARTING_EQUITY,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        funding_zscore_lookback: int = DEFAULT_FUNDING_ZSCORE_LOOKBACK,
        entry_z_threshold: Decimal = DEFAULT_ENTRY_Z_THRESHOLD,
        atr_period: int = DEFAULT_ATR_PERIOD,
        stop_multiplier: Decimal = DEFAULT_STOP_MULTIPLIER,
        target_multiplier: Decimal = DEFAULT_TARGET_MULTIPLIER,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        risk_fraction: Decimal = DEFAULT_RISK_FRACTION,
        vol_period: int = DEFAULT_VOL_LOOKBACK_PERIOD,
        target_annualized_vol: Decimal = DEFAULT_TARGET_ANNUALIZED_VOL,
        min_vol_scalar: Decimal = DEFAULT_MIN_VOL_SCALAR,
        max_vol_scalar: Decimal = DEFAULT_MAX_VOL_SCALAR,
        runs_path: str = experiment_log.DEFAULT_RUNS_PATH,
    ) -> None:
        self._strategy_id = strategy_id
        self._strategy_version = strategy_version
        self._fee_bps = fee_bps
        self._slippage_bps = slippage_bps
        self._funding_rates = funding_rates
        self._starting_equity = starting_equity
        self._bars_per_day = bars_per_day
        self._funding_zscore_lookback = funding_zscore_lookback
        self._entry_z_threshold = entry_z_threshold
        self._atr_period = atr_period
        self._stop_multiplier = stop_multiplier
        self._target_multiplier = target_multiplier
        self._reference_equity = reference_equity
        self._risk_fraction = risk_fraction
        self._vol_period = vol_period
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar
        self._runs_path = runs_path

    def fit(self, train_klines: list[Kline], params: Mapping[str, Any], *, parent_run_id: str) -> Strategy:
        symbol = params.get("symbol", DEFAULT_SYMBOL)

        # Strategy Research Task O fix -- see module docstring's "Fold-
        # boundary state seeding" section for the full mechanism and why
        # each of these two calls uses a different `known_through` cutoff:
        # the scoring pass below walks train_klines from its own start, so
        # it must only know about history strictly up to (not into)
        # train's own beginning; the returned strategy is evaluated
        # against validate_klines (which begins immediately after
        # train_klines ends), so it may know about everything through
        # train's own last bar. Both are look-ahead-safe: fit() never
        # receives validate_klines, so neither seed can reflect anything
        # beyond real, already-known history.
        scoring_seed = self._seed_signal_state(train_klines[0].open_time) if train_klines else None
        scoring_strategy = self._build_strategy(symbol=symbol, initial_signal_state=scoring_seed)
        backtest_result = run_backtest(train_klines, scoring_strategy, self._fee_bps, self._slippage_bps)
        metrics = compute_metrics(
            train_klines,
            backtest_result.filled_intents,
            backtest_result.fills,
            self._starting_equity,
            bars_per_day=self._bars_per_day,
            funding_rates=self._funding_rates,
        )
        self._log_candidate(symbol=symbol, train_klines=train_klines, metrics=metrics, parent_run_id=parent_run_id)

        validate_seed = self._seed_signal_state(train_klines[-1].open_time) if train_klines else None
        return self._build_strategy(symbol=symbol, initial_signal_state=validate_seed)

    def _seed_signal_state(self, known_through: datetime) -> int | None:
        return compute_seed_signal_state(
            self._funding_rates,
            lookback_settlements=self._funding_zscore_lookback,
            entry_z_threshold=self._entry_z_threshold,
            known_through=known_through,
        )

    def _build_strategy(self, *, symbol: str, initial_signal_state: int | None = None) -> FundingExtremityStrategy:
        return FundingExtremityStrategy(
            symbol=symbol,
            funding_rates=self._funding_rates,
            funding_zscore_lookback=self._funding_zscore_lookback,
            entry_z_threshold=self._entry_z_threshold,
            atr_period=self._atr_period,
            stop_multiplier=self._stop_multiplier,
            target_multiplier=self._target_multiplier,
            reference_equity=self._reference_equity,
            risk_fraction=self._risk_fraction,
            vol_period=self._vol_period,
            bars_per_day=self._bars_per_day,
            target_annualized_vol=self._target_annualized_vol,
            min_vol_scalar=self._min_vol_scalar,
            max_vol_scalar=self._max_vol_scalar,
            initial_signal_state=initial_signal_state,
        )

    def _log_candidate(
        self,
        *,
        symbol: str,
        train_klines: list[Kline],
        metrics: Metrics,
        parent_run_id: str,
    ) -> None:
        metrics_summary = _metrics_summary(metrics)
        experiment_log.log_run(
            run_id=str(uuid4()),
            strategy_id=self._strategy_id,
            strategy_version=self._strategy_version,
            params={
                "symbol": symbol,
                "funding_zscore_lookback": self._funding_zscore_lookback,
                "entry_z_threshold": str(self._entry_z_threshold),
                "atr_period": self._atr_period,
                "stop_multiplier": str(self._stop_multiplier),
                "target_multiplier": str(self._target_multiplier),
                "reference_equity": str(self._reference_equity),
                "risk_fraction": str(self._risk_fraction),
                "vol_period": self._vol_period,
                "target_annualized_vol": str(self._target_annualized_vol),
                "min_vol_scalar": str(self._min_vol_scalar),
                "max_vol_scalar": str(self._max_vol_scalar),
            },
            fold_results=[
                {
                    "fold_index": 0,
                    "train_start_index": 0,
                    "train_end_index": len(train_klines),
                    "validate_start_index": 0,
                    "validate_end_index": len(train_klines),
                    "metrics": metrics_summary,
                }
            ],
            aggregate_metrics=metrics_summary,
            data_range=_data_range(train_klines),
            walk_forward_config={
                "train_bars": len(train_klines),
                "validate_bars": 0,
                "step_bars": 0,
                "fold_count": 0,
                "note": (
                    "in-sample scoring inside FundingExtremityTrainable.fit() -- the single "
                    "fixed funding-extremity strategy, no grid search -- not itself a "
                    "walk-forward run"
                ),
            },
            fee_bps=self._fee_bps,
            slippage_bps=self._slippage_bps,
            is_holdout_run=False,
            parent_run_id=parent_run_id,
            candidate_index=0,
            total_candidates=1,
            runs_path=self._runs_path,
        )
