"""Macro-conditioned BTC strategy: 10-year real-yield (DFII10) trend,
INVERTED -- Strategy Research Task X. See CLAUDE.md's "Strategy Research
Methodology" section and `.planning/sr-x-macro-real-yield-strategy.md`
for the full design context: after the BTC-only price-signal research
program ended INCONCLUSIVE (`sr-v`), CLAUDE.md names two live remedies --
multi-symbol expansion, or a genuinely different data source. This module
is the first attempt at the second: a hypothesis about a real
macroeconomic variable (the 10-year Treasury Inflation-Protected
Securities yield, `DFII10` -- a genuinely different generating system,
Fed/Treasury rather than BTC's own price/volume/funding), not a
BTC-price-derived signal at all.

## The hypothesis, and why it is INVERTED

A prior deep research pass (cited in the governing task's own brief)
found real 10-year yields have "a relatively more robust (inverse)
relationship" with BTC than gold/inflation: **falling real yields ->
risk-on -> historically BTC-bullish**, and rising real yields the
reverse. This is INVERTED relative to a naive momentum reading of the
yield series itself -- a *negative* real-yield trend maps to a *long*
BTC signal, and a *positive* trend maps to *short*. Getting this sign
backwards would silently invert the entire hypothesis while still
"running" without error, which is exactly why
`TestInversionAndDirection` in this module's test suite pins both
directions explicitly rather than trusting the arithmetic by inspection.

## The signal: a single, pre-committed lookback, zero search

At each daily BTC bar, look at DFII10's own trend over the trailing
`DEFAULT_LOOKBACK_TRADING_DAYS` REAL (non-holiday, non-weekend) FRED
observations -- i.e. `L` steps back in the sequence of DFII10's actual
business-day readings, not `L` calendar days (`compute_real_yield_trend_
signs` below). `sign(value[t] - value[t-L])` is computed once, directly
on the raw business-day series (skipping `None`/holiday rows as
non-events rather than treating them as zero-change steps), and the
resulting trend-sign series is then forward-filled onto every BTC
calendar bar via `research.macro_alignment` (look-ahead-safe by
construction -- see that module's docstring; reused here rather than
duplicated, since aligning a *trend* series onto BTC bars is exactly the
same "most recent real value dated at/before this bar" problem as
aligning the raw DFII10 *level* would be -- the alignment step doesn't
care what the values it's forward-filling actually represent).

## Lookback choice: 63 trading days, not 21, 126, or 252

**Deliberately minimal free parameters** -- this project's single
most important design lesson, learned the hard way across eight prior
strategy families (CLAUDE.md's Strategy Research Methodology: "the
simplest, zero-fitted-parameter strategy came closest to passing; every
strategy with more knobs did worse"). `fit()` below performs **no
search whatsoever** (`total_candidates: 1`), matching
`daily_tsmom_ensemble.py`'s own zero-fitted-parameter discipline
exactly.

`63` (approximately one calendar quarter of trading days) is the middle
value of the Moskowitz-Ooi-Pedersen (2012) 21/63/126/252 lookback set
this project's own `daily_tsmom_ensemble.py` already uses for BTC price
momentum -- reused here as a defensible, literature-anchored anchor point
rather than a fresh number invented for this task (per the governing
task's own suggestion). Chosen over the other three MOP lookbacks for a
reason specific to what is being measured, not merely "borrowed because
it's already in the codebase":

- **21 trading days (~1 month) is too short for a macro-regime
  signal.** Real yields move on Fed-policy and macro-data-release
  cadences that unfold over months, not weeks; a 1-month lookback risks
  reacting to short-term noise in the yield itself (auction-driven
  wobbles, single-data-point overreactions) rather than a genuine
  regime shift.
- **126/252 trading days (~6/12 months) risk lagging a real turning
  point too far** -- by the time a 6-12-month-old comparison point
  finally registers a sign flip, a real regime shift is already old
  news, which cuts against the whole premise of trading a *leading*
  macro signal rather than a coincident one.
- **63 (~1 quarter) is the middle ground**: long enough to filter
  short-term yield noise, short enough to still register a genuine
  multi-month regime shift (e.g. a Fed pivot) within a plausibly
  tradeable window.

This is a documented, defensible, PRE-COMMITTED reasoning chain, not a
result of having looked at how any lookback performed on this project's
own BTC data -- no BTC price data and no DFII10 value were consulted
while choosing it (see `.planning/sr-x-macro-real-yield-strategy.md` for
the explicit confirmation this project's own `sr-t`/`sr-u` precedent
established as necessary).

## Sizing: constant-target volatility only

Reuses `research.strategies.volatility_targeting`
(`RollingRealizedVolatility`, `compute_vol_scalar`) **unmodified**, this
project's standing 20%-annualized-vol-target convention, same as every
prior strategy including `daily_tsmom_ensemble.py`:

    base_quantity   = reference_equity / entry_price
    target_quantity = base_quantity * vol_scalar

**Deliberately no `abs(signal)` conviction-strength multiplier** --
unlike `daily_tsmom_ensemble.py`'s ensemble (whose `abs(ensemble_value)`
ranges over 9 possible magnitudes because it averages 4 independent
lookback signs), this strategy's signal is a single binary directional
call (`{-1, 0, +1}`, never a fractional value): once non-flat,
`abs(signal)` is always exactly `1`, so multiplying by it would be a
no-op carried only for cosmetic symmetry. Omitted rather than kept as
dead ceremony.

## Order emission: only-on-sign-change (same Option B precedent)

Same edge-triggered convention `daily_tsmom_ensemble.py` established for
this project's daily strategies (itself following `ensemble_momentum.py`/
`hourly_momentum.py`/`funding_extremity.py`'s precedent): a position,
once opened, holds the quantity computed at the moment it was opened
until the signal's own SIGN category changes again (to the opposite
sign, or to flat) -- never resized purely because the underlying real
yield kept moving in the same direction. A direct sign reversal (e.g.
`+1` straight to `-1`) is expressed as one `OrderIntent` combining the
closing quantity and the new target quantity, exactly as
`daily_tsmom_ensemble.py`'s own module docstring describes (reusing the
same already-tested `metrics.position.PositionTracker.
_reduce_or_close_or_flip` machinery).

If the new target cannot be sized (volatility-targeting warmup, or a
degenerate `entry_price <= 0`) at the moment a transition is due, this
module still flattens any stale position -- the signal that justified
holding it has already gone away -- and leaves the tracked position sign
at `0` (flat) so the very next bar retries the transition automatically
once sizing succeeds. Identical retry convention to every sibling
strategy in this package.

## Zero flat-vs-short choice for an exact-zero trend: FLAT

An exact-zero trend reading (the current real yield exactly equal to its
value 63 trading days ago) is a genuine tie, not meaningful evidence in
either direction. This strategy goes FLAT on a zero signal, matching
every sibling strategy's own zero-sign convention in this codebase
(`ensemble_momentum.py`'s net-sign tie, `daily_tsmom_ensemble.py`'s
zero-average ensemble case, `mean_reversion.py`'s neutral z-score band) --
"no clear signal, stay out" rather than an arbitrary directional
tie-break. Documented here explicitly per the governing task's own
instruction to state and justify this choice rather than leave it
implicit.

## Deliberately absent (a clean, standalone test of the macro signal alone)

No ADX regime gate, no ATR stop/target, no risk:reward grid, no funding
signal, and critically **no combination with any BTC-price-derived
momentum signal** -- per the governing task's own instruction, combining
this with a price signal now would make a disappointing result
ambiguous (is the macro signal weak, or is the combination logic at
fault?). This project's own established discipline tests each signal
type standalone before ever blending one (see `sr-e` before `sr-k`'s
blend); this module follows that discipline for the first genuinely
non-price-derived signal this project has ever tested.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import uuid4

from backtest.engine import Strategy, run_backtest
from backtest.kline import Kline
from data.fred_client import ObservationRow
from metrics.metrics import Metrics, compute_metrics
from research import experiment_log
from research.macro_alignment import MacroSeriesCursor
from research.strategies.volatility_targeting import (
    DEFAULT_MAX_VOL_SCALAR,
    DEFAULT_MIN_VOL_SCALAR,
    DEFAULT_TARGET_ANNUALIZED_VOL,
    DEFAULT_VOL_LOOKBACK_PERIOD,
    RollingRealizedVolatility,
    compute_vol_scalar,
)
from schemas.order_intent import OrderIntent, OrderType, Side

# Single-symbol/native-1d conventions matching every sibling strategy
# module in this package (see `daily_tsmom_ensemble.py`).
DEFAULT_SYMBOL = "BTC-USDT"
DEFAULT_STARTING_EQUITY = Decimal("10000")

# The full-notional sizing baseline for constant-target-vol sizing (see
# module docstring): base_quantity = reference_equity / entry_price. Same
# magnitude convention as `daily_tsmom_ensemble.DEFAULT_REFERENCE_EQUITY`
# / `risk_management.DEFAULT_REFERENCE_EQUITY`.
DEFAULT_REFERENCE_EQUITY = Decimal("10000")

# This strategy's native timeframe is 1d (1 bar/day), same as
# daily_tsmom_ensemble.py -- every compute_metrics/RollingRealizedVolatility
# call in this module must pass this explicitly, or a reported Sharpe/
# annualized-vol figure would be silently inflated by the 15m/1h
# assumption's much larger sqrt(bars_per_day * 365) factor.
DEFAULT_BARS_PER_DAY = 1

# 63 trading days -- see module docstring's "Lookback choice" section for
# the full reasoning (the middle value of daily_tsmom_ensemble.py's own
# MOP-2012 21/63/126/252 set, chosen for reasons specific to a macro
# regime signal, not merely borrowed).
DEFAULT_LOOKBACK_TRADING_DAYS = 63

# The FRED series this strategy is conditioned on: 10-Year Treasury
# Inflation-Indexed Security, Constant Maturity (the real yield). See
# `.planning/sr-x-macro-real-yield-strategy.md` for the real backfill
# this module depends on (`data/backfill_macro.py`'s `SERIES_START_DATE`).
DFII10_SERIES_ID = "DFII10"


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def compute_real_yield_trend_signs(
    observations: Sequence[ObservationRow], *, lookback: int
) -> list[ObservationRow]:
    """Given a raw FRED observation series (which may contain `None`
    rows -- FRED's own `"."` marker for a real market holiday, see
    `fred_client.py`'s module docstring), return a NEW `ObservationRow`
    series -- one entry per date that had a REAL (non-`None`) FRED
    observation, starting from the `lookback`'th such date -- whose
    `value` is the SIGN (`Decimal(-1)`/`Decimal(0)`/`Decimal(1)`) of the
    change in the real value over the trailing `lookback` REAL
    observations.

    `lookback` counts steps in the sequence of REAL values only -- a
    `None`/holiday row contributes no step at all (it is simply absent
    from the domain this function ever looks at), so this is genuinely
    "L TRADING days," not "L calendar days" and not "L calendar days
    with a holiday counted as a zero-change step." Weekends never appear
    in `observations` at all (FRED returns no row for them -- see
    `fred_client.py`'s module docstring), so they are automatically
    excluded the same way.

    This function only computes the trend value AT each of DFII10's own
    real observation dates -- it does NOT align the result onto BTC's
    calendar-day bars. That is a deliberately separate, later step
    (`research.macro_alignment.forward_fill_macro_series`/
    `MacroSeriesCursor`), reusing this project's one look-ahead-safe
    alignment mechanism rather than inventing a second, subtly different
    one just for trend values.

    `observations` is sorted defensively by `observation_date` first (see
    `MacroSeriesCursor`'s identical defensive-sort reasoning). Raises
    `ValueError` for a non-positive `lookback` -- there is no sensible
    trend to compute over zero or a negative number of steps.
    """
    if lookback <= 0:
        raise ValueError(f"lookback must be positive, got {lookback}")

    real_observations = sorted(
        (row for row in observations if row.value is not None),
        key=lambda row: row.observation_date,
    )

    trend_rows: list[ObservationRow] = []
    for i in range(lookback, len(real_observations)):
        current_row = real_observations[i]
        lagged_row = real_observations[i - lookback]
        trend_sign = _sign(current_row.value - lagged_row.value)
        trend_rows.append(ObservationRow(observation_date=current_row.observation_date, value=Decimal(trend_sign)))
    return trend_rows


class MacroRealYieldTrendStrategy:
    """Bound, stateful `Strategy`: the INVERTED DFII10-trend signal (see
    module docstring), constant-target-volatility sizing, edge-triggered
    on sign-category changes only (Option B -- same precedent as
    `daily_tsmom_ensemble.DailyTsmomEnsembleStrategy`). No ATR stop/
    target, no regime gate: a position is held until the trend's own sign
    flips or nets to exactly zero.
    """

    __slots__ = (
        "_symbol",
        "_reference_equity",
        "_target_annualized_vol",
        "_min_vol_scalar",
        "_max_vol_scalar",
        "_macro_cursor",
        "_vol",
        "_position_sign",
        "_position_quantity",
    )

    def __init__(
        self,
        *,
        symbol: str,
        trend_observations: Sequence[ObservationRow],
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
        vol_period: int = DEFAULT_VOL_LOOKBACK_PERIOD,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        target_annualized_vol: Decimal = DEFAULT_TARGET_ANNUALIZED_VOL,
        min_vol_scalar: Decimal = DEFAULT_MIN_VOL_SCALAR,
        max_vol_scalar: Decimal = DEFAULT_MAX_VOL_SCALAR,
    ) -> None:
        if reference_equity <= 0:
            raise ValueError(f"reference_equity must be positive, got {reference_equity}")

        self._symbol = symbol
        self._reference_equity = reference_equity
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar
        self._macro_cursor = MacroSeriesCursor(trend_observations)
        self._vol = RollingRealizedVolatility(period=vol_period, bars_per_day=bars_per_day)
        self._position_sign = 0
        self._position_quantity = Decimal(0)

    @property
    def position_sign(self) -> int:
        """The currently held position's sign category: `+1` (long),
        `-1` (short), or `0` (flat). Exposed read-only for testing/
        introspection, same convention as every sibling strategy.
        """
        return self._position_sign

    @property
    def position_quantity(self) -> Decimal:
        return self._position_quantity

    def __call__(self, window: Sequence[Kline]) -> OrderIntent | None:
        current = window[-1]
        realized_vol = self._vol.update(current)
        raw_trend_sign = self._macro_cursor.update(current)

        if raw_trend_sign is None:
            return None  # warmup: no real-yield trend established yet at all

        btc_signal = -raw_trend_sign  # INVERSION -- see module docstring
        current_sign = _sign(btc_signal)
        if current_sign == self._position_sign:
            return None  # Option B: silent unless the SIGN category changes

        if current_sign == 0:
            return self._flatten_to(current)

        target_quantity = self._compute_target_quantity(current, realized_vol)
        if target_quantity is None:
            # Cannot size the new leg right now (vol-targeting warmup, or
            # a degenerate price) -- still relinquish a stale position if
            # one is held, and leave position_sign at 0 so this
            # transition is retried automatically once sizing succeeds.
            return self._flatten_to(current)

        return self._transition_to(current, current_sign, target_quantity)

    def _compute_target_quantity(self, current: Kline, realized_vol: Decimal | None) -> Decimal | None:
        entry_price = current.close
        if entry_price <= 0:
            return None
        vol_scalar = compute_vol_scalar(
            realized_vol,
            target_annualized_vol=self._target_annualized_vol,
            min_scalar=self._min_vol_scalar,
            max_scalar=self._max_vol_scalar,
        )
        if vol_scalar is None:
            return None
        base_quantity = self._reference_equity / entry_price
        target_quantity = base_quantity * vol_scalar
        if target_quantity <= 0:
            return None
        return target_quantity

    def _flatten_to(self, current: Kline) -> OrderIntent | None:
        if self._position_sign == 0:
            return None
        closing_side = Side.SHORT if self._position_sign > 0 else Side.LONG
        quantity = self._position_quantity
        self._position_sign = 0
        self._position_quantity = Decimal(0)
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=closing_side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=quantity,
            limit_price=None,
            signal_timeframe="1d",
            created_at=current.open_time,
        )

    def _transition_to(self, current: Kline, new_sign: int, target_quantity: Decimal) -> OrderIntent:
        closing_quantity = self._position_quantity if self._position_sign != 0 else Decimal(0)
        order_quantity = closing_quantity + target_quantity
        side = Side.LONG if new_sign > 0 else Side.SHORT
        self._position_sign = new_sign
        self._position_quantity = target_quantity
        return OrderIntent(
            intent_id=uuid4(),
            symbol=self._symbol,
            side=side,
            order_type=OrderType.GUARDED_MARKET,
            quantity=order_quantity,
            limit_price=None,
            signal_timeframe="1d",
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


class MacroRealYieldTrendTrainable:
    """`TrainableStrategy` (`python/research/walkforward.py`'s `Protocol`)
    implementation wrapping `MacroRealYieldTrendStrategy`.

    **`fit()` performs no search whatsoever** -- `total_candidates: 1`,
    matching `daily_tsmom_ensemble.DailyTsmomEnsembleTrainable`'s own
    zero-search discipline. It builds exactly one
    `MacroRealYieldTrendStrategy` from this instance's construction-time
    constants (`lookback`, fixed at `DEFAULT_LOOKBACK_TRADING_DAYS`),
    backtests it in-sample against `train_klines` for logging purposes
    only, logs that single evaluation as its own `record_type:
    "backtest_run"` entry (`candidate_index=0, total_candidates=1`), and
    returns a **fresh** strategy instance -- never the one used for
    in-sample scoring, which would carry leftover internal state (the
    macro cursor's position, the vol estimator, an open position).

    `macro_observations` -- the RAW DFII10 `ObservationRow` series
    (including `None`/holiday rows) -- is supplied once at construction
    time and transformed into the fixed `lookback`-trading-day
    trend-sign series exactly ONCE, here in `__init__`, not recomputed on
    every `fit()` call: the transformation depends only on this
    instance's own fixed `lookback` hyperparameter, never on
    `train_klines`/`params`, so recomputing it per fold would be wasted
    work computing the identical result. The full macro history (ideally
    predating the earliest BTC bar this Trainable will ever see by at
    least `lookback` real observations) should be supplied -- unlike
    BTC's own price history, DFII10 has no per-fold "not yet available"
    boundary to respect: it is public macro data whose historical values
    were already known as of the date they are dated, regardless of
    which walk-forward fold's BTC window is currently being scored (see
    `.planning/sr-x-macro-real-yield-strategy.md` for the full reasoning
    on why this differs from BTC-price-only warmup, and why it is not a
    look-ahead-bias risk).
    """

    def __init__(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        fee_bps: Decimal,
        slippage_bps: Decimal,
        macro_observations: Sequence[ObservationRow],
        starting_equity: Decimal = DEFAULT_STARTING_EQUITY,
        bars_per_day: int = DEFAULT_BARS_PER_DAY,
        lookback: int = DEFAULT_LOOKBACK_TRADING_DAYS,
        reference_equity: Decimal = DEFAULT_REFERENCE_EQUITY,
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
        self._starting_equity = starting_equity
        self._bars_per_day = bars_per_day
        self._lookback = lookback
        self._reference_equity = reference_equity
        self._vol_period = vol_period
        self._target_annualized_vol = target_annualized_vol
        self._min_vol_scalar = min_vol_scalar
        self._max_vol_scalar = max_vol_scalar
        self._runs_path = runs_path
        # Computed exactly once -- see class docstring.
        self._trend_observations = compute_real_yield_trend_signs(macro_observations, lookback=lookback)

    @property
    def lookback(self) -> int:
        return self._lookback

    def fit(self, train_klines: list[Kline], params: Mapping[str, Any], *, parent_run_id: str) -> Strategy:
        symbol = params.get("symbol", DEFAULT_SYMBOL)

        scoring_strategy = self._build_strategy(symbol=symbol)
        backtest_result = run_backtest(train_klines, scoring_strategy, self._fee_bps, self._slippage_bps)
        metrics = compute_metrics(
            train_klines,
            backtest_result.filled_intents,
            backtest_result.fills,
            self._starting_equity,
            bars_per_day=self._bars_per_day,
        )
        self._log_candidate(symbol=symbol, train_klines=train_klines, metrics=metrics, parent_run_id=parent_run_id)

        return self._build_strategy(symbol=symbol)

    def _build_strategy(self, *, symbol: str) -> MacroRealYieldTrendStrategy:
        return MacroRealYieldTrendStrategy(
            symbol=symbol,
            trend_observations=self._trend_observations,
            reference_equity=self._reference_equity,
            vol_period=self._vol_period,
            bars_per_day=self._bars_per_day,
            target_annualized_vol=self._target_annualized_vol,
            min_vol_scalar=self._min_vol_scalar,
            max_vol_scalar=self._max_vol_scalar,
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
                "lookback": self._lookback,
                "reference_equity": str(self._reference_equity),
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
                    "in-sample scoring inside MacroRealYieldTrendTrainable.fit() -- the single, fixed, "
                    "zero-fitted-parameter DFII10-trend strategy, no search -- not itself a walk-forward run"
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
